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

# Служебные учётные записи: от их имени карточки заводит робот пресейла и
# вебхуки интеграций. Их нельзя считать исполнителями — иначе работа сорсера,
# запустившего кампанию, исчезает из его статистики и оседает на роботе.
#
# Состав списка меняется: робота могут пересоздать, к нему добавится вебхук
# другой интеграции, а нынешняя запись может однажды стать обычной. Поэтому
# значение в коде — только текущее значение по умолчанию, названное владельцем
# 23.09.2026, и его переопределяет переменная окружения SERVICE_ACCOUNT_IDS
# (идентификаторы через запятую). Правка списка не требует изменения кода и
# деплоя. Кандидатов на добавление дашборд показывает сам — см.
# metrics.origin.serviceCandidates, а запись, числящуюся живым сотрудником,
# помечает предупреждением.
#
# Отключается разбор словом «off», а не пустым значением: GitHub Actions
# подставляет в env пустую строку для каждой НЕзаданной переменной, и «пусто =
# выключено» тихо обнулило бы список на каждом прогоне, где переменную ещё не
# завели. Пустое значение здесь означает «переменной нет» и берётся умолчание.
# 1718 — робот воронки запросов поставщикам, подтверждён владельцем 23.09.2026
# и замером: 138 карточек за отчётное окно, единственная запись IT-отдела среди
# ответственных, карточку создаёт и двигает сам в 88 % случаев.
# 2 — служебная запись портала; в СП-166 не появлялась ни разу за всё время
# (замер того же дня), оставлена в списке как заведомо не-человек.
SERVICE_ACCOUNT_DEFAULT = "1718,2"
SERVICE_ACCOUNT_OFF = "off"
_service_env = (os.getenv("SERVICE_ACCOUNT_IDS") or "").strip()
_service_raw = "" if _service_env.lower() == SERVICE_ACCOUNT_OFF else (
    _service_env or SERVICE_ACCOUNT_DEFAULT)
SERVICE_ACCOUNT_IDS = {u.strip() for u in _service_raw.split(",") if u.strip()}
# ФАЙЛЫ КП СО СТОРОНЫ ПОСТАВЩИКА в карточке СП-166. Закрытый список: добавлять
# поле сюда можно, только убедившись, что его кладёт поставщик, а не мы. «Request
# file» — наш исходящий запрос, и считать его за полученное КП значит объявить
# прокотированным то, что мы сами же и отправили.
#
# Почему КП считаются по файлам, а не по письмам: замер прогона 23.09.2026 —
# 6 150 писем на карточках СП-166 за окно, из них входящих НОЛЬ. Ответы
# поставщиков в карточку письмами не попадают вовсе, они лежат в этих полях.
RFQ_QUOTE_FIELDS = {
    "ufCrm18_1700698211875": "КП поставщика",
    "ufCrm18_1703711961310": "Offer, old",
    "ufCrm18_1703712059311": "Processed offer",
    "ufCrm18_1703712074559": "Processed offer / archive",
    "ufCrm18_1731179998": "Offer from supplier",
}

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
