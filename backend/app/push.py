"""Web Push (VAPID) an alle registrierten Geräte."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush

from .db import Database, now_iso

log = logging.getLogger(__name__)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class Pusher:
    def __init__(self, db: Database, data_dir: Path, subject: str) -> None:
        self.db = db
        self.subject = subject
        self.key_path = data_dir / "vapid_private.pem"
        if not self.key_path.exists():
            key = ec.generate_private_key(ec.SECP256R1())
            self.key_path.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            log.info("Neue VAPID-Schlüssel erzeugt")
        private_key = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
        self.public_key = _b64url(
            private_key.public_key().public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )
        )

    # ── Abos ─────────────────────────────────────────────────────────

    def subscribe(self, subscription: dict[str, Any], user_agent: str | None) -> None:
        keys = subscription.get("keys") or {}
        endpoint = subscription.get("endpoint")
        if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
            raise ValueError("Ungültiges Push-Abo")
        self.db.execute(
            "INSERT INTO push_subscriptions(endpoint, p256dh, auth, user_agent, created_at) "
            "VALUES(?, ?, ?, ?, ?) ON CONFLICT(endpoint) DO UPDATE SET "
            "p256dh = excluded.p256dh, auth = excluded.auth, user_agent = excluded.user_agent, failures = 0",
            (endpoint, keys["p256dh"], keys["auth"], (user_agent or "")[:200], now_iso()),
        )

    def unsubscribe(self, endpoint: str) -> None:
        self.db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))

    def devices(self) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT endpoint, user_agent, created_at, last_success, failures FROM push_subscriptions ORDER BY created_at"
        )
        return [dict(r) for r in rows]

    # ── Senden ───────────────────────────────────────────────────────

    async def send(self, title: str, body: str, url: str = "/", tag: str | None = None) -> int:
        """Schickt an alle Geräte, gibt die Anzahl erfolgreicher Zustellungen zurück."""
        rows = self.db.query("SELECT endpoint, p256dh, auth FROM push_subscriptions")
        if not rows:
            return 0
        payload = json.dumps(
            {"title": title, "body": body, "url": url, "tag": tag, "ts": now_iso()}, ensure_ascii=False
        )
        results = await asyncio.gather(
            *(asyncio.to_thread(self._send_one, dict(row), payload) for row in rows)
        )
        return sum(1 for ok in results if ok)

    def _send_one(self, row: dict[str, Any], payload: str) -> bool:
        subscription = {"endpoint": row["endpoint"], "keys": {"p256dh": row["p256dh"], "auth": row["auth"]}}
        try:
            webpush(
                subscription_info=subscription,
                data=payload,
                vapid_private_key=str(self.key_path),
                vapid_claims={"sub": self.subject},
                ttl=6 * 3600,
                headers={"Urgency": "high"},
                timeout=15,
            )
        except WebPushException as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in (404, 410):
                log.info("Push-Abo abgelaufen, entferne %s…", row["endpoint"][:60])
                self.unsubscribe(row["endpoint"])
            else:
                log.warning("Push fehlgeschlagen (%s): %s", status, exc)
                self.db.execute(
                    "UPDATE push_subscriptions SET failures = failures + 1 WHERE endpoint = ?", (row["endpoint"],)
                )
            return False
        except Exception as exc:
            log.warning("Push fehlgeschlagen: %s", exc)
            return False
        self.db.execute(
            "UPDATE push_subscriptions SET last_success = ?, failures = 0 WHERE endpoint = ?",
            (now_iso(), row["endpoint"]),
        )
        return True
