"""Снимок чатов сделок в двух частях: публичный файл — только числа, закрытое — в KV chat:v1.

Проверяется то, что даёт УТЕЧКУ или ПОТЕРЮ:
1. В публичной части нет ни одной строки: названий сделок, оценок, комментариев.
2. Перенос из файла прежней схемы ничего не теряет: «ждут ответа» и оценки —
   в закрытой части дословно, а вкладка «Коммерсанты» до и после переноса
   получает одно и то же.
3. Порядок записи: KV и сверка чтением — раньше файла. Не записался KV — файл
   не тронут.
4. Журнал прогона — только числа: ни фамилий, ни uid, ни сделок и чатов.
5. Портал ключ chat:v1 не читает.

Корпус придуман (правило 18).
"""
from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path

import pytest

import chat_scan
import reps

ROOT = Path(__file__).resolve().parents[1]

UID_A, UID_B = "900123", "900456"
LABEL_A, NAME_A = "Образцов", "Образцов Пётр"
LABEL_B, NAME_B = "Примеров", "Примеров Иван"
TITLE = "Насос-образец для стенда"
DEAL, CHAT = "770077", 550055


def запись(n: int, *, ждут: bool = False) -> dict:
    r = {"mentions": n, "self": n - 1, "coll": 1, "none": 0, "medMin": 10 + n, "withinSla": 80,
         "w30": 70, "w120": 80, "w1440": 95, "answeredSelf": n - 1, "handledPct": 100,
         "presale": {"mentions": 1, "self": 1, "coll": 0, "none": 0, "medMin": 5, "withinSla": 100,
                     "w30": 100, "w120": 100, "w1440": 100, "answeredSelf": 1, "handledPct": 100},
         "real": {"mentions": 0, "self": 0, "coll": 0, "none": 0, "medMin": None, "withinSla": 0,
                  "w30": 0, "w120": 0, "w1440": 0, "answeredSelf": 0, "handledPct": 0},
         "waiting": [], "waitN": 0, "waitGt1d": 0, "waitGt3d": 0, "waitMaxDays": 0,
         "activeChats": 3, "lastPostDays": 0.5}
    if ждут:
        r["waiting"] = [{"chat": CHAT, "deal": DEAL, "t": f"{TITLE} {n}", "ageH": 30.0 + n,
                         "ageDays": 1.3, "kind": "тег без ответа"}]
        r.update(waitN=1, waitGt1d=1, waitMaxDays=1.3)
    return r


def день(i: int) -> str:
    return (dt.date(2031, 1, 1) + dt.timedelta(days=i)).isoformat()


def прежний_снимок(дней: int = 15) -> dict:
    """Файл прежней схемы: «ждут ответа» с названиями сделок и оценки — в открытую."""
    days = {день(i): {UID_A: запись(i + 2, ждут=i % 2 == 0), UID_B: запись(i + 3)} for i in range(дней)}
    return {"days": days, "updated": "2031-01-15T00:10:00",
            "assessment": {UID_A: {"eng": 4, "prof": 5, "comment": "придуманный отзыв", "date": "2031-01-01",
                                   "model": "модель-образец", "nMsgs": 12}}}


class Хранилище:
    def __init__(self, val=None, fail=None):
        self.val, self.fail, self.puts = val, fail, 0

    def get(self):
        return copy.deepcopy(self.val)

    def put(self, raw):
        if self.fail:
            raise chat_scan.ScanError(self.fail)
        self.puts += 1
        self.val = json.loads(raw)


def сканер(n: int = 5):
    def scan():
        a = запись(n, ждут=True); a["samples"] = [{"deal": DEAL, "ctx": "", "msg": "придуманная реплика"}]
        b = запись(n + 1); b["samples"] = []
        return {UID_A: a, UID_B: b}, {LABEL_A: UID_A, LABEL_B: UID_B}, {UID_A: NAME_A, UID_B: NAME_B}
    return scan


@pytest.fixture
def сегодня(monkeypatch):
    monkeypatch.setattr(chat_scan, "_msk_now", lambda: dt.datetime(2031, 1, 16, 0, 5))
    return день(15)


def записать(tmp_path, снимок) -> Path:
    p = tmp_path / "chat_snapshot.json"
    p.write_text(json.dumps(снимок, ensure_ascii=False), encoding="utf-8")
    return p


# 1 ─ публичная часть ─────────────────────────────────────────────────────────

def test_публичная_запись_белым_списком():
    r = запись(4, ждут=True)
    r["note"] = "строка, которой в публичном файле не место"
    r["presale"]["t"] = TITLE
    pub = chat_scan.public_record(r)
    assert "waiting" not in pub and "note" not in pub and "t" not in pub["presale"]
    assert pub["waitN"] == 1 and pub["presale"]["mentions"] == 1
    assert not any(isinstance(v, str) for v in pub.values())


def test_проверка_публичной_части_ловит_текст():
    pub, _ = chat_scan.split_snapshot(прежний_снимок(2))
    chat_scan.check_public(pub)
    плохой = copy.deepcopy(pub)
    плохой["days"][день(0)][UID_A]["waiting"] = [{"t": TITLE}]
    with pytest.raises(chat_scan.ScanError, match="PUBLIC_HAS_TEXT"):
        chat_scan.check_public(плохой)
    плохой = copy.deepcopy(pub)
    плохой["assessment"] = {}
    with pytest.raises(chat_scan.ScanError, match="PUBLIC_HAS_ASSESSMENT"):
        chat_scan.check_public(плохой)


# 2 ─ перенос без потерь и вкладка до/после ──────────────────────────────────

def test_перенос_ничего_не_теряет():
    снимок = прежний_снимок()
    pub, priv = chat_scan.split_snapshot(снимок)
    было = sum(len(r["waiting"]) for emps in снимок["days"].values() for r in emps.values())
    assert chat_scan.counts(priv) == (8, было, 1)
    assert priv["assessment"][UID_A]["comment"] == "придуманный отзыв"
    текст = json.dumps(pub, ensure_ascii=False)
    assert TITLE not in текст and "придуманный отзыв" not in текст and DEAL not in текст


def test_вкладка_до_и_после_переноса_одинакова(tmp_path):
    снимок = прежний_снимок()
    до = reps._load_react_snapshot([UID_A, UID_B, "900789"], private={}, path=str(записать(tmp_path, снимок)))
    pub, priv = chat_scan.split_snapshot(снимок)
    p = tmp_path / "pub.json"; p.write_text(json.dumps(pub), encoding="utf-8")
    после = reps._load_react_snapshot([UID_A, UID_B, "900789"], private=priv, path=str(p))
    assert до == после
    assert до[UID_A]["waiting"] and до[UID_A]["assessment"]["eng"] == 4


def test_без_закрытой_части_числа_те_же(tmp_path):
    снимок = прежний_снимок()
    до = reps._load_react_snapshot([UID_A], private={}, path=str(записать(tmp_path, снимок)))[UID_A]
    pub, _ = chat_scan.split_snapshot(снимок)
    p = tmp_path / "pub.json"; p.write_text(json.dumps(pub), encoding="utf-8")
    после = reps._load_react_snapshot([UID_A], private={}, path=str(p))[UID_A]
    assert после["waiting"] == [] and после["assessment"] is None
    assert {k: v for k, v in до.items() if k not in ("waiting", "assessment")} == \
           {k: v for k, v in после.items() if k not in ("waiting", "assessment")}


def test_закрытая_часть_из_файла_сборки(tmp_path, monkeypatch):
    pub, priv = chat_scan.split_snapshot(прежний_снимок())
    p = tmp_path / "pub.json"; p.write_text(json.dumps(pub), encoding="utf-8")
    f = tmp_path / "priv.json"; f.write_text(json.dumps(priv), encoding="utf-8")
    monkeypatch.setenv("CHAT_PRIVATE_PATH", str(f))
    r = reps._load_react_snapshot([UID_A], path=str(p))[UID_A]
    assert r["waiting"][0]["t"].startswith(TITLE) and r["assessment"]["prof"] == 5
    f.write_text(json.dumps({"version": 99, "assessment": {UID_A: {"eng": 1}}}), encoding="utf-8")
    assert reps._load_react_snapshot([UID_A], path=str(p))[UID_A]["assessment"] is None


# 3 ─ прогон: порядок записи, хранение, отказ ────────────────────────────────

def test_прогон_переносит_и_пишет_kv_раньше_файла(tmp_path, сегодня, capsys):
    p = записать(tmp_path, прежний_снимок())
    kv = Хранилище()
    assert chat_scan.main([], store=kv, scanner=сканер(), snap_path=p, samples_path=tmp_path / "s.json") == 0
    pub = json.loads(p.read_text(encoding="utf-8"))
    chat_scan.check_public(pub)
    assert sorted(pub["days"]) == [день(i) for i in range(2, 16)]          # 14 дней
    assert sorted(kv.val["days"]) == sorted(d for d in pub["days"] if kv.val["days"].get(d))
    assert kv.val["days"][сегодня][UID_A]["waiting"][0]["t"] == f"{TITLE} 5"
    assert kv.val["days"][день(4)] == {UID_A: {"waiting": прежний_снимок()["days"][день(4)][UID_A]["waiting"]}}
    assert день(0) not in kv.val["days"]                                  # вне хранения — как в файле
    assert kv.val["assessment"][UID_A]["nMsgs"] == 12
    # 4 ─ журнал: только числа
    out = capsys.readouterr().out
    for секрет in (UID_A, UID_B, LABEL_A, LABEL_B, NAME_A, TITLE, DEAL, str(CHAT), "придуманн"):
        assert секрет not in out, секрет


def test_отказ_kv_не_трогает_файл(tmp_path, сегодня):
    p = записать(tmp_path, прежний_снимок())
    было = p.read_bytes()
    kv = Хранилище(fail="CLOUDFLARE_KV_ACCESS_DENIED")
    assert chat_scan.main([], store=kv, scanner=сканер(), snap_path=p, samples_path=tmp_path / "s.json") == 1
    assert p.read_bytes() == было


def test_сверка_чтением_ловит_расхождение(tmp_path, сегодня):
    class CF:
        def __init__(self): self.v, self.sleeps = None, []
        def put(self, ns, key, raw): self.v = raw + b" "                   # записалось не то
        def get(self, ns, key): return self.v
        def sleep(self, s): self.sleeps.append(s)
    cf = CF()
    p = записать(tmp_path, прежний_снимок())
    было = p.read_bytes()
    store = chat_scan.KVStore(cf, "0" * 32, (0, 2, 5))
    assert chat_scan.main([], store=store, scanner=сканер(), snap_path=p, samples_path=tmp_path / "s.json") == 1
    assert p.read_bytes() == было and cf.sleeps == [2, 5]


def test_вхолостую_ничего_не_пишет(tmp_path, сегодня):
    p = записать(tmp_path, прежний_снимок())
    было = p.read_bytes()
    kv = Хранилище()
    assert chat_scan.main(["--dry-run"], store=kv, scanner=сканер(), snap_path=p,
                          samples_path=tmp_path / "s.json") == 0
    assert kv.puts == 0 and kv.val is None and p.read_bytes() == было


def test_следующий_день_оценка_остаётся_в_kv(tmp_path, monkeypatch, сегодня):
    p = записать(tmp_path, прежний_снимок())
    kv = Хранилище()
    chat_scan.main([], store=kv, scanner=сканер(), snap_path=p, samples_path=tmp_path / "s.json")
    monkeypatch.setattr(chat_scan, "_msk_now", lambda: dt.datetime(2031, 1, 17, 0, 5))
    assert chat_scan.main([], store=kv, scanner=сканер(7), snap_path=p, samples_path=tmp_path / "s.json") == 0
    assert kv.val["assessment"][UID_A]["eng"] == 4
    assert sorted(json.loads(p.read_text(encoding="utf-8"))["days"])[-1] == день(16)
    assert день(2) not in kv.val["days"] and kv.val["days"][день(16)][UID_A]["waiting"][0]["t"] == f"{TITLE} 7"


def test_перезапуск_в_тот_же_день_заменяет_день(tmp_path, сегодня):
    p = записать(tmp_path, прежний_снимок())
    kv = Хранилище()
    chat_scan.main([], store=kv, scanner=сканер(), snap_path=p, samples_path=tmp_path / "s.json")

    def никто_не_ждёт():
        react, ids, names = сканер()()
        react[UID_A]["waiting"] = []
        return react, ids, names
    chat_scan.main([], store=kv, scanner=никто_не_ждёт, snap_path=p, samples_path=tmp_path / "s.json")
    assert сегодня not in kv.val["days"]


def test_выгрузка_закрытой_части_для_сборки(tmp_path):
    out = tmp_path / "priv.json"
    assert chat_scan.main(["--fetch-private", str(out)], store=Хранилище()) == 0
    assert not out.exists()                                                 # ключа ещё нет — файла нет
    _, priv = chat_scan.split_snapshot(прежний_снимок())
    assert chat_scan.main(["--fetch-private", str(out)], store=Хранилище(priv)) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == priv


# 5 ─ портал ключ не читает ──────────────────────────────────────────────────

def test_портал_не_читает_закрытый_ключ():
    assert chat_scan.KV_KEY not in (ROOT / "public" / "_worker.js").read_text(encoding="utf-8")
