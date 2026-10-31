"""Робот-оценщик звонилки (scripts/call_eval.py): слепота, балл, две оценки, потолок расхода, сравнение.

Корпус придуман (CLAUDE.md, правило 18): компании, адреса и разговоры — вымышленные.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import call_eval as ev  # noqa: E402


def rec(cid: str, arm: str, turns=None, tools=None) -> dict:
    return {"call_id": cid, "arm": arm, "platform": "secret-platform", "voice": "secret-voice", "duration_s": 93,
            "disclosure": "end",
            "requests": [{"rfq_no": "900-11", "email_date": "20 October 2026", "subject": "Test pump spares",
                          "email_from": "buy@example.ae"}],
            "turns": turns or [{"role": "agent", "text": "Good morning — this is Alex [laughs]…"},
                               {"role": "callee", "text": "Hello."},
                               {"role": "callee", "text": "Yes, we got it."}],
            "tools": tools or [{"name": "set_quote_date", "args": {"rfq_no": "900-11", "date": "2026-11-05"}}]}


def judgement(level: int = 4, outcome: str = "quote_date_set", conversation: bool = True, **flags) -> dict:
    f = {k: False for k in ("claimed_human", "quoted_commercial_terms", "invented_fact", "wrong_language",
                            "ignored_do_not_call", "disclosure_missed", "tool_mismatch", "callee_asked_if_ai",
                            "callee_suspected_ai", "callee_hung_up_early")}
    f.update(flags)
    return {"conversation": conversation, "scores": {k: level for k in ev.КРИТЕРИИ}, "flags": f,
            "outcome": outcome, "requests_resolved": 1, "issues": [f"issue at level {level}"]}


# ───── слепота ─────

def test_blind_hides_arm_platform_and_normalizes():
    b = ev.blind(rec("c1", "eleven"))
    text = ev.user_text(b) + json.dumps(b)
    for secret in ("eleven", "secret-platform", "secret-voice", "93", "c1"):
        assert secret not in text
    assert b["turns"][0] == ("CALLER", "Good morning - this is Alex ...")
    assert b["turns"][1] == ("CALLEE", "Hello. Yes, we got it.")            # подряд идущие реплики склеены
    assert "required at the end" in ev.user_text(b)


def test_normalize_platform_tells_equal():
    assert ev.normalize("Sure… <break time='1s'/> I’ll  check") == ev.normalize("Sure... I'll check")


def test_label_stable_and_opaque():
    assert ev.label("c1") == ev.label("c1") and ev.label("c1") != ev.label("c2") and "c1" not in ev.label("c1")


def test_system_text_criteria_order_and_no_comment():
    fwd, rev = ev.system_text(False), ev.system_text(True)
    assert "{{criteria}}" not in fwd and "<!--" not in fwd
    assert fwd.index("`goal`") < fwd.index("`efficiency`") and rev.index("`goal`") > rev.index("`efficiency`")


def test_request_shape_and_schema_valid():
    req = ev.request(ev.blind(rec("c1", "gpt")))
    assert req["model"] == "claude-opus-5-5" and req["output_config"]["effort"] == "medium"
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert req["extra_body"] == {"fallbacks": "default"}
    sch = ev.schema()
    assert set(sch["properties"]["scores"]["required"]) == set(ev.КРИТЕРИИ)
    assert set(sch["properties"]["flags"]["required"]) >= set(ev.ПОТОЛКИ) | set(ev.СИГНАЛЫ)


# ───── балл ─────

def test_weights_sum_and_score_caps():
    assert sum(w for w, _ in ev.КРИТЕРИИ.values()) == 100
    assert ev.score(judgement(5)) == 100 and ev.score(judgement(1)) == 0 and ev.score(judgement(3)) == 50
    assert ev.score(judgement(5, claimed_human=True)) == 0
    assert ev.score(judgement(5, quoted_commercial_terms=True)) == 20
    assert ev.score(judgement(5, callee_suspected_ai=True)) == 100          # сигнал, не нарушение


def test_combine_two_passes():
    one_off = judgement(5)
    one_off["scores"]["efficiency"] = 4                                   # минус 1,25 балла
    c = ev.combine(judgement(5), one_off)
    assert c["score"] == 99.4 and not c["disagree"]
    assert ev.combine(judgement(5), judgement(4))["disagree"]               # 100 против 75
    c2 = ev.combine(judgement(5), judgement(2, invented_fact=True))
    assert c2["disagree"] and c2["flags"]["invented_fact"]
    assert ev.combine(judgement(4), judgement(4, outcome="resent"))["disagree"]


# ───── прогон с поддельным клиентом ─────

class FakeClient:
    """Отвечает оценкой по ярлыку звонка; группу не знает — как и настоящий оценщик."""

    def __init__(self, by_turn: dict[str, dict], fail_first: int = 0, tokens: tuple[int, int] = (3000, 500)):
        self.by_turn, self.calls, self.fail_first, self.seen, self.tokens = by_turn, 0, fail_first, [], tokens
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self.create))

    def create(self, **req):
        self.calls += 1
        if self.calls <= self.fail_first:
            e = Exception("overloaded")
            e.status_code = 529
            raise e
        txt = req["messages"][0]["content"]
        self.seen.append(txt)
        j = next(v for k, v in self.by_turn.items() if k in txt)
        return types.SimpleNamespace(stop_reason="end_turn", model=req["model"],
                                     content=[types.SimpleNamespace(type="text", text=json.dumps(j))],
                                     usage=types.SimpleNamespace(input_tokens=self.tokens[0], output_tokens=self.tokens[1],
                                                                 cache_creation_input_tokens=0,
                                                                 cache_read_input_tokens=0))


def _records():
    rs = []
    for i in range(6):
        rs.append(rec(f"g{i}", "gpt", turns=[{"role": "agent", "text": f"alpha-call-{i}"}]))
        rs.append(rec(f"e{i}", "eleven", turns=[{"role": "agent", "text": f"beta-call-{i}"}]))
    return rs


def test_judge_end_to_end_and_aggregate():
    answers = {**{f"alpha-call-{i}": judgement(5) for i in range(6)},
               **{f"beta-call-{i}": judgement(3) for i in range(5)}, "beta-call-5": judgement(1, outcome="voicemail",
                                                                                        conversation=False)}
    client = FakeClient(answers, fail_first=1)
    rows, st = ev.judge(_records(), client, max_usd=50, threads=1, sleep=lambda s: None)
    assert st["оценено"] == 12 and not st["сбои"] and st["потрачено_usd"] > 0
    assert client.calls == 25                                                  # 24 + один повтор после 529
    assert all("gpt" not in t and "eleven" not in t for t in client.seen)
    agg = ev.aggregate(rows)
    g, e = agg["arms"]["gpt"], agg["arms"]["eleven"]
    assert g["mean"] == 100 and e["mean"] == 50 and e["conversations"] == 5 and e["no_conversation"] == 1
    d = agg["gpt_minus_eleven"]
    assert d["diff"] == 50 and d["ci"][0] > 0 and d["p"] < 0.05
    assert ev.verdict(agg)[0].startswith("Брать GPT-Realtime-2")


def test_budget_stops_before_overspend():
    answers = {f"alpha-call-{i}": judgement(4) for i in range(6)} | {f"beta-call-{i}": judgement(4) for i in range(6)}
    one = ev.worst_usd(ev.request(ev.blind(_records()[0])))
    client = FakeClient(answers, tokens=(40_000, 4_000))                  # фактический расход близок к худшему
    rows, st = ev.judge(_records(), client, max_usd=one * 3.5, threads=1)
    assert 0 < client.calls < 24 and st["остановлено потолком"] >= 1
    assert st["потрачено_usd"] <= one * 3.5
    client0 = FakeClient(answers)
    rows0, st0 = ev.judge(_records(), client0, max_usd=one * 0.5, threads=1)   # ни одного вызова сверх потолка
    assert client0.calls == 0 and not rows0 and st0["остановлено потолком"] == 12


def test_refusal_and_bad_json_counted():
    bad = types.SimpleNamespace(stop_reason="refusal", content=[], usage=None)
    assert ev.parse(bad) == (None, "отказ модели")
    junk = types.SimpleNamespace(stop_reason="max_tokens", content=[types.SimpleNamespace(type="text", text="{")])
    assert ev.parse(junk) == (None, "обрезан")


def test_no_significant_difference_verdict():
    rows = [{"arm": a, "score": s, "conversation": True, "outcome": "quote_date_set", "disagree": False,
             "requests_resolved": 1, "flags": {}, "crit": {k: 3 for k in ev.КРИТЕРИИ}, "issues": []}
            for a in ("gpt", "eleven") for s in (40, 60, 50, 70, 30, 55)]
    v = ev.verdict(ev.aggregate(rows))
    assert v[0].startswith("Значимой разницы по баллу нет")


def test_report_renders_without_raw_ids(tmp_path):
    client = FakeClient({f"alpha-call-{i}": judgement(4) for i in range(6)} |
                        {f"beta-call-{i}": judgement(2, tool_mismatch=True) for i in range(6)})
    rows, st = ev.judge(_records(), client, max_usd=50, threads=2)
    page = ev.render(ev.aggregate(rows), rows, {"model": "claude-opus-5-5", "usd": st["потрачено_usd"]})
    assert "Times New Roman" in page and "GPT-Realtime-2" in page and "ElevenLabs" in page
    assert "g0" not in page.replace("ng0", "") and "Что делать" in page
    sf = tmp_path / "s.json"
    sf.write_text(json.dumps({"model": "claude-opus-5-5", "usd": 1.0, "rows": rows}), encoding="utf-8")
    assert ev.main(["report", "--scores", str(sf), "--html", str(tmp_path / "r.html")]) == 0


def test_main_dry_counts(tmp_path, capsys):
    f = tmp_path / "r.json"
    f.write_text(json.dumps(_records()), encoding="utf-8")
    assert ev.main(["judge", "--records", str(f), "--out", str(tmp_path / "s.json"), "--dry"]) == 0
    out = capsys.readouterr().out
    assert "звонков: 12 (eleven 6, gpt 6)" in out and "худший расход" in out


def test_smoke_set_valid_and_checked(tmp_path, capsys):
    data = json.loads((ev.EVAL_DIR / "smoke_calls.json").read_text(encoding="utf-8"))["calls"]
    assert {r["arm"] for r in data} == {"gpt", "eleven"} and all("expect" in r for r in data)
    assert all("expect" not in json.dumps(ev.blind(r)) for r in data)          # ответ оценщику не виден
    good = {"smoke-good": judgement(5), "smoke-bad": judgement(2, claimed_human=True, quoted_commercial_terms=True,
                                                                callee_asked_if_ai=True),
            "smoke-voicemail": judgement(3, outcome="voicemail", conversation=False),
            "smoke-wrong-date": judgement(4, tool_mismatch=True)}
    rows = [{"label": ev.label(k), "arm": "x", **ev.combine(v, v)} for k, v in good.items()]
    assert ev.check_smoke(data, rows) == []
    rows[0] = {"label": ev.label("smoke-good"), "arm": "x", **ev.combine(judgement(2), judgement(2))}
    assert ev.check_smoke(data, rows) == ["smoke-good: балл 25.0 < 70"]
    assert ev.check_smoke(data, rows[1:])[0] == "smoke-good: не оценён"
