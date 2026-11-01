"""Строки запроса поставщику — сквозной счёт КП по строкам (решение владельца 31.10.2026: «нужен срез
по строкам запроса; не надо взвешивать, просто считай сквозно по строкам»).

ГДЕ СТРОКИ (зонд v63, scripts/probe_request_lines.py, 31.10.2026). Товарных строк у карточек СП-166 нет
(0 из 100); поля «позиций» нет. Источник строк запроса, по порядку:
  f — файл «Request file» карточки (ufCrm18_1727423346): строки разобранного файла. Его кладёт робот
      пресейла (81 % его карточек, у ручных — 6 %); разбирается 87 % файлов (gt/tools/bitrix_tkp.parse);
  a — вложение первого исходящего письма карточки: строки таблицы позиций самого длинного из вложений
      (до трёх). Читается только вебхуком файлов (BITRIX_FILES_WEBHOOK_URL: основной вебхук получает
      ACCESS_DENIED, зонды 25.09 и v64 31.10.2026 — disk.file.get 168 из 171, строки у 51 % карточек).
      В счёт идут только строки с двумя и более ячейками: текст pdf письмом не считается позициями;
  x — поле «Individual letter text» (ufCrm18_1724931823290): список позиций текстом, строк в нём;
  t — таблица позиций в первом исходящем письме карточки: строк таблицы без шапки;
  o — иначе одна строка: запрос на одну позицию по названию карточки;
  u — файл или вложение есть, а строк ещё нет (не скачан в эту сборку): строки неизвестны, в счёт
      не идут и показаны отдельно (правило 15: статус не врёт).
Вебхука файлов в сборке нет — вложения не читаются, и у ручной карточки строк столько, сколько видно
в тексте письма, иначе одна.

СКВОЗНОЙ СЧЁТ. Группа строк — одно содержание в одной сделке: файл (по хэшу содержимого), текст поля,
таблица письма (по хэшу текста) или название. Спецификация, ушедшая нескольким поставщикам сделки, —
её строки один раз; строка расценена, когда КП дал хотя бы один поставщик группы. Сделки у карточки
нет — группа в пределах карточки.

КЭШ. Строки файла считаются один раз и хранятся по номеру файла в закрытом ключе KV reqlines:v1
({"v": 1, "f": {номер: [строк, хэш]}}; вложение письма — с приставкой «a» к номеру файла Диска;
строк −1 — не разобрался, второй раз не качается). За сборку скачивается не больше БЮДЖЕТ новых
файлов и БЮДЖЕТ_ВЛОЖ вложений — свежие первыми; остальные — «u» до следующей сборки.
В ключе и в журнале — только числа и хэши (правило 17): ни названий, ни содержимого.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

REQ_FIELD = "ufCrm18_1727423346"                 # «Request file» — наш исходящий запрос
LETTER_FIELD = "ufCrm18_1724931823290"           # «Individual letter text» — текст письма с позициями
KV_KEY = "reqlines:v1"
БЮДЖЕТ = 300                                     # новых файлов за сборку
БЮДЖЕТ_ВЛОЖ = 300                                # новых вложений писем за сборку (≈ 2 запроса на каждое)
ВЛОЖ_НА_ПИСЬМО = 3
ОБРЫВ = 10                                       # первые столько вложений подряд без файла — вебхук не работает


def _h(s: str | bytes) -> str:
    b = s if isinstance(s, bytes) else str(s).encode("utf-8")
    return hashlib.sha256(b).hexdigest()[:12]


def file_ref(card: dict) -> tuple[str, str] | None:
    """(номер файла, машинная ссылка) «Request file» карточки или None."""
    v = card.get(REQ_FIELD)
    items = v if isinstance(v, list) else ([v] if v else [])
    for x in items:
        if isinstance(x, dict) and (x.get("urlMachine") or x.get("url")):
            return str(x.get("id") or x.get("urlMachine") or x.get("url")), str(x.get("urlMachine") or x.get("url"))
    return None


def rows_count(rows: list, strict: bool = False) -> int:
    """Позиции разобранного файла: строки с двумя и более непустыми ячейками; первая такая без
    цифр — шапка, не позиция. Нет ни одной такой (текст pdf одной ячейкой) — непустые строки,
    а при strict (вложение письма: там и письма, и чертежи) — ноль."""
    multi, any_ = [], 0
    for _sheet, _n, cells in rows or []:
        vals = [str(c).strip() for c in (cells or []) if c is not None and str(c).strip()]
        if not vals:
            continue
        any_ += 1
        if len(vals) >= 2:
            multi.append(vals)
    if not multi:
        return 0 if strict else any_
    n = len(multi)
    if n > 1 and not any(ch.isdigit() for v in multi[0] for ch in v):
        n -= 1
    return n


def table_rows(html: str) -> tuple[int, str]:
    """(строк таблицы без шапки, хэш текста таблицы) письма; таблицы нет — (0, '')."""
    s = str(html or "")
    trs = re.findall(r"(?is)<tr\b.*?</tr>", s)
    if not trs:
        return 0, ""
    text = " ".join(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)).strip() for t in trs)
    n = len(trs) - 1 if len(trs) > 1 else 1
    return n, _h(text.lower())


def text_lines(text: str) -> int:
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", str(text or ""))
    s = re.sub(r"<[^>]+>", " ", s)
    return sum(1 for x in s.splitlines() if x.strip())


def load_cache(path: str | None) -> dict:
    """Кэш строк файлов из файла сборки (шаг deploy.yml читает ключ KV); нет — пустой."""
    try:
        val = json.loads(Path(path).read_text(encoding="utf-8")) if path else None
    except (OSError, ValueError):
        val = None
    if not isinstance(val, dict) or val.get("v") != 1 or not isinstance(val.get("f"), dict):
        return {"v": 1, "f": {}}
    return {"v": 1, "f": {str(k): v for k, v in val["f"].items()
                          if isinstance(v, list) and len(v) == 2 and isinstance(v[0], int)}}


def attach_ids(files) -> list[str]:
    """Номера файлов вложений письма (FILES crm.activity.list — список {id, url}), не больше трёх."""
    out = []
    for f in files if isinstance(files, list) else []:
        if isinstance(f, dict) and str(f.get("id") or "").strip() not in ("", "0"):
            out.append(str(f["id"]).strip())
    return out[:ВЛОЖ_НА_ПИСЬМО]


def build(rfqs: list[dict], first_out: dict[str, dict], cache: dict, *, fetch=None, parse=None,
          fetch_attach=None, budget: int = БЮДЖЕТ, att_budget: int = БЮДЖЕТ_ВЛОЖ
          ) -> tuple[dict[str, tuple[int, int | None, str]], dict, dict]:
    """→ ({карточка: (группа, строк или None, источник)}, новые записи кэша, сводка).

    first_out — первое исходящее письмо карточки: {"tr": строк таблицы, "tk": хэш таблицы,
    "fa": [номера файлов вложений]}. fetch(url) → (байты | None, причина, имя); parse(имя, байты) →
    (строки, способ); fetch_attach(номер файла Диска) → (байты | None, имя) — только при вебхуке файлов."""
    fc = cache.get("f", {}) if cache else {}
    new: dict[str, list] = {}
    # 1. файлы без строк в кэше — свежие первыми, не больше бюджета
    need = []
    for r in sorted(rfqs, key=lambda r: str(r.get("createdTime") or ""), reverse=True):
        ref = file_ref(r)
        if ref and ref[0] not in fc and ref[0] not in new and ref[0] not in {x[0] for x in need}:
            need.append(ref)
    скачано = 0
    for fid, url in need[:max(0, budget)] if fetch and parse else []:
        body, _why, name = fetch(url)
        скачано += 1
        if body is None:
            continue                                   # сеть, права, пусто — повтор следующей сборкой
        try:
            rows, _how = parse(name or "", body)
        except Exception:                              # noqa: BLE001 — отказ по файлу (правило 13)
            rows = []
        n = rows_count(rows)
        new[fid] = [n if n > 0 else -1, _h(body)]
    # 1а. вложения первого письма у карточек без «Request file» — свежие первыми, не больше бюджета;
    # первые ОБРЫВ вложений подряд без файла — вебхук не работает: до следующей сборки не спрашиваем,
    # а строки таких карточек — из текста письма, как без вебхука (не «u» навсегда)
    need_a: list[str] = []
    if fetch_attach and parse:
        for r in sorted(rfqs, key=lambda r: str(r.get("createdTime") or ""), reverse=True):
            if file_ref(r):
                continue
            for fid in (first_out.get(str(r.get("id"))) or {}).get("fa") or []:
                if "a" + fid not in fc and fid not in need_a:
                    need_a.append(fid)
    влож, мимо, обрыв = 0, 0, False
    for fid in need_a[:max(0, att_budget)]:
        try:
            body, name = fetch_attach(fid)
        except Exception:                              # noqa: BLE001 — отказ по файлу (правило 13)
            body, name = None, ""
        влож += 1
        if body is None:
            мимо += 1
            if мимо == влож and влож >= ОБРЫВ:
                обрыв = True
                break
            continue                                   # права, сеть — повтор следующей сборкой
        try:
            rows, _how = parse(name or "", body)
        except Exception:                              # noqa: BLE001
            rows = []
        n = rows_count(rows, strict=True)
        new["a" + fid] = [n if n > 0 else -1, _h(body)]
    known = {**fc, **new}
    # 2. строки и группа каждой карточки
    keys: dict[str, int] = {}
    out: dict[str, tuple[int, int | None, str]] = {}
    src_n: dict[str, int] = {}
    for r in rfqs:
        cid = str(r.get("id"))
        deal = str(r.get("parentId2") or "")
        scope = deal if deal and deal != "0" else "c" + cid
        ref = file_ref(r)
        letter = str(r.get(LETTER_FIELD) or "").strip()
        mail = first_out.get(cid) or {}
        att = [known.get("a" + fid) for fid in mail.get("fa") or []]
        best = max((v for v in att if v and v[0] > 0), key=lambda v: v[0], default=None)
        if ref:
            v = known.get(ref[0])
            if v and v[0] > 0:
                src, n, key = "f", v[0], "f" + v[1]
            else:
                src, n, key = "u", None, "u" + cid
        elif best:
            src, n, key = "a", best[0], "a" + best[1]
        elif fetch_attach and not обрыв and any(v is None for v in att):
            src, n, key = "u", None, "u" + cid         # вложение ещё не прочитано — строки неизвестны
        elif letter and text_lines(letter) >= 2:
            src, n, key = "x", text_lines(letter), "x" + _h(letter)
        elif mail.get("tr", 0) >= 2:
            src, n, key = "t", int(mail["tr"]), "t" + str(mail.get("tk") or cid)
        else:
            title = re.sub(r"\s+", " ", str(r.get("title") or "").strip().lower())
            src, n, key = "o", 1, "o" + _h(title or cid)
        g = keys.setdefault(scope + ":" + key, len(keys))
        out[cid] = (g, n, src)
        src_n[src] = src_n.get(src, 0) + 1
    stats = {"cards": len(out), "groups": len(keys), "src": src_n, "files": len({file_ref(r)[0] for r in rfqs if file_ref(r)}),
             "filesKnown": sum(1 for r in rfqs if (ref := file_ref(r)) and (known.get(ref[0]) or [0])[0] > 0),
             "fetched": скачано, "cacheNew": len(new), "pending": max(0, len(need) - скачано),
             "att": len({fid for r in rfqs if not file_ref(r) for fid in (first_out.get(str(r.get("id"))) or {}).get("fa") or []}),
             "attFetched": влож, "attMiss": мимо, "attPending": max(0, len(need_a) - влож),
             "attBroken": обрыв}
    return out, new, stats
