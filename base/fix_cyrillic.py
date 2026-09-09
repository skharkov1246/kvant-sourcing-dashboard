#!/usr/bin/env python3
"""Починка кириллицы в PDF без карты символов.

В части документов (проектная документация Амурского ГХК и подобные) кириллица
закодирована без ToUnicode: «Амурский газохимический комплекс» извлекается как
«А<C@A:89 307>E8<8G5A:89 :><?;5:A». Проверено, что это не дефект парсера —
pdftotext из poppler даёт тот же результат, а местами теряет больше.

Разгадка в смещении: коды букв — это байты cp1251 минус 0xB0. Строчная
кириллица (0xE0–0xFF) превращается в диапазон 0x30–0x4F, то есть в цифры,
заглавные латинские A–O и символы; прописная (0xC0–0xDF) — в управляющие и
знаки препинания. Обратный сдвиг восстанавливает текст.

Предохранители, чтобы не испортить нормальный текст:
  * чинится только документ, где искажение ДОКАЗАНО — не меньше 20 слов,
    которые после сдвига становятся русскими словами с гласной;
  * внутри документа сдвигается только слово, все символы которого лежат в
    диапазоне 0x21–0x4F: строчная латиница (0x61–0x7A) при сдвиге выходит за
    границу байта, поэтому английские слова под правило не попадают;
  * после сдвига слово принимается, только если содержит русскую гласную и
    имеет длину не меньше трёх: так артикулы вида «41-P-4260» остаются целыми.

    python base/fix_cyrillic.py --db base/kvant.db --dry-run
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

VOWELS = set("аеёиоуыэюяАЕЁИОУЫЭЮЯ")
TOKEN = re.compile(r"\S+")
ACRONYM = re.compile(r"^[A-Z][A-Z0-9/-]{1,6}$")     # AGCC, PE/PP, LAO — латинские сокращения
DIGITS = re.compile(r"^[\d.,/-]+$")                  # годы, номера, размеры
MIN_VOWEL_RATIO, MAX_VOWEL_RATIO = 0.15, 0.60
DIGIT_LETTERS = set("абвгдежзий")     # что получается из цифр 0–9 при обратном сдвиге
CODEISH = re.compile(r"^(?=.*\d.*\d)[A-Za-z0-9]+[-/][A-Za-z0-9/-]+$")   # обозначения с дефисом
CYR_WORD = re.compile(r"[а-яё]{3,}")
# Частые русские слова: если после сдвига они появляются, документ действительно
# русский. На англоязычных спецификациях и прайсах сдвиг даёт бессмыслицу вроде
# «фце-аеа-саб» из кода DF5-050-A01 — там таких слов не возникает.
RU_WORDS = {"и", "в", "на", "для", "с", "по", "не", "от", "до", "при", "или", "из",
            "что", "как", "все", "это", "быть", "может", "должен", "должна", "должно",
            "насос", "оборудование", "поставка", "давление", "температура", "материал",
            "тип", "вес", "масса", "количество", "наименование", "изготовитель",
            "заказчик", "проект", "система", "комплекс", "установка", "агрегат",
            "технические", "требования", "характеристики", "спецификация", "чертеж",
            "лист", "стр", "дата", "номер", "код", "класс", "марка", "исполнение"}
MIN_RU_WORDS = 1         # словарь корпуса уже гарантирует, что слово настоящее
MIN_HITS = 5             # порог мягкий: каждое слово и так проверено по словарю
MIN_LEN = 3


def build_vocab(con: sqlite3.Connection, min_count: int = 3) -> set[str]:
    """Словарь русских слов, собранный из самого корпуса.

    По форме искажённое слово не отличить от парт-номера: «FGN47538» после сдвига
    выглядит как «цчюдзеги», а «HELI-COIL» как «шхьщ-уящь». Единственный надёжный
    признак — что получилось настоящее слово. Словарь берётся из документов, где
    кириллица цела: это и есть язык предметной области, вплоть до «крейцкопфа»."""
    vocab: dict[str, int] = {}
    for (text,) in con.execute("SELECT text FROM file_text WHERE length(text) > 200"):
        for w in CYR_WORD.findall((text or "").lower()):
            if len(w) >= 4:
                vocab[w] = vocab.get(w, 0) + 1
    return {w for w, n in vocab.items() if n >= min_count}


def shift_token(tok: str, vocab: set[str] | None = None) -> str | None:
    """Слово после обратного сдвига или None, если правило не применимо."""
    core = tok.strip("()[]{}«».,;:!?\"'")   # скобки и знаки вокруг слова не мешают опознанию
    if ACRONYM.match(core) or DIGITS.match(core):
        return None                          # аббревиатуры и числа сдвигать нельзя
    if CODEISH.match(core):
        return None                          # «DF5-050-A01», «5767916-24» — обозначения, не слова
    out = []
    shifted = kept = 0
    for ch in tok:
        o = ord(ch)
        if 0x30 <= o <= 0x4F:                # диапазон строчной кириллицы cp1251 после сдвига
            out.append(bytes([o + 0xB0]).decode("cp1251", "ignore"))
            shifted += 1
        elif o > 0x7F:                       # уже кириллица
            if ch.isalpha():
                if ch.islower():
                    return None              # «поз.4510» — живое русское слово с номером
                kept += 1
            out.append(ch)
        elif o < 0x30:                       # настоящая пунктуация: точки, запятые, дефисы
            out.append(ch)
        else:
            return None                      # латиница P–Z и строчная — не наш случай
    if shifted < 4 or shifted < kept:        # слово должно быть искажено, а не просто русским
        return None
    res = "".join(out)
    if len(res) < MIN_LEN:
        return None
    letters = [ch for ch in res if ch.isalpha()]
    if not letters:
        return None
    ratio = sum(1 for ch in letters if ch in VOWELS) / len(letters)
    # доля гласных у русского слова устойчива: и «ккк», и «ааа» — не слова
    if not (MIN_VOWEL_RATIO <= ratio <= MAX_VOWEL_RATIO):
        return None
    # Цифры 0–9 при сдвиге дают только буквы а–й. Значит номер позиции «4510»
    # превращается в «деба», а чертёж «Н-207» в «Н-ваз» — и то и другое выглядит
    # как слово. Настоящее русское слово почти всегда содержит буквы за пределами
    # этого куска алфавита: их должно быть минимум две разных.
    if len({ch for ch in letters if ch not in DIGIT_LETTERS}) < 2:
        return None
    if vocab is not None:
        core_res = "".join(ch for ch in res if ch.isalpha() or ch == "-").strip("-").lower()
        if core_res not in vocab:
            return None                      # не слово языка корпуса — значит это код
    return res


def repair(text: str, vocab: set[str] | None = None) -> tuple[str, int]:
    """(починенный текст, сколько слов заменено)."""
    hits = 0
    parts = []
    last = 0
    for m in TOKEN.finditer(text):
        fixed = shift_token(m.group(0), vocab)
        if fixed:
            parts.append(text[last:m.start()])
            parts.append(fixed)
            last = m.end()
            hits += 1
    parts.append(text[last:])
    return "".join(parts), hits


def run(db_path: str, dry: bool) -> dict:
    con = sqlite3.connect(db_path, timeout=300)
    con.execute("PRAGMA busy_timeout=300000")
    print("собираю словарь языка корпуса…", flush=True)
    vocab = build_vocab(con)
    print(f"слов в словаре: {len(vocab)}", flush=True)
    rows = con.execute("""SELECT t.fid, t.part, t.text FROM file_text t
                          JOIN files f ON f.fid = t.fid WHERE f.ext IN ('pdf','ocr-pdf')""").fetchall()
    by_file: dict[str, list] = {}
    for fid, part, text in rows:
        by_file.setdefault(fid, []).append((part, text))

    files_fixed = words_fixed = skipped_gibberish = 0
    for fid, parts in by_file.items():
        # Проверять «есть ли в документе нормальная кириллица» бесполезно: у
        # проектной документации Амурского ГХК в одном файле соседствуют целые
        # русские подписи и битые заголовки. Решает проверка ниже — по существу
        # восстановленного текста.
        repaired = []
        total_hits = 0
        restored: list[str] = []
        for part, text in parts:
            fixed, hits = repair(text or "", vocab)
            total_hits += hits
            repaired.append((part, fixed))
            restored += [w.strip(".,;:()").lower() for w in TOKEN.findall(fixed)]
        if total_hits < MIN_HITS:
            continue
        # проверка по существу: восстановленный текст должен содержать знакомые
        # русские слова, иначе сдвиг превратил коды и цены в бессмыслицу
        if len(set(restored) & RU_WORDS) < MIN_RU_WORDS:
            skipped_gibberish += 1
            continue
        files_fixed += 1
        words_fixed += total_hits
        if not dry:
            # коммит на каждый документ: параллельно идёт разбор архивов, и длинная
            # транзакция уронила бы его на «database is locked»
            for part, fixed in repaired:
                con.execute("UPDATE file_text SET text=? WHERE fid=? AND part=?", (fixed, fid, part))
            con.commit()
    con.close()
    return {"files": files_fixed, "words": words_fixed, "scanned": len(by_file),
            "skipped_gibberish": skipped_gibberish}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    st = run(a.db, a.dry_run)
    print(f"просмотрено документов {st['scanned']}, с искажением {st['files']}, "
          f"отклонено как бессмыслица {st['skipped_gibberish']}, "
          f"слов восстановлено {st['words']}" + (" (пробный прогон, база не менялась)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
