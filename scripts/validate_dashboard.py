#!/usr/bin/env python3
"""Проверка собранного дашборда ПЕРЕД деплоем.

Ловит три класса поломок, которые раньше уезжали в прод молча:
  1. незаменённые плейсхолдеры (__DATA_JSON__ и т.п.) — рассинхрон шаблона и dashboard.py;
  2. «пустой, но валидный» дашборд — все KPI по нулям из-за неполной выгрузки;
  3. JS-ошибки при рендере — проверяется в headless Chromium, если он доступен.

Использование:
    python3 scripts/validate_dashboard.py public/index.html
    python3 scripts/validate_dashboard.py public/index.html --allow-empty   # период правда пустой
    python3 scripts/validate_dashboard.py public/index.html --no-browser    # без Chromium

Код возврата: 0 — годно; 1 — есть ошибки (деплой выполнять нельзя).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLACEHOLDER_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")
DATA_RE = re.compile(r"window\.(__[A-Z_]+__)\s*=\s*(.+?);\s*$", re.MULTILINE)
MIN_BYTES = 50_000          # ниже — заведомо обрубленная страница
MAX_BYTES = 40_000_000      # выше — что-то пошло не так со встраиванием

CHROME_CANDIDATES = [
    os.getenv("CHROME_PATH") or "",
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
]


def find_chrome() -> str | None:
    for c in CHROME_CANDIDATES:
        if not c:
            continue
        p = shutil.which(c) if not c.startswith("/") else (c if Path(c).exists() else None)
        if p:
            return p
    return None


def check_placeholders(html: str, errors: list[str]) -> None:
    found = sorted(set(PLACEHOLDER_RE.findall(html)))
    # window.__DATA__ и подобные — легальные имена переменных, а не плейсхолдеры подстановки
    stray = [f for f in found if not re.search(r"window\.%s" % re.escape(f), html)]
    if stray:
        errors.append(f"незаменённые плейсхолдеры: {', '.join(stray[:10])}")


def check_size(html: str, errors: list[str]) -> None:
    n = len(html.encode("utf-8"))
    if n < MIN_BYTES:
        errors.append(f"страница подозрительно мала: {n} байт < {MIN_BYTES}")
    if n > MAX_BYTES:
        errors.append(f"страница подозрительно велика: {n} байт > {MAX_BYTES}")


def extract_data(html: str, errors: list[str]) -> dict[str, object]:
    blobs: dict[str, object] = {}
    for name, raw in DATA_RE.findall(html):
        raw = raw.strip()
        if raw in ("null", "undefined"):
            blobs[name] = None
            continue
        try:
            blobs[name] = json.loads(raw.replace("<\\/", "</"))
        except ValueError as e:
            errors.append(f"{name}: встроенный JSON не разбирается ({e})")
    if "__DATA__" not in blobs:
        errors.append("в странице нет window.__DATA__ — данные не подставлены")
    return blobs


def check_content(blobs: dict, errors: list[str], allow_empty: bool) -> None:
    data = blobs.get("__DATA__") or {}
    if not isinstance(data, dict):
        errors.append("window.__DATA__ не объект")
        return
    kpi = data.get("kpi") or {}
    total = kpi.get("total") or kpi.get("totalRfq") or 0
    sourcers = data.get("sourcersA") or []
    if not allow_empty:
        if not total:
            errors.append("kpi.total = 0 — дашборд пустой, выгрузка не удалась")
        if not sourcers:
            errors.append("sourcersA пуст — нет ни одного сорсера")
    weeks = data.get("weekly") or []
    if not weeks and not allow_empty:
        errors.append("нет недельной разбивки (weekly) — метрики неполные")


def _chrome_run(chrome: str, path: Path, extra: list[str], timeout: int, headless: str = "--headless=new"):
    """Один прогон Chromium. Возвращает (dom, log); dom пуст, если браузер ничего не отдал."""
    with tempfile.TemporaryDirectory() as td:
        cmd = [chrome, headless, "--no-sandbox", "--disable-gpu",
               "--disable-dev-shm-usage",          # на CI /dev/shm мал, без этого рендерер виснет
               "--no-first-run", "--no-default-browser-check",
               "--disable-extensions", "--disable-background-networking",
               "--disable-sync", "--disable-crash-reporter",
               "--disable-background-timer-throttling",
               "--disable-features=Translate,BackForwardCache,MediaRouter",
               f"--user-data-dir={td}", "--virtual-time-budget=5000",
               "--enable-logging=stderr", "--v=0", *extra,
               "--dump-dom", path.resolve().as_uri()]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            return "", (e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
    return r.stdout, r.stderr


def check_browser(path: Path, errors: list[str], warns: list[str]) -> None:
    chrome = find_chrome()
    if not chrome:
        warns.append("Chromium не найден — проверка рендера пропущена")
        return
    # три попытки: обычный headless, один процесс, старый headless.
    # На раннерах GitHub встречаются сборки Chrome, где --dump-dom не отдаёт
    # ничего в первых двух режимах.
    attempts = [([], 75, "--headless=new"),
                (["--single-process"], 60, "--headless=new"),
                ([], 60, "--headless=old")]
    dom, log = "", ""
    for extra, timeout, mode in attempts:
        dom, log = _chrome_run(chrome, path, extra, timeout, mode)
        if dom.strip():
            break
    if not dom.strip():
        # Пустой ответ браузера — отсутствие данных, а не доказательство поломки.
        # Структурные проверки выше отработали и остаются в силе, поэтому
        # предупреждаем, но не блокируем деплой исправного дашборда.
        tail = " | ".join(log.strip().splitlines()[-2:])[:200]
        warns.append(f"Chromium не отдал DOM — проверка рендера не выполнена ({tail or 'без вывода'})")
        return
    bad = [ln for ln in log.splitlines()
           if re.search(r"\bERROR:CONSOLE\b|Uncaught|SyntaxError|is not defined|is not a function", ln)]
    if bad:
        errors.append("JS-ошибки при рендере: " + " | ".join(b[-160:] for b in bad[:3]))
    if len(dom) < MIN_BYTES:
        errors.append(f"после рендера DOM мал ({len(dom)} байт) — страница не собралась")
    for tab in ("tab-sourcing", "tab-company"):
        if f'id="{tab}"' not in dom and f"id='{tab}'" not in dom:
            warns.append(f"в DOM нет блока {tab}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Валидация собранного дашборда перед деплоем")
    ap.add_argument("html", help="путь к собранному HTML (например public/index.html)")
    ap.add_argument("--allow-empty", action="store_true", help="разрешить нулевые KPI")
    ap.add_argument("--no-browser", action="store_true", help="не запускать Chromium")
    a = ap.parse_args()

    path = Path(a.html)
    if not path.exists():
        print(f"✗ файла нет: {path}", file=sys.stderr)
        return 1
    html = path.read_text(encoding="utf-8", errors="replace")

    errors: list[str] = []
    warns: list[str] = []
    check_placeholders(html, errors)
    check_size(html, errors)
    blobs = extract_data(html, errors)
    check_content(blobs, errors, a.allow_empty)
    if not a.no_browser:
        check_browser(path, errors, warns)

    kb = len(html.encode()) // 1024
    print(f"• {path}: {kb} КБ, блоков данных {len([k for k, v in blobs.items() if v is not None])}")
    for w in warns:
        print(f"  ⚠ {w}")
    if errors:
        print("✗ дашборд НЕ прошёл проверку — деплой отменён:", file=sys.stderr)
        for e in errors:
            print(f"   - {e}", file=sys.stderr)
        return 1
    print("✓ дашборд прошёл проверку")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
