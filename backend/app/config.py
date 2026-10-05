"""Konfiguration aus Umgebungsvariablen (.env wird von Docker Compose geladen)."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "ja"}


@dataclass(frozen=True)
class Config:
    sm_email: str
    sm_password: str
    app_password: str
    secret_key: str
    public_url: str
    vapid_subject: str
    data_dir: Path
    static_dir: Path
    tz: ZoneInfo
    demo: bool
    subdivision: str

    @property
    def https(self) -> bool:
        return self.public_url.startswith("https://")


def _persistent_secret(data_dir: Path) -> str:
    path = data_dir / "secret.key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    value = secrets.token_urlsafe(48)
    path.write_text(value, encoding="utf-8")
    return value


def load() -> Config:
    root = Path(__file__).resolve().parent.parent.parent
    data_dir = Path(os.environ.get("DATA_DIR", root / "data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "raw").mkdir(exist_ok=True)

    static_dir = Path(os.environ.get("STATIC_DIR", root / "frontend")).resolve()

    demo = _bool(os.environ.get("DEMO_MODE"))
    sm_email = os.environ.get("SM_EMAIL", "").strip()
    sm_password = os.environ.get("SM_PASSWORD", "")
    if not demo and (not sm_email or not sm_password):
        raise RuntimeError(
            "SM_EMAIL und SM_PASSWORD fehlen. Entweder in .env eintragen oder DEMO_MODE=1 setzen."
        )

    app_password = os.environ.get("APP_PASSWORD", "")
    if not app_password:
        raise RuntimeError("APP_PASSWORD fehlt. Damit wird das Dashboard geschützt.")

    return Config(
        sm_email=sm_email,
        sm_password=sm_password,
        app_password=app_password,
        secret_key=os.environ.get("SECRET_KEY") or _persistent_secret(data_dir),
        public_url=os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/"),
        vapid_subject=os.environ.get("VAPID_SUBJECT", "mailto:admin@example.org"),
        data_dir=data_dir,
        static_dir=static_dir,
        tz=ZoneInfo(os.environ.get("TZ", "Europe/Berlin")),
        demo=demo,
        subdivision=os.environ.get("HOLIDAY_SUBDIVISION", "DE-NW"),
    )
