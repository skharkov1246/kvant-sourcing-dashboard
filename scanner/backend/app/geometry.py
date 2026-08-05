"""Источники геометрии: от рулетки до промышленного сканера — в одну базу.

Методы замера различаются на четыре порядка по точности (рулетка ±5 мм,
сканер ±0.03 мм), и это НЕ повод держать их в разных системах. Одна деталь
может быть снята телефоном на объекте, потом переснята фотограмметрией по
тем же кадрам, потом отсканирована в цехе — и все три результата обязаны
лежать на одной карточке, каждый со своей заявленной погрешностью.

Три правила, ради которых модуль существует:

1. **Ничего не перезаписывается.** Уточнение — это НОВАЯ запись со ссылкой
   `refined_from` (I-5). Иначе однажды выяснится, что «точный» результат
   затёр исходный, и спор с поставщиком не на чем строить.
2. **Точнее — не значит правее.** Если два источника расходятся больше
   суммы их погрешностей, это не «берём который точнее», а спор: скорее
   всего мерили РАЗНЫЕ детали или разные места. Молчаливый выбор здесь
   однажды закажет не ту деталь.
3. **Погрешность заявляется методом, а не гадается.** У каждого источника
   есть паспортная величина; у поверяемых средств (сканер, штангенциркуль)
   она берётся из свидетельства о поверке и попадает под ту же отсечку
   просроченных СИ, что и ручной инструмент (Р-08).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Источники геометрии. tolerance_mm — паспортная погрешность метода;
#: accuracy_class — буква лестницы деградации (§05.2), по ней протокол
#: решает, годится ли метод для шага.
SOURCES: dict[str, dict[str, Any]] = {
    "manual_tape": {
        "title": "рулетка/линейка вручную",
        "tolerance_mm": 5.0, "accuracy_class": "D",
        "needs_evidence": True,     # класс D всегда с фото рулетки (§05.2)
        "needs_instrument": False,
    },
    "photo_marker": {
        "title": "фото с опорным маркером",
        "tolerance_mm": 2.0, "accuracy_class": "C",
        "needs_evidence": True, "needs_instrument": False,
    },
    "ar_depth": {
        "title": "AR-глубина телефона (ARCore/ARKit)",
        "tolerance_mm": 15.0, "accuracy_class": "B",
        "needs_evidence": False, "needs_instrument": False,
    },
    "lidar": {
        "title": "LiDAR телефона",
        "tolerance_mm": 4.0, "accuracy_class": "A",
        "needs_evidence": False, "needs_instrument": False,
    },
    "photogrammetry": {
        "title": "фотограмметрия по кадрам (серверный расчёт)",
        "tolerance_mm": 1.0, "accuracy_class": "A",
        # Масштаб берётся из маркера в кадре — без него облако точек
        # безразмерное, и «точность 1 мм» была бы враньём.
        "needs_evidence": True, "needs_instrument": False,
    },
    "structured_light": {
        "title": "структурированный свет",
        "tolerance_mm": 0.1, "accuracy_class": "A",
        "needs_evidence": False, "needs_instrument": True,
    },
    "industrial_scanner": {
        "title": "промышленный 3D-сканер",
        "tolerance_mm": 0.03, "accuracy_class": "A",
        "needs_evidence": False, "needs_instrument": True,
    },
}

#: Что метод оставляет после себя.
ARTIFACT_KINDS = ("dimensions", "point_cloud", "mesh", "profile")


class GeometryError(ValueError):
    """Нарушение правил источников геометрии, которое сервер отклоняет."""


@dataclass
class GeometryArtifact:
    """Результат одного замера одним методом.

    `asset_id` — файл в объектном хранилище (облако точек, меш, профиль);
    геометрия тяжёлая, в базе живёт ссылка и сводка, а не байты.
    """

    item_id: str
    source: str
    kind: str = "dimensions"
    dimensions_mm: dict[str, float] = field(default_factory=dict)
    asset_id: str | None = None
    #: Фактическая погрешность: у поверяемых средств — из свидетельства,
    #: иначе паспортная величина метода.
    tolerance_mm: float | None = None
    instrument_id: str | None = None
    operator_id: str | None = None
    #: Ссылка на уточняемую запись (I-5): цепочка уточнений восстановима.
    refined_from: str | None = None
    evidence_asset_ids: list[str] = field(default_factory=list)
    created_at: str | None = None
    id: str | None = None

    def __post_init__(self) -> None:
        spec = SOURCES.get(self.source)
        if spec is None:
            raise GeometryError(
                f"Неизвестный источник геометрии: {self.source}; "
                f"допустимы {sorted(SOURCES)}")
        if self.kind not in ARTIFACT_KINDS:
            raise GeometryError(f"Неизвестный вид артефакта: {self.kind}")
        if spec["needs_instrument"] and not self.instrument_id:
            raise GeometryError(
                f"{spec['title']}: обязателен идентификатор средства измерения — "
                "по нему проверяется поверка (Р-08)")
        if spec["needs_evidence"] and not self.evidence_asset_ids:
            raise GeometryError(
                f"{spec['title']}: обязательны кадры-основания — без них "
                "заявленная точность ничем не подтверждена")
        if self.kind == "dimensions" and not self.dimensions_mm:
            raise GeometryError("Замер без единого размера — не результат")
        if self.kind != "dimensions" and not self.asset_id:
            raise GeometryError(
                f"Артефакт «{self.kind}» без файла: облако точек и меш живут "
                "в объектном хранилище, в базе — ссылка")
        if self.tolerance_mm is None:
            self.tolerance_mm = spec["tolerance_mm"]
        if self.tolerance_mm <= 0:
            raise GeometryError("Погрешность должна быть положительной")

    @property
    def accuracy_class(self) -> str:
        return SOURCES[self.source]["accuracy_class"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "item_id": self.item_id,
            "source": self.source,
            "source_title": SOURCES[self.source]["title"],
            "kind": self.kind,
            "dimensions_mm": dict(self.dimensions_mm),
            "asset_id": self.asset_id,
            "tolerance_mm": self.tolerance_mm,
            "accuracy_class": self.accuracy_class,
            "instrument_id": self.instrument_id,
            "operator_id": self.operator_id,
            "refined_from": self.refined_from,
            "evidence_asset_ids": list(self.evidence_asset_ids),
            "created_at": self.created_at,
        }


def best_dimensions(artifacts: list[GeometryArtifact]) -> dict[str, dict[str, Any]]:
    """По каждому размеру — значение метода с наименьшей погрешностью.

    Возвращает не голое число, а число С ПРОИСХОЖДЕНИЕМ: чем мерили, с
    какой погрешностью, какие ещё значения есть. «Одного числа», за которым
    не видно источника, в этой системе не бывает (тот же принцип, что у
    трассировки, §06.1).
    """
    out: dict[str, dict[str, Any]] = {}
    for art in sorted(artifacts, key=lambda a: a.tolerance_mm or 1e9):
        for key, value in art.dimensions_mm.items():
            slot = out.setdefault(key, {
                "value_mm": value,
                "tolerance_mm": art.tolerance_mm,
                "source": art.source,
                "source_title": SOURCES[art.source]["title"],
                "accuracy_class": art.accuracy_class,
                "others": [],
            })
            if slot["source"] != art.source or slot["value_mm"] != value:
                slot["others"].append({
                    "value_mm": value, "source": art.source,
                    "tolerance_mm": art.tolerance_mm,
                })
    return out


def measurement_conflicts(artifacts: list[GeometryArtifact],
                          slack: float = 1.0) -> list[dict[str, Any]]:
    """Расхождения больше суммы погрешностей — спор, а не «берём точнее».

    Сканер ±0.03 говорит 100.0, рулетка ±5 говорит 250 — это не «рулетка
    ошиблась на 150 мм», это мерили разные детали или разные места. Такое
    решает человек; `slack` — множитель запаса на честный разброс базы.
    """
    out: list[dict[str, Any]] = []
    by_key: dict[str, list[GeometryArtifact]] = {}
    for art in artifacts:
        for key in art.dimensions_mm:
            by_key.setdefault(key, []).append(art)

    for key, arts in by_key.items():
        for i, a in enumerate(arts):
            for b in arts[i + 1:]:
                delta = abs(a.dimensions_mm[key] - b.dimensions_mm[key])
                allowed = ((a.tolerance_mm or 0) + (b.tolerance_mm or 0)) * slack
                if delta > allowed:
                    out.append({
                        "dimension": key,
                        "delta_mm": round(delta, 3),
                        "allowed_mm": round(allowed, 3),
                        "left": {"source": a.source, "value_mm": a.dimensions_mm[key]},
                        "right": {"source": b.source, "value_mm": b.dimensions_mm[key]},
                        "note": "расхождение больше суммы погрешностей — "
                                "проверьте, что мерили одну деталь и одно место",
                    })
    return sorted(out, key=lambda c: -c["delta_mm"])


def refinement_chain(artifacts: list[GeometryArtifact],
                     artifact_id: str) -> list[str]:
    """Цепочка уточнений от записи к её первоисточнику (I-5)."""
    by_id = {a.id: a for a in artifacts if a.id}
    chain: list[str] = []
    current = by_id.get(artifact_id)
    seen: set[str] = set()
    while current is not None and current.id and current.id not in seen:
        seen.add(current.id)
        chain.append(current.id)
        current = by_id.get(current.refined_from) if current.refined_from else None
    return chain
