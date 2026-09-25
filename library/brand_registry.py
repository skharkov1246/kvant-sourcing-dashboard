"""Реестр брендов: чистые правила засева и тексты запросов (этап 8.2).

ЗАЧЕМ. Бренд лежал свободным текстом в восьми колонках, и семь правил сводили
написания каждое по-своему (план, этап 8, «Что мешает сегодня», п. 1). Здесь
одно правило и один реестр: lib_brands и lib_brand_alias
(library/supabase/brands_schema.sql). Ввод-вывод — library/load_brands.py.

ДВА КЛЮЧА, И ОНИ РАЗНЫЕ НАМЕРЕННО:
  · ключ БРЕНДА — oem_key из dict/oem.json; новый бренд получает nkey имени
    (scripts/build_dict.py), как велит план: тогда факт роли (8.3) ссылается на
    бренд по ключу, не дожидаясь таблицы;
  · ключ НАПИСАНИЯ — codes_sql.ключ_написания (в SQL — lib_brand_key): то самое
    правило, которым запрос страницы /brands сводит ячейку к ключу. По нему
    написание ищется в реестре. ПРАВИЛО ниже — его версия; она пишется в каждую
    строку, чтобы смена правила была видна по данным.

ИСТОЧНИКИ И ЧТО ОНИ МОГУТ. Заводить бренд могут только выверенные: словарь
dict/oem.json (только записи вида «бренд», library/oem_kind.py), атлас
изготовителей zip/data/oem_atlas.json и справочник марок портала СП-176 (его
ведут люди). Остальные — OEM_ALIAS, компании-изготовители
lib_suppliers и написания из данных (lib_prices.oem, lib_demand.oem,
lib_parts.oem) — только РАЗРЕШАЮТСЯ к существующему бренду; не разрешилось —
встают в очередь, а не пропадают и не плодят брендов из мусора.

ОБВИНЯЕТ ТОЛЬКО ЗАКРЫТЫЙ СПИСОК (CLAUDE.md, правило 7): «не бренд» ставят
пометки незнания library/equipment.OEM_JUNK, вид записи словаря («указание»,
«номер» — library/oem_kind.py) и, для написаний из данных, отсев запроса
/brands (codes_sql.brand_pipeline) — тот же, что у страницы. Написание записи
«несколько» — спорно между её брендами, «описания» — разрешено к его бренду.
Всё прочее неразрешённое — «в очереди».

ДЕЛЕНИЕ НА ЧАСТИ — ОДНО НА ИСТОЧНИК (правило дробления): записи файлов и
компании — по ключу бренда или написания (crc32), справочник портала — по
диапазону номеров элементов (indexer.диапазон_части), написания данных — по
хешу написания в запросе. Каждая запись попадает ровно в одну часть.

В журнал отсюда ничего не печатается: имена брендов — данные, а не агрегаты.
"""
from __future__ import annotations

import collections
import importlib.util
import re
import zlib
from pathlib import Path

from library import codes_sql, crossref, equipment, oem_kind

ROOT = Path(__file__).resolve().parents[1]

# Версия правила ключа написания. Меняется правило в codes_sql — меняется и
# версия; строки прежней версии видны по колонке rule.
ПРАВИЛО = "ключ_написания/1"

ФАЙЛ_СЛОВАРЯ = "dict/oem.json"
ФАЙЛ_АТЛАСА = "zip/data/oem_atlas.json"

ИСТ_СЛОВАРЬ = "dict/oem.json"
ИСТ_АТЛАС = "zip/data/oem_atlas.json"
ИСТ_АЛИАС = "OEM_ALIAS"
ИСТ_СП176 = "СП-176"
ИСТ_КОМПАНИИ = "lib_suppliers"
ИСТ_ДАННЫЕ = ("lib_prices.oem", "lib_demand.oem", "lib_parts.oem")

РАЗРЕШЕНО, СПОРНО, ОЧЕРЕДЬ, НЕ_БРЕНД = "разрешено", "спорно", "в очереди", "не бренд"
# Какие виды компаний lib_suppliers — изготовители (library/load_customs.py:159,
# library/load_parts.py:449).
ВИДЫ_ИЗГОТОВИТЕЛЯ = ("изготовитель", "изготовитель (из каталога)")

# Предел длины написания в строке реестра: ячейки длиннее — описания, а не имена,
# и отсев их всё равно не пропустит. Обрезка — только хранения ради.
ДЛИНА = 300


def ключ(написание) -> str:
    """Ключ написания — единое правило (codes_sql.ключ_написания)."""
    return codes_sql.ключ_написания(написание or "")


def новый_ключ(имя) -> str:
    """Ключ нового бренда — nkey словаря (scripts/build_dict.py)."""
    return codes_sql.bd.nkey(имя or "")


def часть_ключа(k: str, частей: int) -> int:
    """Номер части для записи по её ключу: crc32 байтов UTF-8."""
    return zlib.crc32(str(k).encode("utf-8")) % max(1, частей)


def пометка_незнания(написание) -> bool:
    t = re.sub(r"\s+", " ", str(написание or "").lower().replace("ё", "е")).strip(" .,;:-")
    return t in equipment.OEM_JUNK


_ДЕЛЕНИЕ = re.compile(r"\s*[,;/()\[\]]\s*|\s+(?:и|или|or)\s+", re.I)


def части_имени(имя) -> list[str]:
    """«SKF (Швеция)», «SKF/FAG» → ключи частей; деление — как у запроса."""
    out = []
    for ч in _ДЕЛЕНИЕ.split(str(имя or "")):
        k = ключ(ч)
        if len(k) >= 2 and k not in out:
            out.append(k)
    return out


def разрешить(имя, карта: dict[str, set]) -> tuple[str, set]:
    """Имя → (состояние, ключи брендов) по карте «ключ написания → ключи брендов».

    Сначала всё имя целиком; не нашлось — части имени, и берётся только
    однозначный ответ: одна часть на один бренд. «SKF (Швеция)» разрешится к
    skf, «SKF/FAG» — спорно: это два бренда, а у написания ключ один."""
    k = ключ(имя)
    целиком = карта.get(k, set())
    if len(целиком) == 1:
        return РАЗРЕШЕНО, set(целиком)
    if len(целиком) > 1:
        return СПОРНО, set(целиком)
    # Хвост-страна — общее правило codes_sql.без_страны: «Siemens - Germany»
    # тире не делится, а «Sandvik Tamrock, FINLAND» сводится и по частям.
    без = codes_sql.без_страны(имя)
    if без:
        сам = карта.get(ключ(без), set())
        if len(сам) == 1:
            return РАЗРЕШЕНО, set(сам)
        if len(сам) > 1:
            return СПОРНО, set(сам)
    по_частям = set()
    for ч in части_имени(имя):
        по_частям |= карта.get(ч, set())
    if len(по_частям) == 1:
        return РАЗРЕШЕНО, по_частям
    if len(по_частям) > 1:
        return СПОРНО, по_частям
    return ОЧЕРЕДЬ, set()


# Короче пяти знаков ключ встречается внутри чужих имён случайно («ge», «abb»).
ПОХОЖЕСТЬ_ОТ = 5


def похожие(имя, ключи_брендов) -> set:
    """Бренды, чей ключ стоит внутри ключа имени или наоборот.

    Нужны там, где источник может завести бренд: «INNIO Jenbacher GmbH & Co OG»
    не совпадает с inniojenbacher из-за «OG», а «Caterpillar Energy Solutions
    (MWM)» содержит caterpillar, хотя это другая марка. Выбирать между «тот же»
    и «другой» по вхождению нельзя — такое имя встаёт «спорно» с кандидатами, а
    не заводит двойника и не приклеивается к чужому бренду."""
    k = ключ(имя)
    if len(k) < ПОХОЖЕСТЬ_ОТ:
        return set()
    return {b for b in ключи_брендов
            if len(b) >= ПОХОЖЕСТЬ_ОТ and b != k and (b in k or k in b)}


def _написание(spelling, source, seen_at="", **поля) -> dict:
    текст = str(spelling or "").strip()[:ДЛИНА]
    return {"spelling": текст, "spelling_key": ключ(текст), "source": source,
            "seen_at": str(seen_at or "")[:ДЛИНА], "sp176_id": поля.get("sp176_id"),
            "brand_key": поля.get("brand_key"), "status": поля["status"],
            "candidates": sorted(поля["candidates"]) if поля.get("candidates") else None,
            "n_rows": поля.get("n_rows"), "note": поля.get("note")}


def _бренд(k, имя, источник, **поля) -> dict:
    return {"brand_key": k, "name": str(имя or k).strip()[:ДЛИНА], "owner": поля.get("owner"),
            "former_names": поля.get("former_names"), "country": поля.get("country"),
            "sources": [источник]}


class План:
    """Бренды и написания, которые засев запишет. Карта растёт по ходу засева:
    бренд, заведённый атласом, разрешает написания OEM_ALIAS и справочника.

    не_бренды — ключ написания записи словаря, которая не бренд → {kind, brands,
    oem_key}: имя, совпавшее с ним целиком, бренда не заводит, а получает
    суждение по виду записи (суждение_вида)."""

    def __init__(self):
        self.бренды: dict[str, dict] = {}
        self.написания: list[dict] = []
        self.карта: dict[str, set] = collections.defaultdict(set)
        self.не_бренды: dict[str, dict] = {}

    def завести(self, k, имя, источник, **поля) -> dict:
        b = self.бренды.get(k)
        if b is None:
            b = self.бренды[k] = _бренд(k, имя, источник, **поля)
        else:
            if источник not in b["sources"]:
                b["sources"].append(источник)
            for поле in ("owner", "former_names", "country"):
                if not b.get(поле) and поля.get(поле):
                    b[поле] = поля[поле]
        self.карта[ключ(k)].add(k)
        return b

    def связать(self, написание, k):
        kk = ключ(написание)
        if len(kk) >= 2:
            self.карта[kk].add(k)

    def добавить(self, строка: dict):
        if строка["spelling"]:
            self.написания.append(строка)


# Причина в note строки реестра по виду записи словаря.
ПРИЧИНА_ВИДА = {
    oem_kind.УКАЗАНИЕ: "dict/oem.json: указание к закупке, а не бренд",
    oem_kind.НОМЕР: "dict/oem.json: номер детали, а не бренд",
    oem_kind.НЕСКОЛЬКО: "dict/oem.json: несколько брендов в одной записи",
    oem_kind.ОПИСАНИЕ: "dict/oem.json: бренд с пояснением",
    oem_kind.МАТЕРИАЛ: "dict/oem.json: материал, а не бренд (dict/material.json)",
    oem_kind.ФОРМА: "dict/oem.json: заглушка или реквизит формы, а не бренд",
}


def суждение_вида(вид, бренды) -> dict:
    """Поля строки реестра для написания записи-не-бренда.

    Указание и номер — «не бренд»; описание с одним брендом — разрешено к нему;
    несколько брендов — спорно между ними (выбрать нечем); брендов не нашлось —
    в очереди. Бренд такая запись не заводит никогда."""
    note = ПРИЧИНА_ВИДА.get(вид, "dict/oem.json: не бренд")
    if вид in oem_kind.БЕЗ_БРЕНДА:
        return {"status": НЕ_БРЕНД, "note": note}
    бренды = {b for b in бренды or () if b}
    if вид == oem_kind.ОПИСАНИЕ and len(бренды) == 1:
        return {"status": РАЗРЕШЕНО, "brand_key": next(iter(бренды)), "note": note}
    if бренды:
        return {"status": СПОРНО, "candidates": бренды, "note": note}
    return {"status": ОЧЕРЕДЬ, "note": note}


def суждение_словаря(имя, карта, не_бренды) -> dict | None:
    """Имя целиком — написание записи словаря, которая не бренд, и не написание
    бренда → поля строки по её виду; иначе None (разрешать как обычно)."""
    k = ключ(имя)
    if not не_бренды or k in карта or k not in не_бренды:
        return None
    x = не_бренды[k]
    return суждение_вида(x["kind"], x.get("brands"))


def план_файлов(словарь, атлас, алиасы=None) -> План:
    """Засев из файлов: словарь, атлас, OEM_ALIAS. Детерминирован: каждая часть
    прогона строит его целиком (файлы маленькие) и пишет свою долю."""
    алиасы = equipment.OEM_ALIAS if алиасы is None else алиасы
    п = План()
    # 1. Словарь: запись вида «бренд» — бренд, каждое её написание — строка
    #    реестра. Запись другого вида (указание, несколько, описание, номер —
    #    library/oem_kind.py) бренда не заводит: её написания получают суждение
    #    по виду, а не ключ самой записи.
    записи = [r for r in (словарь or {}).get("records", []) if r.get("oem_key")]
    for r in записи:
        if not oem_kind.бренд_ли(r):
            continue
        п.завести(r["oem_key"], r.get("name"), ИСТ_СЛОВАРЬ)
        for н in {r.get("name")} | {s.get("spelling") for s in r.get("spellings", [])}:
            if н:
                п.связать(н, r["oem_key"])
    for r in записи:
        if oem_kind.бренд_ли(r):
            continue
        for н in {r.get("name")} | {s.get("spelling") for s in r.get("spellings", [])}:
            kk = ключ(н)
            if len(kk) >= 2 and kk not in п.карта:
                п.не_бренды.setdefault(kk, {"kind": oem_kind.вид(r), "brands": list(r.get("brands") or []),
                                            "oem_key": r["oem_key"]})
    for r in записи:
        k = r["oem_key"]
        бренд = oem_kind.бренд_ли(r)
        места = [(s.get("spelling"), s.get("where") or "") for s in r.get("spellings", [])]
        if r.get("name") and r["name"] not in {s for s, _ in места}:
            места.append((r["name"], "dict/oem.json:name"))
        for н, где in места:
            кандидаты = п.карта.get(ключ(н), set())
            if not бренд and not кандидаты:
                п.добавить(_написание(н, ИСТ_СЛОВАРЬ, где, **суждение_вида(oem_kind.вид(r), r.get("brands"))))
            elif len(кандидаты) > 1:
                п.добавить(_написание(н, ИСТ_СЛОВАРЬ, где, status=СПОРНО, candidates=кандидаты,
                                      note="ключ написания у нескольких записей словаря"))
            elif not бренд:
                # Написание записи-не-бренда есть и у бренда словаря: бренд сильнее.
                п.добавить(_написание(н, ИСТ_СЛОВАРЬ, где, status=РАЗРЕШЕНО,
                                      brand_key=next(iter(кандидаты)),
                                      note="написание есть у бренда словаря"))
            else:
                п.добавить(_написание(н, ИСТ_СЛОВАРЬ, где, status=РАЗРЕШЕНО, brand_key=k))
    # 2. Атлас: подпись к существующему бренду либо новый бренд с ключом nkey.
    for m in (атлас or {}).get("makers", []):
        имя = m.get("name")
        if not имя:
            continue
        суд = суждение_словаря(имя, п.карта, п.не_бренды)
        if суд:
            п.добавить(_написание(имя, ИСТ_АТЛАС, "makers[].name", **суд))
            continue
        поля = {"owner": m.get("owner"), "former_names": m.get("former_names"),
                "country": m.get("country")}
        состояние, ключи = разрешить(имя, п.карта)
        if состояние == РАЗРЕШЕНО:
            k = next(iter(ключи))
            п.завести(k, имя, ИСТ_АТЛАС, **поля)
            п.добавить(_написание(имя, ИСТ_АТЛАС, "makers[].name", status=РАЗРЕШЕНО, brand_key=k))
        elif состояние == СПОРНО:
            п.добавить(_написание(имя, ИСТ_АТЛАС, "makers[].name", status=СПОРНО, candidates=ключи))
        else:
            k = новый_ключ(имя)
            if len(k) < 2:
                п.добавить(_написание(имя, ИСТ_АТЛАС, "makers[].name", status=ОЧЕРЕДЬ,
                                      note="у имени нет ключа"))
                continue
            похож = похожие(имя, set(п.бренды) - {k})
            if похож and k not in п.бренды:
                п.добавить(_написание(имя, ИСТ_АТЛАС, "makers[].name", status=СПОРНО,
                                      candidates=похож, note="похоже на существующий бренд"))
                continue
            п.завести(k, имя, ИСТ_АТЛАС, **поля)
            п.связать(имя, k)
            п.добавить(_написание(имя, ИСТ_АТЛАС, "makers[].name", status=РАЗРЕШЕНО, brand_key=k,
                                  note="новый бренд: ключ nkey"))
    # 3. OEM_ALIAS: написание → имя, имя → бренд. Заводить бренд не может.
    for написание, имя in sorted(алиасы.items()):
        строка = суждение_словаря(имя, п.карта, п.не_бренды)
        if строка is None:
            состояние, ключи = разрешить(имя, п.карта)
            if состояние == ОЧЕРЕДЬ:
                строка = суждение_словаря(написание, п.карта, п.не_бренды)
                if строка is None:
                    состояние, ключи = разрешить(написание, п.карта)
            if строка is None:
                строка = {"status": состояние}
                if состояние == РАЗРЕШЕНО:
                    строка["brand_key"] = next(iter(ключи))
                elif состояние == СПОРНО:
                    строка["candidates"] = ключи
        п.добавить(_написание(написание, ИСТ_АЛИАС, "library/equipment.py:OEM_ALIAS", **строка))
    return п


def план_справочника(план: План, элементы) -> tuple[dict[str, dict], list[dict]]:
    """Элементы СП-176 (номер, название) → (новые бренды, написания).

    Справочник ведут люди, поэтому он может завести бренд. Название, не
    найденное в карте файлов, заводит бренд с ключом nkey; совпал nkey с
    существующим брендом — это он же (правило плана «новый ключ — тем же nkey»).
    Пометка незнания — «не бренд»; каждый элемент получает строку: по ней
    разрешаются ключи lib_prices.rfq_brands."""
    бренды: dict[str, dict] = {}
    написания = []
    for ид, название in элементы:
        где = f"СП-176#{ид}"
        if not str(название or "").strip():
            continue
        if пометка_незнания(название) or len(ключ(название)) < 2:
            написания.append(_написание(название, ИСТ_СП176, где, sp176_id=ид, status=НЕ_БРЕНД,
                                        note="пометка незнания или пустой ключ"))
            continue
        # Название — написание записи словаря, которая не бренд («Заказ по
        # спецификации»): бренда справочник не заводит, строка — по виду записи.
        суд = суждение_словаря(название, план.карта, план.не_бренды)
        if суд:
            k = суд.get("brand_key")
            if k in план.бренды:
                бренды.setdefault(k, dict(план.бренды[k], sources=list(план.бренды[k]["sources"])))
            написания.append(_написание(название, ИСТ_СП176, где, sp176_id=ид, **суд))
            continue
        состояние, ключи = разрешить(название, план.карта)
        if состояние == РАЗРЕШЕНО:
            k = next(iter(ключи))
            if k in план.бренды:
                бренды.setdefault(k, dict(план.бренды[k], sources=list(план.бренды[k]["sources"])))
            написания.append(_написание(название, ИСТ_СП176, где, sp176_id=ид,
                                        status=РАЗРЕШЕНО, brand_key=k))
        elif состояние == СПОРНО:
            написания.append(_написание(название, ИСТ_СП176, где, sp176_id=ид,
                                        status=СПОРНО, candidates=ключи))
        else:
            k = новый_ключ(название)
            if len(k) < 2:
                написания.append(_написание(название, ИСТ_СП176, где, sp176_id=ид,
                                            status=ОЧЕРЕДЬ, note="у названия нет ключа"))
                continue
            похож = похожие(название, set(план.бренды) | set(бренды))
            if k in план.бренды:
                b = бренды.setdefault(k, dict(план.бренды[k], sources=list(план.бренды[k]["sources"])))
                note = "по nkey совпал с существующим"
            elif похож and k not in бренды:
                написания.append(_написание(название, ИСТ_СП176, где, sp176_id=ид, status=СПОРНО,
                                            candidates=похож, note="похоже на существующий бренд"))
                continue
            else:
                b = бренды.setdefault(k, _бренд(k, название, ИСТ_СП176))
                note = "новый бренд: ключ nkey"
            if ИСТ_СП176 not in b["sources"]:
                b["sources"].append(ИСТ_СП176)
            написания.append(_написание(название, ИСТ_СП176, где, sp176_id=ид,
                                        status=РАЗРЕШЕНО, brand_key=k, note=note))
    return бренды, написания


def написания_компаний(компании, карта: dict[str, set], не_бренды=None) -> list[dict]:
    """Компании-изготовители lib_suppliers (номер, имя) → строки реестра.

    Только разрешение: бренд компания не заводит. Не разрешилась — очередь.
    не_бренды — План.не_бренды словаря-файла: имя, совпавшее с записью-не-брендом,
    получает суждение по её виду."""
    out = []
    for ид, имя in компании:
        где = f"lib_suppliers#{ид}"
        if not str(имя or "").strip():
            continue
        if пометка_незнания(имя) or not equipment.oem_is_real(str(имя)):
            out.append(_написание(имя, ИСТ_КОМПАНИИ, где, status=НЕ_БРЕНД,
                                  note="пометка незнания, короче трёх знаков или длиннее шести слов"))
            continue
        суд = суждение_словаря(имя, карта, не_бренды)
        if суд:
            out.append(_написание(имя, ИСТ_КОМПАНИИ, где, **суд))
            continue
        состояние, ключи = разрешить(имя, карта)
        строка = {"status": состояние}
        if состояние == РАЗРЕШЕНО:
            строка["brand_key"] = next(iter(ключи))
        elif состояние == СПОРНО:
            строка["candidates"] = ключи
        out.append(_написание(имя, ИСТ_КОМПАНИИ, где, **строка))
    return out


def написания_данных(источник: str, строки) -> list[dict]:
    """Строки запроса ДАННЫЕ_SQL → строки реестра.

    Суждение — запроса страницы /brands (codes_sql.brand_pipeline): причина
    отсева — «не бренд», ключ из карты — «разрешено», остальное — очередь."""
    out = []
    for часть, сырой, бренд, в_карте, причина, строк in строки:
        if not str(часть or "").strip():
            continue
        if причина:
            out.append(_написание(часть, источник, status=НЕ_БРЕНД, n_rows=строк, note=причина))
        elif в_карте:
            out.append(_написание(часть, источник, status=РАЗРЕШЕНО, brand_key=бренд, n_rows=строк))
        else:
            out.append(_написание(часть, источник, status=ОЧЕРЕДЬ, n_rows=строк))
    return out


def карта_из_строк(строки) -> dict[str, set]:
    """Строки (ключ написания, ключ бренда) → карта с множествами (спорные видны)."""
    карта: dict[str, set] = collections.defaultdict(set)
    for k, b in строки:
        if k and b:
            карта[k].add(b)
    return карта


def итоги(написания) -> dict[tuple[str, str], int]:
    """(источник, статус) → число строк. Для журнала: только числа."""
    return dict(collections.Counter((н["source"], н["status"]) for н in написания))


# ── Сверка прежних правил с единым (план 8.2: «прежние сверяются с ним на одном
#    корпусе написаний до замены») ─────────────────────────────────────────────

def _модуль(имя: str, путь: str):
    spec = importlib.util.spec_from_file_location(имя, ROOT / путь)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def прежние_правила() -> dict:
    """Имя правила → функция ключа. Не загрузилось — None, и сверка это скажет."""
    правила = {"scripts/build_dict.py nkey": codes_sql.bd.nkey}
    for имя, путь, функция in (
            ("library/load_suppliers.py norm", "library/load_suppliers.py", "norm"),
            ("scripts/supplier_registry_overlap.py norm_name",
             "scripts/supplier_registry_overlap.py", "norm_name"),
            ("zip/tools/cat_stock.py norm_company", "zip/tools/cat_stock.py", "norm_company")):
        try:
            правила[имя] = getattr(_модуль("kvant_rule_" + функция, путь), функция)
        except Exception:  # noqa: BLE001 — модуль мог не загрузиться без зависимостей
            правила[имя] = None
    # base/extract_positions.load_brands: ключ — строка без кавычек, в нижнем регистре.
    правила["base/extract_positions.py load_brands"] = (
        lambda s: str(s or "").strip(" .«»\"'()").lower())
    return правила


def сверка_правил(написания, правила=None) -> list[dict]:
    """Сколько групп единое правило и прежнее делят по-разному.

    «склеивает» — прежнее правило сводит в один ключ написания, которые единое
    держит разными брендами; «расщепляет» — наоборот. Считаются группы, а не
    пары: одна лишняя склейка трёх написаний — одна группа."""
    правила = прежние_правила() if правила is None else правила
    корпус = sorted({str(н).strip() for н in написания if str(н or "").strip()})
    новое = {н: ключ(н) for н in корпус}
    out = []
    for имя, f in правила.items():
        if f is None:
            out.append({"rule": имя, "loaded": False})
            continue
        старое = {н: f(н) for н in корпус}
        по_старому = collections.defaultdict(set)
        по_новому = collections.defaultdict(set)
        for н in корпус:
            if старое[н] and новое[н]:
                по_старому[старое[н]].add(новое[н])
                по_новому[новое[н]].add(старое[н])
        out.append({"rule": имя, "loaded": True, "corpus": len(корпус),
                    "merges": sum(1 for v in по_старому.values() if len(v) > 1),
                    "splits": sum(1 for v in по_новому.values() if len(v) > 1)})
    return out


def корпус_файлов(словарь, атлас, алиасы=None) -> list[str]:
    алиасы = equipment.OEM_ALIAS if алиасы is None else алиасы
    out = []
    for r in (словарь or {}).get("records", []):
        out.append(r.get("name"))
        out.extend(s.get("spelling") for s in r.get("spellings", []))
    out.extend(m.get("name") for m in (атлас or {}).get("makers", []))
    out.extend(алиасы.keys())
    out.extend(алиасы.values())
    return [x for x in out if x]


# ── SQL ──────────────────────────────────────────────────────────────────────

# Карта для запросов: из реестра базы — вид; без реестра — строки values
# словаря-файла (codes_sql.карта_sql). Форма одна: (k, oem_key).
КАРТА_РЕЕСТРА = codes_sql.КАРТА_РЕЕСТРА

ЕСТЬ_РЕЕСТР_SQL = "select to_regclass('lib_brand_map') is not null"
КАРТА_С_СПОРНЫМИ_SQL = """
select spelling_key, brand_key from lib_brand_alias
 where status in ('разрешено', 'проверено') and brand_key is not null
"""
КОМПАНИИ_SQL = """
select id, name from lib_suppliers
 where kind in ('изготовитель', 'изготовитель (из каталога)')
   and (id %% %(n)s) = %(k)s
 order by id
"""

# Ячейки изготовителя по источнику. Условие части — по ХЕШУ ЧАСТИ ЯЧЕЙКИ ниже,
# а не по ячейке: строка реестра — написание, и одно написание встречается во
# многих ячейках. Деление по ячейке разнесло бы его по частям.
ЯЧЕЙКИ = {
    "lib_prices.oem": "select oem as cell, count(*) as n from lib_prices "
                      "where feed = 'разбор КП' and btrim(coalesce(oem, '')) <> '' group by oem",
    "lib_demand.oem": "select oem as cell, count(*) as n from lib_demand_live "
                      "where btrim(coalesce(oem, '')) <> '' group by oem",
    "lib_parts.oem": "select oem as cell, count(*) as n from lib_parts "
                     "where btrim(coalesce(oem, '')) <> '' group by oem",
}


def _конвейер(карта_sql: str) -> str:
    """Отсев и ключ — запроса страницы /brands, с подставленной картой."""
    return codes_sql.brand_pipeline().replace(codes_sql.МЕТКА_КАРТЫ, карта_sql)


def данные_sql(источник: str, карта_sql: str) -> str:
    """Написания одного источника данных для своей части: (часть ячейки, свой
    ключ, ключ бренда, найден ли в карте, причина отсева, строк)."""
    return f"""\
with
  src as ({ЯЧЕЙКИ[источник]}),
  cells as (select cell, n, 'xx'::text as code, ''::text as name_key,
                null::text as pn from src),
{_конвейер(карта_sql)},
  pieces_n as (
    select tj.piece, tj.raw_key, tj.brand_key, tj.in_dict, tj.text_reject, sum(s.n) as n
      from text_judged tj join src s on s.cell = tj.cell
     group by 1, 2, 3, 4, 5
  )
select piece, raw_key, brand_key, in_dict, text_reject, n
  from pieces_n
 where ((hashtext(piece) & 2147483647) %% %(n)s) = %(k)s
 order by piece"""


def замер_sql(из_реестра: bool, карта_sql: str) -> str:
    """Доля строк цены и спроса с разрешённым брендом — по трём разрезам цены
    (план, «цены из КП»: oem — слово поставщика, rfq_brands — карточка, артикул →
    lib_parts.oem) и по спросу. Только агрегаты.

    «Разрешён» — хотя бы одна часть ячейки прошла отсев страницы /brands и её
    ключ есть в карте. «С брендом» — хотя бы одна часть прошла отсев. Разрез
    карточки без реестра не разрешается ничем: у ключей портала имён нет."""
    сцепка = crossref.СЦЕПКА.strip()
    assert сцепка.startswith("with ")
    сцепка = сцепка[len("with "):].replace("%s", "'разбор КП'")
    if из_реестра:
        карточка = """
  card as (
    select k.id, bool_or(m.brand_key is not null) as resolved
      from kp k
     cross join lateral """ + codes_sql.CARD_KEYS.format(col="k.rfq_brands") + """ b
      left join lib_brand_sp176 m on m.sp176_id::text = btrim(b)
     where btrim(b) ~ '^[0-9]+$'
     group by k.id
  ),"""
    else:
        карточка = """
  card as (
    select k.id, false as resolved
      from kp k
     cross join lateral """ + codes_sql.CARD_KEYS.format(col="k.rfq_brands") + """ b
     where btrim(b) ~ '^[0-9]+$'
     group by k.id
  ),"""
    return f"""\
with
  kp as (select id, oem, rfq_brands, part_number from lib_prices where feed = 'разбор КП'),
  {сцепка},
  art as (
    select k.id, p.oem
      from kp k
      join сцепка с on с.ключ = lib_pn_key(k.part_number)
      join lib_parts p on p.id = с.part_id
     where coalesce(btrim(k.part_number), '') <> ''
  ),
  dem as (
    select d.oem, (f.side = 'заказчик') as customer
      from lib_demand_live d left join lib_files f on f.file_id = d.source_file
  ),
  all_cells as (
    select oem as cell from kp where btrim(coalesce(oem, '')) <> ''
    union select oem from art where btrim(coalesce(oem, '')) <> ''
    union select oem from dem where btrim(coalesce(oem, '')) <> ''
  ),
  cells as (select cell, 'xx'::text as code, ''::text as name_key,
                null::text as pn from all_cells),
{_конвейер(карта_sql)},
  cs as (
    select cell, bool_or(text_reject is null) as has_brand,
           bool_or(text_reject is null and in_dict) as resolved
      from text_judged group by cell
  ),{карточка}
  r as (
    select 1 as ord, 'цены КП · слово поставщика (lib_prices.oem)' as cut,
           count(*) as total,
           count(*) filter (where btrim(coalesce(kp.oem, '')) <> '') as with_text,
           count(*) filter (where cs.has_brand) as with_brand,
           count(*) filter (where cs.resolved) as resolved
      from kp left join cs on cs.cell = kp.oem
    union all
    select 2, 'цены КП · карточка запроса (lib_prices.rfq_brands)',
           (select count(*) from kp),
           count(*), count(*), count(*) filter (where card.resolved)
      from card
    union all
    select 3, 'цены КП · артикул → каталог (lib_parts.oem)',
           (select count(*) from kp),
           count(*) filter (where btrim(coalesce(art.oem, '')) <> ''),
           count(*) filter (where cs.has_brand),
           count(*) filter (where cs.resolved)
      from art left join cs on cs.cell = art.oem
    union all
    select 4, 'цены КП · хотя бы один разрез',
           (select count(*) from kp),
           count(*) filter (where btrim(coalesce(kp.oem, '')) <> '' or card.id is not null
                              or ai.id is not null),
           count(*) filter (where cs.has_brand or card.id is not null or ca.has_brand),
           count(*) filter (where cs.resolved or card.resolved or ca.resolved)
      from kp left join cs on cs.cell = kp.oem
      left join card on card.id = kp.id
      left join (select art.id, bool_or(c2.has_brand) as has_brand,
                        bool_or(c2.resolved) as resolved
                   from art join cs c2 on c2.cell = art.oem group by art.id) ca on ca.id = kp.id
      left join (select distinct id from art) ai on ai.id = kp.id
    union all
    select 5, 'спрос · все живые строки (lib_demand_live.oem)',
           count(*),
           count(*) filter (where btrim(coalesce(dem.oem, '')) <> ''),
           count(*) filter (where cs.has_brand),
           count(*) filter (where cs.resolved)
      from dem left join cs on cs.cell = dem.oem
    union all
    select 6, 'спрос · сторона «заказчик»',
           count(*),
           count(*) filter (where btrim(coalesce(dem.oem, '')) <> ''),
           count(*) filter (where cs.has_brand),
           count(*) filter (where cs.resolved)
      from dem left join cs on cs.cell = dem.oem
     where dem.customer
  )
select ord, cut, total, with_text, with_brand, resolved from r order by ord"""


# Неразрешённые написания данных, которых нет в реестре: после засева должно
# быть ноль — «каждое неразрешённое написание стоит в очереди, а не пропадает».
def вне_очереди_sql(карта_sql: str) -> str:
    части = "\n    union all ".join(
        f"select '{и}'::text as source, cell, n from ({ЯЧЕЙКИ[и]}) s{i}"
        for i, и in enumerate(ИСТ_ДАННЫЕ))
    return f"""\
with
  src as ({части}),
  cells as (select distinct cell, 'xx'::text as code, ''::text as name_key,
                null::text as pn from src),
{_конвейер(карта_sql)},
  unresolved as (
    select distinct s.source, left(btrim(tj.piece), {ДЛИНА}) as piece
      from text_judged tj join src s on s.cell = tj.cell
     where tj.text_reject is null and not tj.in_dict and btrim(tj.piece) <> ''
  )
select u.source, count(*) as unresolved,
       count(*) filter (where a.id is null) as not_in_registry
  from unresolved u
  left join lib_brand_alias a on a.source = u.source and a.spelling = u.piece and a.seen_at = ''
 group by u.source order by u.source"""


РЕЕСТР_ИТОГИ_SQL = """
select source, status, count(*), coalesce(sum(n_rows), 0)
  from lib_brand_alias group by source, status order by source, status
"""
