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

# Маршрут «идентификация невозможна» — для смока сервисных исходов
# (kv_no_id → задача с дедлайном по рабочим дням + service_outcome в реестре).
STORE.put_protocol(json.loads((_EXAMPLES / "ri" / "kv_no_id.json").read_text(encoding="utf-8")))

# Снапшот поставщиков — как будто его уже опубликовал поллер Bitrix (§9.5):
# подсказки в форме трассировки работают на дев-стенде без портала.
STORE.put_dynamic_directory("suppliers", {
    "suppliers": {
        "title": "Поставщики (CRM)",
        "values": [
            {"name": "ООО Уплотнитель", "inn": "7701234567", "ref": "101"},
            {"name": "Ningbo Seals Co", "inn": None, "ref": "102"},
        ],
    },
})
