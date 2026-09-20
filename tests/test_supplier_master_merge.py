"""Сведение поставщиков: статус обязан говорить, чем слияние ПРОВЕРЕНО.

История. Первая версия классификации ставила inferred всему, у чего домен был
хотя бы с одной стороны. Замер показал цену: из 977 слияний доменом проверяемы
94, а 883 — нет, потому что у досье ГТУ сайта нет ни у одной из 1 359 записей.
Восемьсот восемьдесят три слияния уехали бы в базу вторым по силе статусом,
хотя их никто не проверял. ТЗ §5.3: candidate и inferred никогда не повышаются
автоматически — значит и присваиваться должны по тому, что реально есть.

Вторая находка того же разбора: 1 840 из 1 847 «кандидатов» оказались
одиночками — сущностями из одного источника, где сливать попросту нечего.
Называть их кандидатами на слияние неверно: разряд single отделён.

Корпуса придуманы здесь же (правило 18 CLAUDE.md).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for путь in ("scripts", "pnw/tools", "library"):
    sys.path.insert(0, str(ROOT / путь))

SPEC = importlib.util.spec_from_file_location(
    "load_supplier_master", ROOT / "library" / "load_supplier_master.py")
lm = importlib.util.module_from_spec(SPEC)
sys.modules["load_supplier_master"] = lm
SPEC.loader.exec_module(lm)


def свод(наборы):
    с, оч = lm.свести(наборы)
    return {e.статус: e for e in с}, с, оч


def test_домен_совпал_у_двух_источников_это_verified():
    наборы = {
        "а": [("pridumannyzavod", "pridumanny.example.com")],
        "б": [("pridumannyzavodgmbh", "pridumanny.example.com")],
    }
    _, с, _ = свод(наборы)
    assert len(с) == 1, "один домен — одна сущность"
    assert с[0].статус == "verified", с[0].причина
    assert len(с[0].имена) == 2, "оба написания сохранены, ни одно не перезаписано"


def test_имя_совпало_а_домена_нет_у_части_это_candidate():
    """Случай досье ГТУ: у одной стороны сайт есть, у другой нет — проверить нечем."""
    наборы = {
        "pnw": [("pridumannyzavod", "pridumanny.example.com")],
        "досье": [("pridumannyzavod", "")],
    }
    _, с, _ = свод(наборы)
    assert len(с) == 1
    assert с[0].статус == "candidate", (
        f"статус {с[0].статус}: слияние без проверки доменом не может быть сильнее")
    assert "не у всех" in с[0].причина


def test_имя_совпало_домена_нет_ни_у_кого_это_candidate():
    наборы = {"а": [("pridumannyzavod", "")], "б": [("pridumannyzavod", "")]}
    _, с, _ = свод(наборы)
    assert len(с) == 1 and с[0].статус == "candidate"
    assert "ни у одного" in с[0].причина


def test_один_источник_это_не_кандидат_на_слияние():
    наборы = {"а": [("pridumannyzavod", "pridumanny.example.com")]}
    _, с, _ = свод(наборы)
    assert с[0].статус == "single", "одиночку нельзя звать кандидатом: сливать не с чем"


def test_одно_имя_разные_домены_не_сливается():
    """Спорное имя обязано уйти в очередь, а не склеить две компании."""
    наборы = {
        "а": [("pridumanny", "odin.example.com")],
        "б": [("pridumanny", "dva.example.com")],
    }
    _, с, оч = свод(наборы)
    assert len(с) == 2, "две компании с одним именем не должны слиться"
    assert оч, "спорное имя обязано попасть в очередь проверки"
    assert оч[0]["kind"] == "ambiguous_match"


def test_статуса_inferred_не_бывает_и_это_осознанно():
    """Ветка недостижима: домен у всех и совпал → слияние уже по домену."""
    наборы = {
        "а": [("pridumannyzavod", "pridumanny.example.com")],
        "б": [("pridumannyzavodltd", "pridumanny.example.com")],
        "в": [("drugoy", "drugoy.example.com")],
    }
    _, с, _ = свод(наборы)
    assert all(e.статус != "inferred" for e in с)


def test_номер_собирается_и_проверяется_контрольной_цифрой():
    import re
    from kv_number import check as проверить_деталь
    n = lm.номер(1)
    assert re.fullmatch(r"KV-S-\d{6}-\d", n), n
    # контрольная цифра та же, что у номеров деталей: одна функция на репозиторий
    деталь = n.replace("KV-S-", "KV-")
    assert проверить_деталь(деталь)[0], "контрольная цифра расходится с деталями"
    assert lm.номер(42, "G").startswith("KV-G-")


def test_гейты_отменяют_запись_когда_сведение_не_сработало():
    """Правило меряют до применения: не сошлось — записи не будет."""
    пусто = [type("E", (), {"домены": set(), "статус": "single"})() for _ in range(10)]
    провалы = lm.гейты(пусто, строк=10)
    assert провалы, "гейты обязаны заметить, что не схлопнулось ничего"
    assert any("схлопнулось" in p for p in провалы)


def test_на_живых_реестрах_цифры_держатся():
    """Порядок величин замера 20.09.2026 закреплён: уплывёт — разбираться, а не править порог."""
    from supplier_registry_overlap import читать
    с, _ = lm.свести(читать())
    слияния = [e for e in с if e.статус != "single"]
    verified = [e for e in слияния if e.статус == "verified"]
    assert len(с) > 2500, f"сущностей {len(с)}, замер давал 2 817"
    assert len(слияния) > 800, f"слияний {len(слияния)}, замер давал 977"
    assert len(verified) > 50, (
        f"проверяемых доменом слияний {len(verified)}, замер давал 94 — "
        "если обвалилось, домены перестали читаться")
