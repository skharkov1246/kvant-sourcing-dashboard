"""Файл освобождения места: ничего не выполняет сам.

Удаление данных и снятие индексов — решение владельца (CLAUDE.md, «стоять
нельзя»: исключение про удаление данных). Файл собирает то, что нужно решать:
имена, размеры и цену каждого шага. Если он когда-нибудь начнёт применяться
прогоном миграций, первый же прогон снимет индексы без спроса.
"""
from __future__ import annotations

import re
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent.parent
ФАЙЛ = КОРЕНЬ / "library/supabase/osvobodit_mesto.sql"


def test_все_опасные_команды_закомментированы():
    """Ни одной исполняемой строки: файл читают, а не запускают."""
    строки = ФАЙЛ.read_text(encoding="utf-8").splitlines()
    живые = [s for s in строки if s.strip() and not s.strip().startswith("--")]
    assert живые == [], f"в файле есть исполняемые строки: {живые[:3]}"


def test_файл_не_подхватывается_прогоном_миграций():
    """zip-db.yml применяет схемы подряд; попасть в этот список нельзя."""
    yml = (КОРЕНЬ / ".github/workflows/zip-db.yml").read_text(encoding="utf-8")
    assert "osvobodit_mesto" not in yml, "файл попал в список применяемых миграций"


def test_названы_и_размер_и_цена_каждого_шага():
    """Решение принимают по числам и по тому, чем придётся заплатить."""
    текст = ФАЙЛ.read_text(encoding="utf-8")
    for индекс in ("lib_demand_fts", "lib_demand_pnkey", "lib_knowledge_fts",
                   "lib_suppliers_fts"):
        assert индекс in текст, f"не назван индекс {индекс}"
    assert re.search(r"65 МБ", текст) and re.search(r"24 МБ", текст), "нет размеров"
    assert "ЧЕМ ПРИДЁТСЯ ЗАПЛАТИТЬ" in текст, "названа выгода без цены"
    assert "storage.objects" in текст, "не сказано, чего не трогать"


def test_массового_удаления_пометок_в_файле_нет():
    """Пометка снимается одной командой, а удалённую строку не вернуть."""
    текст = ФАЙЛ.read_text(encoding="utf-8").lower()
    assert "delete from lib_row_junk" not in текст.replace("-- ", ""), \
        "в файле лежит готовое массовое удаление пометок"
