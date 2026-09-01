#!/usr/bin/env python3
"""Чистка английских (двуязычных) ТЗ в ove/rfq_en/ перед выкладкой и отправкой.

Русские исходники построены как «письмо + приложение»: в них есть служебная
строка-подсказка и поля под ручное заполнение — Ref. No. [____], To: [____],
Attn: [____], блок подписи. В приложении к электронному письму это лишнее:
письмо отправляется отдельно, а поля остаются незаполненными и выглядят
недоделкой. Скрипт удаляет шапку и подпись, подставляет контакт и адрес ответа.

Обрабатывает обе языковые части. Идемпотентен. Запуск:
python3 ove/tools/rfq_en_clean.py [слаг ...]
"""
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EN = ROOT / "rfq_en"
CONTACT = "Stepan Kharkov, KVANT LLC, stepan@kvantpro.com"
MAIL = "stepan@kvantpro.com"

# абзацы, которых во вложении быть не должно
DROP = [
    r"DELETE THIS LINE", r"删除本行", r"ЭТУ СТРОКУ УДАЛИТЬ",
    r"^ON KVANT LLC LETTERHEAD", r"^以\s*KVANT LLC",
    r"^Ref\.\s*No\.\s*\[", r"^To:\s*\[", r"^Attn:\s*\[",
    r"^发文编号", r"^致[：:]\s*\[", r"^收件人[：:]\s*\[",
    r"\(position\).*\(signature\)", r"（职务）.*（签字）",
]
# точечные подстановки в оставшемся тексте
SUBS = [
    (r"Contact person:\s*\[_+\]\s*\((?:name[^)]*)\)\.?", f"Contact person: {CONTACT}."),
    (r"联系人[：:]\s*\[_+\][（(][^）)]*[）)]。?", f"联系人：{CONTACT}。"),
    (r"(receipt of this request)\s*to\s*\[_+\]", rf"\1 to {MAIL}"),
    (r"(发送至)\s*\[_+\]", rf"\1 {MAIL}"),
    (r"请将报价.{0,12}发送至\s*\[_+\]", f"请将报价发送至 {MAIL}"),
]


def clean_xml(xml: str) -> tuple:
    paras = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
    dropped = 0
    for par in paras:
        text = re.sub(r"<[^>]+>", "", par).strip()
        if not text:
            continue
        if any(re.search(p, text) for p in DROP):
            xml = xml.replace(par, "", 1)
            dropped += 1
    # подстановки и зачистка остатков — на уровне текстовых узлов
    def fix(m):
        t = m.group(1)
        for pat, rep in SUBS:
            t = re.sub(pat, rep, t)
        t = re.sub(r"\s*\[_+\]\s*", " ", t)
        return f"<w:t{m.group(0).split('<w:t', 1)[1].split('>', 1)[0]}>{t}</w:t>"

    xml = re.sub(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>", fix, xml)
    return xml, dropped


def clean(p: Path) -> str:
    src = zipfile.ZipFile(p)
    names = src.namelist()
    xml = src.read("word/document.xml").decode("utf-8")
    new, dropped = clean_xml(xml)
    if new == xml:
        src.close()
        return "уже чистый"
    tmp = p.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for n in names:
            out.writestr(n, new.encode("utf-8") if n == "word/document.xml" else src.read(n))
    src.close()
    shutil.move(str(tmp), p)
    # контроль: файл открывается и текст не потерян
    check = zipfile.ZipFile(p).read("word/document.xml").decode("utf-8")
    body = re.sub(r"<[^>]+>", "", check)
    if len(body) < 800:
        raise RuntimeError(f"{p.name}: после чистки подозрительно мало текста")
    left = len(re.findall(r"\[_+\]", body))
    return f"снято абзацев {dropped}, осталось пустых полей {left}"


def build(only: list | None = None) -> None:
    files = sorted(EN.glob("*.docx"))
    if only:
        files = [f for f in files if f.stem in only]
    for f in files:
        print(f"  {f.name:22} {clean(f)}")


if __name__ == "__main__":
    build(sys.argv[1:] or None)
