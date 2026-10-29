"""Сводка по сделке для модели — закрытый ключ dealdigest:v1 (решение владельца 25.10.2026).

  · сводка — из тех же строк, что вкладка «Реализация»: стадия, шаг, сроки, заказы,
    договоры, экономика, пресейл; только открытые сделки, только живые заказы, до восьми;
  · ни имён людей, ни названий заказчика, поставщика и сделки, ни номеров заказов;
  · ключи сделки — те, что читает chat_context.строки_сводки, и файл принимает
    chat_context.загрузить_сводку: сборка и оценка чатов говорят на одном языке;
  · публикация — после деплоя, только годная и непустая, со сверкой чтением; чтение — в
    файл оценки чатов; ключ Cloudflare — не у живой сверки;
  · main.py пишет файл только при DEALDIGEST_OUT; в журнал — только счётчики.

Корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

import contracts  # noqa: E402
import dealdigest  # noqa: E402
import dealdigest_kv as kv  # noqa: E402
import fixture  # noqa: E402

СЕЙЧАС = dt.datetime(2026, 10, 20, 3, 15, tzinfo=dt.timezone(dt.timedelta(hours=3)))
ПРЕСЕЙЛ = {"openDeals": [
    {"id": "7701", "t": "Сделка Выдуманная Тестова", "stage": "C9:NEW", "stageName": "Поиск поставщиков",
     "days": 5, "ball": "src", "who": "Ипполит Тестовый", "kam": "Аграфена Пробная", "kamId": "41",
     "kamGone": False, "flags": ["norfq", "chuzhoe"], "chat": None, "created": "2026-10-01T10:00:00+03:00"},
    {"id": "6002", "t": "дубль сделки реализации", "stage": "C9:NEW", "stageName": "Квалификация",
     "days": 1, "ball": "kam", "flags": [], "created": ""},
]}


@pytest.fixture(scope="module")
def сборка():
    r = fixture.build_realization()
    ctx = fixture.realctx_raw()
    import dealctx
    dg = dealdigest.build(r["contracts"]["rows"], contracts.life_all(r["contracts"]), r["dealctx"], ПРЕСЕЙЛ,
                          СЕЙЧАС, ctx=dealctx.clean(ctx))
    return r, dg


def _имена(r: dict) -> set[str]:
    """Все имена и названия корпуса: люди, заказчики, поставщики, названия сделок и заказов."""
    out: set[str] = {"Ипполит", "Тестовый", "Аграфена", "Выдуманная"}
    for row in r["contracts"]["rows"]:
        for k in ("kam", "manager", "oss", "ossDeal", "orkResp", "customer", "title"):
            v = row.get(k)
            if v and v not in ("—", ""):
                out.add(str(v))
        for s in str(row.get("suppliers") or "").split(","):
            if s.strip():
                out.add(s.strip())
        for o in row.get("orders") or []:
            for k in ("title", "sup", "oss", "stepWho"):
                v = o.get(k)
                if v and v not in ("—", "не назначен"):
                    out.add(str(v))
    return out


def test_сводка_открытых_сделок_по_форме_и_без_имён(сборка):
    r, dg = сборка
    assert dealdigest.check(dg) == ""
    rows = {x["deal"]: x for x in r["contracts"]["rows"]}
    открытые = {d for d, x in rows.items() if not x.get("closed")}
    assert открытые <= set(dg["deals"]), "у каждой открытой сделки реализации — сводка"
    assert not {d for d, x in rows.items() if x.get("closed")} & set(dg["deals"]), "закрытых нет"
    текст = json.dumps(dg, ensure_ascii=False)
    утекло = sorted(n for n in _имена(r) if n in текст)
    assert not утекло, f"в сводке имена или названия: {len(утекло)}"
    for d, x in dg["deals"].items():
        assert set(x) <= set(dealdigest.DEAL_KEYS), d
        живых = sum(1 for o in rows[d].get("orders") or [] if o.get("live")) if d in rows else 0
        assert len(x.get("orders") or []) == min(живых, dealdigest.ORDERS_MAX), d


def test_сводка_те_же_числа_что_на_вкладке(сборка):
    r, dg = сборка
    rows = {x["deal"]: x for x in r["contracts"]["rows"]}
    late = next(d for d, x in rows.items() if x.get("late") and not x.get("closed"))
    s = dg["deals"][late]
    assert s["dl"]["late"] is True and s["dl"]["lateDays"] == rows[late]["lateDays"]
    assert s["stage"]["name"] == dealdigest._s(rows[late]["stageName"])
    assert s["econ"]["dealSum"] == rows[late]["saleAmt"] and s["econ"]["dealTax"] == rows[late]["saleTax"]
    assert s["stage"]["step"] == ((rows[late].get("stepCell") or {}).get("step") or rows[late].get("dealStep"))
    assert s["econ"]["saleEur"] == rows[late]["saleEur"] and s["econ"]["cur"] == rows[late]["saleCur"]
    # пункты очереди этой сделки — подписями из закрытых таблиц, а не текстом пункта
    life = contracts.life_all(r["contracts"])
    подписи = {**life["labels"], **r["dealctx"]["labels"]}
    ждём = [dealdigest._s(подписи[q["code"]]) for q in life["queue"] + r["dealctx"]["queue"]
            if str(q["deal"]) == late and not q["code"].startswith("chat")]
    assert s["deal"]["flags"] == list(dict.fromkeys(ждём))[:dealdigest.FLAGS_MAX]
    # экономика: у сделки со сверенным файлом — продажа файла и вердикт сверки, как в карточке
    с_файлом = [d for d, c in r["dealctx"]["deals"].items() if c.get("econ") and d in dg["deals"]]
    assert с_файлом
    for d in с_файлом:
        e, cell = dg["deals"][d]["econ"], r["dealctx"]["deals"][d]["econ"]
        assert "fileSale" in e and e.get("gapPct") == cell.get("gap")
        assert ("gap" in e) == (cell.get("gap") is not None or bool(cell.get("gapNote")))
    # договоры: исход сверки и флаги — короткими подписями
    с_договором = [d for d, c in r["dealctx"]["deals"].items() if c.get("contract") and d in dg["deals"]]
    assert с_договором and all(dg["deals"][d]["contract"]["outcome"] for d in с_договором)
    # заказ — шаг, стадия, срок; без поставщика и номера
    o = next(x for x in dg["deals"][late]["orders"] if x.get("late"))
    assert {"step", "stage", "dl"} <= set(o) and not {"sup", "id", "title"} & set(o)


def test_пресейл_в_сводке_без_имён_и_без_дублей(сборка):
    _r, dg = сборка
    p = dg["deals"]["7701"]
    assert p == {"stage": {"name": "Поиск поставщиков", "days": 5},
                 "presale": {"ball": "сорсер", "flags": ["нет запроса поставщику"], "created": "2026-10-01"}}
    assert "presale" not in dg["deals"]["6002"], "сделка реализации не заменяется пресейлом"


def test_сводку_читает_оценка_чатов(сборка, tmp_path):
    import chat_context as cc
    _r, dg = сборка
    assert set(dealdigest.DEAL_KEYS) - {"orders"} <= set(cc.КЛЮЧИ_СВОДКИ)
    assert dealdigest.ORDERS_MAX == sum(1 for k in cc.КЛЮЧИ_СВОДКИ if k.startswith("o") and k[1:].isdigit())
    p = tmp_path / "dealdigest.json"
    dealdigest.write(p, dg)
    deals, состояние = cc.загрузить_сводку(str(p), СЕЙЧАС + dt.timedelta(hours=4))
    assert состояние == "есть" and set(deals) == set(dg["deals"])
    assert cc.загрузить_сводку(str(p), СЕЙЧАС + dt.timedelta(hours=cc.СВОДКА_ЧАСОВ + 1)) == (None, "устарела")
    late = next(d for d, x in dg["deals"].items() if (x.get("dl") or {}).get("late"))
    к = cc.Кандидат(late, "chat1", "real", "Стадия портала")
    строки = dict(cc.строки_сводки(deals[late], к))
    assert {"stage", "dl", "deal", "econ", "o1"} <= set(строки) <= set(cc.КЛЮЧИ_СВОДКИ)
    assert "late=да" in строки["dl"] and "name=Стадия портала" not in строки["stage"], "стадия — из сводки сборки"
    assert all(len(v) < 1200 for v in строки.values())
    пре = dict(cc.строки_сводки(deals["7701"], cc.Кандидат("7701", "c", "pre", "Поиск поставщиков")))
    assert пре["presale"].startswith("ball=сорсер") and "flags=нет запроса поставщику" in пре["presale"]


def test_строка_по_слову():
    assert dealdigest._s("а  б\nв") == "а б в"
    длинная = "доставлено, срок оплаты клиента прошёл, платёжки клиента в заказе нет"
    t = dealdigest._s(длинная)
    assert len(t) <= dealdigest.STR_MAX and t.endswith("…") and длинная.startswith(t[:-1])
    assert t == "доставлено, срок оплаты клиента прошёл, платёжки клиента в…"
    assert len(dealdigest._s("я" * 100)) == dealdigest.STR_MAX


@pytest.mark.parametrize("порча, код", [
    (lambda d: d.update(version=2), "DIGEST_BAD_FORM"),
    (lambda d: d.update(deals=[]), "DIGEST_BAD_FORM"),
    (lambda d: d.update(at="2026-10-20T03:15:00"), "DIGEST_BAD_AT"),
    (lambda d: d.update(at="вчера"), "DIGEST_BAD_AT"),
    (lambda d: d["deals"].update(x={}), "DIGEST_BAD_DEAL"),
    (lambda d: d["deals"].update({"1": {"manager": "лишний ключ"}}), "DIGEST_BAD_DEAL"),
    (lambda d: d["deals"].update({"1": {"orders": [{}] * (dealdigest.ORDERS_MAX + 1)}}), "DIGEST_BAD_DEAL"),
])
def test_форма_сводки(порча, код):
    d = {"version": 1, "at": СЕЙЧАС.isoformat(), "deals": {"1": {"stage": {"name": "x"}}}}
    assert dealdigest.check(d) == ""
    порча(d)
    assert dealdigest.check(d) == код


class Store:
    def __init__(self, raw=None):
        self.raw, self.puts = raw, 0

    def get_raw(self):
        return self.raw

    def put(self, raw):
        self.puts += 1
        self.raw = raw


def test_публикация_и_чтение_ключа(сборка, tmp_path):
    _r, dg = сборка
    src, dst = tmp_path / "out.json", tmp_path / "in" / "dealdigest.json"
    журнал: list[str] = []
    вызван = []
    # файла нет (сборка без «Реализации») — ключ не тронут, клиент KV даже не создаётся
    assert kv.publish(src, lambda: вызван.append(1), печать=журнал.append) == 0 and not вызван
    dealdigest.write(src, {**dg, "deals": {}})
    assert kv.publish(src, lambda: вызван.append(1), печать=журнал.append) == 0 and not вызван, "пустая — не пишется"
    dealdigest.write(src, dg)
    store = Store()
    assert kv.publish(src, store, печать=журнал.append) == 0 and store.puts == 1
    assert store.raw == src.read_bytes()
    assert kv.fetch(dst, store, печать=журнал.append) == 0 and dst.read_bytes() == src.read_bytes()
    # в журнале — только счётчики: ни номеров сделок, ни стадий, ни сумм
    текст = "\n".join(журнал)
    assert not any(d in текст for d in dg["deals"]) and "Поиск поставщиков" not in текст
    # ключа нет — файла нет, выход 0
    assert kv.fetch(tmp_path / "нет.json", Store(), печать=журнал.append) == 0
    assert not (tmp_path / "нет.json").exists()
    # не по форме — постоянный код, ключ не тронут, выход 1
    src.write_text(json.dumps({"version": 1, "at": "вчера", "deals": {}}), encoding="utf-8")
    store = Store(b"old")
    assert kv.main(["--publish", str(src)], store=store) == 1 and store.raw == b"old"
    assert kv.main(["--fetch", str(tmp_path / "x.json")], store=Store(b"{not json")) == 1
    assert not (tmp_path / "x.json").exists()


def test_сбой_ключа_мягкий_жёлтый_и_без_трассы(tmp_path, capsys):
    """Шаг мягкий («|| echo ::warning::»): сам скрипт пишет ::warning::, не ::error::; сбой
    клиента KV не по его коду — классом ошибки, без трассы (ревью 28.10.2026)."""
    class Обрыв(Store):
        def get_raw(self):
            raise ConnectionError("сеть https://example.invalid/секрет")
    assert kv.main(["--fetch", str(tmp_path / "x.json")], store=Обрыв()) == 1
    журнал = capsys.readouterr().out
    assert "::warning::" in журнал and "::error::" not in журнал and "ConnectionError" in журнал
    assert "example.invalid" not in журнал and "секрет" not in журнал
    assert kv.main(["--fetch", str(tmp_path / "x.json")], store=Store(b"{not json")) == 1
    assert "::warning::" in capsys.readouterr().out


def test_ключ_kv_свой_и_сверка_чтением():
    import realization_ctx_kv as rk

    class CF:
        def __init__(self):
            self.kv, self.keys = {}, []

        def get(self, ns, key):
            self.keys.append(key)
            return self.kv.get(key)

        def put(self, ns, key, raw):
            self.keys.append(key)
            self.kv[key] = raw

        def sleep(self, s):
            pass

    cf = CF()
    s = rk.KVStore(cf, "ns", (0,), key=dealdigest.KV_KEY)
    s.put(b"{}")
    assert s.get_raw() == b"{}" and set(cf.keys) == {"dealdigest:v1"}
    cf2 = CF()
    rk.KVStore(cf2, "ns", (0,)).put(b"x")
    assert set(cf2.keys) == {"realctx:v1"}, "свод чатов — прежний ключ"


def _wf(имя: str) -> dict:
    import yaml
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / имя).read_text(encoding="utf-8"))
    wf["on"] = wf.get("on", wf.get(True))
    return wf


def test_деплой_публикует_после_выкладки_а_оценка_чатов_читает():
    шаги = _wf("deploy.yml")["jobs"]["build-and-deploy"]["steps"]
    имена = [s.get("name") for s in шаги]
    ген = next(s for s in шаги if s.get("name", "").startswith("Генерация"))
    assert ген["env"]["DEALDIGEST_OUT"] == "${{ runner.temp }}/dealdigest.json"
    пуб = next(i for i, s in enumerate(шаги) if "dealdigest_kv.py --publish" in (s.get("run") or ""))
    assert пуб > имена.index("Деплой на Cloudflare Pages"), "в ключе — та же сборка, что на сайте"
    assert "|| echo \"::warning::" in шаги[пуб]["run"], "сбой публикации деплой не роняет"
    assert "CLOUDFLARE_API_TOKEN" in шаги[пуб]["env"]
    cc = _wf("chat-context.yml")["jobs"]["context"]["steps"]
    имена = [s.get("name") for s in cc]
    чт = next(i for i, s in enumerate(cc) if "dealdigest_kv.py --fetch" in (s.get("run") or ""))
    оц = имена.index("Оценка")
    assert чт < оц and cc[оц]["env"]["DEALDIGEST_PATH"] == "${{ runner.temp }}/dealdigest.json"
    assert "|| echo \"::warning::" in cc[чт]["run"], "нет ключа — оценка идёт со стадией"
    assert cc[-1]["run"] == "python scripts/realization_ctx.py --publish", "свод — по-прежнему последний"
    сверка = (ROOT / ".github" / "workflows" / "live-check.yml").read_text(encoding="utf-8")
    assert "CLOUDFLARE_API_TOKEN" not in сверка and "dealdigest_kv" not in сверка and "DEALDIGEST_OUT" not in сверка


def test_сборка_пишет_файл_только_при_переменной(сборка, tmp_path, monkeypatch, capsys):
    import dealctx
    import main
    r, _dg = сборка
    ctx = dealctx.clean(fixture.realctx_raw())
    monkeypatch.delenv("DEALDIGEST_OUT", raising=False)
    main._write_digest(r["contracts"], r["dealctx"], ctx, ПРЕСЕЙЛ)
    assert capsys.readouterr().out == ""
    out = tmp_path / "d" / "dealdigest.json"
    monkeypatch.setenv("DEALDIGEST_OUT", str(out))
    main._write_digest(r["contracts"], r["dealctx"], ctx, ПРЕСЕЙЛ)
    журнал = capsys.readouterr().out
    dg = json.loads(out.read_text(encoding="utf-8"))
    assert dealdigest.check(dg) == "" and "7701" in dg["deals"]
    assert "Сводка по сделкам" in журнал and not any(n in журнал for n in _имена(r))
    assert not any(d in журнал for d in dg["deals"])
    # «Реализации» нет (сбой вкладки) — файла нет: сводка из одного пресейла не заменит в ключе
    # полную (ревью 28.10.2026), публикация без файла ключ не трогает
    out.unlink()
    main._write_digest(None, None, None, ПРЕСЕЙЛ)
    assert not out.exists() and "без «Реализации»" in capsys.readouterr().out
    # пустая «Реализация» (открытых сделок нет) — сборка удалась, сводка пресейла годна
    main._write_digest({"rows": []}, None, None, ПРЕСЕЙЛ)
    assert set(json.loads(out.read_text(encoding="utf-8"))["deals"]) == {"7701", "6002"}
    capsys.readouterr()
    # сбой сборки сводки — только класс ошибки, сборка дашборда идёт дальше
    monkeypatch.setattr(dealdigest, "build", lambda *a, **k: 1 / 0)
    main._write_digest(r["contracts"], r["dealctx"], ctx, ПРЕСЕЙЛ)
    assert "ZeroDivisionError" in capsys.readouterr().out


def test_сводка_не_возвращает_модели_её_вчерашний_вердикт():
    """Пункты очереди по чату (chatReply, chatRisk, chatDue) и «подвисла», решённая оценкой
    чата, — вывод модели, а не учёт: в сводке их нет, и «главное» с одним основанием [d:deal]
    на них не встанет (ревью 28.10.2026)."""
    import dealctx
    import chat_context as cc
    from collections import Counter
    подписи_чата = {v[1] for k, v in dealctx.EXC.items() if k.startswith("chat")}
    assert len(подписи_чата) == 3
    payload = {"queue": [{"code": c, "deal": 101} for c in ("chatRisk", "chatDue", "chatReply", "econGap")],
               "labels": {k: v[1] for k, v in dealctx.EXC.items()}, "deals": {}}
    строки = [{"deal": 101, "seq": 7, "stageName": "Производство", "stageDays": 3.0, "closed": False, "orders": [],
               "slow": True, "stuck": True, "why": dealctx.PUBLIC_STUCK_MODEL},
              {"deal": 102, "seq": 8, "stageName": "Производство", "stageDays": 3.0, "closed": False, "orders": [],
               "slow": True, "stuck": True, "why": "простой по стадии · " + dealctx.PUBLIC_STUCK}]
    dg = dealdigest.build(строки, None, payload, None, СЕЙЧАС)
    assert dealdigest.check(dg) == ""
    текст = json.dumps(dg, ensure_ascii=False)
    assert not [p for p in подписи_чата if p in текст], "подпись пункта по чату ушла в сводку"
    d101 = dg["deals"]["101"]["deal"]
    assert d101["flags"] == [dealctx.EXC["econGap"][1]], "пункты учёта остаются"
    assert "stuck" not in d101 and "slow" not in d101, "простой по оценке модели — не учёт"
    d102 = dg["deals"]["102"]["deal"]
    assert d102.get("stuck") is True and d102.get("slow") is True, "простой по чтению чата сборкой — учёт"

    # только пункты по чату: строки [d:deal] нет вовсе, «главное» с одним основанием deal снято
    payload["queue"] = payload["queue"][:3]
    dg = dealdigest.build(строки[:1], None, payload, None, СЕЙЧАС)
    строки_сводки = dict(cc.строки_сводки(dg["deals"]["101"]))
    assert "deal" not in строки_сводки
    v = {"money": [], "contract": [], "sourcing": [], "important_conf": 0.8,
         "important": [{"kind": "срыв_срока_клиента", "severity": "высокая",
                        "statement": "Срок клиента сорван, риск высокий — разобрать",
                        "evidence_msg_ids": [], "evidence_keys": ["deal"]}]}
    сч = Counter()
    итог = cc.сверить_факты(v, {}, {}, None, None, строки_сводки, проход="сутки", сч=сч,
                            со_сводкой=bool(строки_сводки))
    assert not [f for f in итог if f.get("kind") == "срыв_срока_клиента"], "главное встало на вердикт модели"
