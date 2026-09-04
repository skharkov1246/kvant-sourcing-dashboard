"""Чтение шильдика по фото зрением модели (роль acceptance-extractor, L1).

«Точные данные, чтобы лучше подтягивалось»: кадр шильдика превращается в
структурные поля — бренд, обозначение, серийный номер, артикул. Модель
НЕ угадывает: видно — возвращает, не видно — честный null; нечитаемый
шильдик — legible=false и причина, это сигнал на пересъёмку с боковым
светом, а не повод сочинить номер.

Недоступность плеча (нет ключа, сеть) — None: поле остаётся ручному вводу
оператора, приложение не ломается. Та же лестница деградации, что в приёмке.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

from .acceptance import LlmTransport, TransportUnavailable
from .model_registry import Registry, TenantDataPolicy, default_registry

NAMEPLATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["legible", "confidence", "brand", "model_designation",
                 "serial_number", "article", "extra_fields"],
    "properties": {
        "legible": {"type": "boolean",
                    "description": "Шильдик в целом читаем"},
        # min/max у number API схем не поддерживает — диапазон в описании.
        "confidence": {"type": "number",
                       "description": "Уверенность в переписанном, от 0 до 1"},
        "brand": {"type": ["string", "null"]},
        "model_designation": {"type": ["string", "null"],
                              "description": "Обозначение модели/типоразмера"},
        "serial_number": {"type": ["string", "null"]},
        "article": {"type": ["string", "null"]},
        "extra_fields": {
            "type": "array",
            "description": "Прочие видимые пары «подпись: значение»",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "value"],
                "properties": {"label": {"type": "string"},
                               "value": {"type": "string"}},
            },
        },
    },
}

_SYSTEM = """Ты читаешь шильдики промышленного оборудования по фотографии.
Правила, нарушать которые запрещено:
1. Возвращай ТОЛЬКО то, что реально видно на фото. Не восстанавливай и не
   угадывай затёртые символы — лучше null, чем правдоподобная выдумка:
   по этим полям заказывают детали за деньги.
2. Если шильдик как целое нечитаем (блик, грязь, смаз) — legible=false;
   в extra_fields укажи, что именно мешает, меткой "причина".
3. Серийный номер и артикул переписывай посимвольно, включая дефисы и
   регистр. Кириллицу не транслитерируй.
4. confidence — твоя честная уверенность в переписанном (не в том, что
   шильдик существует)."""


@dataclass
class NameplateReading:
    legible: bool
    confidence: float
    brand: str | None = None
    model_designation: str | None = None
    serial_number: str | None = None
    article: str | None = None
    extra_fields: list[dict[str, str]] = field(default_factory=list)
    model_used: str | None = None


def read_nameplate(
    image_bytes: bytes,
    mime: str,
    transport: LlmTransport,
    tenant: TenantDataPolicy,
    registry: Registry | None = None,
) -> NameplateReading | None:
    """None — плечо недоступно (нет ключа/сети): поле остаётся ручному вводу."""
    resolution = (registry or default_registry()).resolve("acceptance-extractor", tenant)
    params = resolution.binding.request_params()
    try:
        result = transport.complete(
            params=params,
            system=_SYSTEM,
            user_content="Прочитай шильдик на фото и заполни поля схемы.",
            schema=NAMEPLATE_SCHEMA,
            images=[(mime, base64.b64encode(image_bytes).decode("ascii"))],
        )
    except TransportUnavailable:
        return None
    doc = json.loads(result.text)
    return NameplateReading(
        legible=doc["legible"],
        confidence=doc["confidence"],
        brand=doc.get("brand"),
        model_designation=doc.get("model_designation"),
        serial_number=doc.get("serial_number"),
        article=doc.get("article"),
        extra_fields=doc.get("extra_fields") or [],
        model_used=result.model,
    )
