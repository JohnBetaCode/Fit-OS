"""Configuración de proceso (variables de entorno). Lo que el usuario cambia en caliente vive en la tabla
`settings` (ver `core.prefs`)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    app_env: Literal["production", "development", "test"] = "production"
    default_language: Literal["es", "en"] = "en"  # UI and WhatsApp; overridable in Ajustes/Settings
    tz: str = "UTC"  # zona del usuario (TZ): fechas de calendario, plan de la mañana, horas de silencio
    log_level: str = "INFO"

    data_dir: Path = Path("/data")
    # Only for `make revision` (Alembic autogenerate against a scratch DB). The app itself opens one database
    # per person under <data_dir>/users/ (core/tenancy.py) and ignores it.
    database_url: str = ""

    portal_base_url: str = ""
    trust_tailscale_headers: bool = True
    owner_logins: str = ""  # coma-separado: logins de Tailscale con acceso
    dev_user_login: str = ""
    peer_net_cidr: str = ""  # red wa-shared: solo /healthz y /webhooks/waha
    # Secreto que Caddy (ops/caddy/Caddyfile) pone en X-Fitos-Proxy tras el basic_auth. Vacío = no se
    # confía en ningún proxy.
    proxy_secret: str = ""
    secrets_key: str = ""

    waha_mode: Literal["live", "fake"] = "live"
    waha_url: str = "http://waha:3000"
    waha_api_key: str = ""
    waha_session: str = "default"
    waha_webhook_hmac_key: str = ""
    wa_gym_chat_id: str = ""
    # session = webhook de sesión en un WAHA compartido (ops/waha-webhook.sh); global = WAHA propio con
    # WHATSAPP_HOOK_URL (perfil `waha` del compose), sin webhook de sesión que vigilar.
    waha_hook_mode: Literal["session", "global"] = "session"
    wa_max_per_hour: int = 120
    wa_send_gap_seconds: float = 0.8

    anthropic_api_key: str = ""
    # WHOOP (integrations/whoop.py): one app per server at developer-dashboard.whoop.com
    whoop_client_id: str = ""
    whoop_client_secret: str = ""
    llm_mode: Literal["live", "fake", "off"] = "off"
    llm_coach_model: str = "claude-opus-5-5"
    llm_fast_model: str = "claude-haiku-5-5"

    git_sha: str = "dev"
    git_branch: str = ""  # the remote branch whose tip was deployed (ops/server.sh); admins see it in the footer

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"

    @property
    def legacy_db_path(self) -> Path:
        """Where the single-person database lived before accounts (`fitos users adopt-legacy` moves it)."""
        return self.data_dir / "db" / "fitos.sqlite"

    @property
    def owners(self) -> set[str]:
        return {x.strip().lower() for x in self.owner_logins.split(",") if x.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
