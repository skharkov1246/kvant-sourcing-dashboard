# -*- coding: utf-8 -*-
"""Внутренний номер КВАНТ: генерация, проверка, присвоение.

ФОРМАТ:  KV-NNNNNN-C
  KV      — постоянный префикс. Нужен, чтобы номер узнавался в чужих документах:
            в письме поставщику, в счёте, в переписке с заказчиком.
  NNNNNN  — шесть цифр, выдаются подряд, СМЫСЛА НЕ НЕСУТ. Ёмкость 999 999.
  C       — контрольная цифра (алгоритм Луна). Ловит одиночные опечатки и
            перестановку соседних цифр — до того, как по неверному номеру
            закажут не ту деталь.

ПОЧЕМУ НОМЕР НЕЗНАЧАЩИЙ. Значащий номер (вида KV-SBS-LTS-010.001) кодирует
одну классификацию и ломается, когда она меняется:
  • деталь применяется на трёх машинах — в номер помещается только одна;
  • после ребрендинга Atlas Copco → Epiroc номер бренда меняется, а деталь та же;
  • ролик, изготовленный для ЛУКОЙЛа, завтра нужен ТАИФ-НК — при номере,
    привязанном к заказчику, появится второй номер на ту же деталь;
  • сегменты кода рано или поздно переполняются, и приходится ломать схему.
Номер должен быть вечным и неизменным, потому что он уходит в договоры,
чертежи и заказы. Всё, что может измениться, живёт в атрибутах, а не в номере.

ПРАВИЛО РЕВИЗИЙ. Изменилась деталь, но она взаимозаменяема со старой — тот же
номер, меняется буква ревизии (KV-000123-4 ревизия B). Взаимозаменяемости нет —
это ДРУГАЯ деталь и другой номер.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "KV"
WIDTH = 6


def luhn(digits):
    """Контрольная цифра по алгоритму Луна."""
    s, alt = 0, True
    for ch in reversed(str(digits)):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        s += d
        alt = not alt
    return (10 - s % 10) % 10


def make(seq):
    """Собрать номер из порядкового числа."""
    body = str(seq).zfill(WIDTH)
    return f"{PREFIX}-{body}-{luhn(body)}"


def check(number):
    """Проверить номер. Возвращает (годен, причина)."""
    m = re.fullmatch(rf"{PREFIX}-(\d{{{WIDTH}}})-(\d)", str(number).strip().upper())
    if not m:
        return False, "формат не KV-NNNNNN-C"
    body, c = m.group(1), int(m.group(2))
    if luhn(body) != c:
        return False, f"контрольная цифра неверна (ожидалась {luhn(body)})"
    return True, "ок"


def seq_of(number):
    ok, _ = check(number)
    return int(number.split("-")[1]) if ok else None


# ── реестр: одна строка = одна деталь, номер выдан навсегда ──────────────
REG = ROOT / "pnw" / "data" / "kv_registry.json"


def load_registry():
    if REG.exists():
        return json.loads(REG.read_text(encoding="utf-8"))
    return {"updated": "", "next_seq": 1, "items": []}


def save_registry(reg):
    REG.parent.mkdir(parents=True, exist_ok=True)
    REG.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    print("Примеры номеров:")
    for s in (1, 42, 752, 12433, 999999):
        n = make(s)
        print(f"  порядковый {s:>7} → {n}   проверка: {check(n)[1]}")
    print("\nЛовля опечаток (контрольная цифра работает):")
    good = make(752)
    bad1 = good.replace("0752", "0753")            # одна цифра
    body = good.split("-")[1]
    bad2 = f"{PREFIX}-{body[:3]}{body[4]}{body[3]}{body[5]}-{good[-1]}"  # перестановка
    for b, why in ((bad1, "изменена одна цифра"), (bad2, "переставлены соседние")):
        ok, msg = check(b)
        print(f"  {b}  ({why}) → {'ПРОПУЩЕНА' if ok else 'ОТКЛОНЕНА: ' + msg}")
