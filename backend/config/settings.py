"""
Application settings — letti da variabili d'ambiente o da .env (sviluppo locale).

Variabili supportate:
  HOST              Indirizzo di ascolto del server           (default: 0.0.0.0)
  PORT              Porta del server                          (default: 8000)
  ALLOWED_ORIGINS   Origini CORS autorizzate, separate da ,   (default: localhost)
"""

from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

# Cerca il file .env nella root del progetto (due livelli sopra questo file)
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8000
    allowed_origins: str = "http://localhost:3000,http://localhost:5173"

    # Telegram Bot — opzionale; se vuoti le notifiche vengono saltate silenziosamente
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @property
    def allowed_origins_list(self) -> List[str]:
        """Restituisce le origini CORS come lista (split su virgola)."""
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


settings = Settings()
