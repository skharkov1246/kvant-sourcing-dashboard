"""Протоколы категорий РИ (kv_router, kv_cat_a, kv_cat_g): схема + поведение.

Двухэшелонная проверка: (1) документ валиден по capture-protocol.schema.json —
иначе клиент его не исполнит; (2) ветвление и приёмка дают именно то поведение,
которое обещано регламентами Р-05/Р-10 — проверяется тем же интерпретатором,
который решает автоприём на сервере.
"""

import json
import pathlib

import jsonschema
import pytest

from app.protocol import SessionContext, StepResult, auto_accept, check_compatibility, visible_steps

SCHEMAS = pathlib.Path(__file__).resolve().parents[2] / "docs" / "schemas"
RI = SCHEMAS / "examples" / "ri"


def _load(name: str) -> dict:
    return json.loads((RI / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads((SCHEMAS / "capture-protocol.schema.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def router() -> dict:
    return _load("kv_router.json")


@pytest.fixture(scope="module")
def cat_a() -> dict:
    return _load("kv_cat_a.json")


@pytest.fixture(scope="module")
def cat_g() -> dict:
    return _load("kv_cat_g.json")


def _done(step_id: str, data: dict | None = None, score: float = 1.0) -> StepResult:
    return StepResult(step_id=step_id, status="COMPLETED", data=data or {}, quality_score=score)


class TestSchemaValidity:
    @pytest.mark.parametrize("name", ["kv_router.json", "kv_cat_a.json", "kv_cat_g.json"])
    def test_document_matches_schema(self, schema, name):
        jsonschema.validate(_load(name), schema)

    @pytest.mark.parametrize("name", ["kv_router.json", "kv_cat_a.json", "kv_cat_g.json"])
    def test_document_is_executable(self, name):
        assert check_compatibility(_load(name)) == {"ok": True}

    def test_step_ids_are_unique(self, router, cat_a, cat_g):
        for doc in (router, cat_a, cat_g):
            ids = [s["id"] for s in doc["steps"]]
            assert len(ids) == len(set(ids)), doc["code"]


class TestRouter:
    def test_three_taps_in_the_normal_case(self, router):
        """Р-05 п.1–2: в норме видимы ровно overview, mark, triage, start —
        поиск по каталогам скрыт, пока нет ответа «Не знаю»."""
        ctx = SessionContext()
        assert [s["id"] for s in visible_steps(router, ctx)] == [
            "r_overview", "r_mark", "r_triage", "r_start"]

    def test_catalog_search_appears_only_on_doubt(self, router):
        ctx = SessionContext(results={
            "r_triage": _done("r_triage", {"is_standard": "Не знаю"})})
        assert "r_search" in [s["id"] for s in visible_steps(router, ctx)]
        ctx.results["r_triage"].data["is_standard"] = "Стандартная"
        assert "r_search" not in [s["id"] for s in visible_steps(router, ctx)]


class TestCatA:
    def test_dimension_step_follows_subtype(self, cat_a):
        """Р-05 п.4: «—» в матрице = шага физически нет, а не required:false."""
        ctx = SessionContext(results={
            "a_decode": _done("a_decode", {"subtype": "подшипник"})})
        ids = [s["id"] for s in visible_steps(cat_a, ctx)]
        assert "a_dim_bearing" in ids
        assert "a_dim_ring" not in ids and "a_dim_fastener" not in ids
        assert "a_seal" not in ids  # эластомер — только кольца и манжеты

    def test_stop_factor_reveals_reclass_and_blocks_auto_accept(self, cat_a):
        """Р-05 A: не лёг в каталожный ряд → шаг переклассификации в C/G
        становится видимым, а reject_if исключает автоприём."""
        ctx = SessionContext(results={
            "a_mark": _done("a_mark"),
            "a_catalog": _done("a_catalog", {"series_matched": False}),
        })
        assert "a_reclass" in [s["id"] for s in visible_steps(cat_a, ctx)]
        assert auto_accept(cat_a, ctx) is False

    def test_happy_path_auto_accepts(self, cat_a):
        ctx = SessionContext(results={
            "a_mark": _done("a_mark", score=0.9),
            "a_catalog": _done("a_catalog", {
                "series_matched": True, "analog2_pn": "6205-2RS"}, score=0.9),
        })
        assert auto_accept(cat_a, ctx) is True

    def test_missing_second_analog_blocks_auto_accept(self, cat_a):
        """Р-05 A: «аналог минимум у 2 производителей» — без второго артикула
        сессия уходит на ручную проверку."""
        ctx = SessionContext(results={
            "a_mark": _done("a_mark", score=0.9),
            "a_catalog": _done("a_catalog", {"series_matched": True}, score=0.9),
        })
        assert auto_accept(cat_a, ctx) is False


class TestCatG:
    def test_readable_marking_hides_measurement_block(self, cat_g):
        """Р-10 п.8: читаемая торцевая маркировка манжеты скрывает обмер —
        главная экономия времени по категории G."""
        ctx = SessionContext(results={
            "g_type": _done("g_type", {"rti_type": "манжета/сальник"}),
            "g_mark_read": _done("g_mark_read", {"marking_readable": True}),
        })
        ids = [s["id"] for s in visible_steps(cat_g, ctx)]
        assert "g_mark_seal" in ids
        for hidden in ("g_seal_dims", "g_seal_profile", "g_seal_profile_photo"):
            assert hidden not in ids
        ctx.results["g_mark_read"].data["marking_readable"] = False
        assert "g_seal_dims" in [s["id"] for s in visible_steps(cat_g, ctx)]

    def test_oring_branch_does_not_show_gasket_steps(self, cat_g):
        ctx = SessionContext(results={
            "g_type": _done("g_type", {"rti_type": "O-ring", "groove_available": False})})
        ids = [s["id"] for s in visible_steps(cat_g, ctx)]
        assert {"g_oring_d2", "g_oring_d1", "g_oring_series"} <= set(ids)
        assert "g_groove" not in ids  # канавка недоступна
        assert not any(i.startswith("g_gasket") for i in ids)
        assert not any(i.startswith("g_seal") for i in ids)

    def test_groove_appears_when_available(self, cat_g):
        """«Канавка точнее кольца»: при доступной канавке d1 берём по ней."""
        ctx = SessionContext(results={
            "g_type": _done("g_type", {"rti_type": "O-ring", "groove_available": True})})
        assert "g_groove" in [s["id"] for s in visible_steps(cat_g, ctx)]

    def test_shore_skipped_on_thin_gasket(self, cat_g):
        """Р-10 п.12: тоньше 6 мм — показания Шора недостоверны, шаг не появляется."""
        base = {"g_type": _done("g_type", {"rti_type": "прокладка плоская"})}
        thin = SessionContext(results={**base,
            "g_gasket": _done("g_gasket", {"thickness_mm": 2.0})})
        assert "g_shore" not in [s["id"] for s in visible_steps(cat_g, thin)]
        thick = SessionContext(results={**base,
            "g_gasket": _done("g_gasket", {"thickness_mm": 6.0})})
        assert "g_shore" in [s["id"] for s in visible_steps(cat_g, thick)]

    def test_aging_only_for_used_parts(self, cat_g):
        new = SessionContext(results={
            "g_type": _done("g_type", {"rti_type": "O-ring", "used": False})})
        assert "g_aging" not in [s["id"] for s in visible_steps(cat_g, new)]

    def test_missing_service_conditions_reject_even_a_perfect_session(self, cat_g):
        """Р-10 п.10: без среды и температуры замер НЕ завершён — reject_if
        срабатывает, даже если всё остальное идеально."""
        ctx = SessionContext(results={
            "g_service": _done("g_service", {"t_min": -20, "t_max": 90}),  # нет medium
            "g_material": _done("g_material", {"elastomer": "NBR"}),
        })
        assert auto_accept(cat_g, ctx) is False

    def test_full_service_data_allows_auto_accept(self, cat_g):
        ctx = SessionContext(results={
            "g_service": _done("g_service", {
                "medium": "минеральное масло", "t_min": -20, "t_max": 90}, score=0.9),
            "g_material": _done("g_material", {"elastomer": "NBR"}, score=0.9),
        })
        assert auto_accept(cat_g, ctx) is True
