"""Бренд машины → компания-владелец: кто стоит за маркой на шильдике.

ЗАЧЕМ. Вопрос владельца 25.09.2026: «Ты отличаешь материнские компании от бренда
конкретных машин?» В справочнике рядов бренды и их холдинги лежали вперемешку:
Solar Turbines и Caterpillar, Warman и The Weir Group, Rosemount и Emerson —
отдельными записями без связи. Теперь у записи бренда в dict/model_series.json
есть владелец (owner), вид записи (role) и история (owner_history), а холдинги,
чьих рядов у нас нет, заведены в список owners. Здесь — одно правило чтения
этих полей для карточки бренда на портале («входит в Caterpillar», «бренды
группы: …») и проверка целостности, которой пользуется тест.

ПРАВИЛО.
  · Бренд с владельцем не сливается: Solar Turbines остаётся своим ключом,
    владелец — ссылка owner=caterpillar. Ключи и написания не меняются.
  · role — закрытый список: «бренд» (марка на шильдике), «владелец» (холдинг,
    чьё имя на шильдике не стоит или стоит рядом), «бывший владелец» (элемент
    owner_history).
  · owner — ключ записи (brands или owners) либо имя компании, если записи нет.
    Ключ отличается от имени видом: только строчная латиница и цифры.
  · Владение бывает и у ряда, когда марка ряда ушла к другому владельцу, чем
    бренд записи (промышленные RB211 и Avon Rolls-Royce — у Siemens Energy).
  · Цепочка владения обходится вверх (Wilden → PSG → Dover); цикл — ошибка
    справочника, обход его не зацикливает.
"""
from __future__ import annotations

import functools
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ФАЙЛ = ROOT / "dict" / "model_series.json"

БРЕНД, ВЛАДЕЛЕЦ, БЫВШИЙ = "бренд", "владелец", "бывший владелец"
РОЛИ = (БРЕНД, ВЛАДЕЛЕЦ, БЫВШИЙ)
_КЛЮЧ = re.compile(r"[a-z0-9]+")


def это_ключ(значение) -> bool:
    """owner — ключ записи или имя компании: ключ пишется строчной латиницей без пробелов."""
    return bool(_КЛЮЧ.fullmatch(str(значение or "")))


@functools.lru_cache(maxsize=4)
def _файл(путь: str | None = None) -> dict:
    with open(путь or ФАЙЛ, encoding="utf-8") as f:
        return json.load(f)


def записи(данные: dict | None = None) -> dict:
    """Ключ → запись: бренды справочника рядов и записи владельцев."""
    данные = данные if данные is not None else _файл()
    out = {}
    for р in list(данные.get("brands", [])) + list(данные.get("owners", [])):
        out[р["brand_key"]] = р
    return out


def _имя(значение, зап: dict) -> str:
    р = зап.get(значение)
    return (р.get("name") or значение) if р else str(значение)


def _связь(owner, поля: dict, зап: dict) -> dict:
    ключ = owner if это_ключ(owner) else None
    out = {"key": ключ, "name": _имя(owner, зап) if ключ else str(owner),
           "role": поля.get("role") or ВЛАДЕЛЕЦ}
    for п in ("owner_since", "owner_until", "owner_source", "owner_note", "co_owners"):
        if поля.get(п) not in (None, "", []):
            out[п.replace("owner_", "")] = поля[п]
    return out


def владелец(ключ: str, данные: dict | None = None) -> dict | None:
    """Нынешний владелец бренда: {key, name, role, since?, source, note?} или None.

    key — ключ записи владельца (None, если он назван только именем)."""
    зап = записи(данные)
    р = зап.get(ключ)
    if not р or not р.get("owner"):
        return None
    return _связь(р["owner"], {**р, "role": ВЛАДЕЛЕЦ}, зап)


def цепочка_владельцев(ключ: str, данные: dict | None = None) -> list[dict]:
    """Владельцы вверх по цепочке: Wilden → [PSG, Dover]. Цикл обрывается."""
    зап, out, видели = записи(данные), [], {ключ}
    тек = ключ
    while True:
        в = владелец(тек, данные)
        if not в:
            return out
        out.append(в)
        if not в["key"] or в["key"] in видели:
            return out
        видели.add(в["key"])
        тек = в["key"]
        if тек not in зап:
            return out


def бывшие_владельцы(ключ: str, данные: dict | None = None) -> list[dict]:
    """История: [{key, name, role: «бывший владелец», since?, until?, source}]."""
    зап = записи(данные)
    р = зап.get(ключ) or {}
    return [_связь(h["owner"], h, зап) for h in р.get("owner_history", []) if h.get("owner")]


def бренды_владельца(ключ: str, данные: dict | None = None, вглубь: bool = True) -> list[dict]:
    """Бренды группы: записи, чей владелец — ключ (вглубь — и через промежуточных
    владельцев: Dover → PSG → Wilden). [{key, name, role, via?, since?}] по имени."""
    зап = записи(данные)
    дети: dict[str, list[str]] = {}
    for к, р in зап.items():
        if р.get("owner") and это_ключ(р["owner"]):
            дети.setdefault(р["owner"], []).append(к)
    out, видели, очередь = [], {ключ}, [(ключ, None)]
    while очередь:
        родитель, через = очередь.pop(0)
        for к in дети.get(родитель, []):
            if к in видели:
                continue
            видели.add(к)
            р = зап[к]
            э = {"key": к, "name": р.get("name") or к, "role": р.get("role") or БРЕНД}
            if р.get("owner_since"):
                э["since"] = р["owner_since"]
            if через:
                э["via"] = через
            out.append(э)
            if вглубь:
                очередь.append((к, к if через is None else через))
    return sorted(out, key=lambda э: (э["role"] != БРЕНД, э["name"].lower()))


def ряды_владельца(ключ: str, данные: dict | None = None) -> list[dict]:
    """Ряды чужих брендов, перешедшие к владельцу: [{brand, series_id, series, since?}]."""
    данные = данные if данные is not None else _файл()
    out = []
    for б in данные.get("brands", []):
        for s in б.get("series", []):
            if s.get("owner") == ключ:
                э = {"brand": б["brand_key"], "series_id": s["id"], "series": s.get("series")}
                if s.get("owner_since"):
                    э["since"] = s["owner_since"]
                out.append(э)
    return out


def подпись(ключ: str, данные: dict | None = None) -> str | None:
    """Строка для карточки бренда: «входит в Caterpillar с 1981» или None."""
    в = владелец(ключ, данные)
    if not в:
        return None
    return f"входит в {в['name']}" + (f" с {в['since']}" if в.get("since") else "")


# ─── целостность ────────────────────────────────────────────────────────────

def _проверить_связь(где: str, поля: dict, зап: dict, ошибки: list, роль: str):
    owner = поля.get("owner")
    if not owner:
        return
    if это_ключ(owner) and owner not in зап:
        ошибки.append(f"{где}: владелец «{owner}» — ключ без записи")
    src = поля.get("owner_source")
    if not (isinstance(src, str) and src.startswith(("http://", "https://"))):
        ошибки.append(f"{где}: у связи с «{owner}» нет owner_source")
    for п in ("owner_since", "owner_until"):
        if п in поля and not (isinstance(поля[п], int) and 1800 <= поля[п] <= 2100):
            ошибки.append(f"{где}: {п} не год")
    if isinstance(поля.get("owner_since"), int) and isinstance(поля.get("owner_until"), int) \
            and поля["owner_since"] > поля["owner_until"]:
        ошибки.append(f"{где}: owner_since позже owner_until")
    if роль == БЫВШИЙ and поля.get("role") != БЫВШИЙ:
        ошибки.append(f"{где}: элемент истории не «{БЫВШИЙ}»")


def проверить(данные: dict | None = None) -> list[str]:
    """Ошибки справочника владения: пустой список — всё цело."""
    данные = данные if данные is not None else _файл()
    зап, ошибки = {}, []
    for р in list(данные.get("brands", [])) + list(данные.get("owners", [])):
        if р["brand_key"] in зап:
            ошибки.append(f"{р['brand_key']}: ключ дважды (brands и owners)")
        зап[р["brand_key"]] = р
    for р in данные.get("brands", []):
        if р.get("role") != БРЕНД:
            ошибки.append(f"{р['brand_key']}: у записи brands role не «{БРЕНД}»")
    for р in данные.get("owners", []):
        if р.get("role") != ВЛАДЕЛЕЦ:
            ошибки.append(f"{р['brand_key']}: у записи owners role не «{ВЛАДЕЛЕЦ}»")
        if р.get("series"):
            ошибки.append(f"{р['brand_key']}: у владельца есть ряды")
    for к, р in зап.items():
        if р.get("role") not in РОЛИ:
            ошибки.append(f"{к}: role вне списка")
        if р.get("owner") == к:
            ошибки.append(f"{к}: владеет сам собой")
        _проверить_связь(к, р, зап, ошибки, ВЛАДЕЛЕЦ)
        for i, h in enumerate(р.get("owner_history", [])):
            _проверить_связь(f"{к}.owner_history[{i}]", h, зап, ошибки, БЫВШИЙ)
        for s in р.get("series", []):
            _проверить_связь(f"{к}/{s.get('id')}", s, зап, ошибки, ВЛАДЕЛЕЦ)
            for i, h in enumerate(s.get("owner_history", [])):
                _проверить_связь(f"{к}/{s.get('id')}.owner_history[{i}]", h, зап, ошибки, БЫВШИЙ)
    # Цикл владения: обход вверх от каждой записи не возвращается в неё.
    for к in зап:
        видели, тек = {к}, зап[к].get("owner")
        while тек and это_ключ(тек) and тек in зап:
            if тек in видели:
                ошибки.append(f"{к}: цикл владения через «{тек}»")
                break
            видели.add(тек)
            тек = зап[тек].get("owner")
    return ошибки
