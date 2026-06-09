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
    # безопасно вставлять в <script>: нейтрализуем закрывающий тег
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def render(metrics: dict, insights: dict, *, title: str = DEFAULT_TITLE, company: dict | None = None,
           kam: dict | None = None, eng: dict | None = None, prod: dict | None = None,
           contracts: dict | None = None, reps: dict | None = None) -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("__TITLE__", title)
    html = html.replace("__DATA_JSON__", _json_for_script(metrics))
    html = html.replace("__INSIGHTS_JSON__", _json_for_script(insights))
    html = html.replace("__COMPANY_JSON__", _json_for_script(company) if company else "null")
    html = html.replace("__KAM_JSON__", _json_for_script(kam) if kam else "null")
    html = html.replace("__ENG_JSON__", _json_for_script(eng) if eng else "null")
    html = html.replace("__PRODUCT_JSON__", _json_for_script(prod) if prod else "null")
    html = html.replace("__CONTRACTS_JSON__", _json_for_script(contracts) if contracts else "null")
    html = html.replace("__REPS_JSON__", _json_for_script(reps) if reps else "null")
    gen, nxt = _update_stamps()
    html = html.replace("__GENERATED_AT__", gen)
    html = html.replace("__NEXT_UPDATE__", nxt)
    return html


def write(metrics: dict, insights: dict, out_path: str | Path, *, title: str = DEFAULT_TITLE,
          company: dict | None = None, kam: dict | None = None,
          eng: dict | None = None, prod: dict | None = None, contracts: dict | None = None,
          reps: dict | None = None) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(metrics, insights, title=title, company=company, kam=kam, eng=eng, prod=prod,
                          contracts=contracts, reps=reps), encoding="utf-8")
    return out
