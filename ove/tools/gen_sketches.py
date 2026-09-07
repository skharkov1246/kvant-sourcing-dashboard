#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генерация статических SVG-файлов листов вкладки «Базовый проект» сайта ОВЭ-75.

Что делает: открывает собранный сайт ove/public/index.html в headless-Chromium
(Playwright), включает вкладку «Базовый проект» (show('bi')), дожидается рендера
и для каждого листа svg.gsvg повторяет логику кнопки «⬇ SVG» (функция dlSvg в
ove/site/index.template.html): берёт outerHTML, разворачивает все var(--...)
в литеральные цвета через getComputedStyle(document.body), добавляет
XML-заголовок — и сохраняет в ove/public/docs/sketches/<имя из data-fn кнопки>.

Соответствие «id листа → имя файла» берётся прямо из DOM (кнопки
`#s-bi [data-dl]`), поэтому новый лист, добавленный в манифест SKETCHES
шаблона, подхватывается автоматически.

⚠ Standalone-утилита, НЕ входит в сборку CI. Запускается ЛОКАЛЬНО после правки
листов (svg*-функций в ove/site/index.template.html) и пересборки сайта
(ove/build.py); полученные файлы в ove/public/docs/sketches/ деплоятся как
обычная статика по адресу docs/sketches/<файл>.

Запуск:
    python3 ove/tools/gen_sketches.py
Путь к Chromium можно задать через $CHROMIUM_PATH (иначе — chromium,
установленный самим Playwright).
"""

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from playwright.sync_api import sync_playwright

OVE_DIR = Path(__file__).resolve().parents[1]          # .../ove
INDEX = OVE_DIR / "public" / "index.html"              # собранный сайт
OUT_DIR = OVE_DIR / "public" / "docs" / "sketches"     # куда класть SVG
MIN_SIZE = 5 * 1024                                    # содержательный лист > 5 КБ

# та же логика, что в dlSvg() шаблона: outerHTML + развёртка var(--...) по body
JS_EXTRACT = """(id) => {
  const el = document.getElementById(id);
  if (!el) return null;
  const cs = getComputedStyle(document.body);
  return '<?xml version="1.0" encoding="UTF-8"?>\\n' +
    el.outerHTML.replace(/var\\((--[a-z0-9-]+)\\)/gi,
      (m, v) => cs.getPropertyValue(v).trim() || '#444');
}"""


def main() -> int:
    if not INDEX.is_file():
        print(f"нет собранного сайта: {INDEX} — сначала запусти ove/build.py", file=sys.stderr)
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    chromium_path = os.environ.get("CHROMIUM_PATH", "")
    launch_kw = {"headless": True}
    if chromium_path:
        launch_kw["executable_path"] = chromium_path

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kw)
        page = browser.new_page(viewport={"width": 1500, "height": 1000},
                                color_scheme="light")  # эскизы фиксируем в светлой теме
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(INDEX.as_uri(), wait_until="load")

        page.evaluate("show('bi')")
        # рендер вкладки синхронный, но дождёмся, пока все листы окажутся в DOM:
        # кнопок «⬇ SVG» ровно столько же, сколько листов svg.gsvg
        page.wait_for_function(
            "document.querySelectorAll('#s-bi svg.gsvg').length > 0 && "
            "document.querySelectorAll('#s-bi svg.gsvg').length === "
            "document.querySelectorAll('#s-bi [data-dl]').length"
        )
        if errors:
            print("JS-ошибки на странице:\n  " + "\n  ".join(errors), file=sys.stderr)
            browser.close()
            return 1

        # манифест «id листа → каноническое имя файла» — из самих кнопок скачивания
        mapping = page.evaluate(
            "Array.from(document.querySelectorAll('#s-bi [data-dl]'))"
            ".map(b => [b.dataset.dl, b.dataset.fn])"
        )
        if not mapping:
            print("на вкладке не нашлось ни одной кнопки [data-dl]", file=sys.stderr)
            browser.close()
            return 1

        failed = []
        report = []
        for svg_id, fname in mapping:
            s = page.evaluate(JS_EXTRACT, svg_id)
            if s is None:
                failed.append(f"{fname}: элемент #{svg_id} не найден")
                continue
            path = OUT_DIR / fname
            path.write_text(s, encoding="utf-8")

            # проверки: валидный XML, не осталось var(--...), файл содержательный
            try:
                ET.fromstring(s)
            except ET.ParseError as e:
                failed.append(f"{fname}: невалидный XML ({e})")
                continue
            if "var(--" in s:
                failed.append(f"{fname}: остались неразвёрнутые var(--...)")
                continue
            size = path.stat().st_size
            if size < MIN_SIZE:
                failed.append(f"{fname}: подозрительно мал ({size} байт < {MIN_SIZE})")
                continue
            report.append((fname, size))

        browser.close()

    print(f"сохранено в {OUT_DIR}:")
    for fname, size in report:
        print(f"  {fname:44s} {size / 1024:7.1f} КБ")
    print(f"итого: {len(report)} из {len(mapping)} листов")
    if failed:
        print("ПРОБЛЕМЫ:\n  " + "\n  ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
