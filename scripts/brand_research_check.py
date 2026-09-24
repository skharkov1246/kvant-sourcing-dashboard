#!/usr/bin/env python3
"""Проверщик набора разведки брендов: data/brand_research/.

ЗАЧЕМ. Разведка бренда — постоянная работа небольшими порциями (распоряжение
владельца 24.09.2026: бренд за брендом, машины, коды из сервисных книг,
субпоставщики). Её ведут разные сессии, и каждая пишет свой файл. Без общей
проверки набор расползается: код без цитаты неотличим от выдуманного, запись
без ссылки нельзя перепроверить, а контакт человека в публичном репозитории —
утечка (CLAUDE.md, правило 5). Правила разведки записаны в навыке
.claude/skills/brand-research/SKILL.md; здесь они проверяются механически.

ЧТО ПРОВЕРЯЕТСЯ.
  * схема: обязательные поля файла и записей, их типы, неизвестные поля файла;
  * у каждой машины, документа, субпоставщика и дилера есть ссылка http(s),
    у каждого кода — source_url;
  * у каждого кода есть дословная цитата, и код в ней стоит — с точностью до
    пробелов и дефисов (в PDF дефис бывает набран знаком минус, а пробел —
    неразрывным);
  * коды внутри бренда не повторяются и не стоят одновременно в принятых и в
    отклонённых;
  * нет e-mail, телефонов и локальных путей рабочей машины (ссылки из
    проверки вынимаются: в адресах цифры и «+» законны);
  * oem_key — существующий ключ dict/oem.json и не ключ со вставленным
    описанием; имя файла совпадает с ключом;
  * очередь queue.json: ключи словаря, статусы из закрытого списка, «сделано»
    только при наличии файла бренда и наоборот.

ПЕЧАТЬ — ТОЛЬКО АГРЕГАТЫ (CLAUDE.md, правило 17): числа и место нарушения
(файл, раздел, номер записи, правило). Ни кодов, ни имён, ни цитат.

    python scripts/brand_research_check.py
    python scripts/brand_research_check.py --dir <папка набора> --dict <словарь>

Код возврата 1 при любом нарушении.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ПАПКА = ROOT / "data" / "brand_research"
СЛОВАРЬ = ROOT / "dict" / "oem.json"
ОЧЕРЕДЬ = "queue.json"
СТАТУСЫ = ("в очереди", "в работе", "сделано")

# Поля файла бренда: имя → тип. Лишнее поле файла — нарушение: опечатка в
# имени раздела иначе молча уводит записи мимо проверки.
ВЕРХ = {
    "oem_key": str, "name": str, "segment_focus": str, "researched_at": str, "method": str,
    "summary": str, "machines": list, "service_docs": list, "parts": list, "parts_rejected": list,
    "sub_suppliers": list, "dealers": list, "gaps": list, "verifier_notes": str,
}
ВЕРХ_НЕОБЯЗАТЕЛЬНЫЕ = {"corrections": list}

# Раздел → (обязательные текстовые поля, поле ссылок). Поле ссылок — список
# ("sources") или одна строка ("source_url"); None — ссылка не требуется.
РАЗДЕЛЫ: dict[str, tuple[tuple[str, ...], str | None]] = {
    "machines": (("model", "kind", "segment", "rating", "status"), "sources"),
    "service_docs": (("title", "machine", "kind"), "sources"),
    "parts": (("code", "issuer", "description", "unit", "quote"), "source_url"),
    "parts_rejected": (("code", "verdict", "reason"), None),
    "sub_suppliers": (("company", "component", "evidence"), "sources"),
    "dealers": (("company", "country", "role"), "sources"),
}

ДАТА = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ССЫЛКА = re.compile(r"^https?://[^\s]+$")
ССЫЛКИ_В_ТЕКСТЕ = re.compile(r"https?://\S+")
# Пробелы любого рода и дефисы любого начертания: «140−3813» из PDF с минусом
# и «3115 9170 91» с неразрывными пробелами — тот же код, что «140-3813».
РАЗДЕЛИТЕЛИ = re.compile(r"[\s\-‐-―−]+")

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# Телефон узнаётся только по явному признаку: «+» и не меньше девяти цифр, метка
# «тел./phone/fax» или российский «8 (xxx)». Голые группы цифр — это номера
# деталей и документов («504-0262 1», «9869 0090 01»), их обвинять нельзя.
ТЕЛЕФОН = (
    re.compile(r"(?<![\w+])\+\s?\d[\d\s().\-]{7,}\d"),
    re.compile(r"(?i)\b(?:тел|телефон|phone|tel|fax|факс)\b\.?\s*[:№]?\s*\+?\d[\d\s().\-]{5,}"),
    re.compile(r"(?<!\d)8\s?\(\d{3,5}\)\s?\d{1,3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)"),
)
ЛОКАЛЬНЫЙ_ПУТЬ = re.compile(r"(?:^|[\s(«\"'])(?:/tmp/|/home/|/root/|/var/|[A-Za-z]:\\)")

ЛАТИНИЦА = re.compile(r"[a-z]")
КИРИЛЛИЦА = re.compile(r"[а-яё]")
# Указания к закупке, попавшие в словарь ключом бренда. Список закрытый
# (CLAUDE.md, правило 7): обвиняем только то, что названо поимённо.
УКАЗАНИЯ = ("заказпоспецификации", "закупкапоспецификации", "крепежныйзавод",
            "любойдистрибьютор", "прочие")


def нормализовать_код(s: str) -> str:
    return РАЗДЕЛИТЕЛИ.sub("", str(s or "")).casefold()


def код_в_цитате(code: str, quote: str) -> bool:
    k = нормализовать_код(code)
    return bool(k) and k in нормализовать_код(quote)


def ключ_с_описанием(k: str) -> bool:
    """Ключ, склеенный из имени бренда и русского пояснения: «caterpillar» плюс
    «соединённые штаты америки». Признак — латиница и кириллица в одном ключе.
    Чисто кириллический ключ («мирколец») — обычное русское имя."""
    return bool(ЛАТИНИЦА.search(k) and КИРИЛЛИЦА.search(k))


def ключ_указание(k: str) -> bool:
    return str(k).startswith(УКАЗАНИЯ)


def ключ_негоден(k: str) -> bool:
    return ключ_с_описанием(k) or ключ_указание(k)


def найти_контакты(text: str) -> list[str]:
    """Какие виды контактов есть в тексте — без самих значений."""
    t = ССЫЛКИ_В_ТЕКСТЕ.sub(" ", str(text or ""))
    found = []
    if EMAIL.search(t):
        found.append("e-mail")
    if any(rx.search(t) for rx in ТЕЛЕФОН):
        found.append("телефон")
    if ЛОКАЛЬНЫЙ_ПУТЬ.search(t):
        found.append("локальный путь")
    return found


def _строки(obj, путь=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _строки(v, f"{путь}.{k}" if путь else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _строки(v, f"{путь}[{i}]")
    elif isinstance(obj, str):
        yield путь, obj


def _ссылки_записи(r: dict, поле: str) -> list:
    v = r.get(поле)
    return v if isinstance(v, list) else [v]


def проверить_бренд(данные, имя_файла: str, ключи: set[str]) -> list[str]:
    """Нарушения одного файла бренда: строки «файл место: правило»."""
    out: list[str] = []

    def нарушение(место: str, правило: str):
        out.append(f"{имя_файла} {место}: {правило}".replace(" : ", ": "))

    if not isinstance(данные, dict):
        нарушение("", "файл не объект")
        return out
    for поле, тип in ВЕРХ.items():
        if поле not in данные:
            нарушение(поле, "нет обязательного поля")
        elif not isinstance(данные[поле], тип):
            нарушение(поле, f"тип не {тип.__name__}")
    for поле, тип in ВЕРХ_НЕОБЯЗАТЕЛЬНЫЕ.items():
        if поле in данные and not isinstance(данные[поле], тип):
            нарушение(поле, f"тип не {тип.__name__}")
    for поле in sorted(set(данные) - set(ВЕРХ) - set(ВЕРХ_НЕОБЯЗАТЕЛЬНЫЕ)):
        нарушение("", "неизвестное поле файла")

    k = данные.get("oem_key")
    if isinstance(k, str):
        if Path(имя_файла).stem != k:
            нарушение("oem_key", "имя файла не совпадает с ключом")
        if k not in ключи:
            нарушение("oem_key", "ключа нет в dict/oem.json")
        if ключ_негоден(k):
            нарушение("oem_key", "ключ со вставленным описанием или указание к закупке")
    if isinstance(данные.get("researched_at"), str) and not ДАТА.match(данные["researched_at"]):
        нарушение("researched_at", "дата не в виде ГГГГ-ММ-ДД")
    for поле in ("method", "summary", "name"):
        if isinstance(данные.get(поле), str) and not данные[поле].strip():
            нарушение(поле, "пусто")

    for раздел, (обязательные, поле_ссылок) in РАЗДЕЛЫ.items():
        записи = данные.get(раздел)
        if not isinstance(записи, list):
            continue
        for i, r in enumerate(записи):
            место = f"{раздел}[{i}]"
            if not isinstance(r, dict):
                нарушение(место, "запись не объект")
                continue
            for поле in обязательные:
                if not isinstance(r.get(поле), str) or not r[поле].strip():
                    нарушение(место, f"нет поля {поле}")
            if поле_ссылок:
                ссылки = _ссылки_записи(r, поле_ссылок)
                if not ссылки or ссылки == [None]:
                    нарушение(место, "нет источника-URL")
                elif not all(isinstance(u, str) and ССЫЛКА.match(u) for u in ссылки):
                    нарушение(место, "источник не ссылка http(s)")
            if раздел == "parts" and isinstance(r.get("code"), str) and isinstance(r.get("quote"), str):
                if len(нормализовать_код(r["code"])) < 3:
                    нарушение(место, "код короче трёх знаков")
                elif not код_в_цитате(r["code"], r["quote"]):
                    нарушение(место, "код не стоит в цитате")

    принятые = Counter(нормализовать_код(r.get("code")) for r in данные.get("parts") or []
                       if isinstance(r, dict))
    for код, n in принятые.items():
        if код and n > 1:
            нарушение("parts", "код повторяется")
    отклонённые = {нормализовать_код(r.get("code")) for r in данные.get("parts_rejected") or []
                   if isinstance(r, dict)}
    if set(принятые) & отклонённые - {""}:
        нарушение("parts_rejected", "код одновременно принят и отклонён")

    for путь, s in _строки(данные):
        for вид in найти_контакты(s):
            нарушение(путь, вид)
    return out


def проверить_очередь(данные, ключи: set[str], бренды: dict[str, dict]) -> list[str]:
    """Нарушения очереди. бренды — oem_key → данные файла бренда."""
    out: list[str] = []

    def нарушение(место: str, правило: str):
        out.append(f"{ОЧЕРЕДЬ} {место}: {правило}")

    if not isinstance(данные, dict) or not isinstance(данные.get("records"), list):
        нарушение("records", "нет списка записей")
        return out
    ключи_очереди: Counter = Counter()
    приоритеты: Counter = Counter()
    for i, r in enumerate(данные["records"]):
        место = f"records[{i}]"
        if not isinstance(r, dict):
            нарушение(место, "запись не объект")
            continue
        k = r.get("oem_key")
        for поле in ("oem_key", "name", "focus", "status"):
            if not isinstance(r.get(поле), str) or not r[поле].strip():
                нарушение(место, f"нет поля {поле}")
        if not isinstance(k, str):
            continue
        ключи_очереди[k] += 1
        if k not in ключи:
            нарушение(место, "ключа нет в dict/oem.json")
        if ключ_негоден(k):
            нарушение(место, "ключ со вставленным описанием или указание к закупке")
        if r.get("status") not in СТАТУСЫ:
            нарушение(место, "статус не из списка")
        if "priority" in r:
            if not isinstance(r["priority"], int):
                нарушение(место, "priority не целое")
            else:
                приоритеты[r["priority"]] += 1
        if r.get("status") == "сделано":
            if k not in бренды:
                нарушение(место, "«сделано», а файла бренда нет")
            elif r.get("researched_at") != бренды[k].get("researched_at"):
                нарушение(место, "дата разведки не совпадает с файлом бренда")
        elif k in бренды:
            нарушение(место, "файл бренда есть, а статус не «сделано»")
    for k, n in ключи_очереди.items():
        if n > 1:
            нарушение("records", "ключ в очереди повторяется")
    for p, n in приоритеты.items():
        if n > 1:
            нарушение("records", "priority повторяется")
    for k in sorted(set(бренды) - set(ключи_очереди)):
        нарушение("records", "у файла бренда нет записи в очереди")
    return out


def сводка(бренды: dict[str, dict]) -> dict[str, int]:
    итог: Counter = Counter()
    for d in бренды.values():
        итог["брендов"] += 1
        for раздел in ("machines", "parts", "parts_rejected", "sub_suppliers", "dealers", "service_docs"):
            итог[раздел] += len(d.get(раздел) or [])
    return dict(итог)


def ключи_словаря(путь: Path) -> set[str]:
    данные = json.loads(путь.read_text(encoding="utf-8"))
    return {r["oem_key"] for r in данные.get("records", []) if r.get("oem_key")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Проверка набора разведки брендов")
    ap.add_argument("--dir", default=str(ПАПКА), help="папка набора")
    ap.add_argument("--dict", default=str(СЛОВАРЬ), help="словарь производителей")
    a = ap.parse_args(argv)
    папка, словарь = Path(a.dir), Path(a.dict)

    ключи = ключи_словаря(словарь)
    нарушения: list[str] = []
    бренды: dict[str, dict] = {}
    for p in sorted(папка.glob("*.json")):
        if p.name == ОЧЕРЕДЬ:
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            нарушения.append(f"{p.name}: не читается как JSON")
            continue
        нарушения += проверить_бренд(d, p.name, ключи)
        if isinstance(d, dict) and isinstance(d.get("oem_key"), str):
            бренды[d["oem_key"]] = d

    очередь_путь = папка / ОЧЕРЕДЬ
    очередь = None
    if очередь_путь.exists():
        try:
            очередь = json.loads(очередь_путь.read_text(encoding="utf-8"))
        except ValueError:
            нарушения.append(f"{ОЧЕРЕДЬ}: не читается как JSON")
        else:
            нарушения += проверить_очередь(очередь, ключи, бренды)
    else:
        нарушения.append(f"{ОЧЕРЕДЬ}: нет файла очереди")

    с = сводка(бренды)
    print(f"разведка брендов: брендов {с.get('брендов', 0)} · машин {с.get('machines', 0)} · "
          f"кодов с цитатой {с.get('parts', 0)} · отклонено кодов {с.get('parts_rejected', 0)} · "
          f"субпоставщиков {с.get('sub_suppliers', 0)} · дилеров {с.get('dealers', 0)} · "
          f"сервисных документов {с.get('service_docs', 0)}")
    if isinstance(очередь, dict) and isinstance(очередь.get("records"), list):
        статусы = Counter(r.get("status") for r in очередь["records"] if isinstance(r, dict))
        print(f"очередь: записей {sum(статусы.values())} · "
              + " · ".join(f"{s} {статусы.get(s, 0)}" for s in СТАТУСЫ))
    описание = sum(1 for k in ключи if ключ_с_описанием(k))
    указания = sum(1 for k in ключи if ключ_указание(k))
    негодных = sum(1 for k in ключи if ключ_негоден(k))
    print(f"словарь dict/oem.json: ключей {len(ключи)} · негодных для очереди {негодных} "
          f"(со вставленным описанием {описание}, указаний к закупке {указания})")
    if нарушения:
        print(f"✗ нарушений: {len(нарушения)}")
        for n in нарушения:
            print(f"   {n}")
        return 1
    print("✓ нарушений нет")
    return 0


if __name__ == "__main__":
    sys.exit(main())
