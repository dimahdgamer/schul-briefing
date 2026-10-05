"""Client für die (inoffizielle) JSON-API von Schulmanager Online.

Ablauf, rekonstruiert aus dem Web-Client und mehreren Open-Source-Projekten:
  1. POST /api/get-salt  -> Salt (als JSON-String)
  2. PBKDF2-SHA512(passwort, salt, 99 999 Iterationen, 512 Byte) als Hex
  3. POST /api/login     -> { jwt, user } | multipleAccounts | 2FA-Flags
  4. POST /api/calls     -> gebündelte RPC-Aufrufe, Ergebnisse positionell
Das JWT wird über den Antwort-Header `x-new-bearer-token` laufend erneuert.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://login.schulmanager-online.de"
BUNDLE_VERSION = "3505280ee7"  # Pflichtfeld, wird vom Server nicht geprüft
USER_AGENT = "SchulBriefing/1.0 (privat, inoffiziell; ein Konto, max. 1 Abruf / 10 Min.)"
PBKDF2_ITERATIONS = 99_999
PBKDF2_BYTES = 512


class SchulmanagerError(Exception):
    def __init__(self, message: str, status: int = 0, kind: str = "unknown") -> None:
        super().__init__(message)
        self.status = status
        self.kind = kind


class LoginError(SchulmanagerError):
    pass


@dataclass
class RpcCall:
    module: str
    endpoint: str
    parameters: dict[str, Any] | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "moduleName": self.module,
            "endpointName": self.endpoint,
            "parameters": self.parameters or {},
        }


@dataclass
class RpcResult:
    status: int
    data: Any

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def salted_hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha512", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS, PBKDF2_BYTES
    ).hex()


class SchulmanagerClient:
    def __init__(
        self,
        email: str,
        password: str,
        load_token: Callable[[], dict[str, Any] | None] | None = None,
        save_token: Callable[[dict[str, Any] | None], None] | None = None,
    ) -> None:
        self.email = email
        self.password = password
        self._load_token = load_token or (lambda: None)
        self._save_token = save_token or (lambda _value: None)
        self._http: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._last_request = 0.0

        stored = self._load_token() or {}
        self.token: str | None = stored.get("jwt")
        self.user: dict[str, Any] = stored.get("user") or {}

    # ── HTTP ─────────────────────────────────────────────────────────

    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=BASE_URL,
                timeout=httpx.Timeout(30.0, connect=10.0),
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    async def _post(self, path: str, body: Any, auth: bool = True) -> httpx.Response:
        # Mindestabstand zwischen zwei Requests, damit wir den Server nie fluten
        wait = 1.0 - (time.monotonic() - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        headers = {}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = await self._client().post(path, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise SchulmanagerError(f"Netzwerkfehler bei {path}: {exc}", 0, "network") from exc
        finally:
            self._last_request = time.monotonic()

        rotated = response.headers.get("x-new-bearer-token")
        if rotated and auth:
            self.token = rotated
            self._persist()
        return response

    def _persist(self) -> None:
        self._save_token({"jwt": self.token, "user": self.user} if self.token else None)

    # ── Login ────────────────────────────────────────────────────────

    async def login(self) -> dict[str, Any]:
        salt_resp = await self._post(
            "/api/get-salt", {"emailOrUsername": self.email, "mobileApp": False, "institutionId": None}, auth=False
        )
        if salt_resp.status_code >= 400:
            raise LoginError(f"Salt-Abfrage fehlgeschlagen (HTTP {salt_resp.status_code})", salt_resp.status_code, "auth")
        salt = salt_resp.json()
        if isinstance(salt, dict):
            salt = salt.get("salt", "")
        password_hash = salted_hash(self.password, salt) if salt else None

        resp = await self._post(
            "/api/login",
            {
                "emailOrUsername": self.email,
                "password": self.password,
                "hash": password_hash,
                "mobileApp": False,
                "twoFactorCode": None,
                "userId": None,
                "institutionId": None,
            },
            auth=False,
        )
        if resp.status_code == 401:
            raise LoginError("Benutzername oder Passwort falsch.", 401, "auth")
        if resp.status_code >= 400:
            raise LoginError(f"Login fehlgeschlagen (HTTP {resp.status_code})", resp.status_code, "auth")

        data = resp.json() or {}
        if data.get("multipleAccounts"):
            raise LoginError(
                "Mehrere Konten mit dieser E-Mail gefunden. Bitte den Benutzernamen statt der E-Mail verwenden.",
                409,
                "auth",
            )
        if data.get("requireTwoFactorEmailCode") or data.get("requireTOTP"):
            raise LoginError("Das Konto verlangt Zwei-Faktor-Anmeldung, das wird noch nicht unterstützt.", 403, "auth")

        token = data.get("jwt") or data.get("token")
        if not token:
            raise LoginError(f"Login lieferte kein Token (Felder: {sorted(data.keys())})", 500, "auth")

        self.token = token
        self.user = data.get("user") or {}
        self._persist()
        log.info("Bei Schulmanager angemeldet als %s", self.display_name)
        return self.user

    @property
    def display_name(self) -> str:
        first = self.user.get("firstname") or ""
        last = self.user.get("lastname") or ""
        return f"{first} {last}".strip() or self.email

    @property
    def student(self) -> dict[str, Any] | None:
        student = self.user.get("associatedStudent")
        if student:
            return student
        parents = self.user.get("associatedParents") or []
        for link in parents:
            if isinstance(link, dict) and link.get("student"):
                return link["student"]
        return None

    @property
    def institution_id(self) -> Any:
        return self.user.get("institutionId")

    # ── RPC ──────────────────────────────────────────────────────────

    async def calls(self, calls: list[RpcCall]) -> list[RpcResult]:
        """Mehrere Aufrufe in einem einzigen HTTP-Request."""
        async with self._lock:
            if not self.token or not self.user:
                await self.login()

            body = {"bundleVersion": BUNDLE_VERSION, "requests": [c.payload() for c in calls]}
            resp = await self._post("/api/calls", body)
            if resp.status_code == 401:
                log.info("Token abgelaufen, melde neu an")
                await self.login()
                resp = await self._post("/api/calls", body)

            if resp.status_code == 429:
                raise SchulmanagerError("Rate-Limit erreicht", 429, "rate-limit")
            if resp.status_code == 503:
                raise SchulmanagerError("Schulmanager wird gewartet", 503, "maintenance")
            if resp.status_code >= 400:
                raise SchulmanagerError(f"HTTP {resp.status_code} bei /api/calls", resp.status_code)

            envelope = resp.json()
            raw_results = envelope.get("results", []) if isinstance(envelope, dict) else envelope
            results: list[RpcResult] = []
            for index in range(len(calls)):
                raw = raw_results[index] if index < len(raw_results) else None
                if not isinstance(raw, dict):
                    results.append(RpcResult(status=0, data=None))
                    continue
                status = raw.get("status", 200)
                if isinstance(status, str):
                    status = 200 if status in {"ok", "success"} else 500
                results.append(RpcResult(status=int(status), data=raw.get("data")))
            return results
