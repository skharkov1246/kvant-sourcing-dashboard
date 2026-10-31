"""Очередь звонилки (scripts/call_queue.py): телефоны, правила отбора, группировка, тексты агента.

Корпус придуман (CLAUDE.md, правило 18): компании, номера и адреса — вымышленные.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import call_queue as q  # noqa: E402

pytest.importorskip("phonenumbers")

TODAY = dt.date(2026, 11, 2)              # понедельник


def wd(a: dt.date, b: dt.date) -> int:
    """Будни Пн–Пт в (a, b] — в тестах вместо календаря РФ."""
    n, d = 0, a
    while d < b:
        d += dt.timedelta(days=1)
        n += d.weekday() < 5
    return n


def mail(day: str, direction: str = q.OUT, subj: str = "RFQ 900-11 Test pump spares", frm: str = "buy@example.ae",
         to: str = "sales@example.cn", owner: str = "11", i: int = 1) -> dict:
    return {"ID": i, "OWNER_ID": owner, "DIRECTION": direction, "CREATED": f"{day}T10:00:00+03:00", "SUBJECT": subj,
            "SETTINGS": {"EMAIL_META": {"from": frm, "to": to}}, "COMMUNICATIONS": []}


def card(cid: int, stage: str = "DT166_24:PREPARATION", company: str = "5", contact: str = "", offer: bool = False,
         deal: str = "900") -> dict:
    return {"id": cid, "title": f"RFQ {deal}-{cid}", "stageId": stage, "companyId": company, "parentId2": deal,
            "ufCrm18SupplContact": contact, "assignedById": 77, "_offer": offer}


# ───── телефоны ─────

def test_phone_e164_country_and_zone():
    assert q.phone_e164("+86 138 0013 8000")[:2] == ("+8613800138000", "CN")
    assert q.phone_e164("+86 138 0013 8000")[2] == "Asia/Shanghai"
    assert q.phone_e164("00852 2123 4567")[1] == "HK"
    assert q.phone_e164("(+65) 6123-4567")[1] == "SG"
    assert q.phone_e164("971 4 312 3456")[1] == "AE"          # без «+», но с кодом страны
    assert q.phone_e164("8 495 123-45-67") is None             # без кода страны не угадываем
    assert q.phone_e164("") is None and q.phone_e164("12345") is None


def test_multifield_mobile_first():
    assert q.multifield([{"VALUE": "+1", "VALUE_TYPE": "WORK"}, {"VALUE": "+2", "VALUE_TYPE": "MOBILE"}]) == ["+2", "+1"]


# ───── письма и причины ─────

def test_last_emails_out_and_any():
    m = q.last_emails([mail("2026-10-20"), mail("2026-10-27", q.IN, i=2), mail("2026-10-22", i=3)])
    assert m["11"]["out"]["t"].date() == dt.date(2026, 10, 22)
    assert m["11"]["any"].date() == dt.date(2026, 10, 27)
    assert m["11"]["out"]["from"] == "buy@example.ae" and m["11"]["out"]["to"] == "sales@example.cn"


def test_reason_sent_dialog_promise():
    m_old = q.last_emails([mail("2026-10-28")])["11"]           # Ср → Пн: 3 будних дня
    m_new = q.last_emails([mail("2026-10-30")])["11"]           # Пт → Пн: 1 будний
    assert q.reason(card(11), m_old, TODAY, wd) == "sent"
    assert q.reason(card(11), m_new, TODAY, wd) == ""
    assert q.reason(card(11, "DT166_24:UC_61BSRU"), m_old, TODAY, wd) == ""      # переписке нужно 5
    m_long = q.last_emails([mail("2026-10-20")])["11"]
    assert q.reason(card(11, "DT166_24:UC_61BSRU"), m_long, TODAY, wd) == "dialog"
    assert q.reason(card(11), None, TODAY, wd) == ""                              # без письма не звоним
    c = card(11)
    c["ufPromise"] = "2026-10-30T00:00:00+03:00"
    assert q.reason(c, None, TODAY, wd, "ufPromise") == "promise"


def test_human_date_english_without_locale():
    assert q.human_date(q.ts("2026-10-21T10:00:00+03:00")) == "21 October 2026"


# ───── сборка очереди ─────

SENDERS = {"example.ae": {"name": "Example Trading FZE", "city": "Dubai"}}


def _companies():
    return {"5": {"name": "Example Pumps Co", "phones": ["+86 138 0013 8000"], "dnc": False},
            "6": {"name": "Sample Valves Pte", "phones": ["+65 6123 4567"], "dnc": True},
            "7": {"name": "No Phone Ltd", "phones": ["123"], "dnc": False}}


def test_build_groups_by_company_and_skips():
    cards = [card(11), card(12, contact="C_31"), card(13, offer=True), card(14, company="6"), card(15, company="7"),
             card(16)]
    mails = q.last_emails([mail("2026-10-20", owner="11"), mail("2026-10-21", owner="12", i=2),
                           mail("2026-10-20", owner="14", i=3), mail("2026-10-20", owner="15", i=4)])
    contacts = {"31": {"name": "Li Wei", "phones": ["+86 139 0013 9000"]}}
    calls, st = q.build(cards, mails, contacts, _companies(), TODAY, wd)
    assert len(calls) == 1 and st["звонков"] == 1
    c = calls[0]
    assert c["phone"] == "+8613900139000" and c["contact_name"] == "Li Wei"     # телефон контакта первым
    assert c["country"] == "CN" and c["from_pool"] == "HK" and c["tz"] == "Asia/Shanghai"
    assert [r["card"] for r in c["requests"]] == ["11", "12"]                    # старейшее письмо первым
    assert c["requests"][0]["rfq_no"] == "900-11" and c["requests"][0]["email_date"] == "20 October 2026"
    assert st["есть КП — не звонить"] == 1 and st["компания: не звонить"] == 1
    assert st["нет годного телефона (компаний)"] == 1 and st["нет нашего письма в CRM"] == 1
    assert c["agent_vars"]["disclosure"] == "end" and "RFQ 900-12" in c["agent_vars"]["requests_block"]


def test_build_limits_requests_per_call_and_attempts():
    cards = [card(20 + i) for i in range(7)]
    acts = [mail(f"2026-10-{10 + i:02d}", owner=str(20 + i), i=i) for i in range(7)]
    cards[0]["ufTries"] = 3
    calls, st = q.build(cards, q.last_emails(acts), {}, _companies(), TODAY, wd, {"attempts": "ufTries"})
    assert len(calls[0]["requests"]) == 5 and st["попыток исчерпано"] == 1
    assert st["отложено на завтра (больше 5 в звонке)"] == 1


def test_pools_and_disclosure_by_country():
    assert q.ПУЛ_НОМЕРОВ["VN"] == "SG" and q.ПУЛ_НОМЕРОВ["TW"] == "HK" and q.ПУЛ_НОМЕРОВ["AE"] == "GB"
    assert q.ОБЪЯВЛЕНИЕ.get("DE") == "start" and q.ОБЪЯВЛЕНИЕ.get("CN") == "end" and q.ОБЪЯВЛЕНИЕ.get("MY") is None


# ───── тексты агента ─────

@pytest.mark.parametrize("phone,mode", [("+86 138 0013 8000", "end"), ("+49 30 1234567", "start"), ("+60 3 2123 4567", "off")])
def test_call_texts_rendered_without_placeholders(phone, mode):
    comps = {"5": {"name": "Example Pumps Co", "phones": [phone], "dnc": False}}
    calls, st = q.build([card(11)], q.last_emails([mail("2026-10-20")]), {}, comps, TODAY, wd,
                        settings={"agent_name": "Alex"}, countries=None, senders=SENDERS)
    c = calls[0]
    assert c["agent_vars"]["disclosure"] == mode and st["тексты: неподставленные переменные"] == 0
    assert "{{" not in c["prompt"] and "{{" not in c["voicemail"]
    assert "Example Trading FZE, Dubai" in c["prompt"] and "RFQ 900-11" in c["prompt"] and "20 October 2026" in c["voicemail"]
    assert "from Example Trading FZE, Dubai, purchasing department" in c["voicemail"]
    assert ("This call was assisted by AI" in c["voicemail"]) == (mode == "end")
    assert ("I am an AI assistant calling" in c["prompt"]) == (mode == "start")
    assert "Never say that you are a human" in c["prompt"]                       # правда на прямой вопрос — всегда


def test_no_texts_without_settings():
    calls, _ = q.build([card(11)], q.last_emails([mail("2026-10-20")]), {}, _companies(), TODAY, wd)
    assert "prompt" not in calls[0]


def test_agent_files_are_valid():
    tools = json.loads((q.AGENT_DIR / "tools.json").read_text(encoding="utf-8"))["tools"]
    assert {t["name"] for t in tools} == {"resend_rfq", "remind_email", "add_contact", "set_quote_date",
                                         "mark_declined", "escalate", "do_not_call"}
    schema = json.loads((q.AGENT_DIR / "postcall_schema.json").read_text(encoding="utf-8"))
    assert "outcome" in schema["required"]
    disc = json.loads((q.AGENT_DIR / "disclosure.json").read_text(encoding="utf-8"))
    assert set(disc) - {"_comment"} == {"start", "end", "off"}


# ───── группы слепого сравнения ─────

def test_arms_stable_and_balanced():
    arms = q.parse_arms("gpt:1,eleven:1,control:1")
    assert arms == [("gpt", 1), ("eleven", 1), ("control", 1)]
    assert q.parse_arms("bad,x:0") == q.parse_arms(q.ГРУППЫ_ПО_УМОЛЧАНИЮ)
    assert q.arm_of("12345", arms) == q.arm_of("12345", arms)                   # навсегда за компанией
    got = [q.arm_of(str(i), arms) for i in range(3000)]
    for name in ("gpt", "eleven", "control"):
        assert 900 < got.count(name) < 1100
    assert {q.arm_of(str(i), [("gpt", 1)]) for i in range(50)} == {"gpt"}


def test_build_sets_arm_per_company():
    calls, st = q.build([card(11)], q.last_emails([mail("2026-10-20")]), {}, _companies(), TODAY, wd,
                        arms=[("eleven", 1)])
    assert calls[0]["arm"] == "eleven" and st["группа eleven"] == 1


# ───── поставщик карточки: никогда не заказчик ─────

def test_crm_refs_parse():
    assert q.crm_refs(["CO_5", "C_7", "CT_8", "9", "CO_0", "x"]) == (["5", "9"], ["7", "8"])
    assert q.crm_refs("C_31") == ([], ["31"]) and q.crm_refs(None) == ([], [])


def test_supplier_of_skips_deal_customer():
    c = card(11, company="900")                                   # companyId унаследован от сделки
    assert q.supplier_of(c, "900", {}) == ("", [])
    c["ufCrm18Supplier"] = ["CO_5"]
    assert q.supplier_of(c, "900", {}) == ("5", [])
    c2 = card(12, company="900")
    c2["ufCrm18SupplContact"] = "C_31"
    assert q.supplier_of(c2, "900", {"31": "6"}) == ("6", ["31"])  # компания контакта поставщика
    assert q.supplier_of(card(13, company="5"), "900", {}) == ("5", [])   # companyId — не заказчик


def test_build_never_calls_customer():
    customer = {"900": {"name": "Customer JSC", "phones": ["+7 495 123 4567"], "dnc": False}}
    comps = {**_companies(), **customer}
    c_ok = card(11, company="900")
    c_ok["ufCrm18Supplier"] = ["CO_5"]
    c_bad = card(12, company="900")                               # поставщика нет — только заказчик
    calls, st = q.build([c_ok, c_bad], q.last_emails([mail("2026-10-20", owner="11"), mail("2026-10-20", owner="12", i=2)]),
                        {}, comps, TODAY, wd, deal_company={"900": "900"})
    assert [c["company_id"] for c in calls] == ["5"] and calls[0]["phone"].startswith("+86")
    assert st["поставщик не указан"] == 1
    assert st["компания карточки — не поставщик (взят из поля Supplier)"] == 1


# ───── номера записей CRM: карточка без сделки валила прогон ─────

def test_num_id_rejects_junk():
    assert [q.num_id(v) for v in (5, "17", " 0042 ", "CO_5", None, "", "None", 0, "0", 1.5, "²", True, "1" * 19)] == \
        ["5", "17", "42", "", "", "", "", "", "", "", "", "", ""]
    assert q.rfq_no({"id": 11, "parentId2": None}) == "11" and q.rfq_no({"id": 11, "parentId2": 900}) == "900-11"


def test_main_dry_run_with_cards_without_deal(monkeypatch, capsys):
    """Холостой прогон целиком на поддельном портале: карточки без сделки (None, «», 0), companyId
    пустой и мусорный — прогон доходит до счётчиков и звонит только поставщику."""
    import types

    now = q.dt.datetime.now(q.MSK)
    old = (now - q.dt.timedelta(days=12)).isoformat()
    cards = [{"id": 11, "title": "RFQ 900-11", "stageId": "DT166_24:PREPARATION", "companyId": 5, "parentId2": None},
             {"id": 12, "title": "RFQ 12", "stageId": "DT166_24:PREPARATION", "companyId": "", "parentId2": "",
              "ufCrm18Supplier": ["CO_6"]},
             {"id": 13, "title": "RFQ 13", "stageId": "DT166_24:PREPARATION", "companyId": None, "parentId2": 0,
              "ufCrm18SupplContact": "C_31"},
             {"id": 14, "title": "RFQ 900-14", "stageId": "DT166_24:PREPARATION", "companyId": 900, "parentId2": 900}]
    acts = [{"ID": i, "OWNER_ID": str(c["id"]), "DIRECTION": q.OUT, "CREATED": old, "SUBJECT": c["title"],
             "SETTINGS": {"EMAIL_META": {"from": "buy@example.ae", "to": "sales@example.cn"}}, "COMMUNICATIONS": []}
            for i, c in enumerate(cards, 1)]
    contacts = [{"ID": "31", "NAME": "Li", "LAST_NAME": "Wei", "PHONE": [{"VALUE": "+86 139 0013 9000"}], "COMPANY_ID": None}]
    comps = [{"ID": "5", "TITLE": "Example Pumps Co", "PHONE": [{"VALUE": "+86 138 0013 8000"}]},
             {"ID": "6", "TITLE": "Sample Valves Pte", "PHONE": [{"VALUE": "+65 6123 4567"}]},
             {"ID": "900", "TITLE": "Customer JSC", "PHONE": [{"VALUE": "+7 495 123 4567"}]}]
    seen_ids: dict[str, list] = {}
    mine = [{"ID": "1", "TITLE": "Example Trading FZE", "EMAIL": [{"VALUE": "office@example.ae"}], "IS_MY_COMPANY": "Y"},
            {"ID": "2", "TITLE": "ООО Пример", "EMAIL": [{"VALUE": "info@example.ru"}], "IS_MY_COMPANY": "Y"}]

    class FakeClient:
        def __init__(self, url):
            pass

        def list_items(self, et, select, filter):
            return [dict(c) for c in cards]

        def call(self, method, params):
            flt = params["filter"]
            if "IS_MY_COMPANY" in flt:
                return [x for x in mine if int(x["ID"]) > flt[">ID"]]
            key = "OWNER_ID" if method == "crm.activity.list" else "@ID"
            ids = {str(x) for x in flt[key]}
            seen_ids.setdefault(method, []).extend(flt[key])
            src = {"crm.activity.list": acts, "crm.contact.list": contacts, "crm.company.list": comps}[method]
            col = key.lstrip("@")
            return [x for x in src if str(x[col]) in ids and int(x["ID"]) > flt[">ID"]]

        def deals_by_ids(self, ids, select=None):
            seen_ids.setdefault("deals", []).extend(ids)
            return {str(i): {"ID": str(i), "COMPANY_ID": "900"} for i in ids}

    cfg = types.SimpleNamespace(SPA_CATEGORY_ID=24, SPA_ENTITY_TYPE_ID=166, RFQ_OFFER_FIELD="ufOffer",
                                Settings=types.SimpleNamespace(load=lambda: types.SimpleNamespace(bitrix_webhook_url="x")))
    monkeypatch.setitem(sys.modules, "config", cfg)
    monkeypatch.setitem(sys.modules, "period", types.SimpleNamespace(workdays_ru=wd))
    monkeypatch.setitem(sys.modules, "bitrix_client", types.SimpleNamespace(BitrixClient=FakeClient))
    for k in ("CALL_PROMISE_FIELD", "CALL_ATTEMPTS_FIELD", "CALL_DNC_FIELD", "CALL_ARMS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("CALL_SENDER_COMPANIES", raising=False)
    monkeypatch.setenv("CALL_AGENT_NAME", "Alex")
    assert q.main([]) == 0
    out = capsys.readouterr().out
    assert "карточек без сделки: 3" in out and "звонков: 3" in out and "поставщик не указан: 1" in out
    assert "наших компаний в Битриксе: 2" in out and "наша компания: название не латиницей: 1" in out
    assert seen_ids["deals"] == ["900"]                                           # ни «None», ни «»
    assert all(isinstance(x, int) for m in ("crm.contact.list", "crm.company.list") for x in seen_ids[m])


# ───── страны пилота ─────

def test_parse_countries():
    assert q.parse_countries("") == q.СТРАНЫ_ПИЛОТА and q.parse_countries("x, 1") == q.СТРАНЫ_ПИЛОТА
    assert q.parse_countries("cn, AE") == frozenset({"CN", "AE"}) and q.parse_countries("*") is None
    assert {"CN", "HK", "AE", "IN", "SG", "VN"} <= q.СТРАНЫ_ПИЛОТА and not {"RU", "TR", "DE", "US"} & q.СТРАНЫ_ПИЛОТА
    assert q.ПУЛ_НОМЕРОВ["IN"] == "GB" and q.ОБЪЯВЛЕНИЕ.get("IN") is None      # Индия: номер +44, без объявления


def test_build_skips_countries_outside_pilot():
    comps = {"5": {"name": "Example Pumps Co", "phones": ["+86 138 0013 8000"], "dnc": False},
             "6": {"name": "Sample Valves GmbH", "phones": ["+49 30 1234567"], "dnc": False}}
    cards = [card(11), card(12, company="6")]
    mails = q.last_emails([mail("2026-10-20", owner="11"), mail("2026-10-20", owner="12", i=2)])
    calls, st = q.build(cards, mails, {}, comps, TODAY, wd)
    assert [c["country"] for c in calls] == ["CN"] and st["вне пилота: страна DE"] == 1
    calls, _ = q.build(cards, mails, {}, comps, TODAY, wd, countries=None)
    assert sorted(c["country"] for c in calls) == ["CN", "DE"]


# ───── наша компания — та, с чьего адреса ушло письмо ─────

def test_email_domain_and_sender_map():
    assert q.email_domain("Buyer <buy@Mail.Example.AE>") == "mail.example.ae" and q.email_domain("no address") == ""
    assert q.web_domain("https://www.example.ae/about") == "example.ae"
    mine = [{"TITLE": "Example Trading FZE", "EMAIL": [{"VALUE": "a@example.ae"}], "WEB": [{"VALUE": "www.example-trading.com"}]},
            {"TITLE": "ООО Пример", "EMAIL": [{"VALUE": "b@example.ru"}]},
            {"TITLE": "Sample LLC", "EMAIL": [{"VALUE": "c@shared.example"}]},
            {"TITLE": "Other LLC", "EMAIL": [{"VALUE": "d@shared.example"}]}]
    m, st = q.sender_map(mine, {"example.hk": "Example HK Ltd", "@example.sg": {"name": "Example SG Pte", "city": "Singapore"},
                                "bad.example": "Пример"})
    assert m["example.ae"]["name"] == m["example-trading.com"]["name"] == "Example Trading FZE"
    assert "example.ru" not in m and "shared.example" not in m and "bad.example" not in m
    assert m["example.sg"] == {"name": "Example SG Pte", "city": "Singapore"} and m["example.hk"]["city"] == ""
    assert st["наша компания: название не латиницей"] == 1 and st["домен у нескольких наших компаний — отброшен"] == 1
    assert q.sender_of("Buyer <buy@mail.example.ae>", m)["name"] == "Example Trading FZE"       # поддомен
    assert q.sender_of("someone@gmail.com", m) is None and q.sender_of("", m) is None


def test_build_company_from_sender_and_split_by_sender():
    senders = {"example.ae": {"name": "Example Trading FZE", "city": "Dubai"}, "example.hk": {"name": "Example HK Ltd", "city": ""}}
    cards = [card(11), card(12), card(13)]
    mails = q.last_emails([mail("2026-10-20", owner="11", frm="buy@example.ae"),
                           mail("2026-10-20", owner="12", frm="Sales <s@example.hk>", i=2),
                           mail("2026-10-20", owner="13", frm="x@gmail.com", i=3)])
    calls, st = q.build(cards, mails, {}, _companies(), TODAY, wd, settings={"agent_name": "Alex"}, senders=senders)
    assert sorted(c["our_company"] for c in calls) == ["Example HK Ltd", "Example Trading FZE"]
    assert {c["company_id"] for c in calls} == {"5"}                         # один поставщик — два звонка
    assert st["поставщиков с запросами от разных наших компаний"] == 1
    assert st["отправитель письма не сопоставлен с нашей компанией"] == 1
    hk = next(c for c in calls if c["our_company"] == "Example HK Ltd")
    assert hk["agent_vars"]["our_company_intro"] == "Example HK Ltd" and "Dubai" not in hk["prompt"]
    assert "from Example HK Ltd, purchasing department" in hk["voicemail"]
