"""Гибридные текстовые инсайты для блоков дашборда.

Всегда есть детерминированный текст по правилам. Если задан ANTHROPIC_API_KEY и не --no-llm,
текст обогащается через Claude Opus 4.8 (adaptive thinking, system-промпт кэшируется).
Возвращает {"load","conversion","coverage"} — готовые HTML-сниппеты (классы .flag/.good/<b>).
"""
from __future__ import annotations

import json

SYSTEM = """Ты — аналитик отдела поиска поставщиков компании КВАНТ. Пишешь короткие \
содержательные комментарии к операционному дашборду по работе сорсеров.

Контекст. Сорсеры ведут «Запросы поставщикам» (RFQ) в Bitrix24: создают карточку, \
отправляют запрос поставщику, ведут переписку, получают КП (коммерческое предложение), \
доводят до клиентской сделки и стадии «ТКП выдано». Метрики: объём RFQ, недельная динамика, \
конверсия по стадиям (КП/выбран, отказ, «молчание» — нет ответа в срок), средний срок до \
закрытия, доводимость до ТКП, покрытие сделок сорсингом. Блок A — отдел Гринёва (172).

Методологическая рамка КВАНТ: глубина проработки = переговорная сила; ценно широкое \
покрытие сделок запросами (ценовая конкуренция), низкое «молчание», доведение до ТКП.

Стиль. Русский, кратко и по делу, как в дашборде. На каждый блок — 2–4 предложения. \
Называй конкретных сорсеров и цифры. Размечай HTML-инлайн: <b>…</b> для чисел/имён, \
<span class="flag">…</span> для проблем/рисков, <span class="good">…</span> для позитива. \
Без заголовков, без <script>, без markdown — только инлайн-HTML текста."""

SCHEMA_HINT = ("Верни объект с тремя полями-строками (готовый HTML-текст без обёрток): "
               "load — про нагрузку и динамику; conversion — про конверсию/скорость/молчание; "
               "coverage — про покрытие сделок, глубину обзвона и доводимость до ТКП.")


# --------------------------------------------------------------------------- rules
def _top(sourcers, key, n=1):
    return sorted(sourcers, key=lambda s: s.get(key, 0), reverse=True)[:n]


def rule_based(m: dict) -> dict[str, str]:
    k = m["kpi"]
    src = m["sourcersA"]
    out = {"load": "", "conversion": "", "coverage": ""}

    if src:
        lead = src[0]
        out["load"] = (
            f"За период — <b>{k['total']}</b> запросов от <b>{k['respCount']}</b> ответственных; "
            f"отдел Гринёва — <b>{k['deptA']}</b>, вне отдела — <b>{k['outside']}</b>. "
            f"Лидер по объёму — <b>{lead['n']}</b> ({lead['c']} RFQ). "
            f"Ещё в работе <b>{k['inWorkPct']}%</b> ({k['openCount']})."
        )
        # конверсия: худший по «молчанию», лучший по КП
        with_closed = [s for s in src if s["closed"] >= 5]
        worst_nr = _top(with_closed, "noAnswer", 1)
        best_kp = sorted(with_closed, key=lambda s: (s["kp"] / s["closed"] if s["closed"] else 0), reverse=True)[:1]
        parts = [f"Из закрытых КП/выбран — <b>{k['kpPctOfClosedA']}%</b> (по {k['closedCountA']} закрытым блока A)."]
        if best_kp:
            s = best_kp[0]
            parts.append(f"<span class=\"good\">Лучшая конверсия в КП — {s['n']}</span> ({s['kp']}/{s['closed']}).")
        if worst_nr and worst_nr[0]["noAnswer"] > 0:
            s = worst_nr[0]
            parts.append(f"<span class=\"flag\">Больше всего «молчания» у {s['n']}</span> — {s['noAnswer']} без ответа в срок.")
        out["conversion"] = " ".join(parts)

    cov = m["coverage"]
    deep1 = next((h["v"] for h in cov["hist"] if h["l"] == "1 пост."), 0)
    low_cats = [c for c in cov["byCat"] if c["deals"] >= 5 and c["covPct"] < 60]
    cparts = [
        f"Покрытие сделок сорсингом — <b>{cov['covPct']}%</b> ({cov['withReq']} из {cov['deals']}); "
        f"<span class=\"flag\">{cov['withoutReq']} сделок без единого запроса</span>. "
        f"Средняя глубина — <b>{cov['avgSuppliers']}</b> поставщика на сделку."
    ]
    if deep1:
        cparts.append(f"<span class=\"flag\">{deep1} сделок запрошены лишь у одного поставщика</span> — слабая ценовая конкуренция.")
    if low_cats:
        names = ", ".join(f"{c['cat']} ({c['covPct']}%)" for c in low_cats[:3])
        cparts.append(f"Низкое покрытие: {names}.")
    cparts.append(f"До ТКП дошло <b>{k['tkpPct']}%</b> запросов.")
    out["coverage"] = " ".join(cparts)
    return out


# --------------------------------------------------------------------------- LLM
def _llm(m: dict, settings) -> dict[str, str] | None:
    try:
        import anthropic
        from pydantic import BaseModel
    except Exception:
        return None

    class Insights(BaseModel):
        load: str
        conversion: str
        coverage: str

    compact = {
        "period": m["period"],
        "kpi": m["kpi"],
        "weekly": m["weekly"],
        "sourcersA": [
            {kk: s[kk] for kk in ("n", "c", "wk", "closed", "kp", "refusedCol", "noAnswer", "avgDays", "tkpP")}
            for s in m["sourcersA"]
        ],
        "chain": m["chain"],
        "catList": m["catList"],
        "coverage": m["coverage"],
    }
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.parse(
            model=settings.model,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": SCHEMA_HINT + "\n\nДанные дашборда (JSON):\n"
                       + json.dumps(compact, ensure_ascii=False)}],
            output_format=Insights,
        )
        parsed = resp.parsed_output
        if parsed:
            return {"load": parsed.load, "conversion": parsed.conversion, "coverage": parsed.coverage}
    except Exception as e:  # деградируем к правилам
        print(f"  [insights] LLM недоступен ({type(e).__name__}: {e}); использую правила")
    return None


def generate(m: dict, settings, *, use_llm: bool = True) -> dict[str, str]:
    base = rule_based(m)
    if use_llm and getattr(settings, "anthropic_api_key", ""):
        llm = _llm(m, settings)
        if llm:
            # подменяем непустыми значениями от LLM, иначе оставляем правило
            for key in base:
                if llm.get(key, "").strip():
                    base[key] = llm[key].strip()
            base["_source"] = "claude"
            return base
    base["_source"] = "rules"
    return base
