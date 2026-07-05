#!/usr/bin/env python3
"""Извлечение спроса заказчика и прайса конкурента из Excel в JSON.

Вход (кладутся рядом или путь через аргумент):
  - лимиты ИСМ ПАО «ГМК «Норильский никель», ЗФ: СПГТ — *.xlsb (по годам);
  - прейскурант цен конкурента (АО «Машиностроительный холдинг») — *.xlsx.
Выход:
  - zip/data/demand.json            — потребление/бюджет по позициям и годам;
  - zip/data/competitor_prices.json — прайс конкурента (для сравнения с афтермаркетом).

Запуск: python zip/tools/parse_demand.py <dir-с-файлами>
Файлы обновляются раз в год (новый лимит) — перегенерировать и закоммитить JSON.
"""
import json, re, sys, warnings
from pathlib import Path
import pandas as pd
warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
BRANDS = ["Atlas Copco", "Epiroc", "Paus", "Resemin", "Sandvik", "Furukawa",
          "Montabert", "Boart", "Normet", "Deutz", "Doosan", "Tamrock"]
CURMAP = {"XEUR": "EUR", "XCNY": "CNY", "XUSD": "USD", "RUB": "RUB"}


def num(x):
    v = pd.to_numeric(x, errors="coerce")
    return None if pd.isna(v) else float(v)


def brand_of(name):
    for b in BRANDS:
        if b.lower() in str(name).lower():
            return b
    return None


def parse_limits(path, year):
    xl = pd.ExcelFile(path, engine="pyxlsb")
    sh = [s for s in xl.sheet_names if "Лимит" in s][0]
    raw = xl.parse(sh, header=None)
    hr = next(i for i in range(6) if raw.iloc[i].astype(str).str.contains("Ключ").any())
    df = xl.parse(sh, header=hr).dropna(how="all")
    df.columns = [str(c).strip() for c in df.columns]
    df = df[df["Наименование"].notna()]
    col = lambda *ks: next((c for c in df.columns if any(k in c for k in ks)), None)
    c_plan, c_price, c_fact = col("Плановая стоимость"), col("Цена"), col("Факт")
    c_val, c_cur = col("В валюте без НДС"), col("Валюта")
    out = []
    for _, r in df.iterrows():
        nm = str(r["Наименование"]).strip()
        out.append({
            "year": year, "customer": "ГМК Норильский никель",
            "plant": str(r.get("Наименование завода", "")).strip(),
            "oem": brand_of(nm), "name": nm,
            "enn": None if pd.isna(r.get("ЕНН")) else int(r["ЕНН"]),
            "qty": num(r.get("Кол-во")),
            "currency": CURMAP.get(str(r.get(c_cur)).strip(), str(r.get(c_cur)).strip()),
            "import_value": num(r.get(c_val)),
            "plan_cost_rub": num(r.get(c_plan)) if c_plan else None,
            "price_rub": num(r.get(c_price)) if c_price else None,
            "fact_rub": num(r.get(c_fact)) if c_fact else None,
            "supplier": None if pd.isna(r.get("Поставщик")) else str(r["Поставщик"]).strip(),
            "delivery": None if pd.isna(r.get("Дата поставки")) else str(r["Дата поставки"]).strip(),
        })
    return out


def parse_pricelist(path):
    df = pd.ExcelFile(path, engine="openpyxl").parse("Лист1", header=16)
    df.columns = [str(c).strip() for c in df.columns]
    col = lambda *ks: next((c for c in df.columns if any(k in c.lower() for k in ks)), None)
    c_name, c_cat, c_model = col("наимен"), col("каталож"), col("модель")
    c_price, c_unit, c_ens = col("цена"), col("ед. изм", "ед.изм"), col("енс")
    df = df[df[c_name].notna()]
    out = []
    for _, r in df.iterrows():
        p = num(r.get(c_price))
        nm = str(r[c_name]).strip()
        # пропускаем строку-нумератор колонок (1|2|3|…) и пустые
        if p is None or re.fullmatch(r"\d+([.,]\d+)?", nm) or len(nm) < 3:
            continue
        out.append({
            "source": "Машиностроительный холдинг",
            "ens": None if pd.isna(r.get(c_ens)) else str(r[c_ens]).strip(),
            "model": None if pd.isna(r.get(c_model)) else str(r[c_model]).strip(),
            "catalog_no": None if pd.isna(r.get(c_cat)) else str(r[c_cat]).strip(),
            "name": str(r[c_name]).strip(),
            "unit": None if pd.isna(r.get(c_unit)) else str(r[c_unit]).strip(),
            "price_rub": p,
        })
    return out


def main(src):
    src = Path(src)
    demand = []
    for f in src.glob("*.xlsb"):
        m = re.search(r"(20\d{2})", f.name)
        year = int(m.group(1)) if m else 0
        try:
            demand += parse_limits(f, year)
            print(f"лимиты {year}: {f.name}")
        except Exception as e:
            print(f"пропуск {f.name}: {e}")
    compet = []
    for f in src.glob("*.xlsx"):
        try:
            compet += parse_pricelist(f)
            print(f"прайс: {f.name} → {len(compet)} позиций")
        except Exception as e:
            print(f"пропуск {f.name}: {e}")
    (ROOT / "data" / "demand.json").write_text(json.dumps(demand, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / "data" / "competitor_prices.json").write_text(json.dumps(compet, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"demand.json: {len(demand)} · competitor_prices.json: {len(compet)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
