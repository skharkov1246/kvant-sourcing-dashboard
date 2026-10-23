"""Гибридные текстовые инсайты для блоков дашборда.

Всегда есть детерминированный текст по правилам. Если задан ANTHROPIC_API_KEY и не --no-llm,
текст обогащается через Claude Opus 4.8 (adaptive thinking, system-промпт кэшируется).
Возвращает {"load","conversion","coverage"} — готовые HTML-сниппеты (классы .flag/.good/<b>).
"""
from __future__ import annotations

import datetime as dt
import json

SYSTEM = """Ты — аналитик отдела поиска поставщиков компании КВАНТ. Пишешь короткие \
содержательные комментарии к операционному дашборду по работе сорсеров.

Контекст. Сорсеры ведут «Запросы поставщикам» (RFQ) в Bitrix24: создают карточку, \
отправляют запрос поставщику, ведут переписку, получают КП (коммерческое предложение). \
Метрики: объём RFQ, недельная динамика, \
конверсия по стадиям (КП засчитывается только файлом в «Offer from supplier»; Price at Work — КП без итога; \
Selected — выбран; «не выбран» — Not Selected, не прошли по цене, не подошло по технике; отказ; «молчание» — \
нет ответа в срок; правила отметки КП владельца от 19.10.2026 и их нарушения — без признаков \
«до правил»: наследие до 19.10 и льготный срок graceUntil для Price at Work без итога и файла без \
перевода стадии), средний срок до закрытия, покрытие сделок сорсингом. Блок A — отдел Гринёва (172). \
Долю «дошло до ТКП» не называй: она считается по текущей стадии сделки, пресейл не размечен, \
на странице она скрыта до пересчёта по истории стадий. «quotes» — КП файлом в карточке; до 19.10.2026 \
файл не был обязателен — КП без файла не учтены, за период доля может быть ниже числа полученных КП. \
Глубина обзвона — число запросов \
(карточек) на сделку, а не разных поставщиков.

Состав отдела меняется: людей принимают и увольняют. У каждого сорсера своё окно \
работы внутри периода (from–to, wd рабочих дней); act=false — ушёл. Сравнивай людей по \
темпу (perDay — запросов в рабочий день своего окна, perDay4 — за последние 4 недели), \
а не по итогу c: итог растёт со стажем. Ушедших не ставь в рейтинг, их открытые \
карточки — работа на переназначение.

Методологическая рамка КВАНТ: глубина проработки = переговорная сила; ценно широкое \
покрытие сделок запросами (ценовая конкуренция), низкое «молчание».

Стиль. Русский, кратко и по делу, как в дашборде. На каждый блок — 2–4 предложения. \
Называй конкретных сорсеров и цифры. Размечай HTML-инлайн: <b>…</b> для чисел/имён, \
<span class="flag">…</span> для проблем/рисков, <span class="good">…</span> для позитива. \
Без заголовков, без <script>, без markdown — только инлайн-HTML текста."""

SCHEMA_HINT = ("Верни объект с тремя полями-строками (готовый HTML-текст без обёрток): "
               "load — про нагрузку и динамику; conversion — про конверсию/скорость/молчание; "
               "coverage — про покрытие сделок и глубину обзвона (запросов на сделку).")


# --------------------------------------------------------------------------- rules
def _dmy(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}"


def _grace_text(m: dict) -> str:
    """Пока идёт льготный срок правил КП — когда простой и файл без стадии станут
    нарушениями. После срока — пусто."""
    g = (m.get("kpRules") or {}).get("graceUntil")
    end = (m.get("period") or {}).get("end")
    if not g or not end or end > g:
        return ""
    с = (dt.date.fromisoformat(g) + dt.timedelta(days=1)).isoformat()
    return f"Price at Work без итога и файл без перевода стадии станут нарушениями с {_dmy(с)}."


def _top(sourcers, key, n=1):
    return sorted(sourcers, key=lambda s: s.get(key, 0), reverse=True)[:n]


def rule_based(m: dict) -> dict[str, str]:
    k = m["kpi"]
    src = m["sourcersA"]
    out = {"load": "", "conversion": "", "coverage": ""}

    if src:
        # лидер — по темпу среди действующих, а не по итогу с начала сорсинга:
        # итог растёт со стажем, и новичок при любой работе стоял бы последним
        act = [s for s in src if s.get("act", True)] or src
        lead = act[0]
        # темпа нет (первый день окна) — «—», а не 0
        темп = (f"{lead['perDay4']} запр. в раб. день за 4 недели" if lead.get("perDay4") is not None
                else f"{lead['perDay'] if lead.get('perDay') is not None else '—'} запр. в раб. день")
        parts = [
            f"За период — <b>{k['total']}</b> запросов от <b>{k['respCount']}</b> ответственных; "
            f"отдел Гринёва — <b>{k['deptA']}</b>, вне отдела — <b>{k['outside']}</b>"
            + (f" (из них без исполнителя {k['unresolved']}: сорсер в сделке не указан или не определён)"
               if k.get("unresolved") else "") + ". "
            f"Лидер по темпу — <b>{lead['n']}</b> ({темп}). "
            f"Ещё в работе <b>{k['inWorkPct']}%</b> ({k['openCount']})."
        ]
        новички = [s["n"] for s in act if s.get("short")]
        if новички:
            parts.append(f"Меньше четырёх рабочих недель в окне: {', '.join(новички[:3])} — темп ещё неустойчив.")
        if k.get("staffGone"):
            parts.append(f"<span class=\"flag\">Ушли из отдела {k['staffGone']} чел.: открытых карточек за ними "
                         f"{k.get('goneOpen', 0)}</span> — переназначить действующим.")
        if k.get("goneAfter"):
            parts.append(f"<span class=\"flag\">{k['goneAfter']} карточек легли на ушедших уже после ухода</span> — "
                         f"в сделках не сменён «Сорсер».")
        out["load"] = " ".join(parts)
        # конверсия: худший по «молчанию», лучший по КП
        with_closed = [s for s in src if s["closed"] >= 5]
        worst_nr = _top(with_closed, "noAnswer", 1)
        best_kp = sorted([s for s in src if s["c"] >= 20], key=lambda s: s["quotes"] / s["c"], reverse=True)[:1]
        # КП засчитывается только файлом в карточке; до правил 19.10 файл не был
        # обязателен — оговорка идёт вместе с числом
        parts = [f"КП засчитано (файл КП в карточке) — <b>{k.get('kpCountA', 0)}</b>, это <b>{k.get('kpPctA', 0)}%</b> "
                 f"запросов отдела (до 19.10.2026 файл не был обязателен — КП без файла не учтены, за период доля может быть ниже числа полученных КП); "
                 f"из закрытых с КП — <b>{k['kpPctOfClosedA']}%</b> (по {k['closedCountA']} закрытым)."]
        if best_kp:
            s = best_kp[0]
            parts.append(f"<span class=\"good\">Больше всего КП файлом на запрос — {s['n']}</span> ({s['quotes']}/{s['c']}).")
        kr = m.get("kpRules") or {}
        if kr.get("cards"):
            t = kr.get("total") or {}
            parts.append(f"<span class=\"flag\">Правила отметки КП с 19.10 нарушены в {kr['cards']} карточках</span>: "
                         f"Price at Work без итога дольше {kr.get('staleDays', 14)} дн. — {t.get('pawStale', 0)}, "
                         f"стадия КП без файла — {t.get('noFile', 0)}, файл без перевода стадии — {t.get('fileNoStage', 0)}.")
        if kr.get("preCards"):
            parts.append(f"Ещё {kr['preCards']} карточек — «до правил» (наследие до 19.10 и льготный срок): "
                         f"на доразметку, а не нарушение.")
        льгота = _grace_text(m)
        if льгота:
            parts.append(льгота)
        if worst_nr and worst_nr[0]["noAnswer"] > 0:
            s = worst_nr[0]
            parts.append(f"<span class=\"flag\">Больше всего «молчания» у {s['n']}</span> — {s['noAnswer']} без ответа в срок.")
        out["conversion"] = " ".join(parts)

    cov = m["coverage"]
    deep1 = next((h["v"] for h in cov["hist"] if h["l"] == "1 запрос"), 0)
    low_cats = [c for c in cov["byCat"] if c["deals"] >= 5 and c["covPct"] < 60]
    cparts = [
        f"Покрытие сделок с суммой — <b>{cov['covPct']}%</b> ({cov['withReq']} из {cov['deals']}); "
        f"<span class=\"flag\">{cov['withoutReq']} сделок без единого запроса</span>. "
        f"Средняя глубина — <b>{cov['avgSuppliers']}</b> запроса (карточки) на сделку."
    ]
    if deep1:
        cparts.append(f"<span class=\"flag\">{deep1} сделок — всего один запрос поставщику</span>: слабая ценовая конкуренция.")
    if low_cats:
        names = ", ".join(f"{c['cat']} ({c['covPct']}%)" for c in low_cats[:3])
        cparts.append(f"Низкое покрытие: {names}.")
    # Долю «дошло до ТКП» текст не называет: правило по текущей стадии сделки не
    # видит стадий пресейла, на странице доля скрыта до пересчёта по истории стадий.
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

    # доля ТКП скрыта на странице — модели её не передаём, чтобы не попала в текст
    compact = {
        "period": m["period"],
        "kpi": {kk: v for kk, v in m["kpi"].items() if kk != "tkpPct"},
        "weekly": m["weekly"],
        "sourcersA": [
            {kk: s.get(kk) for kk in ("n", "act", "from", "to", "wd", "c", "perDay", "perDay4", "wk",
                                      "closed", "kp", "quotes", "refusedCol", "noAnswer", "avgDays")}
            for s in m["sourcersA"]
        ],
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
