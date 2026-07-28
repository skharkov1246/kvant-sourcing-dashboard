"""Проверки приведения к стандартным рядам — Р-08 п. 3.3, Р-10 пп. 4–5.

Случаи взяты прямо из текста регламента: если тест падает, значит приложение
начнёт советовать не то, что написано в методике.
"""

import pytest

from app.series import (
    ISO3601_D2,
    analyze_surface,
    classify_thread,
    gasket_thickness,
    gost6636_series,
    match_oring,
    oring_d1_from_perimeter,
    restore_nominal,
)


class TestGost6636:
    def test_ra20_decade_matches_standard(self):
        ra20 = [v for v in gost6636_series("Ra20", 1.0, 10.0)]
        assert ra20 == [1.0, 1.1, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.5, 2.8,
                        3.2, 3.6, 4.0, 4.5, 5.0, 5.6, 6.3, 7.1, 8.0, 9.0, 10.0]

    def test_coarser_rows_are_subsets(self):
        ra40 = set(gost6636_series("Ra40", 1.0, 100.0))
        for row in ("Ra20", "Ra10", "Ra5"):
            assert set(gost6636_series(row, 1.0, 100.0)) <= ra40

    def test_ra10_is_every_fourth(self):
        assert gost6636_series("Ra10", 1.0, 10.0) == [1.0, 1.2, 1.6, 2.0, 2.5, 3.2, 4.0, 5.0, 6.3, 8.0, 10.0]


class TestRestoreNominal:
    def test_worn_shaft_rounds_up(self):
        """Изношенный вал: реальный размер был больше измеренного (Р-08 п. 3.3)."""
        g = restore_nominal(44.82, direction="shaft", series="Ra20")
        assert g.nominal_mm == 45.0
        assert g.deviation_mm > 0
        assert "вверх" in g.rationale

    def test_worn_bore_rounds_down(self):
        """Изношенное отверстие разбито — номинал вниз."""
        g = restore_nominal(45.31, direction="bore", series="Ra20")
        assert g.nominal_mm == 45.0
        assert g.deviation_mm < 0
        assert "вниз" in g.rationale

    def test_unworn_surface_rounds_to_nearest(self):
        assert restore_nominal(45.4, direction="none", series="Ra20").nominal_mm == 45.0

    def test_inch_origin_is_flagged_not_silently_metricated(self):
        """25.4 мм = 1 дюйм: методика прямо запрещает тихо округлять в метрику."""
        g = restore_nominal(25.38, direction="shaft")
        assert g.inch_candidate_mm == pytest.approx(25.4, abs=0.01)
        assert "дюймов" in g.rationale.lower() or "дюймо" in g.rationale.lower()

    def test_rationale_is_ready_for_the_protocol_field(self):
        # Р-08 п. 3.2 требует обоснования принятого номинала в протоколе —
        # оно должно быть готовым текстом, а не «додумай сам».
        g = restore_nominal(19.9, direction="shaft")
        assert "ГОСТ 6636" in g.rationale and str(g.nominal_mm).rstrip("0").rstrip(".") in g.rationale

    def test_rejects_nonpositive(self):
        with pytest.raises(ValueError):
            restore_nominal(0, direction="shaft")


# Выборка внутренних диаметров ряда — в проде приезжает справочником с сервера.
D1_TABLE = (44.04, 45.62, 47.22, 48.82, 50.39)


class TestORing:
    def test_used_ring_section_rounds_up(self):
        """Р-10 п. 5.2: у б/у кольца сечение занижено обжатием — берём больший d2."""
        m = match_oring(d1_mm=47.05, d2_mm=5.55, used=True, d1_table=D1_TABLE)
        assert m.d2_mm == 5.70
        assert m.d1_mm == 47.22
        assert m.is_standard
        assert "категория A" in m.note

    def test_new_ring_takes_nearest_section(self):
        """У нового кольца деформации нет — ближайшее значение ряда."""
        m = match_oring(d1_mm=47.05, d2_mm=5.45, used=False, d1_table=D1_TABLE)
        assert m.d2_mm == 5.33

    def test_far_from_series_means_nonstandard(self):
        m = match_oring(d1_mm=47.05, d2_mm=9.9, used=True, d1_table=D1_TABLE, d2_table=ISO3601_D2)
        assert not m.is_standard
        assert "категория G" in m.note

    def test_without_d1_table_verdict_is_honest_not_guessed(self):
        """Ряд d1 не выводится из ряда линейных размеров — не выдумываем значение."""
        m = match_oring(d1_mm=47.05, d2_mm=5.55, used=True)
        assert m.d1_matched is False
        assert m.d1_deviation_mm is None
        assert m.d1_mm == 47.05          # вернули измеренное, а не подогнанное
        assert m.is_standard is False    # «не сверено» не равно «стандартное»
        assert "не сверялся" in m.note and "предварительный" in m.note

    def test_d1_from_perimeter(self):
        """d1 = P/π − d2, когда кольцо нельзя натянуть без растяжения."""
        import math
        d1, d2 = 47.22, 5.70
        perimeter = math.pi * (d1 + d2)
        assert oring_d1_from_perimeter(perimeter, d2) == pytest.approx(d1, abs=0.01)


class TestGasket:
    def test_used_gasket_rounds_up(self):
        """Б/у прокладка обжата — новая толще (Р-10 п. 5.3)."""
        value, why = gasket_thickness(1.12, used=True)
        assert value == 1.5
        assert "вверх" in why

    def test_new_gasket_rounds_to_nearest(self):
        assert gasket_thickness(1.12, used=False)[0] == 1.0

    def test_exact_series_value_is_kept(self):
        assert gasket_thickness(2.0, used=True)[0] == 2.0


class TestSurfaceStats:
    def test_shaft_base_is_max_reading(self):
        """Вал: база — максимум у краёв вне дорожки трения (Р-10 п. 4.1)."""
        s = analyze_surface([[45.01, 45.00], [44.82, 44.79], [45.00, 44.99]], surface="shaft")
        assert s.base_mm == 45.01
        assert "максимальн" in s.advice

    def test_bore_base_is_min_in_depth(self):
        """Отверстие: у кромки раструб, база — наименьший стабильный в глубине (Р-10 п. 4.2)."""
        s = analyze_surface([[60.4, 60.35], [60.02, 60.00], [60.38, 60.33]], surface="bore")
        assert s.base_mm == 60.00
        assert "глубин" in s.advice

    def test_heavy_wear_triggers_regulation_advice(self):
        s = analyze_surface([[45.00, 44.90], [44.82, 44.70], [45.00, 44.88]],
                            surface="shaft", expected_tolerance_mm=0.05)
        assert s.heavy_wear
        assert "Р-10 п. 4.1" in s.advice
        assert s.ovality_mm >= 0.1

    def test_clean_surface_does_not_cry_wolf(self):
        s = analyze_surface([[45.001, 45.000], [44.999, 45.000], [45.000, 45.001]],
                            surface="shaft", expected_tolerance_mm=0.05)
        assert not s.heavy_wear

    def test_regulation_minimums_are_enforced(self):
        with pytest.raises(ValueError, match="3 сечен"):
            analyze_surface([[45.0, 45.0], [45.0, 45.0]])
        with pytest.raises(ValueError, match="перпендикулярн"):
            analyze_surface([[45.0], [45.0], [45.0]])


class TestThread:
    def test_metric_thread(self):
        t = classify_thread(span_mm=7.5, turns=5)      # шаг 1.5 мм
        assert t.verdict == "metric" and t.metric_pitch_mm == 1.5

    def test_inch_thread_is_not_metricated(self):
        t = classify_thread(span_mm=25.4 / 11 * 6, turns=6)   # 11 ниток/дюйм
        assert t.verdict == "inch" and t.inch_tpi == 11
        assert "не переводить в метрику" in t.note.lower()

    def test_ambiguous_pitch_is_reported_honestly(self):
        # 25.4/20 = 1.27 мм — почти метрический 1.25: методика требует не гадать.
        t = classify_thread(span_mm=1.27 * 6, turns=6)
        assert t.verdict == "ambiguous"
        assert "резьбомер" in t.note

    def test_fewer_than_five_turns_is_refused(self):
        with pytest.raises(ValueError, match="5 неповреждённых витков"):
            classify_thread(span_mm=6.0, turns=4)
