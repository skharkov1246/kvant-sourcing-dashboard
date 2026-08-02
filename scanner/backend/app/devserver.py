"""Дев-сервер: приложение + фикстурные данные.

Для локальной разработки и живых смоков (jvm-check/LiveBackendSmokeTest):
    python -m uvicorn app.devserver:app --port 8077

Отличие от боевого app.main:app одно — при старте в хранилище кладётся
активный протокол из фикстур, чтобы inbound-задания и pull работали сразу.
В прод такой модуль не едет: там протоколы публикует технолог через реестр.
"""

import json
import pathlib

from .main import STORE, app  # noqa: F401  (app — точка входа uvicorn)

_EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "docs" / "schemas" / "examples"

STORE.put_protocol(json.loads((_EXAMPLES / "pallet_general_v7.json").read_text(encoding="utf-8")))
