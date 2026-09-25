"""Бренд компонента /library — общим правилом /p и /brands (выдуманный корпус).

Публикатор scripts/publish_library_v2.py кладёт в статью компонента проекцию
library_brand: crossref.бренды_каталога по crossref.реестр_сборки — то же, чем
карточка /p называет ячейку изготовителя. Эталон здесь — не само правило, а
независимые от него вещи: ключи страницы /brands (brands.ключи_ячейки), имя записи
словаря и разрешение ревизии (brand_registry.разрешить). Корпус придуман
(CLAUDE.md, п. 18).
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("publisher_v2_brand", ROOT / "scripts" / "publish_library_v2.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

from library import brand_registry, brands, crossref  # noqa: E402

NOW = "2026-01-01T00:00:00Z"
SEGMENTS = [{"id": "s1", "name": "Выдуманное оборудование", "note": ""}]

СЛОВАРЬ = {"records": [
    {"oem_key": "skf", "name": "SKF", "kind": "бренд",
     "spellings": [{"spelling": "Skf"}, {"spelling": "СКФ"}]},
    {"oem_key": "fag", "name": "FAG", "kind": "бренд", "spellings": [{"spelling": "F.A.G."}]},
    {"oem_key": "vydumkamash", "name": "Выдумка Маш", "kind": "бренд",
     "spellings": [{"spelling": "VYDUMKA-MASH"}, {"spelling": "Vydumka Mash GmbH"}]},
    {"oem_key": "skffag", "name": "SKF, FAG", "kind": "несколько", "brands": ["skf", "fag"],
     "spellings": [{"spelling": "SKF, FAG"}]},
]}

# (написание в карточке, артикул) — выдуманные ячейки изготовителя.
КОРПУС = [
    ("SKF", "NU 316"), ("Skf", "6205-2RS"), ("СКФ", "22220 E"), ("SKF (Швеция)", "NU 316"),
    ("SKF - Germany", "NU 316"), ("SKF, SWEDEN", "NU 316"), ("F.A.G.", "PL-2280"),
    ("SKF/FAG", "NU 316"), ("SKF, FAG", "NU 316"), ("FAG и SKF", "NU 316"),
    ("VYDUMKA-MASH", "VM-101"), ("Vydumka Mash GmbH", "VM-102"),
    ("Прочая Лавка", "PL-1"), ("не указан", "PL-2"), ("Россия", "PL-3"), ("VM-103", "VM-103"),
]


def реестр():
    return crossref.реестр_брендов(СЛОВАРЬ, разложение=brands.разложение_словаря(СЛОВАРЬ))


def компонент(n, oem, pn="PN-1", **sources):
    ид = f"synthetic:{n:06d}"
    поля = {"part_number": pn, "name": "Выдуманная деталь"}
    if oem is not None:
        поля["oem"] = oem
    return {"id": ид, "segment_id": "s1", "title": f"Деталь {n}", "topic": "QA", "body": "Выдуманный текст",
            "sources": {"kind": "component", "importer_id": ид, "publication_approved": True,
                        "component_fields": поля, **sources},
            "confidence": "med", "updated_at": NOW}


# ── Правило ──────────────────────────────────────────────────────────────────

def test_ключи_совпадают_с_ключами_страницы_brands_и_имя_со_словарём():
    р = реестр()
    карта, записи, _ = brands.карта_словаря(СЛОВАРЬ)
    разложение = brands.разложение_словаря(СЛОВАРЬ)
    for написание, pn in КОРПУС:
        проекция = p.component_brand({"oem": написание, "part_number": pn}, р)
        узнанные = [b for b in (проекция or {}).get("brands", []) if b["known"]]
        ключи_brands = [k for k in brands.ключи_ячейки(написание, карта, разложение) if k in записи]
        assert [b["key"] for b in узнанные] == ключи_brands, написание
        for b in узнанные:
            assert b["name"] == записи[b["key"]]["name"], написание


def test_случаи_корпуса():
    р = реестр()
    def ключи(oem, pn="NU 316"):
        проекция = p.component_brand({"oem": oem, "part_number": pn}, р)
        return [(b["key"], b["name"], b["known"]) for b in (проекция or {}).get("brands", [])]
    assert ключи("SKF (Швеция)") == [("skf", "SKF", True)]
    assert ключи("SKF - Germany") == [("skf", "SKF", True)]
    assert ключи("СКФ") == [("skf", "SKF", True)]
    assert ключи("Vydumka Mash GmbH") == [("vydumkamash", "Выдумка Маш", True)]
    assert ключи("SKF/FAG") == [("skf", "SKF", True), ("fag", "FAG", True)]
    # Не узнанное — слово ячейки своим ключом, без ссылки; пометка незнания,
    # страна и сам артикул — не бренд.
    assert ключи("Прочая Лавка") == [("прочаялавка", "Прочая Лавка", False)]
    assert ключи("не указан") == [] and ключи("Россия") == []
    assert ключи("VM-103", "VM-103") == []
    assert p.component_brand({"part_number": "X-1"}, р) is None
    assert p.component_brand({"oem": 42}, р) is None
    проекция = p.component_brand({"oem": "SKF"}, р)
    assert (проекция["version"], проекция["producer"], проекция["rule"]) == (1, "publisher-v2", p.BRAND_RULE)


def test_замер_у_скольких_хуже_на_корпусе():
    """«Сумма прячет потерю»: до правки страница показывала написание как есть и
    ссылки не имела. После — ни одно написание, которое ревизия разрешала в марку,
    не остаётся без узнанного бренда (потерь 0), имя не пропадает ни у кого."""
    р = реестр()
    карта = __import__("collections").defaultdict(set)
    for r in СЛОВАРЬ["records"]:
        if r["kind"] != "бренд":
            continue
        for н in [r["name"], r["oem_key"]] + [x["spelling"] for x in r["spellings"]]:
            карта[brand_registry.ключ(н)].add(r["oem_key"])
    сменили = потеряли = приобрели = 0
    for написание, pn in КОРПУС:
        проекция = p.component_brand({"oem": написание, "part_number": pn}, р)
        бренды_ = (проекция or {}).get("brands", [])
        показ = ", ".join(b["name"] for b in бренды_) if бренды_ else написание
        assert показ, написание
        сменили += показ != написание
        состояние, ключи_ = brand_registry.разрешить(написание, карта)
        узнанные = {b["key"] for b in бренды_ if b["known"]}
        if состояние == brand_registry.РАЗРЕШЕНО and not ключи_ <= узнанные:
            потеряли += 1
        приобрели += bool(узнанные)
    assert потеряли == 0
    # Сменили имя: Skf, СКФ, три хвоста-страны, F.A.G., SKF/FAG, «FAG и SKF» и два
    # написания Выдумки; приобрели ссылку все, кроме четырёх не-брендов и слова.
    assert (сменили, приобрели) == (10, 12)


def test_реестр_сборки_один_у_p_и_library():
    spec_ = importlib.util.spec_from_file_location("publish_crossref_brand", ROOT / "scripts" / "publish_crossref.py")
    pc = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(pc)
    из_файла = crossref.реестр_сборки(None)
    assert pc.реестр_сборки(None) == из_файла == p.brand_registry(None)
    assert из_файла["откуда"] == "словарь-файл"
    база = {"словарь": СЛОВАРЬ, "карточка": {"7": "skf"}}
    assert pc.реестр_сборки(база) == p.brand_registry(база)
    assert p.brand_registry(база)["откуда"] == "реестр базы"


# ── Публикатор ───────────────────────────────────────────────────────────────

class Store:
    def __init__(self):
        self.values = {}

    def put(self, value):
        raw = p.encode(value)
        ref = {"sha256": p.digest(raw), "bytes": len(raw)}
        self.values[ref["sha256"]] = value
        return ref


def собрать(rows, registry):
    builder = p.Builder(Store(), SEGMENTS, None, registry)
    for r in sorted(rows, key=lambda x: x["id"]):
        builder.add(r)
    каталог = [i for items in builder.buffers.values() for i in items if "summary" not in i and "id" in i
               and "block" not in i]
    return builder, {i["id"]: i for i in каталог}, {r["id"]: r for r in builder.body_rows}


def test_проекция_в_кратком_и_полном_и_старая_пересчитывается():
    старая = {"version": 1, "producer": "publisher-v2", "rule": p.BRAND_RULE,
              "brands": [{"key": "устарело", "name": "Устарело", "known": True}]}
    rows = [компонент(1, "SKF (Швеция)"), компонент(2, None), компонент(3, "не указан"),
            компонент(4, "Прочая Лавка", library_brand=старая),
            компонент(5, "не указан", library_brand=старая)]
    исход = copy.deepcopy(rows)
    builder, краткие, полные = собрать(rows, реестр())
    assert rows == исход, "исходные записи не меняются"
    b1 = полные["synthetic:000001"]["sources"]["library_brand"]["brands"]
    assert b1 == [{"key": "skf", "name": "SKF", "known": True}]
    assert краткие["synthetic:000001"]["sources"]["library_brand"]["brands"] == b1
    # Без изготовителя и с пометкой незнания — запись как есть, без проекции.
    assert полные["synthetic:000002"] == rows[1]
    assert полные["synthetic:000003"] == rows[2]
    # Проекция прежней публикации не переживает сборку: считается заново.
    assert полные["synthetic:000004"]["sources"]["library_brand"]["brands"] == [
        {"key": "прочаялавка", "name": "Прочая Лавка", "known": False}]
    # Бренда по правилу нет — прежняя проекция снимается, а не остаётся.
    assert "library_brand" not in полные["synthetic:000005"]["sources"]
    assert dict(builder.brand_metrics) == {"components": 5, "components_with_brand": 2,
                                           "components_with_known_brand": 1}


def test_без_реестра_сборка_прежняя():
    rows = [компонент(1, "SKF")]
    _, _, полные = собрать(rows, None)
    assert полные["synthetic:000001"] == rows[0]


class CF:
    def __init__(self):
        self.values = {}

    def namespace(self):
        return "a" * 32

    def draft_keys(self, namespace):
        return []

    def get(self, namespace, key):
        return self.values.get(key)

    def put(self, namespace, key, raw):
        self.values[key] = raw

    def verify(self, namespace, key, raw):
        assert self.values.get(key) == raw

    def preserve(self, namespace, key, raw):
        self.values.setdefault(key, raw)


class DB:
    def __init__(self, rows, registry=None, read_ok=True):
        self.rows, self.registry, self.read_ok, self.inserts = rows, registry, read_ok, []

    def read_segments(self):
        return copy.deepcopy(SEGMENTS)

    def read_brand_registry(self):
        return self.registry, self.read_ok

    def insert_drafts(self, rows):
        self.inserts.extend(rows)

    @contextmanager
    def stream(self):
        yield copy.deepcopy(SEGMENTS), iter(sorted(self.rows, key=lambda x: x["id"]))


@pytest.mark.parametrize("registry,read_ok,откуда", [
    (None, True, "словарь-файл"),
    ({"словарь": СЛОВАРЬ, "карточка": {}}, True, "реестр базы"),
    (None, False, "словарь-файл (реестр не прочитан)"),
])
def test_прогон_берёт_реестр_базы_и_пишет_откуда(registry, read_ok, откуда):
    итог = p.run(DB([компонент(1, "Vydumka Mash GmbH")], registry, read_ok), CF())
    assert итог["ok"] and итог["component_brands"]["registry"] == откуда
    assert итог["component_brands"]["components"] == 1
    # Выдуманный бренд знает только реестр базы: по файлу он не узнан.
    assert итог["component_brands"].get("components_with_known_brand", 0) == (1 if registry else 0)


def test_чтение_реестра_базы_только_на_чтение_и_сбой_не_роняет():
    class Cursor:
        def __init__(self, fail): self.fail, self.sql = fail, []
        def execute(self, sql, params=None):
            if self.fail:
                raise RuntimeError("выдуманный сбой")
            self.sql.append(sql)
        def fetchone(self): return (False,)
        def close(self): pass
    class Connection:
        def __init__(self, fail): self.c, self.closed, self.rolled = Cursor(fail), False, False
        def cursor(self, name=None): return self.c
        def rollback(self): self.rolled = True
        def close(self): self.closed = True
    for fail, ожидаемо in ((False, (None, True)), (True, (None, False))):
        conn = Connection(fail)
        db = object.__new__(p.Database)
        db.connect = lambda conn=conn: conn
        assert db.read_brand_registry() == ожидаемо
        assert conn.closed and conn.rolled
        if not fail:
            assert conn.c.sql == [brands.ЕСТЬ_РЕЕСТР_SQL]


# ── Страница ─────────────────────────────────────────────────────────────────

HTML = (ROOT / "public" / "library.html").read_text(encoding="utf-8")


def test_страница_ведёт_на_p_и_brands_одним_ключом_и_не_сводит_сама():
    тело = re.search(r"function brandDetails\(article\) \{.*?\n  \}\n", HTML, re.S).group(0)
    assert "const key=encodeURIComponent(b.key)" in тело
    assert "'/p#brand='+key" in тело and "'/brands#b='+key" in тело
    assert "b.known===true" in тело
    # Своего сведения нет: страница не держит словаря брендов.
    assert "library_brand" in HTML and "oem.json" not in HTML
    assert "key==='oem'&&kind(article)==='component'?brandDetails(article)" in HTML
    assert "identity=[brandLabel(article)" in HTML


@pytest.mark.skipif(not shutil.which("node"), reason="нет node")
def test_страница_показывает_имя_правила_и_написание_источника():
    функции = "".join(re.search(r"(  function " + имя + r"\(article\) \{.*?\n)(?=  function )", HTML, re.S).group(1)
                      for имя in ("componentBrands", "brandLabel", "brandDetails"))
    р = реестр()
    статьи = [компонент(1, "SKF (Швеция)"), компонент(2, "Прочая Лавка"), компонент(3, "SKF")]
    for s in статьи:
        пр = p.component_brand(s["sources"]["component_fields"], р)
        if пр:
            s["sources"]["library_brand"] = пр
    статьи.append(компонент(4, "Без проекции"))
    статьи.append(компонент(5, "SKF", library_brand={"version": 2, "brands": [{"key": "x", "name": "Чужое"}]}))
    js = """
const mk=(tag,cls,t)=>({tag,cls,text:t==null?'':String(t),kids:[],href:null,
  append(...k){this.kids.push(...k)},set textContent(v){this.text=String(v)},get textContent(){return this.text}});
const document={createTextNode:t=>({tag:'#text',text:String(t),kids:[]})};
const node=(tag,cls,v)=>mk(tag,cls,v);
const link=(label,href,cls)=>{const a=mk('a',cls,label);a.href=href;return a;};
const text=v=>typeof v==='string'?v:(typeof v==='number'?String(v):'');
const normalize=v=>text(v).normalize('NFKC').toLocaleLowerCase('ru').replace(/ё/g,'е');
function sourceInfo(a){const v=a.sources;return v&&typeof v==='object'?v:{};}
const flat=n=>n.text+n.kids.map(flat).join('');
const links=n=>(n.href?[n.href]:[]).concat(...n.kids.map(links));
""" + функции + """
const out=ARTICLES.map(a=>{const dd=brandDetails(a);return {label:brandLabel(a),shown:flat(dd),links:links(dd)};});
console.log(JSON.stringify(out));
"""
    js = js.replace("ARTICLES", json.dumps(статьи, ensure_ascii=False))
    вывод = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30, check=True).stdout
    r1, r2, r3, r4, r5 = json.loads(вывод)
    assert r1["label"] == "SKF" and r1["links"] == ["/p#brand=skf", "/brands#b=skf"]
    assert "в источнике: SKF (Швеция)" in r1["shown"]
    assert r2["label"] == "Прочая Лавка" and r2["links"] == [] and "в источнике" not in r2["shown"]
    assert r3["label"] == "SKF" and "в источнике" not in r3["shown"]
    # Проекции нет или она чужой версии — написание источника как есть, без ссылок.
    assert (r4["label"], r4["shown"], r4["links"]) == ("Без проекции", "Без проекции", [])
    assert (r5["label"], r5["links"]) == ("SKF", [])
