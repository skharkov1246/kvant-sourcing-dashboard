"""Набор перепроверки не должен противоречить сам себе.

Написано после трёх собственных ошибок за одну ночь 17–18.09.2026, каждую из
которых поймала проверка, а не внимательность: справка утверждала про поля
«Result, ТКП» то, что замер опроверг; измеритель покрытия не считал КП
поставщиков и печатал «сошлось 0»; вывод по LF16031 был построен на цене
ПОХОЖЕГО номера и указывал в противоположную сторону.

Здесь проверяется внутренняя связность того, что уходит владельцу: вердикт по
вилке обязан согласовываться с наличием цены, а «нечем проверить» не может
соседствовать с рекомендованной цифрой.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "gt/data/ship_reverify.json"
VERDICTS = {"ЗАНИЖЕНА", "ЗАВЫШЕНА", "ВЕРНА", "НЕ ПОДТВЕРЖДЕНА", "ДУБЛЬ",
            "НЕЧЕМ ПРОВЕРИТЬ"}
PRICED = {"ЗАНИЖЕНА", "ЗАВЫШЕНА", "ВЕРНА"}


def rows() -> list[dict]:
    if not SRC.exists():
        pytest.skip("набора перепроверки нет")
    d = json.loads(SRC.read_text(encoding="utf-8"))
    return d["rows"] if isinstance(d, dict) else d


def verdict(r: dict) -> str:
    v = (r.get("band_verdict") or "").upper()
    return next((k for k in VERDICTS if k in v), "")


def has_number(*vals) -> bool:
    """Есть ли в тексте хоть одно число, похожее на цену."""
    for v in vals:
        if re.search(r"\d[\d\s .,]*\d", str(v or "")):
            return True
    return False


def test_вердикт_из_закрытого_списка():
    bad = [r["pn"] for r in rows() if not verdict(r)]
    assert not bad, f"вердикт не опознан у строк: {bad}"


def test_у_каждой_строки_есть_чем_подтверждено():
    """Без этого поля строка — утверждение без основания."""
    bad = [r["pn"] for r in rows() if not (r.get("note") or "").strip()]
    assert not bad, f"нет поля «чем подтверждено»: {bad}"


def test_вердикт_с_ценой_опирается_на_источник():
    """ЗАНИЖЕНА, ЗАВЫШЕНА и ВЕРНА выносятся ТОЛЬКО по найденной цене.

    Иначе вердикт — догадка, а выглядит как замер.
    """
    bad = []
    for r in rows():
        if verdict(r) not in PRICED:
            continue
        if not has_number(r.get("price_low"), r.get("price_high"),
                          r.get("price_authorized"), r.get("recommended")):
            bad.append(r["pn"])
        elif not (r.get("price_source") or "").strip():
            bad.append(f'{r["pn"]} (нет price_source)')
    assert not bad, f"вердикт по цене без цены или без источника: {bad}"


def test_нечем_проверить_не_даёт_рекомендованной_цифры():
    """Самая опасная связка: «проверить нечем» рядом с конкретной ценой.

    Такая строка читается как подтверждённая, хотя подтверждения нет.
    Рекомендация при этом остаётся полезной, если она говорит, что закладывать
    нечего или чего не хватает, — поэтому запрещено именно ЧИСЛО.
    """
    bad = []
    for r in rows():
        if verdict(r) != "НЕЧЕМ ПРОВЕРИТЬ":
            continue
        rec = str(r.get("recommended") or "")
        # разрешаем числа, которые описывают НАШУ вилку или объём, а не
        # рекомендованный уровень: их вводят словами «наша вилка», «по строке»
        if re.search(r"\d[\d\s .,]*\d", rec) and not re.search(
                r"наша вилка|нечем|не опроверг|не подтвержд|по строке|закладывать нечего"
                r"|проверить нечего|для порядка величин", rec, re.I):
            bad.append(r["pn"])
    assert not bad, ("«нечем проверить» с конкретной рекомендованной ценой "
                     f"без оговорки: {bad}")


def test_скептики_списком_и_с_полем_holds():
    bad = []
    for r in rows():
        sk = r.get("skeptics")
        if sk is None or not isinstance(sk, list):
            bad.append(r["pn"])
            continue
        for s in sk:
            if not isinstance(s, dict) or "holds" not in s:
                bad.append(f'{r["pn"]} (скептик без holds)')
    assert not bad, f"поле проверки на опровержение испорчено: {bad}"


def test_артикулы_не_дублируются():
    """Дубликат удваивает строку в документе и в любой сумме по нему."""
    seen, dup = set(), []
    for r in rows():
        k = re.sub(r"[^A-Z0-9]", "", str(r.get("pn") or "").split("(")[0].upper())
        if k in seen:
            dup.append(r["pn"])
        seen.add(k)
    assert not dup, f"дубликаты артикулов: {dup}"

def test_строка_говорит_о_том_же_предмете_что_заявка():
    """Разбор обязан быть про ТОТ предмет, который назван в заявке.

    Оплачено ошибкой на VS-4-57 и VS-6-82: предмет строки был выведен из
    префикса артикула («VS» → бренд Vibrostop → виброизолятор), хотя в заявке
    прямо стоят изготовитель Victory Energy, модель Vision, наименование
    «Электрод розжига» и категория «зажигание и свечи». Строки «виброизолятор»
    в заявке нет ни одной, а вердикт «ЗАВЫШЕНА» с уровнем 30–60 USD/шт был
    вынесен именно по классу виброопор. Вердикт не о том предмете опаснее
    отсутствия вердикта.
    """
    src = ROOT / "gt/data/ship_lukoil.json"
    if not src.exists():
        pytest.skip("сводки заявки нет")
    ask = {re.sub(r"[^A-Z0-9]", "", str(r.get("pn") or "").upper()): r
           for r in json.loads(src.read_text(encoding="utf-8"))["rows"]}
    stop = {"для", "и", "в", "с", "на", "по", "от", "до", "сборе", "шт", "мм", "модель"}

    def words(text: str) -> set[str]:
        return {w for w in re.findall(r"[а-яёa-z0-9]+", (text or "").lower())
                if len(w) > 3 and w not in stop}

    bad = []
    for r in rows():
        key = re.sub(r"[^A-Z0-9]", "", str(r.get("pn") or "").split("(")[0].upper())
        row = ask.get(key)
        if row is None:          # номер записан с пояснением — сверять нечего
            continue
        want = words(row.get("name"))
        have = words(" ".join(str(r.get(k) or "") for k in
                              ("what_it_is", "note", "volume_head", "recommended")))
        if want and not (want & have):
            bad.append(f'{r["pn"]}: в заявке «{(row.get("name") or "")[:40]}», '
                       f"а в разборе это слово не встречается")
    assert not bad, f"разбор не о том предмете, что заявка: {bad}"


DOMAIN = re.compile(r"[a-z0-9][a-z0-9-]*\.(com|net|org|ru|de|co\.uk|cz|pl|in|cn|eu|at|io|uk|store)\b",
                    re.I)
NO_LINK = "ССЫЛКА НЕ СОХРАНЕНА"


def test_вердикт_по_цене_называет_страницу():
    """У найденной цены должна быть названа страница, а не только проза.

    Оплачено разбором семи строк Allen-Bradley и Pepperl+Fuchs: шесть выводов из
    семи не устояли, и восстанавливать, какую страницу открывали, приходилось из
    другого набора. Три цифры оказались не с карточек, а возвращёнными из уже
    опровергнутых значений — при названной ссылке это было бы видно сразу.

    Где ссылка честно утеряна, в источнике стоит явная пометка: она хуже ссылки,
    но лучше умолчания — её видно в документе.
    """
    bad = []
    for r in rows():
        if verdict(r) not in PRICED:
            continue
        src = r.get("price_source") or ""
        if not DOMAIN.search(src) and NO_LINK not in src:
            bad.append(r["pn"])
    assert not bad, f"вердикт по цене без названной страницы и без пометки об утере: {bad}"


OWN_NUMBERS = ("qty", "lo", "hi", "expo", "band_lo", "band_hi", "exposure")


def test_строка_не_хранит_своих_количеств_и_вилок():
    """Количество, вилка и экспозиция живут в сводке заявки, и только там.

    Оплачено разбором трёх строк: они несли выдуманные числа — «экспозиция
    33 600 USD» при настоящих 4 410, «21 250» при 892, «56 250» при 788, — и
    вердикт «завышена» выносился против вилки, которой в данных нет. Пока
    источник этих чисел один, такое в документ попасть не может; поле в строке
    перепроверки снова откроет эту дверь.
    """
    bad = [f'{r["pn"]}.{f}' for r in rows() for f in OWN_NUMBERS if f in r]
    assert not bad, ("количество, вилка и экспозиция берутся из gt/data/ship_lukoil.json, "
                     f"а не из строки перепроверки: {bad}")
