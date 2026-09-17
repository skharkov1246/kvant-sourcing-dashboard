"""Рендер HTML-дашборда: подстановка metrics + insights в шаблон."""
from __future__ import annotations

import datetime as dt
import json
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
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


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



def render(metrics: dict, insights: dict, *, title: str = DEFAULT_TITLE, company: dict | None = None,
           kam: dict | None = None, eng: dict | None = None, prod: dict | None = None,
           contracts: dict | None = None, reps: dict | None = None, advisor: dict | None = None,
           people: dict | None = None) -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("__TITLE__", title)
    html = html.replace("__DATA_JSON__", _json_for_script(_pack_metrics(metrics)))
    html = html.replace("__INSIGHTS_JSON__", _json_for_script(insights))
    html = html.replace("__COMPANY_JSON__", _json_for_script(_pack_company(company)) if company else "null")
    html = html.replace("__KAM_JSON__", _json_for_script(kam) if kam else "null")
    html = html.replace("__PEOPLE_JSON__", _json_for_script(people) if people else "null")
    html = html.replace("__ENG_JSON__", _json_for_script(eng) if eng else "null")
    html = html.replace("__PRODUCT_JSON__", _json_for_script(prod) if prod else "null")
    html = html.replace("__CONTRACTS_JSON__", _json_for_script(contracts) if contracts else "null")
    html = html.replace("__REPS_JSON__", _json_for_script(reps) if reps else "null")
    html = html.replace("__ADVISOR_JSON__", _json_for_script(advisor) if advisor else "null")
    gen, nxt = _update_stamps()
    html = html.replace("__GENERATED_AT__", gen)
    html = html.replace("__NEXT_UPDATE__", nxt)
    return html


def write(metrics: dict, insights: dict, out_path: str | Path, *, title: str = DEFAULT_TITLE,
          company: dict | None = None, kam: dict | None = None,
          eng: dict | None = None, prod: dict | None = None, contracts: dict | None = None,
          reps: dict | None = None, advisor: dict | None = None, people: dict | None = None) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(metrics, insights, title=title, company=company, kam=kam, eng=eng, prod=prod,
                          contracts=contracts, reps=reps, advisor=advisor, people=people), encoding="utf-8")
    return out
