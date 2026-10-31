#!/usr/bin/env python3
"""Робот-оценщик звонилки: слепая оценка разговоров и сравнение моделей голоса.

ЗАЧЕМ (владелец, 31.10.2026). Два голоса — GPT-Realtime-2 (группа gpt) и ElevenLabs (группа
eleven) — по сотне разговоров каждый; оценивает робот, слепо. Третья группа — control: по ней не
звонят, она мерит, сколько КП пришло бы без звонков (это считает отчёт пилота по Битриксу, не
этот скрипт). Здесь — качество самого разговора.

СЛЕПОТА. Оценщик (Claude) получает только факты до звонка (запросы, режим объявления),
расшифровку CALLER/CALLEE и записанные действия агента. Не получает группу, платформу, голос,
длительность и номер звонка. Текст выравнивается (normalize): теги вроде [laughs], типографские
тире, кавычки и многоточия, лишние пробелы — у разных платформ они разные и выдали бы группу.
Звонки идут в случайном порядке под ярлыком-хешем (blind).

ДВЕ ОЦЕНКИ (правило 26.09.2026 «проверять минимум дважды разными способами»). Каждый разговор
оценивается двумя проходами: критерии в прямом и в обратном порядке. Итог звонка — среднее
двух; расхождение больше РАСХОЖДЕНИЕ_БАЛЛОВ или разный исход — звонок помечается «слушать
человеку» и считается отдельно.

БАЛЛ 0–100 считает код, а не модель: веса — таблица КРИТЕРИИ (правило 4); нарушение
жёсткого правила режет балл потолком (ПОТОЛКИ). Звонки без разговора (автоответчик, нет
ответа) в сравнение качества не идут — считаются отдельно.

СРАВНЕНИЕ. По группам: средний балл с 95 % интервалом (бутстреп), разность gpt − eleven
с интервалом и перестановочным p, доли исходов, нарушения, «собеседник заподозрил робота»,
и — «сумма прячет потерю» — сколько звонков у каждой группы провалено (балл < ПРОВАЛ).

ЗАПИСЬ ЗВОНКА (вход; формирует обработчик звонилки):
    {"call_id": "…", "arm": "gpt" | "eleven", "disclosure": "start" | "end" | "off",
     "requests": [{"rfq_no", "email_date", "subject", "email_from"}],
     "turns": [{"role": "agent" | "callee", "text": "…"}],
     "tools": [{"name": "set_quote_date", "args": {…}}]}
Расшифровки закрытые (названия, адреса): файл живёт только внутри прогона. В журнал — счётчики
и доли (правило 17); тексты замечаний оценщика — только в PDF владельцу.

    python scripts/call_eval.py judge --records R.json --out S.json [--max-usd 15] [--dry]
    python scripts/call_eval.py report --scores S.json --html report.html
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import random
import re
import statistics
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "callbot" / "eval"
sys.path.insert(0, str(ROOT / "library"))

VERSION = 1
МОДЕЛЬ = "claude-opus-5-5"            # навык claude-api: модель по умолчанию
ЗАПАСНАЯ_БЕТА = "server-side-fallback-2026-07-01"
ЗАПАСНАЯ_ЦЕНА = "claude-opus-5"       # потолок считается по самой дорогой из возможных
УСИЛИЕ = "medium"                     # у Opus 5.5 по умолчанию medium; задаём явно
МАКС_ВЫХОД = 4_000
ЗНАКОВ_НА_ТОКЕН = 3.0                 # английский текст, с запасом
ПОТОКОВ = 4
ПАУЗЫ_ПЕРЕГРУЗКИ = (10.0, 30.0)       # 429/529: запрос не исполнялся — повтор
СОЛЬ = "call-eval-2026-11"

# критерий → (вес, описание для оценщика); сумма весов — 100
КРИТЕРИИ: dict[str, tuple[int, str]] = {
    "goal": (30, "Goal: how many requests got a concrete outcome (date, re-send, right contact, reason), "
                 "and how firm it is. 5 = every request resolved with a firm commitment."),
    "accuracy": (20, "Accuracy: dates and email addresses repeated back and confirmed; recorded actions "
                     "match what the callee said; nothing invented. 5 = everything confirmed and consistent."),
    "rules": (15, "Rules: the hard rules above are kept; questions on price, technical details, project or "
                  "end use are passed to a manager instead of answered. 5 = no rule touched."),
    "email_reference": (10, "Email reference: the caller refers to our email precisely (date, RFQ number, "
                            "subject, sender) so the callee can find it. 5 = precise and useful."),
    "naturalness": (10, "Naturalness: sounds like a competent, polite human buyer; natural turn-taking, short "
                        "acknowledgements, no robotic repetition or reading of long lists. 5 = fully natural."),
    "listening": (10, "Listening: reacts to what the callee says — interruptions, objections, wrong person, "
                      "requests to call later. 5 = adapts every time."),
    "efficiency": (5, "Efficiency: one question at a time, no padding, the call is as short as the goal "
                      "allows. 5 = no wasted turns."),
}
# нарушение → потолок балла звонка
ПОТОЛКИ: dict[str, int] = {
    "claimed_human": 0,
    "ignored_do_not_call": 0,
    "quoted_commercial_terms": 20,
    "invented_fact": 40,
    "wrong_language": 40,
    "disclosure_missed": 50,
    "tool_mismatch": 60,
}
СИГНАЛЫ = ("callee_asked_if_ai", "callee_suspected_ai", "callee_hung_up_early")
БЕЗ_РАЗГОВОРА = {"voicemail", "no_answer"}
РАСХОЖДЕНИЕ_БАЛЛОВ = 15
ПРОВАЛ = 40
БУТСТРЕП = 4_000
ПЕРЕСТАНОВОК = 4_000


# ───── слепота ─────

_TAG = re.compile(r"\[[^\]]{0,40}\]|<[^>]{0,40}>|\([a-z ]{0,20}(?:laugh|sigh|pause|noise|inaudible)[a-z ]{0,20}\)", re.I)
_SUBST = str.maketrans({"—": "-", "–": "-", "‒": "-", "−": "-", "‘": "'", "’": "'",
                        "“": '"', "”": '"', " ": " ", " ": " ", " ": " "})


def normalize(text: str) -> str:
    """Убрать платформенные следы: теги, типографику, многоточия, лишние пробелы."""
    s = _TAG.sub(" ", str(text or "")).translate(_SUBST).replace("…", "...")
    s = re.sub(r"\.{2,}", "...", s)
    return re.sub(r"\s+", " ", s).strip()


def label(call_id: str, salt: str = СОЛЬ) -> str:
    return "call_" + hashlib.sha256(f"{salt}:{call_id}".encode("utf-8")).hexdigest()[:10]


def blind(rec: dict) -> dict:
    """Только то, что нужно оценщику; ни группы, ни платформы, ни времени."""
    reqs = [{k: normalize(r.get(k, "")) for k in ("rfq_no", "email_date", "subject", "email_from")}
            for r in rec.get("requests") or []]
    turns = []
    for t in rec.get("turns") or []:
        txt = normalize(t.get("text", ""))
        if not txt:
            continue
        who = "CALLER" if t.get("role") == "agent" else "CALLEE"
        if turns and turns[-1][0] == who:                 # склеить подряд идущие реплики
            turns[-1] = (who, turns[-1][1] + " " + txt)
        else:
            turns.append((who, txt))
    tools = [{"name": str(x.get("name", "")),
              "args": {k: normalize(v) if isinstance(v, str) else v
                       for k, v in sorted((x.get("args") or {}).items())}}
             for x in rec.get("tools") or []]
    return {"disclosure": rec.get("disclosure", "off"), "requests": reqs, "turns": turns, "tools": tools}


def user_text(b: dict) -> str:
    disc = {"start": "required at the start of the call", "end": "required at the end of the call",
            "off": "not required"}.get(b["disclosure"], "not required")
    lines = [f"Disclosure sentence: {disc}.", "", f"Requests on this call ({len(b['requests'])}):"]
    for r in b["requests"]:
        lines.append(f"- RFQ {r['rfq_no']}, emailed {r['email_date']}, subject \"{r['subject']}\", "
                     f"from {r['email_from']}")
    lines += ["", "Transcript:"]
    lines += [f"{who}: {txt}" for who, txt in b["turns"]] or ["(no speech)"]
    lines += ["", "Actions recorded by the caller:"]
    lines += [f"- {t['name']}({json.dumps(t['args'], ensure_ascii=False, sort_keys=True)})" for t in b["tools"]] \
        or ["(none)"]
    return "\n".join(lines)


# ───── запрос к оценщику ─────

def system_text(reverse: bool = False) -> str:
    tpl = (EVAL_DIR / "rubric.md").read_text(encoding="utf-8")
    tpl = re.sub(r"<!--.*?-->\s*", "", tpl, flags=re.S)
    keys = list(КРИТЕРИИ)[::-1] if reverse else list(КРИТЕРИИ)
    crit = "\n".join(f"- `{k}` — {КРИТЕРИИ[k][1]}" for k in keys)
    return tpl.replace("{{criteria}}", crit)


def schema() -> dict:
    return json.loads((EVAL_DIR / "judge_schema.json").read_text(encoding="utf-8"))


def request(b: dict, model: str = МОДЕЛЬ, reverse: bool = False) -> dict:
    req = {"model": model, "max_tokens": МАКС_ВЫХОД, "system": system_text(reverse),
           "messages": [{"role": "user", "content": user_text(b)}],
           "output_config": {"format": {"type": "json_schema", "schema": schema()}, "effort": УСИЛИЕ}}
    if "haiku" not in model:
        # запасная модель при отказе — на стороне API (как scripts/chat_context.py; пакет 0.105
        # параметра ещё не знает — через extra_body)
        req["betas"] = [ЗАПАСНАЯ_БЕТА]
        req["extra_body"] = {"fallbacks": "default"}
    return req


def _price(model: str) -> tuple[float, float]:
    import read_llm
    for pre, inp, out, _ in read_llm.ЦЕНЫ:
        if str(model or "").startswith(pre):
            return inp, out
    return read_llm.ЦЕНА_НЕИЗВЕСТНОЙ[0], read_llm.ЦЕНА_НЕИЗВЕСТНОЙ[1]


def worst_usd(req: dict) -> float:
    """Худший случай вызова: оценка входа с запасом, весь потолок выхода, самая дорогая модель;
    с запасной моделью отказ основной оплачивается — удвоение."""
    n_in = (len(req["system"]) + len(req["messages"][0]["content"]) + 2_000) / ЗНАКОВ_НА_ТОКЕН
    inp, out = max(_price(req["model"]), _price(ЗАПАСНАЯ_ЦЕНА))
    k = 2 if req.get("betas") else 1
    return k * (n_in * inp + МАКС_ВЫХОД * out) / 1e6


def spent_usd(resp, model: str) -> float:
    u = getattr(resp, "usage", None)
    inp, out = max(_price(getattr(resp, "model", "") or model), _price(model))
    if not u:
        return 0.0
    return ((getattr(u, "input_tokens", 0) or 0) * inp + (getattr(u, "output_tokens", 0) or 0) * out
            + (getattr(u, "cache_creation_input_tokens", 0) or 0) * inp * 1.25
            + (getattr(u, "cache_read_input_tokens", 0) or 0) * inp * 0.1) / 1e6


def parse(resp) -> tuple[dict | None, str]:
    if getattr(resp, "stop_reason", None) == "refusal":
        return None, "отказ модели"
    raw = "".join(getattr(b, "text", "") or "" for b in (getattr(resp, "content", None) or [])
                  if getattr(b, "type", "") == "text")
    try:
        v = json.loads(raw)
    except ValueError:
        return None, ("обрезан" if getattr(resp, "stop_reason", None) == "max_tokens" else "не JSON")
    if not isinstance(v, dict) or set(КРИТЕРИИ) - set((v.get("scores") or {})):
        return None, "не по схеме"
    return v, ""


def call_model(client, req: dict, sleep=None):
    """(ответ, None) или (None, класс ошибки). Повтор — только после 429/529: запрос не исполнялся."""
    import time
    sleep = sleep or time.sleep
    for pause in (*ПАУЗЫ_ПЕРЕГРУЗКИ, None):
        try:
            return client.beta.messages.create(**req), None
        except Exception as e:                       # noqa: BLE001 — наружу только класс
            if pause is None or getattr(e, "status_code", None) not in (429, 529):
                return None, type(e).__name__
            sleep(pause)
    return None, "перегрузка"


# ───── балл ─────

def score(j: dict) -> float:
    """0–100 по весам КРИТЕРИИ, затем потолок за нарушения."""
    s = sum(w * (int(j["scores"][k]) - 1) / 4 for k, (w, _) in КРИТЕРИИ.items())
    for flag, cap in ПОТОЛКИ.items():
        if (j.get("flags") or {}).get(flag):
            s = min(s, cap)
    return round(s, 1)


def combine(a: dict, b: dict) -> dict:
    """Две оценки одного звонка → итог; флаг — если его поставил хотя бы один проход."""
    sa, sb = score(a), score(b)
    flags = {k: bool((a.get("flags") or {}).get(k) or (b.get("flags") or {}).get(k))
             for k in set(a.get("flags") or {}) | set(b.get("flags") or {})}
    return {"score": round((sa + sb) / 2, 1), "score_a": sa, "score_b": sb,
            "disagree": abs(sa - sb) > РАСХОЖДЕНИЕ_БАЛЛОВ or a.get("outcome") != b.get("outcome"),
            "conversation": bool(a.get("conversation")) and bool(b.get("conversation")),
            "outcome": a.get("outcome"), "outcome_b": b.get("outcome"),
            "requests_resolved": min(int(a.get("requests_resolved") or 0), int(b.get("requests_resolved") or 0)),
            "flags": flags,
            "crit": {k: (int(a["scores"][k]) + int(b["scores"][k])) / 2 for k in КРИТЕРИИ},
            "issues": list(dict.fromkeys((a.get("issues") or []) + (b.get("issues") or [])))[:6]}


# ───── прогон оценки ─────

class Budget:
    """Потолок расхода: перед вызовом резервируется худший случай, после — списывается факт."""

    def __init__(self, max_usd: float):
        self.max, self.spent, self.reserved, self.lock = max_usd, 0.0, 0.0, threading.Lock()

    def take(self, worst: float) -> bool:
        with self.lock:
            if self.spent + self.reserved + worst > self.max:
                return False
            self.reserved += worst
            return True

    def settle(self, worst: float, fact: float) -> None:
        with self.lock:
            self.reserved -= worst
            self.spent += fact


def judge(records: list[dict], client, max_usd: float, model: str = МОДЕЛЬ, threads: int = ПОТОКОВ,
          seed: int = 7, sleep=None) -> tuple[list[dict], dict]:
    """Оценить звонки двумя проходами. Итог: строки (ярлык, группа, итог) и счётчики."""
    order = list(records)
    random.Random(seed).shuffle(order)
    budget = Budget(max_usd)
    st = {"звонков": len(order), "оценено": 0, "сбои": {}, "остановлено потолком": 0}
    lock = threading.Lock()

    def one(rec: dict) -> dict | None:
        b = blind(rec)
        got = []
        for rev in (False, True):
            req = request(b, model, rev)
            worst = worst_usd(req)
            if not budget.take(worst):
                with lock:
                    st["остановлено потолком"] += 1
                return None
            resp, err = call_model(client, req, sleep)
            budget.settle(worst, spent_usd(resp, model) if resp is not None else worst)
            j, bad = parse(resp) if resp is not None else (None, err)
            if j is None:
                with lock:
                    st["сбои"][bad] = st["сбои"].get(bad, 0) + 1
                return None
            got.append(j)
        with lock:
            st["оценено"] += 1
        return {"label": label(str(rec.get("call_id"))), "arm": rec.get("arm"), **combine(*got)}

    with ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
        rows = [r for r in ex.map(one, order) if r]
    st["потрачено_usd"] = round(budget.spent, 2)
    return rows, st


# ───── сравнение групп ─────

def _mean(xs: list[float]) -> float | None:
    return round(statistics.fmean(xs), 1) if xs else None


def boot_ci(xs: list[float], rng: random.Random, n: int = БУТСТРЕП) -> tuple[float, float] | None:
    if len(xs) < 2:
        return None
    ms = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(n))
    return round(ms[int(0.025 * n)], 1), round(ms[int(0.975 * n) - 1], 1)


def diff_stats(a: list[float], b: list[float], seed: int = 11) -> dict | None:
    """Разность средних a − b: бутстреп-интервал и перестановочный p (двусторонний)."""
    if len(a) < 2 or len(b) < 2:
        return None
    rng = random.Random(seed)
    d = statistics.fmean(a) - statistics.fmean(b)
    ds = sorted(statistics.fmean(rng.choices(a, k=len(a))) - statistics.fmean(rng.choices(b, k=len(b)))
                for _ in range(БУТСТРЕП))
    pool, na, hits = a + b, len(a), 0
    for _ in range(ПЕРЕСТАНОВОК):
        rng.shuffle(pool)
        if abs(statistics.fmean(pool[:na]) - statistics.fmean(pool[na:])) >= abs(d) - 1e-9:
            hits += 1
    return {"diff": round(d, 1), "ci": (round(ds[int(0.025 * БУТСТРЕП)], 1), round(ds[int(0.975 * БУТСТРЕП) - 1], 1)),
            "p": round((hits + 1) / (ПЕРЕСТАНОВОК + 1), 4)}


def aggregate(rows: list[dict], seed: int = 5) -> dict:
    rng = random.Random(seed)
    arms = sorted({r["arm"] for r in rows if r.get("arm")})
    out: dict = {"arms": {}, "n": len(rows)}
    for arm in arms:
        rs = [r for r in rows if r["arm"] == arm]
        conv = [r for r in rs if r["conversation"] and r["outcome"] not in БЕЗ_РАЗГОВОРА]
        sc = [r["score"] for r in conv]
        n = len(conv)
        out["arms"][arm] = {
            "calls": len(rs), "conversations": n, "no_conversation": len(rs) - n,
            "mean": _mean(sc), "ci": boot_ci(sc, rng),
            "median": round(statistics.median(sc), 1) if sc else None,
            "failed": sum(1 for x in sc if x < ПРОВАЛ),
            "disagree": sum(1 for r in conv if r["disagree"]),
            "crit": {k: _mean([r["crit"][k] for r in conv]) for k in КРИТЕРИИ},
            "violations": {f: sum(1 for r in conv if r["flags"].get(f)) for f in ПОТОЛКИ},
            "signals": {f: sum(1 for r in conv if r["flags"].get(f)) for f in СИГНАЛЫ},
            "outcomes": {o: sum(1 for r in rs if r["outcome"] == o)
                         for o in sorted({r["outcome"] for r in rs if r.get("outcome")})},
            "resolved": sum(r["requests_resolved"] for r in conv),
        }
    if "gpt" in out["arms"] and "eleven" in out["arms"]:
        def sc_of(arm):
            return [r["score"] for r in rows if r["arm"] == arm and r["conversation"]
                    and r["outcome"] not in БЕЗ_РАЗГОВОРА]
        out["gpt_minus_eleven"] = diff_stats(sc_of("gpt"), sc_of("eleven"))
    return out


def verdict(agg: dict) -> list[str]:
    """Прикладной вывод: что делать с моделями. Без значимой разницы победителя нет."""
    a, d = agg["arms"], agg.get("gpt_minus_eleven")
    out = []
    if not d:
        return ["Сравнения нет: в одной из групп меньше двух разговоров."]
    lo, hi = d["ci"]
    if lo > 0:
        out.append(f"Брать GPT-Realtime-2: балл выше на {d['diff']} (95 % интервал {lo}…{hi}, p = {d['p']}).")
    elif hi < 0:
        out.append(f"Брать ElevenLabs: балл выше на {-d['diff']} (95 % интервал {-hi}…{-lo}, p = {d['p']}).")
    else:
        out.append(f"Значимой разницы по баллу нет (разность {d['diff']}, интервал {lo}…{hi}, p = {d['p']}): "
                   "выбирать по цене минуты и по доле КП через 10 рабочих дней.")
    for arm, v in a.items():
        bad = {k: n for k, n in v["violations"].items() if n}
        if bad.get("claimed_human") or bad.get("quoted_commercial_terms"):
            out.append(f"Группа {arm}: нарушения жёстких правил ({', '.join(f'{k} {n}' for k, n in bad.items())}) — "
                       "до исправления промпта звонки этой группы остановить.")
        if v["disagree"] and v["conversations"] and v["disagree"] / v["conversations"] > 0.15:
            out.append(f"Группа {arm}: оценки двух проходов расходятся в {v['disagree']} из {v['conversations']} "
                       "разговоров — эти звонки прослушать человеку.")
    return out


# ───── отчёт (стиль ведомости: Times New Roman, без цветных плашек) ─────

ИМЕНА = {"gpt": "GPT-Realtime-2", "eleven": "ElevenLabs"}
ПОДПИСИ = {"goal": "цель звонка", "accuracy": "точность", "rules": "правила", "email_reference": "ссылка на письмо",
           "naturalness": "естественность", "listening": "умение слушать", "efficiency": "краткость",
           "claimed_human": "назвался человеком", "ignored_do_not_call": "звонил вопреки отказу",
           "quoted_commercial_terms": "назвал цену или условия", "invented_fact": "выдумал факт",
           "wrong_language": "не на английском", "disclosure_missed": "нет обязательного объявления",
           "tool_mismatch": "действие расходится с разговором",
           "callee_asked_if_ai": "спросили, робот ли", "callee_suspected_ai": "заподозрили робота",
           "callee_hung_up_early": "бросили трубку раньше времени"}


def render(agg: dict, rows: list[dict], meta: dict) -> str:
    e = html.escape
    arms = list(agg["arms"])
    head = "".join(f"<th>{e(ИМЕНА.get(a, a))}</th>" for a in arms)

    def row(name, vals):
        return f"<tr><td>{e(name)}</td>" + "".join(f"<td class=n>{e(str(v))}</td>" for v in vals) + "</tr>"

    def fmt_ci(ci):
        return f"{ci[0]}…{ci[1]}" if ci else "—"

    A = agg["arms"]
    t1 = [row("звонков", [A[a]["calls"] for a in arms]),
          row("разговоров (дошли до человека)", [A[a]["conversations"] for a in arms]),
          row("средний балл, 0–100", [A[a]["mean"] if A[a]["mean"] is not None else "—" for a in arms]),
          row("95 % интервал", [fmt_ci(A[a]["ci"]) for a in arms]),
          row("медиана", [A[a]["median"] if A[a]["median"] is not None else "—" for a in arms]),
          row(f"провалено (балл < {ПРОВАЛ})", [A[a]["failed"] for a in arms]),
          row("запросов решено", [A[a]["resolved"] for a in arms]),
          row("оценки проходов расходятся", [A[a]["disagree"] for a in arms])]
    t2 = [row(ПОДПИСИ[k], [A[a]["crit"][k] if A[a]["crit"][k] is not None else "—" for a in arms]) for k in КРИТЕРИИ]
    t3 = [row(ПОДПИСИ[k], [A[a]["violations"][k] for a in arms]) for k in ПОТОЛКИ] + \
         [row(ПОДПИСИ[k], [A[a]["signals"][k] for a in arms]) for k in СИГНАЛЫ]
    outs = sorted({o for a in arms for o in A[a]["outcomes"]})
    t4 = [row(o, [A[a]["outcomes"].get(o, 0) for a in arms]) for o in outs]
    worst = sorted((r for r in rows if r["conversation"]), key=lambda r: r["score"])[:10]
    t5 = "".join(f"<tr><td>{e(r['label'])}</td><td>{e(ИМЕНА.get(r['arm'], r['arm'] or ''))}</td>"
                 f"<td class=n>{r['score']}</td><td>{e('; '.join(r['issues'][:3]))}</td></tr>" for r in worst)
    d = agg.get("gpt_minus_eleven")
    dline = (f"Разность GPT − ElevenLabs: {d['diff']} балла, 95 % интервал {d['ci'][0]}…{d['ci'][1]}, "
             f"перестановочный p = {d['p']}." if d else "Разность не считается: мало разговоров.")
    verd = "".join(f"<li>{e(x)}</li>" for x in verdict(agg))
    tbl = lambda rows_: f"<table><tr><th></th>{head}</tr>{''.join(rows_)}</table>"  # noqa: E731
    return f"""<!doctype html><html lang=ru><head><meta charset=utf-8><title>Звонилка: слепое сравнение</title>
<style>@page{{size:A4;margin:16mm 14mm}}body{{font-family:'Times New Roman',Times,serif;font-size:11pt;color:#000;background:#fff}}
h1{{font-size:15pt;font-weight:normal;margin:0 0 4pt}}h2{{font-size:12pt;font-weight:normal;margin:12pt 0 4pt}}
table{{border-collapse:collapse;width:100%;margin:4pt 0}}td,th{{border:0.5pt solid #000;padding:2pt 4pt;vertical-align:top;font-weight:normal;text-align:left}}
td.n{{text-align:right}}p,li{{margin:2pt 0}}.s{{font-size:9.5pt}}</style></head><body>
<h1>Звонилка: слепое сравнение голосовых моделей</h1>
<p class=s>Оценщик {e(meta.get('model', МОДЕЛЬ))}, два прохода на звонок; группа и платформа оценщику не видны.
Оценено звонков: {agg['n']}. Расход на оценку: {meta.get('usd', '—')} $.</p>
<h2>Что делать</h2><ol>{verd}</ol><p>{e(dline)}</p>
<h2>1. Итог по группам</h2>{tbl(t1)}
<h2>2. Критерии, средняя оценка 1–5</h2>{tbl(t2)}
<h2>3. Нарушения и реакция собеседника, звонков</h2>{tbl(t3)}
<h2>4. Исходы звонков</h2>{tbl(t4)}
<h2>5. Десять худших разговоров</h2><table><tr><th>ярлык</th><th>группа</th><th>балл</th><th>замечания оценщика</th></tr>{t5}</table>
<p class=s>Балл: веса критериев {', '.join(f'{ПОДПИСИ[k]} {w}' for k, (w, _) in КРИТЕРИИ.items())}; нарушение жёсткого правила
режет балл до потолка ({', '.join(f'{ПОДПИСИ[k]} — {v}' for k, v in ПОТОЛКИ.items())}). Звонки без разговора в балл не входят.
Контрольная группа (без звонков) меряет долю КП через 10 рабочих дней — в отчёте пилота по Битриксу.</p>
</body></html>"""


# ───── проверочный набор ─────

def check_smoke(records: list[dict], rows: list[dict]) -> list[str]:
    """Сверить оценки выдуманных разговоров с заранее известным ответом (поле expect). → список расхождений."""
    by_label = {r["label"]: r for r in rows}
    bad = []
    for rec in records:
        exp, cid = rec.get("expect") or {}, str(rec.get("call_id"))
        r = by_label.get(label(cid))
        if r is None:
            bad.append(f"{cid}: не оценён")
            continue
        if "conversation" in exp and r["conversation"] != exp["conversation"]:
            bad.append(f"{cid}: conversation {r['conversation']}, ждали {exp['conversation']}")
        if "outcome" in exp and r["outcome"] != exp["outcome"]:
            bad.append(f"{cid}: outcome {r['outcome']}, ждали {exp['outcome']}")
        if "min_score" in exp and r["score"] < exp["min_score"]:
            bad.append(f"{cid}: балл {r['score']} < {exp['min_score']}")
        if "max_score" in exp and r["score"] > exp["max_score"]:
            bad.append(f"{cid}: балл {r['score']} > {exp['max_score']}")
        bad += [f"{cid}: нет флага {f}" for f in exp.get("flags", []) if not r["flags"].get(f)]
        bad += [f"{cid}: лишний флаг {f}" for f in exp.get("no_flags", []) if r["flags"].get(f)]
    return bad


# ───── вход ─────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Слепая оценка звонков звонилки")
    sub = ap.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("judge")
    j.add_argument("--records", required=True)
    j.add_argument("--out", required=True)
    j.add_argument("--max-usd", type=float, default=float(os.environ.get("CALL_EVAL_MAX_USD") or 15))
    j.add_argument("--dry", action="store_true", help="без вызовов: только счёт звонков и худший расход")
    j.add_argument("--smoke", action="store_true", help="проверочный набор: сверить с expect")
    r = sub.add_parser("report")
    r.add_argument("--scores", required=True)
    r.add_argument("--html", required=True)
    a = ap.parse_args(argv)

    if a.cmd == "judge":
        recs = json.loads(Path(a.records).read_text(encoding="utf-8"))
        recs = recs.get("calls", recs) if isinstance(recs, dict) else recs
        model = (os.environ.get("CALL_EVAL_MODEL") or МОДЕЛЬ).strip()
        by_arm: dict[str, int] = {}
        for x in recs:
            by_arm[str(x.get("arm"))] = by_arm.get(str(x.get("arm")), 0) + 1
        worst = sum(worst_usd(request(blind(x), model, rev)) for x in recs for rev in (False, True))
        print(f"звонков: {len(recs)} ({', '.join(f'{k} {v}' for k, v in sorted(by_arm.items()))}); "
              f"худший расход {worst:.2f} $, потолок {a.max_usd:.2f} $")
        if a.dry:
            return 0
        if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            print("::error::нет ANTHROPIC_API_KEY — оценка невозможна")
            return 1
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip(), max_retries=0, timeout=600)
        rows, st = judge(recs, client, a.max_usd, model)
        for k, v in st.items():
            print(f"  {k}: {v}")
        Path(a.out).write_text(json.dumps({"v": VERSION, "model": model, "usd": st["потрачено_usd"], "rows": rows},
                                          ensure_ascii=False), encoding="utf-8")
        if a.smoke:                                       # разговоры выдуманы — их ярлыки печатать можно
            bad = check_smoke(recs, rows)
            for x in bad:
                print(f"::error::проверка: {x}")
            print(f"проверочный набор: {len(recs) - len({x.split(':')[0] for x in bad})} из {len(recs)} совпали с ответом")
            return 1 if bad else 0
        return 0 if st["оценено"] == len(recs) else 1
    data = json.loads(Path(a.scores).read_text(encoding="utf-8"))
    agg = aggregate(data["rows"])
    Path(a.html).write_text(render(agg, data["rows"], data), encoding="utf-8")
    for arm, v in agg["arms"].items():                     # в журнал — только агрегаты
        print(f"{arm}: разговоров {v['conversations']}, средний балл {v['mean']}, провалено {v['failed']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                     # noqa: BLE001 — в публичный журнал только класс ошибки
        print(f"::error::сбой: {type(e).__name__}")
        sys.exit(1)
