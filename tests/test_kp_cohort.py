"""КП по когортам недель (просьба владельца 31.10.2026): стадии КП — те же, что у зонда
scripts/probe_unpriced.py; дни до первого КП есть только у карточек, побывавших в стадии КП.
Корпус придуман (tests.fixture, CLAUDE.md, правило 18)."""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import stages  # noqa: E402
from tests import fixture  # noqa: E402


def test_стадии_кп_как_у_зонда():
    ids = stages.kp_stage_ids()
    assert set(ids) == {"DT166_24:UC_H49RUE", "DT166_24:1", "DT166_24:SUCCESS", "DT166_24:FAIL",
                        "DT166_24:4", "DT166_24:5"}
    # сверка вторым способом: каждая — стадия КП по классификатору, остальные известные — нет
    assert all(stages.classify_stage(s) in stages.KP_STAGES for s in ids)
    for s in ("DT166_24:NEW", "DT166_24:PREPARATION", "DT166_24:UC_61BSRU", "DT166_24:2", "DT166_24:3"):
        assert s not in ids and stages.classify_stage(s) not in stages.KP_STAGES


def test_дни_до_кп_только_у_стадий_кп():
    m = fixture.kp_cohorts(fixture.build_metrics())
    assert m["kpHist"] is True
    det = [d for s in m["sourcersA"] for d in s["details"]]
    with_kp = [d for d in det if "kpd" in d]
    assert with_kp and len(with_kp) < len(det)
    assert all(d["st"] in stages.KP_STAGES for d in with_kp)
    assert all(0 <= d["kpd"] < 40 for d in with_kp)
