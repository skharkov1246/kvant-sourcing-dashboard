#!/usr/bin/env python3
"""Сборка gt/public/plugs.html — свечи зажигания ГПУ: закупки, адресаты, эффективность.

Данные: gt/data/plugs_world.json (разведка мировых каналов + живая выгрузка Bitrix).
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    data = json.load(open(ROOT / "data/plugs_world.json", encoding="utf-8"))
    tpl = (ROOT / "site/plugs.template.html").read_text(encoding="utf-8")
    assert "__PLUGS_JSON__" in tpl, "нет плейсхолдера __PLUGS_JSON__"
    out = ROOT / "public/plugs.html"
    out.write_text(tpl.replace("__PLUGS_JSON__", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
    print(f"OK → {out} ({out.stat().st_size // 1024} КБ) | адресатов {len(data['addressees'])}, "
          f"закупок {len(data['deals'])}, позиций спроса {len(data['demand'])}")


if __name__ == "__main__":
    main()
