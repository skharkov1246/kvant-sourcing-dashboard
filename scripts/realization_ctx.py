#!/usr/bin/env python3
"""Свод оценок чатов и разбора договоров по сделкам → закрытый ключ KV realctx:v1.

ЗАЧЕМ. Решение владельца 23.10.2026 (макет «Реализация: чаты и договоры»): вкладка
«Реализация» показывает оценку хода сделки по чату и риски договоров. Строки лежат в
закрытой базе, а сборка дашборда базы не видит и не должна. Поэтому прогоны, которые их
пишут (chat-context.yml, contract-terms.yml), после записи кладут свод по сделкам в
закрытый ключ KV — так же, как снимок чатов (chat:v1, chat_scan.py). Сборка читает ключ
в файл на раннере (--fetch) и отдаёт его dealctx.py. Воркер портала этот ключ не читает.

ЧТО В КЛЮЧЕ — только номера сделок, даты и значения из закрытых списков:
  chats      по сделке: когда прогон оценки последний раз читал чат (checked), когда
             в нём последний раз писал человек (human), последняя непомеченная оценка
             (deal_chat_ctx_latest): состояние, направление, риск, виды риска, мяч,
             ждут ли ответа, срок из переписки и что держит.
  contracts  по сделке — последний прогон чтения договоров, прошедший гейт стороны
             («недочитана» — прежним полным разбором, если документы те же:
             выбор_прогона): исход разбора (contract_deal_outcome), сколько документов
             каждой стороны прочитано и коды флагов сверки (contract_b2b_flags).
  econ       по сделке — финальная экономика из файла «Экономика проекта» (решение
             владельца 23.10.2026, scripts/econ_files.py): последнее чтение сделки
             (econ_file_latest), и только если оно «сверено» — продажа в валюте подачи и
             в €, закупка в €, наценка над поставщиком, переторжка (у неё — и продажа
             «Нашего КП», kpSale: «Сумма», оставленная по ней, — пункт), позиций, курсы файла
             и сверка с «Суммой» на день чтения (ratio, note: по ней сборка видит, что «Сумму»
             меняли после чтения). Вид берёт только прогоны, прошедшие гейт сумм
             (econ_file_runs): упавший или снятый прогон в свод не попадает — ни здесь, ни в
             ночном своде chat-context.yml. Ни пути, ни имени файла. Ключ закрытый; на
             странице числа — только в DEALCTX.
Ни текста чатов, ни условий договоров, ни цитат.

ДВЕ ПРОВЕРКИ (распоряжение 26.09.2026). Исход и флаги пишет прогон чтения; здесь они
пересчитываются по строкам contract_terms тем же кодом (contract_terms.флаги_сделки).
Не сошлось — сделка уходит с исходом «разошлось» и без флагов, а шаг кончается кодом 1
ПОСЛЕ записи остального: вкладка не покажет непроверенного, а журнал позовёт разбор.
Строки прогона до 23.10.2026 (исходов тогда не писали) берутся пересчётом — только у
прогона, записавшего флаги (значит, гейт стороны пройден). Экономику файла база отбирает
по статусу и проверяет ограничением строки, здесь — второй раз (сверить_экономику):
не прошла — сделка без экономики, шаг кончается кодом 1 после записи. Готовый свод перед
записью проходит разбор dealctx.clean: запись не по форме — стоп, ключ не трогается.

ЗАПИСЬ. KV → перечитать и сверить (как chat_scan). В журнал — только счётчики (правило 17).

    python scripts/realization_ctx.py --publish     # база → KV (SUPABASE_DB_URL, CLOUDFLARE_*)
    python scripts/realization_ctx.py --dry         # база → счётчики, без записи
    python scripts/realization_ctx.py --fetch ПУТЬ  # KV → файл (сборка дашборда)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import dealctx  # noqa: E402  (форма свода — общая со сборкой)
# клиент KV и чтение ключа — в realization_ctx_kv.py: сборке (--fetch) не нужны ни база,
# ни разбор договоров
from realization_ctx_kv import KV_KEY, CtxError, KVStore, fetch, kv_store, _kv  # noqa: E402,F401

MAX_BYTES = 8 * 1024 * 1024
MSK = dealctx.MSK
#: Статус сверенного файла экономики — scripts/econ_template.СВЕРЕНО (сверяет тест):
#: модуль шаблона тянет openpyxl, своду он не нужен.
ECON_VERIFIED = "сверено"
#: Порядок строк сделки в выгрузке — как contract_terms.порядок_строки: вид документа по
#: contract_terms.ПОРЯДОК_ВИДОВ (спецификации 1174 и 132, Purchase order 172, рамочные
#: 1192 и 175), новая карточка, длинный и больший номер файла раньше. Сверяет тест.
ПОРЯДОК_СТРОК_SQL = ("array_position(array[1174, 132, 172, 1192, 175], card_entity), card_id desc, "
                     "length(file_id) desc, file_id desc")
#: Исходы неполного разбора: прежним полным разбором (выбор_прогона) заменяется только
#: «недочитана» — и только если документы те же; «не читается» (файла нет у портала,
#: формат, предел чтения) следующий прогон не вылечит — показывается всегда.
НЕ_ЗАТИРАЮТ = ("недочитана", "не читается")
#: Записанный исход ↔ пересчёт, которые сходятся: до 25.10.2026 «не читается» (файла нет
#: у портала, формат, предел) записывалось как «недочитана» — пересчёт нынешним кодом
#: точнее, и вкладка показывает его, а не «разошлось».
СХОДЯТСЯ = {("недочитана", "не читается")}


def выбор_прогона(исходы: list[dict], файлы: dict[tuple[int, str], frozenset]) -> dict:
    """Прогон, чей разбор сделки показывает вкладка. исходы — записанные исходы сделки от
    нового к старому; файлы — {(сделка, прогон): файлы документов (вид, карточка, файл)}.

    Последний исход — кроме «недочитана», которую заменяет последний полный разбор, если
    у него ровно те же файлы: прогон дошёл до срока или бюджета, а документы с прошлого
    чтения не менялись (недельный прогон читает от новых сделок, старые обрываются
    сроком). Нашёлся новый документ (рамочный, Purchase order, прежний процесс) или
    пропал прежний — прежний разбор собран не из того набора, и его флаги соврали бы:
    показывается «недочитана». «Не читается» не заменяется: само оно не пройдёт, нужен
    человек, а прежние флаги без нечитаемого файла — неполные."""
    последний = исходы[0]
    if последний["outcome"] != "недочитана":
        return последний
    полный = next((x for x in исходы if x["outcome"] not in НЕ_ЗАТИРАЮТ), None)
    if полный is None:
        return последний
    свои = файлы.get((последний["deal"], последний["run"]), frozenset())
    return полный if свои == файлы.get((полный["deal"], полный["run"]), frozenset()) else последний


def _ct():
    """scripts/contract_terms.py — исход и флаги сделки тем же кодом, что у прогона чтения."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import contract_terms
    return contract_terms


def _iso(t) -> str | None:
    if t is None:
        return None
    if isinstance(t, dt.datetime):
        return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).isoformat()
    return str(t)


# ── закрытая база ───────────────────────────────────────────────────────────────

class База:
    """statement_timeout — в строке подключения (правило 9): SET откатывается с транзакцией."""

    def __init__(self, dsn: str, схема: str | None = None):
        import psycopg2
        опции = "-c statement_timeout=120000"
        if схема:
            опции += f" -c search_path={схема}"
        self.с = psycopg2.connect(dsn, options=опции)
        self.с.set_session(readonly=True)

    def есть(self, *таблицы: str) -> bool:
        with self.с, self.с.cursor() as c:
            c.execute("select " + ", ".join(["to_regclass(%s) is not null"] * len(таблицы)), таблицы)
            return all(c.fetchone())

    def _все(self, sql: str, p=()) -> list[tuple]:
        with self.с, self.с.cursor() as c:
            c.execute(sql, p)
            return c.fetchall()

    def чаты(self) -> list[dict]:
        """checked — последнее УДАЧНОЕ чтение чата прогоном оценки (read_at): checked_at
        сдвигает и неудача (нет доступа), и по нему недоступный чат выглядел бы свежим.
        Строки до read_at (23.10.2026) — время их оценки: оценка была по прочитанному."""
        if not self.есть("deal_chat_state", "deal_chat_ctx_latest"):
            return []
        with self.с, self.с.cursor() as c:
            c.execute("select count(*) from pg_attribute where attrelid = to_regclass('deal_chat_state') "
                      "and attname = 'read_at' and not attisdropped")
            есть_read_at = c.fetchone()[0] == 1
        прочитан = "coalesce(s.read_at, l.assessed_at)" if есть_read_at else "l.assessed_at"
        rows = self._все(f"""
            select coalesce(s.deal_id, l.deal_id), {прочитан}, s.last_human_at,
                   l.assessed_at, l.state, l.direction, l.risk, l.ball_role, l.reply_expected,
                   l.due_date, l.due_kind, l.verdict->'risk_kinds', l.verdict->'blocker', l.confidence
              from deal_chat_state s
              full outer join deal_chat_ctx_latest l on l.deal_id = s.deal_id""")
        ключи = ("deal", "checked", "human", "at", "state", "direction", "risk", "ball", "reply",
                 "due", "dueKind", "kinds", "blocker", "conf")
        return [dict(zip(ключи, r)) for r in rows]

    def договоры(self) -> tuple[list[dict], list[dict], list[dict]]:
        """(выбор прогона по сделке, строки условий выбранных пар, флаги выбранных пар)."""
        if not self.есть("contract_terms", "contract_b2b_flags"):
            return [], [], []
        выбор: dict[int, dict] = {}
        if self.есть("contract_deal_outcome"):
            # последний исход сделки; «недочитана» — прежним полным разбором с теми же
            # файлами (выбор_прогона): файлы обоих прогонов — вторым запросом, только
            # у таких сделок
            по_сделке: dict[int, list[dict]] = defaultdict(list)
            for deal, run, исход, at, вер in self._все("""
                    select deal_id, run_id, outcome, created_at, method_ver
                      from contract_deal_outcome
                     order by deal_id, created_at desc, run_id desc"""):
                по_сделке[int(deal)].append({"deal": int(deal), "run": run, "outcome": исход, "at": at, "ver": вер})
            сверить = [(x["deal"], x["run"]) for rr in по_сделке.values() if rr[0]["outcome"] == "недочитана"
                       for x in (rr[0], next((y for y in rr if y["outcome"] not in НЕ_ЗАТИРАЮТ), None)) if x]
            файлы: dict[tuple[int, str], set] = defaultdict(set)
            if сверить:
                for deal, run, вид, карточка, файл in self._все(
                        "select deal_id, run_id, card_entity, card_id, file_id from contract_terms "
                        "where (deal_id, run_id) in (select * from unnest(%s::bigint[], %s::text[])) "
                        "and status <> 'no_file'", ([d for d, _r in сверить], [r for _d, r in сверить])):
                    файлы[(int(deal), run)].add((int(вид), int(карточка), str(файл)))
            замороженные = {к: frozenset(v) for к, v in файлы.items()}
            for deal, rr in по_сделке.items():
                выбор[deal] = выбор_прогона(rr, замороженные)
            прежние = """
                select distinct on (t.deal_id) t.deal_id, t.run_id, t.created_at, t.method_ver
                  from contract_terms t
                 where exists (select 1 from contract_b2b_flags f where f.run_id = t.run_id)
                   and not exists (select 1 from contract_deal_outcome o where o.deal_id = t.deal_id)
                 order by t.deal_id, t.created_at desc, t.run_id desc"""
        else:
            прежние = """
                select distinct on (t.deal_id) t.deal_id, t.run_id, t.created_at, t.method_ver
                  from contract_terms t
                 where exists (select 1 from contract_b2b_flags f where f.run_id = t.run_id)
                 order by t.deal_id, t.created_at desc, t.run_id desc"""
        # прогоны до 23.10.2026: исхода нет — пересчёт, и только у прогона с флагами
        for deal, run, at, вер in self._все(прежние):
            выбор.setdefault(int(deal), {"deal": int(deal), "run": run, "outcome": None, "at": at, "ver": вер})
        if not выбор:
            return [], [], []
        сделки = [v["deal"] for v in выбор.values()]
        прогоны = [v["run"] for v in выбор.values()]
        пары = "(deal_id, run_id) in (select * from unnest(%s::bigint[], %s::text[]))"
        # порядок строк сделки — порядок свода условий прогона чтения (contract_terms.
        # ПОРЯДОК_ВИДОВ и порядок_строки, сверяет тест): спецификации, Purchase order,
        # рамочные; новая карточка и новый файл раньше. Разойдись он — пересчёт свёл бы
        # условия в другом порядке и назвал бы сделку «разошлось».
        # link_source — флаги по отсутствию у заказа, где у поставщика только Purchase order;
        # note — статус строк до 25.10.2026 по нынешним правилам (contract_terms.уточнить_статус)
        строки = self._все(f"""
            select deal_id, run_id, card_entity, card_id, file_id, order_id, side, side_company, side_link,
                   side_template, status, terms, dropped, link_source, note
              from contract_terms where {пары}
             order by deal_id, {ПОРЯДОК_СТРОК_SQL}""",
                            (сделки, прогоны))
        ключи = ("deal_id", "run_id", "card_entity", "card_id", "file_id", "order_id", "side", "side_company",
                 "side_link", "side_template", "status", "terms", "dropped", "link_source", "note")
        флаги = self._все(f"select deal_id, run_id, order_id, code, days from contract_b2b_flags where {пары}",
                          (сделки, прогоны))
        return (list(выбор.values()), [dict(zip(ключи, r)) for r in строки],
                [{"deal_id": int(r[0]), "run_id": r[1], "order_id": r[2], "code": r[3], "days": r[4]}
                 for r in флаги])

    def экономика(self) -> list[dict]:
        """Последнее чтение файла экономики каждой сделки — только сверенное и только из
        прогона, прошедшего гейт сумм. Чтение со сбоем доставки или разбора последним не
        считается (вид econ_file_latest)."""
        if not self.есть("econ_file_latest"):
            return []
        # вид до второй ревизии (25.10.2026) — без продажи «Нашего КП»: свод не падает, пока
        # схема не применена заново (econ-files.yml или zip-db.yml)
        with self.с, self.с.cursor() as c:
            c.execute("select count(*) from pg_attribute where attrelid = to_regclass('econ_file_latest') "
                      "and attname = 'kp_sale_sub' and not attisdropped")
            кп = "kp_sale_sub" if c.fetchone()[0] == 1 else "null::numeric"
        ключи = ("deal", "at", "status", "cur", "sale", "saleEur", "buyEur", "markup", "rebid", "items", "fx",
                 "ratio", "note", "kpSale")
        rows = self._все(f"""
            select deal_id, read_at, status, sub_cur, sale_sub, sale_eur, purchase_eur, markup, rebid, items, fx,
                   bitrix_ratio, bitrix_note, {кп}
              from econ_file_latest where status = %s""", (ECON_VERIFIED,))
        return [dict(zip(ключи, r)) for r in rows]

    def закрыть(self) -> None:
        self.с.close()


# ── свод ─────────────────────────────────────────────────────────────────────────

def свод_чатов(строки: list[dict]) -> dict[str, dict]:
    out = {}
    for r in строки:
        if r.get("deal") is None or r.get("checked") is None:
            continue
        x = {"checked": _iso(r["checked"]), "human": _iso(r.get("human")), "at": _iso(r.get("at"))}
        if r.get("at") is not None:
            b = r.get("blocker")
            x.update(state=r["state"], direction=r["direction"], risk=r["risk"], ball=r["ball"],
                     reply=r["reply"], kinds=list(r.get("kinds") or []),
                     due=r["due"].isoformat() if r.get("due") else None,
                     dueKind=r.get("dueKind") if r.get("due") else None,
                     blocker={"kind": b.get("kind"), "on": b.get("on_role")} if isinstance(b, dict) else None,
                     # уверенность модели в оценке — показывается рядом: это оценка, а не факт
                     conf=round(float(r["conf"]), 2) if r.get("conf") is not None else None)
        out[str(int(r["deal"]))] = x
    return out


def свод_договоров(выбор: list[dict], строки: list[dict], флаги: list[dict], ct) -> tuple[dict, Counter]:
    """→ ({сделка: запись}, счётчики). Исход и флаги — из записи прогона и пересчётом."""
    по_паре: dict[tuple, list[dict]] = defaultdict(list)
    for r in строки:
        по_паре[(int(r["deal_id"]), r["run_id"])].append(r)
    флаги_пары: dict[tuple, list[dict]] = defaultdict(list)
    for f in флаги:
        флаги_пары[(f["deal_id"], f["run_id"])].append(f)
    out, n = {}, Counter()
    for v in выбор:
        key = (v["deal"], v["run"])
        # статус строк до 25.10.2026 — по нынешним правилам: временный отказ закачки тогда
        # писался download, и пересчёт назвал бы сделку «не читается»
        rs = [{**r, "status": ct.уточнить_статус(r["status"], r.get("note"))} for r in по_паре.get(key, [])]
        записано = флаги_пары.get(key, [])
        флаги_пересчёт, исход_пересчёт = ct.флаги_сделки(rs) if rs else ([], "нет договоров")
        исход = v["outcome"] or исход_пересчёт
        if (исход, исход_пересчёт) in СХОДЯТСЯ:
            исход = исход_пересчёт
        n["итог" if v["outcome"] else "пересчёт"] += 1
        сошлось = (исход == исход_пересчёт
                   and (not записано or исход == "пара")
                   and {(f["order_id"], f["code"]) for f in записано}
                   == {(f["order_id"], f["code"]) for f in флаги_пересчёт})
        if not сошлось:
            n["разошлось"] += 1
            # красный шаг — только по исходу текущего метода: строки прогона до 23.10 и
            # другой версии правил пересчитываются нынешними и вправе разойтись
            if v["outcome"] and v.get("ver") == ct.METHOD_VER:
                n["разошлось_текущий"] += 1
            исход = "разошлось"
        # документ — файл карточки: рамочный над спецификациями двух заказов — одна строка счёта
        прочитано = Counter(сторона for (_в, _к, _ф, сторона) in {
            (r["card_entity"], r["card_id"], r.get("file_id"), r["side"]) for r in rs
            if r["status"] in ct.ПРОЧИТАН and r.get("terms") and r.get("side") in ("customer", "supplier")})
        out[str(v["deal"])] = {
            "at": _iso(v["at"]), "outcome": исход,
            "read": {"customer": прочитано["customer"], "supplier": прочитано["supplier"]},
            "flags": ([{"code": f["code"], "order": f["order_id"], "days": f["days"]}
                       for f in sorted(записано, key=lambda f: (f["order_id"] or 0, f["code"]))]
                      if исход == "пара" else []),
        }
        n["исход:" + исход] += 1
    return out, n


def сверить_экономику(r: dict) -> bool:
    """Второй способ после базы: статус, числа положительны и согласованы между собой."""
    def pos(x) -> bool:
        return x is not None and not isinstance(x, bool) and float(x) > 0
    try:
        if r.get("status") != ECON_VERIFIED or not all(pos(r.get(k)) for k in ("sale", "saleEur", "buyEur", "items")):
            return False
        se, buy, mk = float(r["saleEur"]), float(r["buyEur"]), r.get("markup")
        # продажа «Нашего КП» — только у переторжки и только положительная
        if r.get("kpSale") is not None and (r.get("rebid") is not True or not pos(r["kpSale"])):
            return False
        return (se >= buy and mk is not None and abs(float(mk) - (se / buy - 1)) <= 1e-4
                and isinstance(r.get("rebid"), bool) and isinstance(r.get("cur"), str))
    except (TypeError, ValueError):
        return False


def свод_экономики(строки: list[dict]) -> tuple[dict, Counter]:
    out, n = {}, Counter()
    for r in строки:
        if r.get("deal") is None:
            continue
        if not сверить_экономику(r):
            n["экономика: не прошла сверку"] += 1
            continue
        fx = r.get("fx") if isinstance(r.get("fx"), dict) else {}
        out[str(int(r["deal"]))] = {
            "at": _iso(r["at"]), "sale": round(float(r["sale"]), 2), "cur": r["cur"],
            "saleEur": round(float(r["saleEur"]), 2), "buyEur": round(float(r["buyEur"]), 2),
            "markup": round(float(r["markup"]), 6), "rebid": bool(r["rebid"]), "items": int(r["items"]),
            "fx": {str(k): float(v) for k, v in fx.items()},
            "kpSale": round(float(r["kpSale"]), 2) if r.get("kpSale") is not None else None,
            # сверка с «Суммой» на день чтения — сборке, чтобы видеть «Сумму», изменённую позже
            "ratio": round(float(r["ratio"]), 6) if r.get("ratio") is not None else None,
            "note": r.get("note") or None}
        n["экономика: переторжка"] += bool(r["rebid"])
    return out, n


def собрать(база, ct=None, сейчас: dt.datetime | None = None) -> tuple[dict, Counter]:
    ct = ct or _ct()
    чаты = свод_чатов(база.чаты())
    выбор, строки, флаги = база.договоры()
    договоры, n = свод_договоров(выбор, строки, флаги, ct)
    экономика, n_эк = свод_экономики(база.экономика())
    n.update(n_эк)
    свод = {"version": dealctx.VERSION,
            "at": (сейчас or dt.datetime.now(MSK)).astimezone(MSK).isoformat(timespec="seconds"),
            "chats": чаты, "contracts": договоры, "econ": экономика}
    # второй разбор — тем же кодом, что читает сборка: запись не по форме не уйдёт в ключ
    разбор = dealctx.clean(json.loads(json.dumps(свод, ensure_ascii=False)))
    if разбор is None or разбор["bad"]:
        raise CtxError(f"SHAPE_REJECTED:{0 if разбор is None else разбор['bad']}")
    n["чатов"] = len(чаты)
    n["оценок"] = sum(1 for x in чаты.values() if x.get("at"))
    n["сделок с договорами"] = len(договоры)
    n["флагов"] = sum(len(x["flags"]) for x in договоры.values())
    n["экономика"] = len(экономика)
    return свод, n


def журнал(n: Counter, raw: bytes) -> list[str]:
    исходы = " · ".join(f"{k.split(':', 1)[1]} {v}" for k, v in sorted(n.items()) if k.startswith("исход:"))
    return [f"  чаты: {n['чатов']} (с оценкой {n['оценок']})",
            f"  договоры: сделок {n['сделок с договорами']} (исход прогона {n['итог']}, пересчёт прежних "
            f"{n['пересчёт']}); исходы: {исходы or 'нет'}; флагов {n['флагов']}",
            f"  финальная экономика: сделок со сверенным файлом {n['экономика']} (переторжка "
            f"{n['экономика: переторжка']}); не прошли вторую сверку {n['экономика: не прошла сверку']}",
            f"  размер свода {len(raw)} Б"]




# ── прогоны ─────────────────────────────────────────────────────────────────────

def publish(база, store, *, dry: bool, ct=None, печать=print) -> int:
    свод, n = собрать(база, ct)
    raw = json.dumps(свод, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise CtxError("CTX_TOO_LARGE")
    печать(f"• Свод чатов и договоров для вкладки «Реализация» ({KV_KEY})")
    for s in журнал(n, raw):
        печать(s)
    if dry:
        печать("  вхолостую: ключ не записан")
    else:
        _kv(store.put, raw)
        печать(f"  ✓ KV {KV_KEY} записан и сверен чтением")
    if n["экономика: не прошла сверку"]:
        печать(f"::error::финальная экономика: у {n['экономика: не прошла сверку']} сделок строка базы «сверено» "
               "не прошла вторую сверку — во вкладку они не попали, нужен разбор")
    if n["разошлось_текущий"]:
        печать(f"::error::договоры: у {n['разошлось_текущий']} сделок исход или флаги прогона не сошлись с "
               "пересчётом — во вкладке они «сверка разошлась», нужен разбор")
        return 1
    if n["экономика: не прошла сверку"]:
        return 1
    if n["разошлось"]:
        печать(f"::warning::договоры: у {n['разошлось']} сделок прежних прогонов (до 23.10 или прежних правил) "
               "пересчёт разошёлся — во вкладке они «сверка разошлась» до следующего чтения")
    return 0


def main(argv=None, **kw) -> int:
    ap = argparse.ArgumentParser(description="Свод чатов и договоров → закрытый ключ KV")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--publish", action="store_true", help="база → KV")
    g.add_argument("--dry", action="store_true", help="база → счётчики, без записи")
    g.add_argument("--fetch", metavar="ПУТЬ", help="KV → файл для сборки дашборда")
    a = ap.parse_args(argv)
    база = None
    try:
        if a.fetch:
            return fetch(Path(a.fetch), kw.get("store") or kv_store())
        dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
        if not dsn and "база" not in kw:
            print("::error::нет SUPABASE_DB_URL — свод собирать не из чего")
            return 2
        база = kw.get("база") or База(dsn)
        store = None if a.dry else (kw.get("store") or kv_store())
        return publish(база, store, dry=a.dry, ct=kw.get("ct"))
    except CtxError as e:
        print(f"::error::свод чатов и договоров: {e}")
        return 1
    finally:
        if база is not None and "база" not in kw:
            база.закрыть()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        # текст исключения в журнал не идёт: у ошибки базы в нём бывают значения строки
        код = getattr(e, "pgcode", None)
        print(f"::error::свод упал: {type(e).__name__}" + (f" (pgcode {код})" if код else ""))
        raise SystemExit(1)
