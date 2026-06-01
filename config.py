"""Конфигурация инструмента анализа работы сорсеров.

Все секреты и настройки берутся из .env (см. .env.example).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# --- доменные константы КВАНТ · Bitrix24 (подтверждены разведкой портала) ---
SPA_ENTITY_TYPE_ID = 166          # смарт-процесс «Запросы поставщикам»
SPA_CATEGORY_ID = 24              # воронка СП-166
DEPT_SOURCING_ID = 172            # «Отдел поиска поставщиков» (глава — Artem Grinev, 76)
PERIOD_FLOOR = "2026-01-01"       # жёсткий пол по дате создания записей
DEFAULT_PERIOD_ANCHOR = "2026-05-01"  # дефолтный старт отчётного окна


@dataclass
class Settings:
    bitrix_webhook_url: str
    anthropic_api_key: str
    model: str = "claude-opus-4-8"
    max_workers: int = 6

    @classmethod
    def load(cls) -> "Settings":
        webhook = (os.getenv("BITRIX_WEBHOOK_URL") or "").strip().rstrip("/") + "/"
        if not webhook or "rest/" not in webhook:
            raise SystemExit(
                "BITRIX_WEBHOOK_URL не задан или неверный. Формат: "
                "https://<портал>.bitrix24.ru/rest/<id>/<токен>/  (см. .env)"
            )
        key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
        return cls(
            bitrix_webhook_url=webhook,
            anthropic_api_key=key,
            model=(os.getenv("SOURCING_MODEL") or "claude-opus-4-8").strip(),
            max_workers=int(os.getenv("SOURCING_MAX_WORKERS") or "6"),
        )

    def require_anthropic(self) -> None:
        if not self.anthropic_api_key:
            raise SystemExit(
                "ANTHROPIC_API_KEY не задан в .env — нужен для LLM-анализа. "
                "Для выгрузки без анализа используйте флаг --dry-run."
            )
