"""Запись цены из КП в lib_prices — одним местом для всех разборов.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ. Разборов у нас два: обычный (indexer.py) и распознавание
сканов (ocr.py). Оба кладут позиции в lib_demand, и оба обязаны класть цену.
CLAUDE.md, правило 14: две вставки в одну таблицу правятся вместе — колонка,
добавленная в одну, роняет вторую на каждой строке, ошибка молча уходит в
счётчик пропущенных, а файл уже отмечен разобранным. Здесь их не две: она одна,
и разойтись им негде.

СВЯЗЬ С ПОСТАВЩИКОМ НЕ ПРИДУМЫВАЕТСЯ. supplier_id не заполняется: там внешний
ключ на lib_suppliers, старый справочник, а ключ портала ведёт в sup_identifier
нового реестра. Пишется rfq_company, а соединяет их вид sup_quote_price.
"""

from __future__ import annotations

FEED = "разбор КП"
ИСТОЧНИК = "КП"

КОЛОНКИ = ("segment_id", "item_name", "part_number", "price", "currency", "basis",
           "qty", "qty_unit", "source_url", "rfq_id", "rfq_company", "lead_days",
           "source", "feed", "confidence", "note", "price_date")

ВСТАВКА = f"""
insert into lib_prices
  ({", ".join(КОЛОНКИ)})
values %s"""

# Идемпотентность по файлу: переразбор того же КП снимает свои прежние строки.
# Естественного ключа у цены нет (одна позиция законно имеет и цену, и сумму),
# поэтому ключ — файл, из которого она пришла.
СНЯТЬ = "delete from lib_prices where feed = %s and source_url = any(%s)"

_ФАЙЛ = КОЛОНКИ.index("source_url")


def строка(поз: dict, ц: dict, обрезать) -> tuple:
    """Позиция плюс её ценовая часть → кортеж ровно под КОЛОНКИ."""
    return (поз.get("segment_id"), обрезать(поз.get("item_name"))[:500],
            обрезать(поз.get("part_number"))[:120], ц["price"], ц["currency"],
            ц["basis"], поз.get("qty"), обрезать(поз.get("unit"))[:40],
            поз["source_file"], поз.get("deal_id"), поз.get("company"),
            ц["lead_days"], ИСТОЧНИК, FEED, ц["confidence"],
            обрезать(ц.get("note"))[:300] or None, None)


def записать(cur, буфер: list[tuple], execute_values) -> None:
    """Снять прежние строки этих файлов и вставить новые.

    Построчного досыла здесь НЕТ намеренно: пакет цен падает целиком только по
    одной причине — в базе нет колонок lead_days, rfq_id или rfq_company. Тогда
    не пройдёт и построчная вставка, а файлы уже были бы отмечены разобранными,
    и цены пропали бы молча. Вызывающий обязан откатить всё и упасть.
    """
    if not буфер:
        return
    файлы = sorted({r[_ФАЙЛ] for r in буфер})
    cur.execute(СНЯТЬ, (FEED, файлы))
    execute_values(cur, ВСТАВКА, буфер, page_size=500)


ПОДСКАЗКА = ("запись цен не прошла — вероятно, в lib_prices нет колонок lead_days, "
             "rfq_id или rfq_company: примените library/supabase/schema.sql прогоном "
             "«ZIP base — apply DB migrations»")
