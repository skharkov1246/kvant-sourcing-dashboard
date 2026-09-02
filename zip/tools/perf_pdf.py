#!/usr/bin/env python3
"""HTML → PDF для zip/orders/ПЕРФОРАТОРЫ-СОРСИНГ.html (Chromium через Playwright, A4 альбом)."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "orders" / "ПЕРФОРАТОРЫ-СОРСИНГ.html"
DST = ROOT / "orders" / "ПЕРФОРАТОРЫ-СОРСИНГ.pdf"


async def main():
    from playwright.async_api import async_playwright
    errors = []
    async with async_playwright() as p:
        exe = Path("/opt/pw-browsers/chromium")  # предустановленный Chromium в контейнере
        b = await p.chromium.launch(executable_path=str(exe) if exe.exists() else None)
        pg = await b.new_page(viewport={"width": 1400, "height": 900})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        await pg.goto(SRC.as_uri())
        await pg.wait_for_load_state("networkidle")
        ov = await pg.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        await pg.screenshot(path=str(ROOT / "orders" / ".perf_preview.png"), full_page=False)
        await pg.pdf(path=str(DST), format="A4", landscape=True, print_background=True,
                     margin={"top": "10mm", "bottom": "10mm", "left": "10mm", "right": "10mm"})
        await b.close()
    print(f"PDF: {DST.name} {DST.stat().st_size:,} байт | JS-ошибок: {len(errors)} | гориз. оверфлоу: {ov}")
    for e in errors[:5]:
        print("  ", e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
