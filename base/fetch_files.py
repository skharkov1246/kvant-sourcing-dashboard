#!/usr/bin/env python3
"""Скачивание и разбор вложений сделок Bitrix в базу base/kvant.db.

Один проход на файл: скачали → вытащили текст → положили в БД и в полнотекст →
бинарник удалили. Так 22 тыс. вложений укладываются в сотни мегабайт текста
вместо десятка гигабайт исходников.

Ключевой момент доступа: файлы карточек НЕ отдаются по ссылкам вида
`/bitrix/tools/...` и `crm_show_file.php` — там нужна сессия портала, вебхук
получает страницу входа. Рабочая ссылка — `urlMachine` из crm.item.list
(entityTypeId=2 для сделок): это REST-ссылка с одноразовым токеном, и по ней
файл отдаётся как есть.

    python base/fetch_files.py attachments.json --db base/kvant.db --workers 8
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import queue
import re
import sqlite3
import threading
import time
import zipfile
from pathlib import Path

import requests

MAX_CHARS = 400_000          # больше в один файл не берём: спецификации бывают огромными
CHUNK = 40_000               # размер куска текста в file_text
_WS = re.compile(r"[ \t ]+")
_NL = re.compile(r"\n{3,}")


def _clean(s: str) -> str:
    # Разбор битых кодировок оставляет одиночные суррогаты (\udcXX): такой текст
    # SQLite сохранить не может и роняет весь прогон. Выбрасываем их сразу.
    s = (s or "").encode("utf-8", "ignore").decode("utf-8", "ignore")
    s = _WS.sub(" ", s)
    return _NL.sub("\n\n", s).strip()


def sniff(b: bytes) -> str:
    if b[:4] == b"%PDF":
        return "pdf"
    if b[:2] == b"PK":
        return "zipish"
    if b[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "ole"
    if b[:4] == b"\x89PNG" or b[:3] == b"\xff\xd8\xff" or b[:6] in (b"GIF87a", b"GIF89a"):
        return "image"
    head = b.lstrip()[:16].lower()
    if head.startswith(b"<!doctype htm") or head.startswith(b"<html"):
        return "html"
    if head.startswith(b"<?xml"):
        return "xml"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    return "other"


def text_pdf(b: bytes) -> tuple[str, int]:
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(b))
    out, n = [], 0
    for page in r.pages:
        try:
            out.append(page.extract_text() or "")
        except Exception:
            continue
        n += 1
        if sum(len(x) for x in out) > MAX_CHARS:
            break
    return "\n".join(out), n


def text_xlsx(b: bytes) -> tuple[str, int]:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(b), read_only=True, data_only=True)
    out, n = [], 0
    for ws in wb.worksheets:
        n += 1
        out.append(f"### лист: {ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
            if cells:
                out.append(" | ".join(cells))
            if sum(len(x) for x in out) > MAX_CHARS:
                break
        if sum(len(x) for x in out) > MAX_CHARS:
            break
    wb.close()
    return "\n".join(out), n


def text_docx(b: bytes) -> tuple[str, int]:
    import docx
    d = docx.Document(io.BytesIO(b))
    out = [p.text for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        for row in t.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                out.append(" | ".join(cells))
    return "\n".join(out), len(d.paragraphs)


def text_xls(b: bytes) -> tuple[str, int]:
    """Старый .xls (OLE2). Таких в корпусе больше тысячи — раньше все шли в «пусто»."""
    import xlrd
    wb = xlrd.open_workbook(file_contents=b)
    out, n = [], 0
    for ws in wb.sheets():
        n += 1
        out.append(f"### лист: {ws.name}")
        for i in range(ws.nrows):
            cells = [str(c.value).strip() for c in ws.row(i) if str(c.value).strip()]
            if cells:
                out.append(" | ".join(cells))
            if sum(len(x) for x in out) > MAX_CHARS:
                break
        if sum(len(x) for x in out) > MAX_CHARS:
            break
    return "\n".join(out), n


def text_doc(b: bytes) -> str:
    """Старый .doc — через antiword/catdoc: чистого разбора OLE-Word в Python нет."""
    import shutil
    import subprocess
    import tempfile
    tool = shutil.which("antiword") or shutil.which("catdoc")
    if not tool:
        return ""
    with tempfile.NamedTemporaryFile(suffix=".doc", delete=True) as f:
        f.write(b)
        f.flush()
        try:
            r = subprocess.run([tool, f.name], capture_output=True, timeout=60)
            return r.stdout.decode("utf-8", "ignore")
        except Exception:
            return ""


_WT = re.compile(r"<w:t[^>]*>(.*?)</w:t>", re.S)
_WP = re.compile(r"</w:p>")
_TAG = re.compile(r"<[^>]+>")


def text_ooxml_raw(b: bytes, kind: str) -> str:
    """Текст из docx и xlsx напрямую из XML, минуя python-docx и openpyxl.

    Нужен там, где библиотеки отказывают, а файл целый:
      * openpyxl падает с «TypeError: expected datetime» на книге с битой датой;
      * python-docx отдаёт пустоту, если текст лежит в надписи или колонтитуле,
        а не в абзацах — таких документов в корпусе сотни.
    """
    import html as _html
    try:
        z = zipfile.ZipFile(io.BytesIO(b))
    except Exception:
        return ""
    out: list[str] = []
    if kind == "docx":
        parts = [n for n in z.namelist()
                 if n.startswith("word/") and n.endswith(".xml")
                 and ("document" in n or "header" in n or "footer" in n or "footnotes" in n)]
        for part in parts:
            try:
                xml = z.read(part).decode("utf-8", "ignore")
            except Exception:
                continue
            for para in _WP.split(xml):
                line = " ".join(_html.unescape(m) for m in _WT.findall(para))
                line = _TAG.sub("", line).strip()
                if line:
                    out.append(line)
    else:
        shared: list[str] = []
        try:
            xml = z.read("xl/sharedStrings.xml").decode("utf-8", "ignore")
            shared = [_TAG.sub("", _html.unescape(m)).strip()
                      for m in re.findall(r"<si>(.*?)</si>", xml, re.S)]
        except Exception:
            pass
        for part in sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)):
            try:
                xml = z.read(part).decode("utf-8", "ignore")
            except Exception:
                continue
            out.append(f"### лист: {part.rsplit('/', 1)[1]}")
            for row in re.findall(r"<row[^>]*>(.*?)</row>", xml, re.S):
                cells = []
                # тип ячейки ищем в её атрибутах целиком: порядок атрибутов в
                # xlsx не фиксирован, и попытка выцепить t="s" одним шаблоном
                # промахивается — вместо строк в текст попадают их номера
                for attrs, body in re.findall(r"<c([^>]*)>(.*?)</c>", row, re.S):
                    val = _TAG.sub("", _html.unescape(body)).strip()
                    if 't="s"' in attrs and val.isdigit() and int(val) < len(shared):
                        val = shared[int(val)]
                    elif 't="s"' in attrs:
                        val = ""
                    if val:
                        cells.append(val)
                if cells:
                    out.append(" | ".join(cells))
            if sum(len(x) for x in out) > MAX_CHARS:
                break
    return "\n".join(out)


def text_via_office(b: bytes, suffix: str) -> str:
    """Последний рубеж для старых книг и документов: конвертация LibreOffice.

    xlrd читает только классический BIFF и на части настоящих .xls падает с
    AssertionError. Такие файлы — примерно пятая часть остатка, и среди них
    формы RFQ заказчиков, поэтому терять их нельзя."""
    import shutil
    import subprocess
    import tempfile
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return ""
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / ("in" + suffix)
        src.write_bytes(b)
        want = "csv" if suffix in (".xls", ".xlsx") else "txt:Text"
        try:
            subprocess.run([soffice, "--headless", "--norestore", f"-env:UserInstallation=file://{td}/p",
                            "--convert-to", want, "--outdir", td, str(src)],
                           capture_output=True, timeout=180)
        except Exception:
            return ""
        for out in Path(td).glob("in.*"):
            if out.suffix.lower() in (".csv", ".txt"):
                return out.read_text(encoding="utf-8", errors="ignore")
    return ""


_ASCII_RUN = re.compile(rb"[\x20-\x7e\r\n\t]{12,}")
_UTF16_RUN = re.compile(rb"(?:[\x20-\x7e]\x00){12,}")


def text_ole_strings(b: bytes) -> str:
    """Текстовые строки прямо из OLE-контейнера — когда его не берёт никто.

    Часть файлов с расширением .xls и .doc — контейнеры OLE вообще без потоков
    Workbook и WordDocument: ни xlrd, ни LibreOffice их не открывают («source
    file could not be loaded»). Внутри при этом лежит обычный текст технических
    требований. Для полнотекстового поиска и разбора номенклатуры этого хватает,
    поэтому выбираем читаемые последовательности напрямую."""
    parts = [m.group(0).decode("cp1251", "ignore") for m in _ASCII_RUN.finditer(b)]
    parts += [m.group(0).decode("utf-16le", "ignore") for m in _UTF16_RUN.finditer(b)]
    text = "\n".join(s.strip() for s in parts if len(s.strip()) >= 12)
    # мусорные последовательности вида BFRRRRGJNNEEB отсеиваем по доле гласных
    keep = [ln for ln in text.split("\n")
            if sum(ch.lower() in "aeiouаеёиоуыэюя" for ch in ln) >= max(2, len(ln) // 12)]
    return "\n".join(keep)


def text_spreadsheetml(b: bytes) -> tuple[str, int]:
    """Книга Excel в формате XML Spreadsheet 2003.

    Так выгружает 1С: файл называется .xls, а внутри XML. Сигнатуры OLE у него
    нет, xlrd на нём падает, и до этого разбора такие книги целиком уходили в
    «пусто» — а это спецификации заказчика."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(b.decode("utf-8", "ignore"))
    ns = "{urn:schemas-microsoft-com:office:spreadsheet}"
    out, sheets = [], 0
    for ws in root.iter(f"{ns}Worksheet"):
        sheets += 1
        out.append(f"### лист: {ws.get(f'{ns}Name') or sheets}")
        for row in ws.iter(f"{ns}Row"):
            cells = []
            for cell in row.iter(f"{ns}Cell"):
                data = cell.find(f"{ns}Data")
                val = "".join(data.itertext()).strip() if data is not None else ""
                if val:
                    cells.append(val)
            if cells:
                out.append(" | ".join(cells))
            if sum(len(x) for x in out) > MAX_CHARS:
                break
    return "\n".join(out), sheets


def text_rtf(b: bytes) -> str:
    """Текст из RTF: часть .doc на деле сохранена в этом формате."""
    s = b.decode("cp1251", "ignore")
    s = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]).decode("cp1251", "ignore"), s)
    s = re.sub(r"\\u(-?\d+)\s?\??", lambda m: chr(int(m.group(1)) % 65536), s)
    s = re.sub(r"\{\\\*[^{}]*\}", " ", s)          # служебные группы целиком
    s = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", s)       # управляющие слова
    s = s.replace("{", " ").replace("}", " ")
    return s


def extract(b: bytes, name: str) -> tuple[str, int, str]:
    """(текст, число страниц/листов, вид). Пустой текст — не ошибка: бывают сканы."""
    kind = sniff(b)
    ext = (name.rsplit(".", 1)[-1].lower() if "." in name else "")[:8]
    try:
        if kind == "pdf":
            t, n = text_pdf(b)
            return _clean(t), n, "pdf"
        if kind == "zipish":
            names = zipfile.ZipFile(io.BytesIO(b)).namelist()
            if any(x.startswith("xl/") for x in names):
                try:
                    t, n = text_xlsx(b)
                except Exception:
                    t, n = "", 0
                if not t.strip():                       # openpyxl отказал — читаем XML сами
                    t, n = text_ooxml_raw(b, "xlsx"), 0
                return _clean(t), n, "xlsx"
            if any(x.startswith("word/") for x in names):
                try:
                    t, n = text_docx(b)
                except Exception:
                    t, n = "", 0
                if not t.strip():                       # текст в надписи или колонтитуле
                    t, n = text_ooxml_raw(b, "docx"), 0
                return _clean(t), n, "docx"
            return "", 0, "zip"
        if kind == "ole":
            try:
                t, n = text_xls(b)
                if t.strip():
                    return _clean(t), n, "xls"
            except Exception:
                pass
            t = text_doc(b)
            if t.strip():
                return _clean(t), 0, "doc"
            t = text_via_office(b, ".xls" if ext in ("xls", "xlsm") else ".doc")
            if t.strip():
                return _clean(t), 0, "office"
            t = text_ole_strings(b)
            return (_clean(t), 0, "ole-strings") if len(t.strip()) > 200 else ("", 0, "ole")
        if kind == "xml":
            t, n = text_spreadsheetml(b)
            return _clean(t), n, "xml-xls"
        if kind == "rtf":
            return _clean(text_rtf(b)), 0, "rtf"
        if kind == "html":
            return _clean(re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>|<[^>]+>", " ", b.decode("utf-8", "ignore"))), 0, "html"
        if kind == "other" and ext in ("txt", "csv", "xml", "json", "eml"):
            return _clean(b.decode("utf-8", "ignore")[:MAX_CHARS]), 0, ext
        return "", 0, kind
    except Exception as e:                                  # битые и защищённые файлы
        return "", 0, f"err:{type(e).__name__}"


def filename_from(resp: requests.Response, fid: str) -> str:
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*=utf-8''([^;]+)", cd) or re.search(r'filename="?([^";]+)', cd)
    if m:
        from urllib.parse import unquote
        return unquote(m.group(1)).strip('"')
    return f"file_{fid}"


def worker(q: "queue.Queue", out: "queue.Queue", stop: threading.Event, session: requests.Session) -> None:
    while not stop.is_set():
        try:
            item = q.get(timeout=2)
        except queue.Empty:
            return
        fid, deal, field, fname, url = item["fid"], item["deal"], item["field"], item.get("field_name"), item["url"]
        try:
            r = session.get(url, timeout=180)
            if r.status_code != 200 or not r.content:
                out.put((fid, deal, field, fname, None, None, 0, "", "", f"http {r.status_code}", 0, 0))
                continue
            b = r.content
            name = filename_from(r, fid)
            text, pages, ext = extract(b, name)
            out.put((fid, deal, field, fname, name, ext, len(b), hashlib.sha1(b).hexdigest(), text, "", pages, len(text)))
        except Exception as e:
            out.put((fid, deal, field, fname, None, None, 0, "", "", f"{type(e).__name__}: {e}"[:200], 0, 0))
        finally:
            q.task_done()


def run(att_path: str, db_path: str, workers: int = 8, limit: int | None = None) -> None:
    att = json.loads(Path(att_path).read_text(encoding="utf-8"))
    con = sqlite3.connect(db_path, timeout=300)
    # аналитика читает и пишет ту же базу параллельно: без ожидания качалка
    # обрывалась на «database is locked» и теряла часы разбора
    con.execute("PRAGMA busy_timeout=300000")
    con.executescript((Path(__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8"))
    done = {r[0] for r in con.execute("SELECT fid FROM files WHERE status IN ('parsed','empty')")}
    todo = [a for a in att if a["fid"] not in done]
    if limit:
        todo = todo[:limit]
    print(f"вложений всего {len(att)}, уже в базе {len(done)}, к обработке {len(todo)}", flush=True)

    q: queue.Queue = queue.Queue()
    out: queue.Queue = queue.Queue()
    for a in todo:
        q.put(a)
    stop = threading.Event()
    session = requests.Session()
    threads = [threading.Thread(target=worker, args=(q, out, stop, session), daemon=True) for _ in range(workers)]
    for t in threads:
        t.start()

    n = ok = empty = err = 0
    chars = 0
    t0 = time.time()
    pending = len(todo)
    while pending > 0:
        try:
            fid, deal, field, fname, name, ext, size, sha, text, errmsg, pages, nchars = out.get(timeout=300)
        except queue.Empty:
            break
        pending -= 1
        n += 1
        status = "error" if errmsg else ("parsed" if text else "empty")
        if errmsg:
            err += 1
        elif text:
            ok += 1
            chars += nchars
        else:
            empty += 1
        con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (fid, int(deal), field, fname, name, ext, size, sha, "", "", status, errmsg, pages, nchars))
        if text:
            con.execute("DELETE FROM file_text WHERE fid=?", (fid,))
            for i in range(0, len(text), CHUNK):
                con.execute("INSERT INTO file_text VALUES (?,?,?)", (fid, i // CHUNK, text[i:i + CHUNK]))
            con.execute("INSERT INTO search (body, kind, ref, deal_id) VALUES (?,'file',?,?)",
                        (f"{name}\n{text[:CHUNK * 3]}", fid, int(deal)))
        if n % 100 == 0:
            con.commit()
            sp = n / max(time.time() - t0, 1)
            eta = (pending) / max(sp, 0.01) / 60
            print(f"  {n}/{len(todo)} · текст {ok} · пусто {empty} · ошибок {err} · "
                  f"{chars/1e6:.1f} млн символов · {sp:.1f} файл/с · осталось ~{eta:.0f} мин", flush=True)
    con.commit()
    stop.set()
    print(f"готово: обработано {n}, с текстом {ok}, пустых {empty}, ошибок {err}, "
          f"символов {chars/1e6:.1f} млн, за {(time.time()-t0)/60:.1f} мин", flush=True)
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("attachments")
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    run(a.attachments, a.db, a.workers, a.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
