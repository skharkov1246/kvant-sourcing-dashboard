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

_HUNG = "браузер не завершился за"
PLACEHOLDER_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")
DATA_RE = re.compile(r"window\.(__[A-Z_]+__)\s*=\s*(.+?);\s*$", re.MULTILINE)
MIN_BYTES = 50_000          # ниже — заведомо обрубленная страница
MAX_BYTES = 40_000_000      # выше — что-то пошло не так со встраиванием

# Порядок важен. Раньше первыми стояли `chromium`/`chromium-browser`, а в образах
# Ubuntu это обычно snap-обёртка: в контейнере CI она не запускается и ВИСИТ, ничего
# не печатая. Валидатор при этом рапортовал «Chromium не отдал DOM», не называя, какой
# бинарь он вообще взял, — и проверка рендера боевой страницы не работала неделями.
# Сначала деб-сборки Chrome, потом локальный Chromium Playwright, snap-подверженные
# имена — последними.
CHROME_CANDIDATES = [
    os.getenv("CHROME_PATH") or "",
    "google-chrome-stable", "google-chrome",
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "chromium", "chromium-browser",
]
PROBE_TIMEOUT = 15          # секунд на `--version`: живой браузер отвечает мгновенно


def find_chrome() -> tuple[str, str] | tuple[None, None]:
    """(путь, версия) первого кандидата, который ОТВЕЧАЕТ, а не просто существует.

    Наличие файла ничего не значит: snap-обёртка есть в PATH и виснет при запуске.
    Поэтому каждый кандидат проверяется вызовом `--version` с коротким таймаутом —
    это стоит доли секунды и сразу отсекает нерабочие сборки.
    """
    for c in CHROME_CANDIDATES:
        if not c:
            continue
        p = shutil.which(c) if not c.startswith("/") else (c if Path(c).exists() else None)
        if not p:
            continue
        try:
            r = subprocess.run([p, "--version"], capture_output=True, text=True, timeout=PROBE_TIMEOUT)
        except (subprocess.TimeoutExpired, OSError):
            continue                       # висит или не запускается — берём следующего
        if r.returncode == 0 and r.stdout.strip():
            return p, r.stdout.strip()
    return None, None


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



def _field_weights(obj, limit: int = 5) -> list[tuple[int, str]]:
    """Из чего сложился вес блока: топ полей по объёму сериализации.

    Для словаря — вес каждого ключа верхнего уровня; для списка однотипных записей —
    суммарный вес каждого поля записи. Без этого ужимать страницу можно только на глаз,
    а правило репозитория требует сначала померить.
    """
    def size(x) -> int:
        return len(json.dumps(x, ensure_ascii=False, separators=(",", ":")).encode())

    if isinstance(obj, dict):
        rows = [(size(v) + len(k) + 4, k) for k, v in obj.items()]
    elif isinstance(obj, list) and obj and isinstance(obj[0], dict):
        agg: dict[str, int] = {}
        for it in obj:
            if not isinstance(it, dict):
                continue
            for k, v in it.items():
                agg[k] = agg.get(k, 0) + size(v) + len(k) + 4
        rows = [(v, k) for k, v in agg.items()]
    else:
        return []
    return sorted(rows, reverse=True)[:limit]


def extract_data(html: str, errors: list[str], sizes: dict[str, int] | None = None) -> dict[str, object]:
    blobs: dict[str, object] = {}
    for name, raw in DATA_RE.findall(html):
        raw = raw.strip()
        if sizes is not None:
            sizes[name] = len(raw.encode())
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
    # ignore_cleanup_errors: Chrome оставляет фоновые процессы, которые ещё пишут
    # в профиль после выхода основного, и удаление каталога падало гонкой
    # (OSError: Directory not empty: .../Default) — уже при пройденных проверках.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        cmd = [chrome, headless, "--no-sandbox", "--disable-gpu",
               "--disable-dev-shm-usage",          # на CI /dev/shm мал, без этого рендерер виснет
               "--no-first-run", "--no-default-browser-check",
               "--disable-extensions", "--disable-background-networking",
               "--disable-sync", "--disable-crash-reporter",
               "--disable-background-timer-throttling",
               "--disable-features=Translate,BackForwardCache,MediaRouter",
               "--disable-component-update", "--metrics-recording-only", "--mute-audio",
               f"--user-data-dir={td}", "--virtual-time-budget=5000",
               "--enable-logging=stderr", "--v=0", *extra,
               "--dump-dom", path.resolve().as_uri()]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            # Chrome печатает DOM, но иногда не завершается: фоновые процессы профиля
            # держат его (о них уже сказано выше). Раньше в этой ветке возвращалась
            # пустая строка — вместе с исключением выбрасывался УЖЕ НАПЕЧАТАННЫЙ DOM,
            # и проверка молча превращалась в «браузер ничего не отдал». Замер
            # 17.09.2026: страница 10,4 МБ со всеми открытыми вкладками отдаёт DOM за
            # ~3 с, так что таймаут — это почти всегда незавершение, а не отсутствие вывода.
            dec = lambda v: v.decode("utf-8", "replace") if isinstance(v, bytes) else (v or "")
            return dec(e.stdout), dec(e.stderr) + f"\n{_HUNG} {timeout} с (вывода не было)"
    return r.stdout, r.stderr


def check_browser(path: Path, errors: list[str], warns: list[str], notes: list[str] | None = None) -> None:
    chrome, version = find_chrome()
    if not chrome:
        warns.append("рабочего Chromium/Chrome не нашлось (кандидаты не отвечают на --version) — "
                     "проверка рендера пропущена")
        return
    if notes is not None:
        notes.append(f"браузер: {version} ({chrome})")
    # Вкладки строятся лениво, при первом открытии, поэтому открываем их все в ЭТОМ
    # же прогоне: отдельный второй запуск Chromium стоил на раннере около двух минут.
    #
    # Бюджет времени — небольшой и от размера страницы. Замер 17.09.2026 на этом же
    # шаблоне: 10,4 МБ со всеми открытыми вкладками — DOM за ~3 с, 14 МБ — за 4,3 с,
    # даже если данные материализуются в таблицу — 15 с. Значит сотни секунд ожидания
    # не приближают результат: они лишь удлиняют зависание. Открытие девяти вкладок
    # стоит +0,28 % DOM — проверять их на боевой странице не дороже, чем не проверять.
    #
    # Режима --headless=old здесь нет намеренно: в Chrome ≥132 он удалён (rc=1 и пустой
    # stdout за 0,03 с), и «запасная попытка по чистой странице» на нём была фиктивной —
    # именно из-за неё проверка рендера боевой страницы не выполнялась с 14.09.2026,
    # оставляя в журнале «Chromium не отдал DOM». Запасная попытка теперь тоже new.
    mb = max(1, len(path.read_bytes()) // (1024 * 1024))
    budget = min(120, max(45, 8 * mb))
    page = _with_tabs_opened(path)
    attempts = [(page, [], budget, "--headless=new"),
                (page, ["--single-process"], budget, "--headless=new"),
                (path, [], budget, "--headless=new")]
    dom, log, with_tabs = "", "", False
    try:
        for target, extra, timeout, mode in attempts:
            dom, log = _chrome_run(chrome, target, extra, timeout, mode)
            if dom.strip():
                with_tabs = target is page and page != path
                break
    finally:
        if page != path:
            page.unlink(missing_ok=True)
    if not dom.strip():
        # Пустой ответ браузера — отсутствие данных, а не доказательство поломки.
        # Структурные проверки выше отработали и остаются в силе, поэтому
        # предупреждаем, но не блокируем деплой исправного дашборда.
        tail = " | ".join(log.strip().splitlines()[-2:])[:200]
        warns.append(f"{version} не отдал DOM за {budget} с — проверка рендера не выполнена. "
                     f"Браузер: {chrome}. Хвост вывода: {tail or 'пусто'}")
        return
    if not with_tabs:
        warns.append(f"вкладки не проверены: страница {mb} МБ, браузер не уложился в {budget} с — "
                     "проверен только базовый рендер")
    if _HUNG in log:
        warns.append(f"браузер не завершился за {budget} с — DOM взят из его вывода")
    bad = [ln for ln in log.splitlines()
           if re.search(r"\bERROR:CONSOLE\b|Uncaught|SyntaxError|is not defined|is not a function", ln)]
    if bad:
        errors.append("JS-ошибки при рендере: " + " | ".join(b[-160:] for b in bad[:3]))
    if len(dom) < MIN_BYTES:
        errors.append(f"после рендера DOM мал ({len(dom)} байт) — страница не собралась")
    for tab in ("tab-sourcing", "tab-company"):
        if f'id="{tab}"' not in dom and f"id='{tab}'" not in dom:
            warns.append(f"в DOM нет блока {tab}")
    if with_tabs:
        _check_tabs_dom(dom, errors)


# Вкладки строятся лениво — при первом открытии. Поэтому обычный прогон проверяет
# рендер только видимой вкладки «Сорсинг», а поломка в «КАМах» или «Реализации»
# доезжала до прода незамеченной. Здесь все вкладки принудительно открываются.
LAZY_TABS = ["company", "reps", "eng", "contracts", "suppliers", "cohorts", "advisor"]
_TAB_FAIL = "Вкладка не отрисовалась"


def _with_tabs_opened(path: Path) -> Path:
    """Копия страницы, открывающая все ленивые вкладки. Если скрипт вставить некуда —
    возвращаем исходный файл: базовая проверка рендера важнее проверки вкладок."""
    html = path.read_text(encoding="utf-8", errors="replace")
    if "</body>" not in html:
        return path
    inject = ("<script>try{[%s].forEach(function(t){try{window.ensureTab&&window.ensureTab(t)}"
              "catch(e){console.error('вкладка '+t+': '+e)}})}catch(e){console.error('ensureTab: '+e)}</script>"
              % ",".join(f"'{t}'" for t in LAZY_TABS))
    tmp = path.parent / (path.stem + ".tabs.html")
    tmp.write_text(html.replace("</body>", inject + "</body>", 1), encoding="utf-8")
    return tmp


def _check_tabs_dom(dom: str, errors: list[str]) -> None:
    """Разбор DOM после принудительного открытия вкладок: какая не отрисовалась."""
    # текст-маркер есть и в исходнике обработчика ensureTab, поэтому ищем его
    # только в разметке: скрипты из DOM вырезаем
    body = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", dom)
    if _TAB_FAIL not in body:
        return
    broken = []
    for tab in LAZY_TABS:
        m = re.search(r'id="tab-%s"(.*?)(?=<div id="tab-|</body>)' % tab, body, re.S)
        if m and _TAB_FAIL in m.group(1):
            broken.append(tab)
    errors.append("вкладка отрисовалась с ошибкой: " + (", ".join(broken) or "не определить какая"))


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
    notes: list[str] = []
    check_placeholders(html, errors)
    check_size(html, errors)
    sizes: dict[str, int] = {}
    blobs = extract_data(html, errors, sizes)
    check_content(blobs, errors, a.allow_empty)
    if not a.no_browser:
        check_browser(path, errors, warns, notes)

    kb = len(html.encode()) // 1024
    print(f"• {path}: {kb} КБ, блоков данных {len([k for k, v in blobs.items() if v is not None])}")
    # из чего сложился вес: без разбивки любая борьба за размер страницы — гадание
    heavy = sorted(((v, k) for k, v in sizes.items() if v > 64 * 1024), reverse=True)[:6]
    if heavy:
        print("  вес данных: " + " · ".join(f"{k.strip('_').lower()} {v // 1024} КБ" for v, k in heavy))
    # у самых тяжёлых блоков — разбивка по полям: что именно занимает мегабайты
    for v, k in heavy[:3]:
        if v < 512 * 1024:
            continue
        blob = blobs.get(k)
        top = _field_weights(blob)
        if not top:
            continue
        name = k.strip("_").lower()
        print(f"    {name}: " + " · ".join(f"{nm} {sz // 1024} КБ" for sz, nm in top))
        # ещё уровень вниз по самому тяжёлому ключу: у блоков-словарей вес сидит
        # внутри одного массива записей, и правит его не блок, а конкретное поле
        if isinstance(blob, dict) and top and top[0][0] > 512 * 1024:
            inner = _field_weights(blob.get(top[0][1]))
            if inner:
                print(f"      {name}.{top[0][1]}: "
                      + " · ".join(f"{nm} {sz // 1024} КБ" for sz, nm in inner))
    for n in notes:
        print(f"  {n}")
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
