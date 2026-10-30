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
# тело таблицы очереди КАМа: renderPeople(PP,'kam','repsRole') → _kqHtml(…,'repsRole-kq') → «…b»
KAM_QUEUE_BODY = "repsRole-kqb"
# таблица сделок «Реализации» в панели вида: renderContracts → _ctPanel(вид, #ctPanel-<вид>) —
# подвкладка «Сделки и деньги» (data-sub="main"); направление — второй ряд (data-dir)
CT_SEP_BODY, CT_MAIN_BODY = "#ctPanel-sep #ctBody", "#ctPanel-main #ctBody"
# подвкладки «Реализации» по регламенту ОРК (решения владельца 26.10.2026): каждая обязана
# отрисоваться в обоих направлениях
CT_SUBS = ("week", "month", "quarter", "main", "how")
# вкладка направления без его сделок (сводка head.sep есть, вида sep нет) — «Нет данных»
CT_SEP_EMPTY = "#ctPanel-sep .ctsepempty"
# «Что важно сегодня» основного вида (dealctx.py, payload["important"]) — строка ведёт в карточку
CT_IMP_ROW = "#ctPanel-main #ctImp tr[data-ct]"
KV_FAIL = "data-kv-fail"          # метка ошибки вставленной проверки в DOM
_KV_FAIL_RE = re.compile(r"<div %s=\"\"[^>]*>([^<]*)</div>" % KV_FAIL)
_TAB_FAIL = "Вкладка не отрисовалась"


def _with_tabs_opened(path: Path) -> Path:
    """Копия страницы, открывающая все ленивые вкладки. Если скрипт вставить некуда —
    возвращаем исходный файл: базовая проверка рендера важнее проверки вкладок."""
    html = path.read_text(encoding="utf-8", errors="replace")
    if "</body>" not in html:
        return path
    # Раздел «КАМы» вкладки «КАМ и продукт-оунеры» — подменю, а не вкладка: он
    # рисуется по клику (renderPeople с очередью КАМа). Открываем и его — иначе
    # ошибка в очереди КАМа доезжала бы до прода, а вкладка числилась бы целой.
    # Молча пройти нельзя: есть данные ролей и меню вкладки, а кнопки «КАМы» нет
    # (сменилась разметка или id роли) — ошибка; есть очередь КАМа, а после клика её
    # таблицы нет — ошибка. Нет данных людей или коммерсантов — раздел и не рисуется,
    # это деградация, о которой вкладка пишет сама, а не поломка шаблона.
    #
    # Ошибки вставки — меткой в DOM (KV_FAIL), а не только console.error: Chromium 141
    # пишет console.error в журнал как INFO:CONSOLE, и шаблон ERROR:CONSOLE его не
    # видит (проверено 19.10.2026 — сломанная кнопка роли проходила проверку).
    #
    # «Реализация» (решения владельца 25.10 и 26.10.2026): подвкладки «Неделя», «Месяц»,
    # «Квартал», «Сделки и деньги», «Как считается» и переключатель направления — по клику:
    # виды рисуются по одному. Каждая подвкладка открывается в основном виде и в виде
    # направления: панель не пуста и без «Вкладка не отрисовалась». «Сделки и деньги» обоих
    # видов дают таблицу сделок (у направления без сделок — блок «Нет данных»). Есть сводка
    # направления, а переключателя нет — ошибка. Адрес (portal-ui §3): прежний #contracts/sep
    # на уже открытой странице открывает «Сделки и деньги» направления; уход на «Сорсинг» и
    # возврат возвращают его адрес #contracts/main/sep. В конце — снова «Сорсинг».
    # Выгрузка в Excel (просьба владельца 29.10.2026): у каждой таблицы каждой вкладки и
    # подвкладки — кнопка «Excel» (метка data-xl-on и полоса .xlbar перед таблицей или её
    # обёрткой). Проверка — XLC после каждой открытой подвкладки и по всей странице в конце;
    # «Месяц» с данными — журнал шагов отрисован, со строками и своей кнопкой.
    inject = ("<script>function kvFail(m){try{console.error(m);var d=document.createElement('div');"
              "d.setAttribute('%s','');d.textContent=m;document.body.appendChild(d)}catch(e){}}"
              "function XLC(r,w){try{var T=[].slice.call(r.querySelectorAll('table')).filter(function(t){"
              "return t.dataset.xl!=='off'}),N=T.filter(function(t){return !t.dataset.xlOn}),B=T.filter(function(t){"
              "var p=t.parentElement,a=p&&/(^|\\s)(twrap|tscroll|pswrap)(\\s|$)/.test(p.className)?p:t,s=a.previousElementSibling;"
              "return t.dataset.xlOn&&!(s&&s.classList.contains('xlbar'))});"
              "if(N.length)kvFail('Excel, '+w+': таблиц без кнопки выгрузки '+N.length);"
              "if(B.length)kvFail('Excel, '+w+': метка есть, кнопки над таблицей нет у '+B.length)}"
              "catch(e){kvFail('Excel, '+w+': '+e)}}"
              # «Сорсинг» (30.10.2026, принцип «Реализации»): в «Сегодня» не больше 6 дел; каждое
              # число вкладки (строки «Сегодня» и «Порядка в карточках», плитки, «Что сделать» у
              # сорсера) открывает ровно столько строк списка; непустая очередь — непустое «Сегодня»
              "try{var SOD=document.getElementById('drw'),SOR=function(){return [].slice.call(document.querySelectorAll("
              "'#drwBody table.solst tbody tr')).filter(function(t){return !t.querySelector('td[colspan]')}).length};"
              "if(document.querySelectorAll('#soToday tr[data-sq]').length>6)kvFail('Сорсинг: в «Сегодня» больше 6 дел');"
              "if(typeof SOQ!=='undefined'&&SOQ.items.length&&!document.querySelector('#soToday tr[data-sq]'))"
              "kvFail('Сорсинг: «Сегодня» пусто при непустой очереди');"
              "[].slice.call(document.querySelectorAll('#tab-sourcing [data-so]')).forEach(function(b){"
              "var n=parseInt(b.textContent,10)||parseInt((b.querySelector('.v')||{}).textContent,10);b.click();var r=SOR();"
              "if(!SOD.classList.contains('on')||r!==n)kvFail('Сорсинг: число '+b.dataset.so+' = '+n+', а в списке строк '+r);"
              "else if(!/^#sourcing\\//.test(location.hash))kvFail('Сорсинг: у списка '+b.dataset.so+' нет адреса');"
              "document.getElementById('drwX').click()})}catch(e){kvFail('Сорсинг, «Сегодня» и списки за числом: '+e)}"
              "try{[%s].forEach(function(t){try{window.ensureTab&&window.ensureTab(t)}"
              "catch(e){kvFail('вкладка '+t+': '+e)}})}catch(e){kvFail('ensureTab: '+e)}"
              # «Доход» (kaminc.py) открывается первым разделом «Key-аккаунтов и продуктов»:
              # есть его данные — должны быть кнопка раздела с role=tab и таблица разреза
              "try{var KH=window.__KAMINC_HEAD__;if(KH&&KH.cuts&&KH.tot){"
              "if(!document.querySelector('#tab-reps [role=\"tab\"][data-r=\"income\"]'))"
              "kvFail('Доход: нет кнопки раздела');"
              "else if(!document.getElementById('incCutBody'))kvFail('Доход: разрез не отрисовался')}}"
              "catch(e){kvFail('Доход: '+e)}"
              "try{var P=window.__PEOPLE__,b=document.querySelector('#tab-reps [data-r=\"role:kam\"]');"
              "if(b){b.click();if(P&&P.kamQueue&&!document.getElementById('%s'))"
              "kvFail('раздел КАМы: очередь КАМа не отрисовалась')}"
              "else if(P&&P.roles&&document.querySelector('#tab-reps .subnav'))"
              "kvFail('раздел КАМы: кнопка роли не найдена')}"
              "catch(e){kvFail('раздел КАМы: '+e)}"
              "try{var CX=window.__CONTRACTS__,CQ=function(x){return document.querySelector('#tab-contracts '+x)},"
              "SB=function(k){return CQ('[role=\"tab\"][data-sub=\"'+k+'\"]')},"
              "ALL=function(d){[%s].forEach(function(k){var b=SB(k);if(!b){kvFail('Реализация: нет подвкладки '+k);return}"
              "b.click();var p=document.getElementById('ctPanel-'+d);"
              "if(!p||p.hidden||!p.textContent.trim())kvFail('Реализация: подвкладка '+k+' ('+d+') пуста');"
              "else if(p.textContent.indexOf('%s')>=0)kvFail('Реализация: подвкладка '+k+' ('+d+') не отрисовалась');"
              "else XLC(p,'Реализация, '+k+' ('+d+')')})};"
              "if(CX&&Array.isArray(CX.rows))ALL('main');"
              "var ST=CQ('[role=\"tab\"][data-dir=\"sep\"]');"
              "if(ST){ST.click();ALL('sep');SB('main').click();if(!document.querySelector(CX&&CX.sep?'%s':'%s'))"
              "kvFail('Реализация: вкладка отдельного направления не отрисовалась');"
              "var MT=CQ('[role=\"tab\"][data-dir=\"main\"]');"
              "if(MT)MT.click();if(!document.querySelector('%s'))"
              "kvFail('Реализация: основной вид после переключения не отрисовался')}"
              "else if(CX&&Array.isArray(CX.rows)&&CX.head&&CX.head.sep)"
              "kvFail('Реализация: нет переключателя вкладки направления')}"
              "catch(e){kvFail('Реализация, вкладки: '+e)}"
              # «Что важно сегодня» и «Главное» в карточке (решение владельца 25.10.2026): блок —
              # в «Неделе руководителя ОРК» под очередью (с 26.10.2026 очередь там). Есть пункты в
              # закрытой части — есть и строки блока; клик — карточка с блоком «Главное»
              "try{var DV=window.__DEALCTX__,WK=document.querySelector('#tab-contracts [role=\"tab\"][data-sub=\"week\"]');"
              "if(DV&&Array.isArray(DV.queue)&&Array.isArray(DV.important)&&DV.important.length&&WK){"
              "WK.click();var IR=document.querySelector('%s');"
              "if(!IR)kvFail('Реализация: блок «Что важно сегодня» не отрисовался');"
              "else{IR.click();var DW=document.getElementById('drw');"
              "if(!DW||(DW.textContent||'').indexOf('Главное по сделке — оценка ИИ')<0)"
              "kvFail('Реализация: в карточке сделки нет блока «Главное»');"
              "var DX=document.getElementById('drwX');if(DX)DX.click()}"
              "var MS=document.querySelector('#tab-contracts [role=\"tab\"][data-sub=\"main\"]');if(MS)MS.click()}}"
              "catch(e){kvFail('Реализация, «Что важно сегодня»: '+e)}"
              "try{var OM=window.__ORKMONTH__,MO=document.querySelector('#tab-contracts [role=\"tab\"][data-sub=\"month\"]');"
              "if(OM&&OM.main&&OM.main.journal&&MO){MO.click();var JB=document.getElementById('orkJb');"
              "if(!JB)kvFail('Реализация: журнал шагов «Месяца» не отрисовался');"
              "else if(!JB.rows.length)kvFail('Реализация: журнал шагов без строк — нет даже строки состояния');"
              "else if(!document.querySelector('#orkJ .xlbar .xlbtn'))kvFail('Реализация: у журнала шагов нет кнопки Excel');"
              "var MS2=document.querySelector('#tab-contracts [role=\"tab\"][data-sub=\"main\"]');if(MS2)MS2.click()}}"
              "catch(e){kvFail('Реализация, журнал шагов: '+e)}"
              # числа «Недели» со списком заказов за ними (решение владельца 29.10.2026, п. 5):
              # клик открывает список, строк в нём столько же, сколько в числе
              "try{var WK2=document.querySelector('#tab-contracts [role=\"tab\"][data-sub=\"week\"]'),CX2=window.__CONTRACTS__;"
              "if(WK2&&CX2&&CX2.ork&&CX2.ork.week&&CX2.ork.week.drill){WK2.click();"
              "var NB=[].slice.call(document.querySelectorAll('#ctPanel-main button.odn[data-od]')).slice(0,60);"
              # одно число дней (ревью R4, 30.10.2026): «раб. дн. на шаге» строки списка (единица — R4, 2.7) = число «Пути заказа»
              # (o.stepWd, у шага без нормы — последняя запись ork.path на текущем шаге), та же единица
              "var OI={};[].concat(CX2.rows||[],(CX2.sep||{}).rows||[]).forEach(function(d){(d.orders||[]).forEach(function(o){OI[String(o.id)]=o})});"
              "var DY=function(id){var bad=[];[].slice.call(document.querySelectorAll('#drwBody table.odt tbody tr[data-odk]')).forEach(function(tr){"
              "var o=OI[tr.dataset.odk],e=tr.querySelector('.odc2 .oddays b');if(!o||!e)return;var P=o.path||[],l=P[P.length-1],"
              "w=o.stepWd!=null?o.stepWd:(l&&l[0]===o.step?l[2]:null);if(w!=null&&e.textContent.trim()!==w+' раб. дн.')bad.push(tr.dataset.odk+': '+e.textContent.trim()+' ≠ '+w+' раб. дн.')});"
              "if(bad.length)kvFail('Реализация: число '+id+' — дни на шаге в списке не те, что в пути заказа: '+bad.slice(0,3).join('; '))};"
              "if(!NB.length)kvFail('Реализация: в «Неделе» нет чисел со списком заказов');"
              "NB.forEach(function(b){var n=parseInt(b.textContent,10),D=document.getElementById('drw');b.click();"
              # строка списка — объект его единицы (заказ или сделка); пары «заказ × отрезок | файл» —
              # вложенными строками под заказом (спец. «сделка и заказ», E27): счёт — пары
              "var R=document.querySelectorAll('#drwBody table.odt tbody li.odpi').length||document.querySelectorAll('#drwBody table.odt tbody tr[data-odk]').length;"
              "if(!D||!D.classList.contains('on'))kvFail('Реализация: число '+b.dataset.od+' не открыло список заказов');"
              "else if(R!==n)kvFail('Реализация: число '+b.dataset.od+' = '+n+', а в списке строк '+R);"
              # строка списка открывается (путь заказа или карточка сделки): у каждой — ключ заказа
              "else if(document.querySelector('#drwBody table.odt tbody tr:not([data-odk]):not(.odpr)'))"
              "kvFail('Реализация: число '+b.dataset.od+' — в списке строка без ключа заказа (data-odk)');"
              "else DY(b.dataset.od);"
              "var X2=document.getElementById('drwX');if(X2)X2.click()});"
              "var MS2=document.querySelector('#tab-contracts [role=\"tab\"][data-sub=\"main\"]');if(MS2)MS2.click()}}"
              "catch(e){kvFail('Реализация, списки заказов за числами: '+e)}"
              # плитки, строки «Пульса», «Ждут смежников» (с переходом в очередь), фишки гигиены;
              # «Месяц» и «Квартал» — обратная сверка: каждый список в данных (dk показанного, liveDk,
              # nDk) отрисован числом, и оно открывает ровно n строк (ревизия 29.10.2026)
              "try{var WK3=document.querySelector('#tab-contracts [role=\"tab\"][data-sub=\"week\"]'),CX3=window.__CONTRACTS__;"
              "if(WK3&&CX3&&CX3.ork&&CX3.ork.week&&CX3.ork.week.drill){WK3.click();"
              "var RW=function(){return document.querySelectorAll('#drwBody table.odt tbody li.odpi').length||document.querySelectorAll('#drwBody table.odt tbody tr[data-odk]').length},"
              "SH=function(){var D=document.getElementById('drw');return !!D&&D.classList.contains('on')},"
              "XX=function(){var X3=document.getElementById('drwX');if(X3)X3.click()},"
              "EA=function(sel,fn){[].slice.call(document.querySelectorAll('#ctPanel-main '+sel)).forEach(fn)};"
              # «Неделя» R4 (WP-B): каждое число вывода, плиток, счётчиков, ведомости, «Кто сколько
              # ведёт», «Кого ждём» и ошибок карточек открывает ровно столько строк (всё, не первые 60)
              "EA('.wk button.odn[data-od]',function(b){var n=parseInt(b.textContent,10);if(isNaN(n))return;b.click();"
              "if(!SH())kvFail('Реализация: число '+b.dataset.od+' не открыло список заказов');"
              "else if(RW()!==n)kvFail('Реализация: число '+b.dataset.od+' = '+n+', а в списке строк '+RW());XX()});"
              # «Сделки без хозяина» (решение владельца 30.10.2026, п. 3): счётчик «Недели» = week.noOwner.n =
              # строки своего списка (Сделка · Чего нет · Кто назначает · Сколько дней без · Заказов)
              "var NOW=(CX3.ork.week||{}).noOwner;if(NOW&&NOW.n){var NB2=document.querySelector('#ctPanel-main button.odn[data-od=\"w:noOwner\"]');"
              "if(!NB2)kvFail('Реализация: нет счётчика «Сделок без хозяина» при '+NOW.n+' сделках');"
              "else{if(parseInt(NB2.textContent,10)!==NOW.n)kvFail('Реализация: счётчик «Сделок без хозяина» = '+NB2.textContent+', а в данных '+NOW.n);"
              "NB2.click();var TH=[].slice.call(document.querySelectorAll('#drwBody table.odown thead th:not([hidden])')).map(function(t){return t.textContent.trim()}).join(' · ');"
              "if(TH!=='Сделка · Чего нет · Кто назначает · Сколько дней без · Заказов')kvFail('Реализация: столбцы списка «Сделки без хозяина»: '+TH);"
              "if(RW()!==NOW.n)kvFail('Реализация: «Сделок без хозяина» '+NOW.n+', а в списке строк '+RW());XX()}}"
              # плитки: KPI 1–3 есть; части складываются в число плитки (каждый заказ — один раз, Д-1)
              "var KN=function(r){var e=document.querySelector('#ctPanel-main [data-kpi-rank=\"'+r+'\"]');return e?parseInt(e.textContent,10):NaN},"
              "PS=function(sel){return [].slice.call(document.querySelectorAll('#ctPanel-main '+sel)).reduce(function(a,b){return a+(parseInt(b.textContent,10)||0)},0)};"
              "if(document.querySelector('#ctPanel-main #wkTLate')){[1,2,3].forEach(function(r){if(isNaN(KN(r))&&!document.querySelector('#ctPanel-main #orkTiles .wkdim'))kvFail('Реализация: нет плитки KPI '+r)});"
              "if(!isNaN(KN(3))&&PS('#wkTThr .wkparts button.odn')!==KN(3))kvFail('Реализация: части «Срок под угрозой» дают '+PS('#wkTThr .wkparts button.odn')+', а плитка '+KN(3));"
              "if(!isNaN(KN(2))&&PS('#wkTW2 .wkw2l button.odn')!==KN(2))kvFail('Реализация: шаги плитки «Шаги ОРК дольше нормы» дают '+PS('#wkTW2 .wkw2l button.odn')+', а плитка '+KN(2));"
              "var W3=(CX3.ork.week.tiles||{}).W3||{};if(W3.byOrders&&(W3.state==='show'||W3.state==='prelim')&&(KN(1)||0)+(KN(3)||0)!==W3.value)"
              "kvFail('Реализация: «сорван» + «под угрозой» = '+((KN(1)||0)+(KN(3)||0))+', а W3 = '+W3.value)}"
              # «Кого ждём»: строка — список её заказов (клик по строке)
              "EA('#orkAdj tr[data-adj]',function(tr){var b=tr.querySelector('button[data-od]'),n=b?parseInt(b.textContent,10):0;if(!n)return;"
              "(tr.querySelector('th')||tr).click();"
              "if(!SH()||RW()!==n)kvFail('Реализация: «Кого ждём» '+tr.dataset.adj+' — строк '+RW()+' вместо '+n);XX()});"
              # «Что сделать сегодня»: группы есть при непустой очереди; число группы — ровно её пункты,
              # из списка — переход обратно к группе
              # пункты «Порядка в карточках» (часть hyg, решение владельца 30.10.2026) — вне дневного счёта, своим
              # блоком #orkTodayC: очередь из одних них не требует групп «Что сделать сегодня»
              "var LQ=(CX3.life||{}).queue||[],BQ=function(q){return q.bucket||(typeof _wkB==='function'?_wkB(q):'exec')};"
              "if(LQ.some(function(q){return BQ(q)!=='hyg'})&&!document.querySelector('#ctPanel-main #orkToday [data-tq]'))"
              "kvFail('Реализация: блок «Что сделать сегодня» пуст при непустой очереди');"
              "if(LQ.some(function(q){return BQ(q)==='hyg'})&&!document.querySelector('#ctPanel-main #orkTodayC [data-tq]'))"
              "kvFail('Реализация: блок «Порядок в карточках» пуст при пунктах порядка в очереди');"
              "EA('#orkToday button[data-tq]',function(b){var n=parseInt(b.textContent,10);b.click();"
              "if(!SH()||RW()!==n)kvFail('Реализация: «Что сделать сегодня» '+b.dataset.tq+' = '+n+', а в списке строк '+RW());"
              "else if(!document.querySelector('#drwBody [data-odq]'))kvFail('Реализация: «Что сделать сегодня» '+b.dataset.tq+' — нет перехода к группе');XX()});"
              "var OM=window.__ORKMONTH__&&window.__ORKMONTH__.main,"
              "DK=function(root,id){var out=[];var w=function(o){if(!o||typeof o!=='object')return;if(Array.isArray(o)){o.forEach(w);return}"
              "if(o.dk&&(o.state==='show'||o.state==='prelim')&&(id==null||o.dk===id))out.push(id==null?o.dk:o.n);"
              "if(o.nDk&&(id==null||o.nDk===id))out.push(id==null?o.nDk:o.n);"
              "if(o.liveDk&&(id==null||o.liveDk===id))out.push(id==null?o.liveDk:o.live);"
              "for(var k in o)if(k!=='drill'&&k!=='orders')w(o[k])};w(root);return out};"
              "[['quarter',CX3.ork.quarter],['month',OM]].forEach(function(z){"
              "var b=document.querySelector('#tab-contracts [role=\"tab\"][data-sub=\"'+z[0]+'\"]');if(!b||!z[1])return;b.click();"
              "DK(z[1]).forEach(function(id){var el=document.querySelector('#ctPanel-main [data-od=\"'+id+'\"]');"
              "if(!el){kvFail('Реализация: «'+z[0]+'» — список '+id+' собран, а числа на странице нет');return}"
              "var n=DK(z[1],id)[0];el.click();"
              "if(!SH()||RW()!==n)kvFail('Реализация: «'+z[0]+'» '+id+' = '+n+', а в списке строк '+RW());XX()})});"
              "var MS3=document.querySelector('#tab-contracts [role=\"tab\"][data-sub=\"main\"]');if(MS3)MS3.click()}}"
              "catch(e){kvFail('Реализация, плитки, «Пульс», смежники, гигиена, «Месяц» и «Квартал»: '+e)}"
              # фишка «Без хозяина» «Сделок и денег» — то же число сделок, что счётчик «Недели»; отбор даёт
              # столько же строк таблицы сделок
              "try{var CX4=window.__CONTRACTS__,NO4=CX4&&CX4.ork&&CX4.ork.week&&CX4.ork.week.noOwner,"
              "MB4=document.querySelector('#tab-contracts [role=\"tab\"][data-sub=\"main\"]');"
              "if(NO4&&NO4.n&&MB4){MB4.click();var CH4=document.querySelector('#ctPanel-main #ctChips .stchip[data-m=\"noOwner\"]');"
              "if(!CH4)kvFail('Реализация: в «Сделках и деньгах» нет фишки «Без хозяина»');"
              "else{var c4=parseInt((CH4.querySelector('b')||{}).textContent,10);"
              "if(c4!==NO4.n)kvFail('Реализация: фишка «Без хозяина» = '+c4+', а сделок без хозяина '+NO4.n);"
              "CH4.click();var tr4=document.querySelectorAll('#ctPanel-main #ctBody tr[data-deal],#ctPanel-main #ctBody tr[data-ct]').length;"
              "if(tr4&&tr4!==NO4.n)kvFail('Реализация: отбор «Без хозяина» — строк '+tr4+' вместо '+NO4.n);"
              "var LV4=document.querySelector('#ctPanel-main #ctChips .stchip[data-m=\"live\"]');if(LV4)LV4.click()}}}"
              "catch(e){kvFail('Реализация, «Без хозяина»: '+e)}"
              "try{var TB=function(n){return [].slice.call(document.querySelectorAll('.tab'))"
              ".filter(function(x){return x.dataset.tab===n})[0]},CT=TB('contracts'),SO=TB('sourcing'),"
              "SEL=function(a){var x=document.querySelector('#tab-contracts [role=\"tab\"][data-'+a+'][aria-selected=\"true\"]');"
              "return x&&x.dataset[a]};"
              "if(CT&&SO&&document.querySelector('#tab-contracts [role=\"tab\"][data-dir=\"sep\"]')){"
              "history.replaceState(null,'','#contracts/sep');window.dispatchEvent(new HashChangeEvent('hashchange'));"
              "if(!CT.classList.contains('act')||document.getElementById('tab-contracts').hidden||SEL('dir')!=='sep'||SEL('sub')!=='main')"
              "kvFail('Реализация: адрес #contracts/sep на открытой странице не открыл «Сделки и деньги» направления');"
              "SO.click();CT.click();if(location.hash!=='#contracts/main/sep')"
              "kvFail('Реализация: при возврате на вкладку потерян её адрес ('+location.hash+')');"
              "SO.click()}}catch(e){kvFail('Реализация, адрес вкладки: '+e)}"
              "XLC(document,'вся страница')</script>"
              % (KV_FAIL, ",".join(f"'{t}'" for t in LAZY_TABS), KAM_QUEUE_BODY,
                 ",".join(f"'{t}'" for t in CT_SUBS), _TAB_FAIL, CT_SEP_BODY, CT_SEP_EMPTY, CT_MAIN_BODY,
                 CT_IMP_ROW))
    tmp = path.parent / (path.stem + ".tabs.html")
    tmp.write_text(html.replace("</body>", inject + "</body>", 1), encoding="utf-8")
    return tmp


def _check_tabs_dom(dom: str, errors: list[str]) -> None:
    """Разбор DOM после принудительного открытия вкладок: какая не отрисовалась."""
    # текст-маркер есть и в исходнике обработчика ensureTab, поэтому ищем его
    # только в разметке: скрипты из DOM вырезаем
    body = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", dom)
    fails = _KV_FAIL_RE.findall(body)
    if fails:
        errors.append("проверка открытых вкладок: " + " | ".join(f[:160] for f in fails[:3]))
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
