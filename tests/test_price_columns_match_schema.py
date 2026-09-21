"""Каждая колонка, в которую пишется цена, обязана существовать в схеме.

ЗАЧЕМ. Разборщик вставляет цену одним списком колонок (price_store.КОЛОНКИ).
Колонки, которой нет в базе, достаточно, чтобы пакет упал целиком, — а падает
он ночью, без человека. За 21.09.2026 в этот список добавились две колонки
(oem, rfq_brands), и ровно так же в него можно добавить третью, забыв DDL:
тесты останутся зелёными, ruff промолчит, и узнаем мы об этом из упавшего
ночного прогона.

Проверка идёт по тексту миграции, без базы: колонка обязана быть либо в теле
create table lib_prices, либо среди alter table … add column. Обратное
равенство НЕ требуется — в таблице законно есть колонки, которые разбор КП не
заполняет (supplier_id, country, exporter, year).

Заодно сверяется подсказка, которую читают в момент падения: она называет
колонки, и до 21.09.2026 называла три из пяти — то есть отправляла чинить не то.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from library import price_store  # noqa: E402

СХЕМА = (ROOT / "library" / "supabase" / "schema.sql").read_text(encoding="utf-8")


def колонки_схемы() -> set[str]:
    тело = re.search(r"create table if not exists lib_prices\s*\((.*?)\n\);",
                     СХЕМА, re.S)
    assert тело, "в схеме больше нет таблицы lib_prices"
    имена = set()
    for строка in тело.group(1).splitlines():
        строка = строка.split("--")[0].strip()
        m = re.match(r"^([a-z_][a-z0-9_]*)\s+\S", строка)
        if m and m.group(1) not in ("primary", "references", "constraint"):
            имена.add(m.group(1))
    имена |= set(re.findall(
        r"alter table lib_prices add column if not exists\s+(\w+)", СХЕМА))
    return имена


def test_все_колонки_записи_цены_есть_в_схеме():
    нет = sorted(set(price_store.КОЛОНКИ) - колонки_схемы())
    assert not нет, (
        f"разборщик пишет в колонки, которых нет в схеме: {нет}. "
        "Ночной прогон упадёт целиком — добавьте DDL в library/supabase/schema.sql")


def test_подсказка_называет_колонки_которые_добавляет_миграция():
    добавленные = set(re.findall(
        r"alter table lib_prices add column if not exists\s+(\w+)", СХЕМА))
    названные = set(price_store.ДОБАВЛЕННЫЕ_МИГРАЦИЕЙ)
    assert названные <= добавленные, (
        f"подсказка называет колонки, которых миграция не добавляет: "
        f"{sorted(названные - добавленные)}")
    # Всё, что пишет разбор КП и чего нет в исходной таблице, обязано быть
    # названо: иначе подсказка при падении укажет не на ту колонку.
    тело = re.search(r"create table if not exists lib_prices\s*\((.*?)\n\);",
                     СХЕМА, re.S).group(1)
    исходные = {m.group(1) for m in
                (re.match(r"^([a-z_][a-z0-9_]*)\s+\S", s.split("--")[0].strip())
                 for s in тело.splitlines()) if m}
    нужно_назвать = (set(price_store.КОЛОНКИ) & добавленные) - исходные
    assert нужно_назвать <= названные, (
        f"подсказка не называет колонки, без которых запись цены падает: "
        f"{sorted(нужно_назвать - названные)}")


def test_строка_цены_совпадает_по_длине_с_колонками():
    """Кортеж и список колонок правятся вместе — иначе вставка сдвинет значения."""
    поз = {"segment_id": "gtu", "item_name": "деталь", "part_number": "56017080",
           "oem": "Пример", "qty": 2, "unit": "шт", "source_file": "f1",
           "deal_id": "42", "company": "1234", "brands": "7,8"}
    ц = {"price": 100.0, "currency": "EUR", "basis": None, "lead_days": 30,
         "confidence": "high", "note": None}
    assert len(price_store.строка(поз, ц, lambda s: str(s or ""))) == len(price_store.КОЛОНКИ)
