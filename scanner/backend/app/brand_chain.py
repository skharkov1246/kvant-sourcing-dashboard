"""Многоуровневый бренд детали: кто её сделал, в чьём узле, в чьей установке.

Реальность склада: фильтр PALL стоит в насосе FISHER, насос — в турбине
SIEMENS. Все трое напечатают на детали свой артикул и продадут её по своей
цене. Плоское поле «бренд» здесь врёт всегда: какое из трёх имён в него
ни положи, две трети правды потеряно, а именно эта разница и есть маржа
снабжения — купить у изготовителя, а не у владельца шильдика.

Поэтому у детали не бренд, а ДВЕ разные структуры:

1. Цепочка установки (`level`) — ГДЕ деталь стоит: установка → узел →
   деталь. Это контекст: по нему ищут «что ещё есть в этой турбине» и
   определяют неизвестную деталь по соседям.
2. Ссылки бренда (`BrandRef`) — ЧТО на ней написано: у одной физической
   детали столько пар «бренд + артикул», сколько рук её перепродало.
   Роль каждой пары названа явно (изготовитель / OEM узла / OEM установки
   / частная марка / дистрибьютор), и это НЕ разные детали, а один
   предмет под разными номерами — то есть готовые пути закупки.

Уровень доверия у каждой ссылки свой и тот же, что в трассировке (§06.1):
`verified` — подтверждено документом или реестром, `declared` — со слов,
`inferred` — выведено системой (OCR шильдика, префикс GS1). Роль без
доверия бессмысленна: «изготовитель PALL со слов кладовщика» и
«изготовитель PALL по инвойсу» — разные основания для закупки.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Уровни установки, от общего к частному. Порядок значим: он же — порядок
#: сужения поиска и порядок, в котором цепочка показывается человеку.
LEVELS: tuple[str, ...] = ("equipment", "assembly", "part")

LEVEL_TITLES = {
    "equipment": "установка",
    "assembly": "узел",
    "part": "деталь",
}

#: Роли бренда у ОДНОЙ физической детали. Порядок — «ближе к изготовителю
#: → дальше», он же порядок предпочтения при закупке: дешевле всего у того,
#: кто сделал, дороже всего у того, чей шильдик на установке.
BRAND_ROLES: tuple[str, ...] = (
    "manufacturer",     # физический изготовитель: PALL
    "assembly_oem",     # чей узел: FISHER
    "equipment_oem",    # чья установка: SIEMENS
    "private_label",    # частная марка (перемаркировка без производства)
    "distributor",      # дистрибьютор/поставщик под своим номером
)

ROLE_TITLES = {
    "manufacturer": "изготовитель",
    "assembly_oem": "OEM узла",
    "equipment_oem": "OEM установки",
    "private_label": "частная марка",
    "distributor": "дистрибьютор",
}

CONFIDENCE = ("verified", "declared", "inferred")

#: Уверенность, ниже которой путь закупки нельзя предлагать как рабочий.
TRUSTED = ("verified", "declared")


class BrandChainError(ValueError):
    """Нарушение правила бренд-цепочки, которое сервер отклоняет."""


def normalize_article(article: str) -> str:
    """Артикул для СРАВНЕНИЯ: регистр, пробелы и разделители не значимы.

    `HC 9600-FKS`, `hc9600fks` и `HC9600 FKS` — один и тот же фильтр; без
    нормализации база наплодит три позиции вместо одной. Исходная строка
    при этом сохраняется как есть — слияние обязано быть обратимым.
    """
    return re.sub(r"[^0-9a-zа-яё]", "", article.strip().lower())


@dataclass(frozen=True)
class BrandRef:
    """Пара «бренд + артикул» с ролью: один путь купить эту деталь."""

    brand: str
    article: str | None
    role: str
    confidence: str
    #: Кадры-основания: шильдик, упаковка, инвойс.
    evidence_asset_ids: list[str] = field(default_factory=list)
    #: Уровень установки, на котором это имя прочитано (для контекста).
    level: str | None = None

    def __post_init__(self) -> None:
        if self.role not in BRAND_ROLES:
            raise BrandChainError(
                f"Неизвестная роль бренда: {self.role}; допустимы {BRAND_ROLES}")
        if self.confidence not in CONFIDENCE:
            raise BrandChainError(f"Недопустимый уровень доверия: {self.confidence}")
        if self.level is not None and self.level not in LEVELS:
            raise BrandChainError(f"Неизвестный уровень установки: {self.level}")
        if not self.brand.strip():
            raise BrandChainError("Бренд не может быть пустым — есть служебное "
                                  "значение «БРЕНД НЕ ОПРЕДЕЛЁН»")

    @property
    def article_norm(self) -> str | None:
        return normalize_article(self.article) if self.article else None

    def as_dict(self) -> dict:
        return {
            "brand": self.brand,
            "article": self.article,
            "article_norm": self.article_norm,
            "role": self.role,
            "role_title": ROLE_TITLES[self.role],
            "confidence": self.confidence,
            "level": self.level,
            "evidence_asset_ids": list(self.evidence_asset_ids),
        }


@dataclass(frozen=True)
class InstallationNode:
    """Звено цепочки «где стоит»: установка, узел или сама деталь."""

    level: str
    brand: str | None = None
    designation: str | None = None   # SST-300, D4 Control Valve
    position: str | None = None      # «поз. 14», «нагнетание»
    #: Заводской номер — только у установки он делает её ОДНОЙ физической
    #: машиной, а не типом: две турбины SST-300 на площадке — разные обходы.
    serial: str | None = None

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise BrandChainError(f"Неизвестный уровень установки: {self.level}")

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "level_title": LEVEL_TITLES[self.level],
            "brand": self.brand,
            "designation": self.designation,
            "position": self.position,
            "serial": self.serial,
        }


def installation_key(node: InstallationNode) -> str | None:
    """Ключ ФИЗИЧЕСКОЙ установки: бренд + обозначение + заводской номер.

    По нему детали разных сессий собираются в один обход. Без заводского
    номера ключ всё равно строится (по бренду и обозначению), но тогда две
    одинаковые машины на площадке сольются в одну — поэтому шаг съёмки
    шильдика установки просит номер, а не «тип».
    """
    if node.level != "equipment":
        return None
    parts = [normalize_article(p) for p in
             (node.brand, node.designation, node.serial) if p]
    return "|".join(p for p in parts if p) or None


def validate_installation(nodes: list[InstallationNode]) -> list[InstallationNode]:
    """Цепочка установки: сверху вниз, без повторов уровня и без дыр в конце.

    Пропуск СЕРЕДИНЫ разрешён намеренно: «фильтр в турбине Siemens, чей
    насос неизвестен» — честное состояние знания, а не ошибка ввода.
    Отсутствие самой детали (`part`) — ошибка: цепочка описывает деталь.
    """
    seen = [n.level for n in nodes]
    if len(seen) != len(set(seen)):
        raise BrandChainError("Уровень установки повторяется — цепочка не дерево")
    if "part" not in seen:
        raise BrandChainError("В цепочке нет самой детали (level=part)")
    return sorted(nodes, key=lambda n: LEVELS.index(n.level))


def procurement_paths(refs: list[BrandRef]) -> list[dict]:
    """Пути закупки одной детали, от изготовителя к владельцу установки.

    Это и есть ответ снабженцу: «тот же фильтр можно взять у PALL по
    HC9600FKS, у FISHER по 12-345 или у SIEMENS по A5E00...» — три цены на
    один предмет. Непроверенные (`inferred`) пути не выбрасываются, но
    помечаются: по догадке OCR заказ не делают, её сначала подтверждают.
    """
    ordered = sorted(refs, key=lambda r: (BRAND_ROLES.index(r.role),
                                          r.confidence not in TRUSTED))
    return [
        {**ref.as_dict(), "actionable": ref.confidence in TRUSTED}
        for ref in ordered
    ]


def equivalent_articles(refs: list[BrandRef]) -> dict[str, list[str]]:
    """Нормализованный артикул → бренды, под которыми он встречается.

    Собирается для поиска: пришёл артикул с чужой накладной — по нему
    находится вся семья имён той же детали, включая изготовителя.
    """
    out: dict[str, list[str]] = {}
    for ref in refs:
        key = ref.article_norm
        if not key:
            continue
        if ref.brand not in out.setdefault(key, []):
            out[key].append(ref.brand)
    return out


def conflicting_manufacturers(refs: list[BrandRef]) -> list[str]:
    """Два РАЗНЫХ изготовителя у одной детали — спор, а не данные.

    Такое бывает честно (перемаркировка, смена контрактного завода), но
    решать это должен человек: молча выбрать один бренд — значит однажды
    заказать не ту деталь.
    """
    names = sorted({r.brand for r in refs
                    if r.role == "manufacturer" and r.confidence in TRUSTED})
    return names if len(names) > 1 else []
