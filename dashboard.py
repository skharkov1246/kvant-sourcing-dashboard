"""Рендер HTML-дашборда: подстановка metrics + insights в шаблон."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from config import BASE_DIR

TEMPLATE = BASE_DIR / "templates" / "dashboard_core.html"
DEFAULT_TITLE = "Анализ работы сорсеров · КВАНТ"

_MSK = dt.timezone(dt.timedelta(hours=3))


def _update_stamps() -> tuple[str, str]:
    """(время генерации, время следующего ожидаемого обновления) — МСК, ISO-8601.
    Cron в .github/workflows/deploy.yml: `7 */2 * * *` — каждые 2 часа в :07 UTC
    (чётные часы UTC). Считаем ближайший такой слот строго после now."""
    now_utc = dt.datetime.now(dt.timezone.utc)
    nxt = now_utc.replace(minute=7, second=0, microsecond=0)
    while nxt <= now_utc or nxt.hour % 2 != 0:
        nxt += dt.timedelta(hours=1)
    return now_utc.astimezone(_MSK).isoformat(timespec="seconds"), nxt.astimezone(_MSK).isoformat(timespec="seconds")


def _json_for_script(obj) -> str:
    # безопасно вставлять в <script>: нейтрализуем закрывающий тег.
    # separators без пробелов: json.dumps по умолчанию ставит «, » и «: », а на странице
    # в 10 МБ это около мегабайта пробелов — десятая часть веса, отданная ни за что.
    # U+2028 и U+2029 — экранируем: регулярка воркера, вырезающая закрытые массивы, читает
    # их как конец строки, и название сделки с таким знаком обрывало бы строку массива —
    # а с ней и весь скрипт данных страницы
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


# Вкладка «Key-аккаунты и продукты»: деньги — только у сделок, сверенных файлом экономики
# (решение владельца 25.10.2026, DECISIONS: «Деньги на вкладке … — только у сделок,
# сверенных файлом экономики; остальные — счётом, причиной и действием»), и только
# закрытым массивом «Дохода». Массивы ролей, направлений и личных дашбордов (PEOPLE, KAM,
# PRODUCT, REPS) считаются по «Сумме» Битрикса — поэтому в страницу они идут без денег:
# без сумм, подписей сумм, маржи и её доли. Расчёт (people.py, kam.py, reps.py) не
# меняется: те же числа читают живая сверка и премии. Список ключей — по модулям этих
# массивов; строка с «€» вне текстовых полей (название, имя, стадия) — тоже деньги.
TAB_MONEY_KEYS = frozenset({
    "raw", "buyRaw", "bigRaw", "cleanRaw", "marginRaw", "presaleRaw", "realRaw", "salesRaw", "salesBRaw",
    "openSumRaw", "overdueRaw", "loadRaw", "loadClean", "loadCleanLbl", "money", "moneyLbl", "amt", "sum",
    "buy", "margin", "marginPct", "sales", "salesB", "pipeline", "wonSum", "realSum", "realSumB", "presaleSum",
    "lateSum", "bigSum", "bigShare", "cleanSum", "perPersonSum", "overdueSum", "orphanSum", "openSum",
    "noneSum", "lostSum", "medAmt", "bigCut", "avgCheck", "byRevenue",
})
TAB_TEXT_KEYS = frozenset({"t", "title", "name", "label", "lbl", "customer", "pos", "dept", "group", "grp",
                           "cat", "stage", "what", "act", "why", "how", "own", "kam", "boss", "comment"})


def _no_money(obj, key: str = ""):
    """Копия массива вкладки без денег (TAB_MONEY_KEYS и строки с «€» вне TAB_TEXT_KEYS)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in TAB_MONEY_KEYS or (isinstance(v, str) and "€" in v and k not in TAB_TEXT_KEYS):
                continue
            out[k] = _no_money(v, k)
        return out
    if isinstance(obj, list):
        return [_no_money(v, key) for v in obj]
    return obj


# Самые тяжёлые массивы страницы — списки одинаковых записей. Замер живой страницы
# 17.09.2026: подробности запросов сорсеров 2 227 КБ, сделки когорт 1 456 КБ, заказы и
# таймлайн контрактов 1 234 КБ. В обычном JSON имена полей повторяются в КАЖДОЙ записи:
# у сорсеров это 6 467 записей по тринадцать ключей — сотни килобайт чистого повтора.
# Пакуем такие списки в {f: [имена], r: [[значения]]}: содержимое то же, читается на
# клиенте один раз функцией _unpack. Пилот на самом тяжёлом списке; остальные — после
# замера выигрыша, чтобы не менять форму данных там, где это ничего не даёт.
PACK_MARK = "_p"


def _pack_records(rows: list) -> dict | list:
    """Список одинаковых записей → {f: имена полей, r: строки значений}."""
    if not isinstance(rows, list) or len(rows) < 20 or not all(isinstance(r, dict) for r in rows):
        return rows
    fields: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                fields.append(k)
    return {PACK_MARK: 1, "f": fields, "r": [[r.get(k) for k in fields] for r in rows]}


def _pack_company(company: dict | None) -> dict | None:
    """То же для сделок когорт: один список на 1 456 КБ, читается в одном месте."""
    if not isinstance(company, dict):
        return company
    coh = company.get("cohorts")
    if not isinstance(coh, dict) or not isinstance(coh.get("deals"), list):
        return company
    out = dict(company)
    out["cohorts"] = dict(coh, deals=_pack_records(coh["deals"]))
    return out


def _pack_metrics(metrics: dict) -> dict:
    """Копия метрик, где подробности запросов упакованы. Исходный словарь не трогаем:
    его же пишет отчёт в reports/ и читают другие модули."""
    src = metrics.get("sourcersA")
    if not isinstance(src, list) or not src:
        return metrics
    out = dict(metrics)
    out["sourcersA"] = [
        (dict(s, details=_pack_records(s["details"]))
         if isinstance(s, dict) and isinstance(s.get("details"), list) else s)
        for s in src
    ]
    return out



# Самые тяжёлые массивы страницы — списки одинаковых записей. Замер живой страницы
# 17.09.2026: подробности запросов сорсеров 2 227 КБ, сделки когорт 1 456 КБ, заказы и
# таймлайн контрактов 1 234 КБ. В обычном JSON имена полей повторяются в КАЖДОЙ записи:
# у сорсеров это 6 467 записей по тринадцать ключей — сотни килобайт чистого повтора.
# Пакуем такие списки в {f: [имена], r: [[значения]]}: содержимое то же, читается на
# клиенте один раз функцией _unpack. Пилот на самом тяжёлом списке; остальные — после
# замера выигрыша, чтобы не менять форму данных там, где это ничего не даёт.
PACK_MARK = "_p"


def _pack_records(rows: list) -> dict | list:
    """Список одинаковых записей → {f: имена полей, r: строки значений}."""
    if not isinstance(rows, list) or len(rows) < 20 or not all(isinstance(r, dict) for r in rows):
        return rows
    fields: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                fields.append(k)
    return {PACK_MARK: 1, "f": fields, "r": [[r.get(k) for k in fields] for r in rows]}


def _pack_metrics(metrics: dict) -> dict:
    """Копия метрик, где подробности запросов упакованы. Исходный словарь не трогаем:
    его же пишет отчёт в reports/ и читают другие модули."""
    src = metrics.get("sourcersA")
    if not isinstance(src, list) or not src:
        return metrics
    out = dict(metrics)
    out["sourcersA"] = [
        (dict(s, details=_pack_records(s["details"]))
         if isinstance(s, dict) and isinstance(s.get("details"), list) else s)
        for s in src
    ]
    return out



def _pack_kaminc(head: dict) -> dict:
    """Сделки «Дохода» (тысячи одинаковых записей) — упакованным списком, как сорсеры."""
    return dict(head, deals=_pack_records(head.get("deals") or []))


def render(metrics: dict, insights: dict, *, title: str = DEFAULT_TITLE, company: dict | None = None,
           kam: dict | None = None, eng: dict | None = None, prod: dict | None = None,
           contracts: dict | None = None, reps: dict | None = None, advisor: dict | None = None,
           people: dict | None = None, premia: dict | None = None, dealctx: dict | None = None,
           kaminc: dict | None = None, ork_month: dict | None = None) -> str:
    # premia принимается и не выводится: «Премии» сняты с портала по решению владельца
    # 12.10.2026 — ни вкладки, ни массива в странице. Расчёт premia.py остаётся в сборке
    # (main.py, живая сверка); вкладка до снятия — в истории, PR #508.
    del premia
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("__TITLE__", title)
    # Все массивы — ОДНИМ проходом по шаблону: при подстановке по очереди текст из данных
    # (название сделки «__DEALCTX_JSON__» в CONTRACTS) подменялся бы следующим массивом —
    # закрытая часть «Реализации» (dealctx.py) уехала бы в общий массив вкладки.
    вставки = {
        "__DATA_JSON__": _json_for_script(_pack_metrics(metrics)),
        "__INSIGHTS_JSON__": _json_for_script(insights),
        "__COMPANY_JSON__": _json_for_script(_pack_company(company)) if company else "null",
        # массивы вкладки «Key-аккаунты и продукты» — без денег (TAB_MONEY_KEYS)
        "__KAM_JSON__": _json_for_script(_no_money(kam)) if kam else "null",
        "__PEOPLE_JSON__": _json_for_script(_no_money(people)) if people else "null",
        "__ENG_JSON__": _json_for_script(eng) if eng else "null",
        "__PRODUCT_JSON__": _json_for_script(_no_money(prod)) if prod else "null",
        "__CONTRACTS_JSON__": _json_for_script(contracts) if contracts else "null",
        "__REPS_JSON__": _json_for_script(_no_money(reps)) if reps else "null",
        "__ADVISOR_JSON__": _json_for_script(advisor) if advisor else "null",
        # закрытая часть «Реализации» (чаты и договоры по сделкам, dealctx.py): отдельный
        # массив — воркер вырезает его всем, у кого нет права «договоры и деньги»
        "__DEALCTX_JSON__": _json_for_script(dealctx) if dealctx else "null",
        # «Доход» вкладки «Key-аккаунты и продукты» (kaminc.py): открытая часть — счёт, даты,
        # стадии, действия — всем, у кого вкладка; деньги — отдельным массивом, воркер
        # оставляет его только праву «Деньги КАМов» (решение владельца 25.10.2026)
        "__KAMINC_HEAD_JSON__": _json_for_script(_pack_kaminc(kaminc["head"])) if kaminc else "null",
        "__KAMINC_JSON__": _json_for_script(kaminc["money"]) if kaminc and kaminc.get("money") else "null",
        # показатели менеджеров ОРК поимённо (подвкладка «Месяц» «Реализации», orkview.py):
        # отдельный массив — воркер оставляет его только праву «Реализация: показатели
        # менеджеров ОРК» (решение владельца 26.10.2026, п. 3); в CONTRACTS их нет
        "__ORKMONTH_JSON__": _json_for_script(ork_month) if ork_month else "null",
    }
    html = re.sub("|".join(map(re.escape, вставки)), lambda m: вставки[m.group(0)], html)
    gen, nxt = _update_stamps()
    html = html.replace("__GENERATED_AT__", gen)
    html = html.replace("__NEXT_UPDATE__", nxt)
    return html


def write(metrics: dict, insights: dict, out_path: str | Path, *, title: str = DEFAULT_TITLE,
          company: dict | None = None, kam: dict | None = None,
          eng: dict | None = None, prod: dict | None = None, contracts: dict | None = None,
          reps: dict | None = None, advisor: dict | None = None, people: dict | None = None,
          premia: dict | None = None, dealctx: dict | None = None, kaminc: dict | None = None,
          ork_month: dict | None = None) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(metrics, insights, title=title, company=company, kam=kam, eng=eng, prod=prod,
                          contracts=contracts, reps=reps, advisor=advisor, people=people,
                          premia=premia, dealctx=dealctx, kaminc=kaminc, ork_month=ork_month),
                   encoding="utf-8")
    return out
