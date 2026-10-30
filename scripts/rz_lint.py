#!/usr/bin/env python3
"""Текст вкладки «Реализация» против словаря R4 (спецификация R4, 2.7 и приёмка П-5).

Три правила:
  1. Запрещённые слова и имена кода на экране (2.7): «хозяин», «в копии», «гигиена», «р.д.»,
     «Σ», ISO-даты, s1Over/noUtd и прочие имена кода, голые S-коды без «(шаг N)» и т. д.
  2. Сокращения словаря (ОРК, КАМ, ОСС, УПД, PO, OC, СП-172, P80): первое появление на
     подвкладке — словами рядом («отдел реализации контрактов (ОРК)»), дальше — в <abbr>
     с подсказкой (abbr.rzabbr, словарь CT_GLOSS шаблона).
  3. Коды показателей W1–W5, M1–M3, Q1–Q3 — только серым хвостом (.rzcode).

Запуск:
  python scripts/rz_lint.py smoke/index.html            # все подвкладки обоих видов, браузер
  python scripts/rz_lint.py smoke/index.html --scope frame --max 0   # только шапка и строка подвкладок
  python scripts/rz_lint.py smoke/index.html --scope drawer --max 0  # основные экраны панели: список, заказ, сделка
  python scripts/rz_lint.py --text файл.txt             # правило 1 по готовому тексту, без браузера
Код возврата 1 — нарушений больше --max (по умолчанию 0). --json путь — подробности в файл.
Ядро (forbidden, abbr_check, code_check) — чистые функции: их проверяют тесты без браузера.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# (имя правила, регэксп, чем заменить) — спец. 2.7 и ia_copy 8.2
FORBIDDEN: list[tuple[str, str, str]] = [
    ("хозяин", r"(?i)хозя[ие]н", "«кто делает»"),
    ("в копии", r"(?i)в\s+копии", "«в курсе»"),
    ("вида", r"(?i)(?<![А-Яа-яЁё])вида(?![А-Яа-яЁё])", "«в работе (без Залива / Залив)»"),
    ("гигиена", r"(?i)гигиен", "«ошибки в карточках Битрикса»"),
    ("артефакт", r"(?i)артефакт", "«обязательный файл»"),
    ("опора", r"(?i)(?<![А-Яа-яЁё])опор(?:а|ы|у|е|ой)(?![А-Яа-яЁё])", "«проверка другим способом»"),
    ("ворота доверия", r"(?i)ворота\s+доверия", "«пороги доверия»"),
    ("покрытие", r"(?i)покрыти[ея](?!\s*\d)", "«даты есть у N %»"),
    ("зонд", r"(?i)зонд\w*\s*v\d", "«служебная проверка данных» — только «Как считается»"),
    ("Σ", r"Σ", "«итого», «сумма»"),
    ("р.д.", r"(?<![А-Яа-яЁё])р\.\s?д\.", "«раб. дн.»"),
    ("заказ(ов)", r"\((?:ов|а|и)\)", "число и существительное в падеже"),
    ("сдел.", r"(?<![А-Яа-яЁё])сдел\.", "«сделок»"),
    ("Ан. §", r"Ан\.\s?§", "«разд. N аналитики ОРК»"),
    ("Рег.", r"(?<![А-Яа-яЁё])Рег\.\s", "«п. N регламента»"),
    ("ISO-дата", r"(?<!\d)20\d\d-[01]\d-[0-3]\d(?!\d)", "«ДД.ММ.ГГГГ»"),
    ("имя кода", r"\b(?:s[1-9]Over|noUtd|noPayPlan|noPlanDates|prodDev|planAfterDl|payNoContract|dealClose|dealOpen"
                 r"|noOrder|noDl|noFact|chatDue|chatRisk|chatReply|contractRisk|contractMissing|econGap|waitUtd"
                 r"|stepWaitUtd|orkMgr|orkHead|ork\.\w+|lifecycle\.\w+)\b", "слова, а не имя кода"),
    ("S-код без шага", r"(?<![\w-])S(?:10|[1-9])(?![\w])(?!\s*\(шаг)", "«S3 (шаг 5)»"),
]
_FORB = [(n, re.compile(rx), h) for n, rx, h in FORBIDDEN]

# сокращения, которые расшифровываются при первом появлении: ключ → основы слов расшифровки
# (рядом с первым появлением должна стоять хотя бы одна). Сверяется с CT_GLOSS шаблона тестом.
ABBR: dict[str, tuple[str, ...]] = {
    "ОРК": ("отдел реализации контрактов", "отдела реализации контрактов", "руководитель отдела реализации"),
    "КАМ": ("менеджер клиента", "менеджера клиента", "менеджеры клиента"),
    "ОСС": ("сопровождение сделки", "сопровождения сделки"),
    "УПД": ("передаточный документ", "передаточного документа"),
    "PO": ("заказ поставщику", "Purchase order"),
    "OC": ("подтверждение заказа", "Order confirmation"),
    "СП-172": ("Заказ поставщику",),
    "P80": ("обычно не дольше", "8 из 10"),
}
_ABBR_RX = {k: re.compile(r"(?<![\wА-Яа-яЁё-])" + re.escape(k) + r"(?![\wА-Яа-яЁё])") for k in ABBR}
_CODE_RX = re.compile(r"(?<![\w-])(?:W[1-5]|M[1-3]|Q[1-3])(?![\w])")
WINDOW = 90           # знаков вокруг первого появления, где ищем расшифровку


def forbidden(text: str) -> list[dict]:
    """Правило 1: [{rule, match, ctx, hint}] по тексту."""
    out = []
    for name, rx, hint in _FORB:
        for m in rx.finditer(text):
            a, b = max(0, m.start() - 30), min(len(text), m.end() + 30)
            out.append({"rule": name, "match": m.group(0), "ctx": text[a:b].replace("\n", " "), "hint": hint})
    return out


def _joined(segments: list[dict]) -> tuple[str, list[tuple[int, int, dict]]]:
    """Склейка сегментов текста в одну строку и их границы."""
    parts, spans, pos = [], [], 0
    for s in segments:
        t = s.get("t") or ""
        parts.append(t); spans.append((pos, pos + len(t), s)); pos += len(t) + 1
    return "\n".join(parts), spans


def abbr_check(segments: list[dict], abbr: dict[str, tuple[str, ...]] | None = None) -> dict:
    """Правило 2. segments — текстовые узлы подвкладки по порядку: {t, abbr: в abbr.rzabbr}.
    → {first: [сокращения, впервые без расшифровки рядом], later: [сокращения не в <abbr> после
    первого], n_later: число таких мест}."""
    abbr = abbr or ABBR
    text, spans = _joined(segments)
    first, later = [], []
    for k, stems in abbr.items():
        rx = _ABBR_RX.get(k) or re.compile(r"(?<![\wА-Яа-яЁё-])" + re.escape(k) + r"(?![\wА-Яа-яЁё])")
        hits = list(rx.finditer(text))
        if not hits:
            continue
        h0 = hits[0]
        win = text[max(0, h0.start() - WINDOW):h0.end() + WINDOW].casefold()
        if not any(s.casefold() in win for s in stems):
            first.append({"abbr": k, "ctx": text[max(0, h0.start() - 40):h0.end() + 40].replace("\n", " ")})
        for h in hits[1:]:
            seg = next((s for a, b, s in spans if a <= h.start() < b), None)
            if seg is not None and not seg.get("abbr"):
                later.append({"abbr": k, "ctx": text[max(0, h.start() - 30):h.end() + 30].replace("\n", " ")})
    return {"first": first, "later": later, "n_later": len(later)}


def code_check(segments: list[dict]) -> list[dict]:
    """Правило 3: коды W/M/Q вне серого хвоста (.rzcode)."""
    out = []
    for s in segments:
        if s.get("code"):
            continue
        for m in _CODE_RX.finditer(s.get("t") or ""):
            t = s["t"]
            out.append({"code": m.group(0), "ctx": t[max(0, m.start() - 30):m.end() + 30]})
    return out


def lint_segments(segments: list[dict]) -> dict:
    text = "\n".join(s.get("t") or "" for s in segments)
    f = forbidden(text)
    a = abbr_check(segments)
    c = code_check(segments)
    return {"forbidden": f, "abbrFirst": a["first"], "abbrLater": a["later"], "codes": c,
            "total": len(f) + len(a["first"]) + len(a["later"]) + len(c)}


# ---- браузер: текстовые узлы подвкладки в порядке документа ----
COLLECT = r"""(scope)=>{
  const root=document.getElementById('tab-contracts'); if(!root) return [];
  const pick=scope==='frame'?[...root.querySelectorAll(':scope > header, :scope > .ctbar, :scope > .rzalarm, :scope > .rzkamnote')]:[root];
  const out=[];
  const vis=el=>{for(let e=el;e&&e!==document.body;e=e.parentElement){if(e.hidden||e.classList.contains('ctvh')||e.classList.contains('orkvh')) return false;
      if(e.tagName==='DETAILS'&&!e.open&&!el.closest('summary')) return false;}
    return el.getClientRects().length>0;};
  pick.forEach(r=>{const w=document.createTreeWalker(r,NodeFilter.SHOW_TEXT);
    for(let n=w.nextNode();n;n=w.nextNode()){const t=n.nodeValue; if(!t||!t.trim()) continue; const el=n.parentElement;
      if(!el||el.closest('script,style,select')||!vis(el)) continue;
      const gl=!!el.closest('.rzgl-l'); out.push({t:t.replace(/\s+/g,' '),abbr:gl||!!el.closest('abbr.rzabbr,abbr[data-tip]'),code:gl||!!el.closest('.rzcode')});}});
  return out;}"""


# панель (основные экраны П-5: список за числом У3, заказ У4, сделка У4д): текст #drw без шапки-крошек.
# Первое появление сокращения считается на подвкладке, с которой открыта панель, — здесь только
# запрещённые слова, сокращения вне <abbr> и коды вне хвоста
COLLECT_DRW = r"""()=>{
  const root=document.getElementById('drw'); if(!root||!root.classList.contains('on')) return [];
  const out=[];
  const vis=el=>{for(let e=el;e&&e!==document.body;e=e.parentElement){if(e.hidden||e.classList.contains('odvh')||e.classList.contains('op-vh')||e.classList.contains('ctvh')) return false;
      if(e.tagName==='DETAILS'&&!e.open&&!el.closest('summary')) return false;}
    return el.getClientRects().length>0;};
  const w=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
  for(let n=w.nextNode();n;n=w.nextNode()){const t=n.nodeValue; if(!t||!t.trim()) continue; const el=n.parentElement;
    if(!el||el.closest('script,style,select')||!vis(el)) continue;
    out.push({t:t.replace(/\s+/g,' '),abbr:!!el.closest('abbr.rzabbr,abbr[data-tip]'),code:!!el.closest('.rzcode')});}
  return out;}"""


def lint_drawer(html: Path, exe: str | None = None) -> dict:
    from playwright.sync_api import sync_playwright
    url = html.resolve().as_uri()
    res: dict = {}
    with sync_playwright() as p:
        kw = {"executable_path": exe} if exe else {}
        try:
            b = p.chromium.launch(**kw)
        except Exception:                                    # noqa: BLE001 — своя сборка Chromium не скачана
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from scripts.validate_dashboard import find_chrome
            e, _v = find_chrome()
            if not e:
                raise SystemExit("нет браузера для Playwright")
            b = p.chromium.launch(executable_path=e)
        pg = b.new_page(viewport={"width": 1280, "height": 900})

        def one(name):
            pg.wait_for_timeout(250)
            segs = pg.evaluate(COLLECT_DRW)
            r = lint_segments(segs)
            r["total"] -= len(r["abbrFirst"])
            r["abbrFirst"] = []
            res[name] = r
        pg.goto(url + "#contracts/week")
        pg.wait_for_timeout(300)
        pg.click('#tab-contracts [data-kpi-rank="1"]')
        one("панель: список")
        pg.click("#drwBody #odTable [data-odgo]")
        one("панель: заказ")
        pg.goto("about:blank")
        pg.goto(url + "#contracts/main")
        pg.wait_for_timeout(300)
        pg.click("#ctBody tr[data-ct] button.ctd-no")
        one("панель: сделка")
        b.close()
    return res


def lint_page(html: Path, scope: str = "all", exe: str | None = None) -> dict:
    from playwright.sync_api import sync_playwright
    url = html.resolve().as_uri()
    subs = ["week", "month", "quarter", "main", "how"]
    res: dict = {}
    with sync_playwright() as p:
        kw = {"executable_path": exe} if exe else {}
        try:
            b = p.chromium.launch(**kw)
        except Exception:                                    # noqa: BLE001 — своя сборка Chromium не скачана
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from scripts.validate_dashboard import find_chrome
            e, _v = find_chrome()
            if not e:
                raise SystemExit("нет браузера для Playwright")
            b = p.chromium.launch(executable_path=e)
        pg = b.new_page(viewport={"width": 1280, "height": 900})
        for d in ("main", "sep"):
            for s in subs:
                pg.goto("about:blank")
                pg.goto(url + f"#contracts/{s}" + ("/sep" if d == "sep" else ""))
                pg.wait_for_timeout(250)
                if d == "sep" and not pg.query_selector("#ctDirs"):
                    continue
                res[f"{s}{'/sep' if d == 'sep' else ''}"] = lint_segments(pg.evaluate(COLLECT, scope))
        b.close()
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("html", nargs="?")
    ap.add_argument("--text", help="проверить готовый текст (только правило 1)")
    ap.add_argument("--scope", choices=["all", "frame", "drawer"], default="all",
                    help="frame — только шапка, строка подвкладок и тревоги (каркас WP-A)")
    ap.add_argument("--max", type=int, default=0, help="допустимо нарушений (по умолчанию 0)")
    ap.add_argument("--json", help="подробности в файл")
    ap.add_argument("--chrome", help="путь к Chromium")
    a = ap.parse_args(argv)
    if a.text:
        f = forbidden(Path(a.text).read_text(encoding="utf-8"))
        for x in f:
            print(f"  {x['rule']}: «{x['match']}» … {x['ctx']} → {x['hint']}")
        print(f"запрещённых мест: {len(f)}")
        return 1 if len(f) > a.max else 0
    if not a.html:
        ap.error("нужен путь к странице или --text")
    res = lint_drawer(Path(a.html), a.chrome) if a.scope == "drawer" else lint_page(Path(a.html), a.scope, a.chrome)
    total = 0
    for scr, r in res.items():
        total += r["total"]
        print(f"{scr:<14} запрещённых {len(r['forbidden']):>3} · сокращение впервые без слов {len(r['abbrFirst']):>2}"
              f" · сокращение не в <abbr> {len(r['abbrLater']):>3} · код вне хвоста {len(r['codes']):>3}")
        for x in r["forbidden"][:6]:
            print(f"     {x['rule']}: «{x['match']}» … {x['ctx']}")
        for x in r["abbrFirst"]:
            print(f"     впервые без слов: {x['abbr']} … {x['ctx']}")
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"всего нарушений: {total} (допустимо {a.max})")
    return 1 if total > a.max else 0


if __name__ == "__main__":
    sys.exit(main())
