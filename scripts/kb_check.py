#!/usr/bin/env python3
"""Проверка базы знаний docs/kb и docs/agents — до пуша, в preflight и гейте.

ЗАЧЕМ. Репозиторий публичный, а база знаний пишется агентами каждый день. Три
способа её испортить незаметно: сослаться на файл, которого уже нет; вписать
почту, телефон или цену позиции (коммерческое живёт только в Notion); дать
указателю разойтись с каталогом данных. Всё три ловятся механически.

ЧТО ПРОВЕРЯЕТСЯ.
1. У каждой страницы в первых строках — ссылка на свою страницу Notion, и её
   номер есть в реестре NOTION ниже (опечатка в номере = мёртвая ссылка: закрытые
   страницы Notion без входа не открываются, проверить их по сети нельзя).
2. Каждая ссылка на файл репозитория — `путь` в обратных кавычках или
   [текст](путь) — ведёт на существующий файл или папку.
3. Нет почт, телефонов и цен (число рядом с валютой). Исключение — перенесённый
   дословно текст (LESSONS.md, раздел `/suppliers` в DECISIONS.md): он уже
   открыт в истории и несёт суммы-агрегаты, а не цены позиций.
4. Блок каталога в docs/kb/INDEX.md совпадает с data/catalog.json.
5. --lessons: каждая непустая строка CLAUDE.md до сокращения (коммит BASE)
   есть в нынешнем CLAUDE.md или в docs/agents/LESSONS.md — правило не потеряно.

    python scripts/kb_check.py            # проверки 1–4 (и 5, если коммит доступен)
    python scripts/kb_check.py --fix      # пересобрать блок каталога в INDEX.md
    python scripts/kb_check.py --online   # плюс HEAD по внешним http-ссылкам
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "docs" / "kb" / "INDEX.md"
LESSONS = ROOT / "docs" / "agents" / "LESSONS.md"
# CLAUDE.md до сокращения 30.09.2026 — последний коммит main перед ним.
BASE = "05b839a"

# Страницы Notion «КВАНТ — архитектура знаний и агентов» (закрытые).
NOTION = {
    "3eb74b7ffc648117a06ae5d38e32fa84": "корень",
    "3eb74b7ffc6481c7873de8f1424a6a9f": "Архитектор",
    "3eb74b7ffc6481e4a35ed6cb3f9e0f72": "Очередь прогонов",
    "3eb74b7ffc64816d989dd1a79b4fb2ab": "Решения владельца",
    "3eb74b7ffc64810793d2fa41b2f7d296": "Состояние",
    "3eb74b7ffc64811194b2c33215dad591": "Состояние / КП и цены",
    "3eb74b7ffc6481d1bddfc5fb287ab629": "Состояние / Бренды и коды",
    "3eb74b7ffc6481a99341ea6cfed60a41": "Состояние / Поставщики",
    "3eb74b7ffc64818290d2df3a3d1d4586": "Состояние / Библиотеки ГТУ-ГПУ-ГШО",
    "3eb74b7ffc6481dab36dd92fe59433c2": "Состояние / Портал",
    "3eb74b7ffc64818b818cc89cf685d009": "Знания",
    "3eb74b7ffc6481c8a833f923300a25e0": "Знания / Глоссарий",
    "3eb74b7ffc6481a0814aec2512ff0484": "Знания / Бренды",
    "3eb74b7ffc648150848ff6db5b67e2c7": "Знания / OEM и ODM",
    "3eb74b7ffc648167b3a7c9ed474c66b0": "Знания / Коды",
    "3eb74b7ffc64812c9edcf152c5de4b69": "Знания / Цены",
    "3eb74b7ffc6481d9ad28c65c7c2de443": "Знания / Машины и узлы",
    "3eb74b7ffc6481119445e4b826deb24a": "Знания / Поставщики и география",
    "3eb74b7ffc64818290e1cbe5383abcd3": "Знания / Источники",
    "3eb74b7ffc648124ad70df4840d66d31": "Знания / Сайты портала",
}

NOTION_RE = re.compile(r"https://(?:app\.notion\.com|www\.notion\.so)/p/([0-9a-f]{32})")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]*[a-z]{2,}", re.I)
PHONE_RE = re.compile(r"(?<![\w/.-])(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]?\d{2}[\s-]?\d{2}(?!\d)"
                      r"|(?<![\w/.-])\+\d{1,3}[\s(-]+\d{2,4}[\s)-]+\d{3}[\s-]?\d{2,4}(?!\d)")
CUR = r"(?:USD|EUR|RUB|CNY|руб\.?|рубл\w*|₽|\$|€|¥|юан\w*|долл\w*)"
NUM = r"\d[\d\s  ]*(?:[.,]\d+)?"
PRICE_RE = re.compile(rf"(?<![\w.]){NUM}\s*(?:тыс\.?\s*|млн\.?\s*)?{CUR}(?!\w)|{CUR}\s*{NUM}", re.I)
PATH_RE = re.compile(r"`([^`\s]+)`|\]\(([^)\s]+)\)")
PATH_EXT = re.compile(r"\.(?:py|md|json|ya?ml|html|js|mjs|sql|txt|toml|csv)$")
CAT_BEGIN, CAT_END = "<!-- kb:catalog:begin -->", "<!-- kb:catalog:end -->"


def pages() -> list[Path]:
    return sorted([*ROOT.glob("docs/kb/*.md"), *ROOT.glob("docs/agents/**/*.md")])


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def path_exists(ref: str, page: Path) -> bool | None:
    """True/False для ссылки на файл репозитория, None — если это не путь."""
    ref = ref.split("#")[0].rstrip(".,;:")
    if not ref or "://" in ref or ref.startswith(("<", "$", "/", "-")) or ":" in ref:
        return None
    if "…" in ref or "{" in ref or "<" in ref:
        return None
    if not (PATH_EXT.search(ref) or (ref.endswith("/") and "/" in ref[:-1])):
        return None
    if ref.startswith("."):
        if not ref.startswith((".github/", ".claude/", ".agent/")):
            return None  # расширение само по себе («.py»), а не путь
    cands = [ROOT / ref, page.parent / ref]
    if "/" not in ref and ref.endswith((".yml", ".yaml")):
        cands.append(ROOT / ".github" / "workflows" / ref)
    if "/" not in ref and "*" not in ref:
        # голое имя файла (`main.py`, `STATE.md`) — ищем в корне, рядом и в
        # docs/agents; точного места не требуем, но файл должен существовать
        return any(c.exists() for c in cands) or any(ROOT.glob(f"**/{ref}"))
    if "*" in ref:
        return any(glob.glob(str(c)) for c in cands)
    # путь внутри навыка («scripts/diff_docs.py» из .claude/skills/pdf-analysis)
    # пишут от папки навыка — допускается совпадение хвостом пути
    return any(c.exists() for c in cands) or any(ROOT.glob(f"**/{ref}"))


def catalog_block() -> str:
    cat = json.loads((ROOT / "data" / "catalog.json").read_text(encoding="utf-8"))
    rows: dict[str, dict[str, int]] = {}
    for d in cat["datasets"]:
        r = rows.setdefault(d.get("subproject") or "—", {})
        r["всего"] = r.get("всего", 0) + 1
        s = d.get("sensitivity") or {}
        s = (s.get("level") if isinstance(s, dict) else s) or "—"
        r[s] = r.get(s, 0) + 1
    sens = ["публикуемо", "внутреннее", "конфиденциально"]
    out = [CAT_BEGIN,
           "<!-- блок собирает `python scripts/kb_check.py --fix` из data/catalog.json; руками не править -->",
           "",
           f"Наборов данных: {len(cat['datasets'])}.",
           "",
           "| подпроект | наборов | " + " | ".join(sens) + " |",
           "|---|---:|" + "---:|" * len(sens)]
    for name in sorted(rows):
        r = rows[name]
        out.append(f"| {name} | {r['всего']} | " + " | ".join(str(r.get(s, 0)) for s in sens) + " |")
    out.append(CAT_END)
    return "\n".join(out)


def check_index(fix: bool) -> list[str]:
    text = INDEX.read_text(encoding="utf-8")
    if CAT_BEGIN not in text or CAT_END not in text:
        return [f"{rel(INDEX)}: нет блока каталога {CAT_BEGIN} … {CAT_END}"]
    a, b = text.index(CAT_BEGIN), text.index(CAT_END) + len(CAT_END)
    want = catalog_block()
    if text[a:b] == want:
        return []
    if fix:
        INDEX.write_text(text[:a] + want + text[b:], encoding="utf-8")
        print(f"пересобран блок каталога в {rel(INDEX)}")
        return []
    return [f"{rel(INDEX)}: блок каталога разошёлся с data/catalog.json — "
            "python scripts/kb_check.py --fix"]


def check_page(p: Path) -> list[str]:
    errs: list[str] = []
    name = rel(p)
    lines = p.read_text(encoding="utf-8").splitlines()
    if not any(NOTION_RE.search(l) for l in lines[:6]):
        errs.append(f"{name}: в первых строках нет ссылки на страницу Notion")
    verbatim_from = None
    if name == "docs/agents/LESSONS.md":
        verbatim_from = 0
    elif name == "docs/agents/DECISIONS.md":
        verbatim_from = next((i for i, l in enumerate(lines)
                              if l.startswith("## Раздел `/suppliers`")), None)
    for i, line in enumerate(lines, 1):
        for m in NOTION_RE.finditer(line):
            if m.group(1) not in NOTION:
                errs.append(f"{name}:{i}: ссылка на неизвестную страницу Notion {m.group(1)}")
        for m in EMAIL_RE.finditer(line):
            errs.append(f"{name}:{i}: почта «{m.group(0)}»")
        for m in PHONE_RE.finditer(line):
            errs.append(f"{name}:{i}: телефон «{m.group(0).strip()}»")
        if verbatim_from is None or i - 1 < verbatim_from:
            for m in PRICE_RE.finditer(line):
                errs.append(f"{name}:{i}: цена «{m.group(0).strip()}» — цены только в Notion")
        for m in PATH_RE.finditer(line):
            ref = m.group(1) or m.group(2)
            if path_exists(ref, p) is False:
                errs.append(f"{name}:{i}: ссылка на несуществующий путь «{ref}»")
    return errs


def check_lessons() -> list[str]:
    old = subprocess.run(["git", "show", f"{BASE}:CLAUDE.md"], cwd=ROOT,
                         capture_output=True, text=True)
    if old.returncode != 0:
        print(f"  коммит {BASE} недоступен (мелкий клон) — сверка LESSONS пропущена")
        return []
    norm = lambda s: " ".join(s.split())  # noqa: E731
    have = {norm(l) for f in (ROOT / "CLAUDE.md", LESSONS)
            for l in f.read_text(encoding="utf-8").splitlines()}
    lost = [l for l in (norm(x) for x in old.stdout.splitlines()) if l and l not in have]
    return [f"правило из прежнего CLAUDE.md потеряно: «{l[:100]}»" for l in lost]


def check_online() -> list[str]:
    errs, seen = [], set()
    for p in pages():
        for url in re.findall(r"https?://[^\s)`|>]+", p.read_text(encoding="utf-8")):
            url = url.rstrip(".,;")
            if url in seen or NOTION_RE.match(url) or "pages.dev" in url:
                continue  # закрытые страницы без входа не открываются
            seen.add(url)
            try:
                req = urllib.request.Request(url, method="HEAD")
                with urllib.request.urlopen(req, timeout=15) as r:
                    if r.status >= 400:
                        errs.append(f"{rel(p)}: {url} → {r.status}")
            except Exception as e:  # noqa: BLE001
                errs.append(f"{rel(p)}: {url} → {e}")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="пересобрать блок каталога в INDEX.md")
    ap.add_argument("--online", action="store_true", help="проверить внешние ссылки по сети")
    a = ap.parse_args()
    errs: list[str] = []
    ps = pages()
    for p in ps:
        errs += check_page(p)
    errs += check_index(a.fix)
    errs += check_lessons()
    if a.online:
        errs += check_online()
    for e in errs:
        print("✗", e)
    print(f"база знаний: страниц {len(ps)}, замечаний {len(errs)}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
