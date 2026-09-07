#!/usr/bin/env python3
"""Единые метаданные документов ОВЭ-75: автор — ООО «КВАНТ», человеческие
заголовки вместо технических. Прогоняется сборкой по всем DOCX/PDF в
ove/public/docs (кроме архива ревизий rev/ — он исторический)."""
import re
import shutil
import zipfile
from datetime import date
from pathlib import Path

ORG = "ООО «КВАНТ»"
APP = "ООО «КВАНТ» · проект ОВЭ-75"
DOCS = Path(__file__).resolve().parent.parent / "public" / "docs"

CORE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>{title}</dc:title><dc:creator>{org}</dc:creator><cp:lastModifiedBy>{org}</cp:lastModifiedBy>
<dcterms:created xsi:type="dcterms:W3CDTF">{d}T09:00:00Z</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{d}T09:00:00Z</dcterms:modified>
</cp:coreProperties>"""
APPXML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
<Application>{app}</Application><Company>{org}</Company></Properties>"""


def _title(p: Path) -> str:
    s = p.stem.replace("ove75-", "").replace("-", " ")
    return f"ОВЭ-75 · {s}"


def docx(p: Path) -> None:
    src = zipfile.ZipFile(p)
    names = src.namelist()
    if "docProps/core.xml" in names:
        core = src.read("docProps/core.xml").decode("utf-8", errors="ignore")
        if ORG in core:
            src.close()
            return
        # чужой автор (например, статический шаблон) — переписываем поля
        tmp = p.with_suffix(".docx.tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            for n in names:
                data = src.read(n)
                if n == "docProps/core.xml":
                    c = data.decode("utf-8", errors="ignore")
                    c = re.sub(r"<dc:creator>[^<]*</dc:creator>", f"<dc:creator>{ORG}</dc:creator>", c)
                    c = re.sub(r"<cp:lastModifiedBy>[^<]*</cp:lastModifiedBy>", f"<cp:lastModifiedBy>{ORG}</cp:lastModifiedBy>", c)
                    if "<dc:creator>" not in c:
                        c = c.replace("</cp:coreProperties>", f"<dc:creator>{ORG}</dc:creator></cp:coreProperties>")
                    data = c.encode("utf-8")
                out.writestr(n, data)
        src.close()
        shutil.move(tmp, p)
        return
    tmp = p.with_suffix(".docx.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for n in names:
            data = src.read(n)
            if n == "[Content_Types].xml":
                data = data.replace(b"</Types>",
                    b'<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                    b'<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>')
            if n == "_rels/.rels":
                data = data.replace(b"</Relationships>",
                    b'<Relationship Id="rIdCore" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                    b'<Relationship Id="rIdApp" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>')
            out.writestr(n, data)
        out.writestr("docProps/core.xml", CORE.format(title=_title(p), org=ORG, d=date.today().isoformat()))
        out.writestr("docProps/app.xml", APPXML.format(app=APP, org=ORG))
    src.close()
    shutil.move(tmp, p)


def pdf(p: Path) -> None:
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception:
        return  # на CI pypdf нет — PDF в репозитории уже проштампованы локально
    r = PdfReader(str(p))
    meta = r.metadata or {}
    if str(meta.get("/Author", "")) == ORG:
        return
    w = PdfWriter()
    for pg in r.pages:
        w.add_page(pg)
    w.add_metadata({"/Title": _title(p), "/Author": ORG, "/Creator": APP, "/Producer": APP})
    tmp = p.with_suffix(".pdf.tmp")
    with open(tmp, "wb") as f:
        w.write(f)
    shutil.move(tmp, p)


def build() -> None:
    nd = np = 0
    for p in sorted(DOCS.rglob("*")):
        if "rev" in p.parts[len(DOCS.parts):-1] or not p.is_file():
            continue
        if p.suffix == ".docx":
            docx(p); nd += 1
        elif p.suffix == ".pdf":
            pdf(p); np += 1
    print(f"метаданные: DOCX {nd}, PDF {np} — автор {ORG}")


if __name__ == "__main__":
    build()
