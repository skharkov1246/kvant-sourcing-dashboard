"""Буфер Р-13 и гейты реестра: каждый тест — пункт регламента.

Смысл набора: правила Р-13/Р-03 должны исполняться кодом, а не памятью
владельца реестра. Тесты — зеркало CHECK-ов и триггеров schema_ri.sql.
"""

import datetime as dt

import pytest

from app.buffer import (
    RKD_ON_HOLD,
    RKD_REJECTED,
    SERVICE_BRAND,
    SERVICE_EQUIPMENT,
    Actor,
    Buffer,
    BufferError,
    BufferRow,
    WorkCalendar,
    advance_measure,
    advance_rkd,
    temp_folder_name,
)

OWNER = Actor("u-owner", "registry_owner")
ENGINEER = Actor("u-eng", "engineer")


def _row(**kw) -> BufferRow:
    defaults = dict(temp_folder_name="З-1041_WR-115", added_by="u-eng",
                    raw_folder_url="nextcloud://raw/1041")
    defaults.update(kw)
    return BufferRow(**defaults)


@pytest.fixture()
def buffer() -> Buffer:
    b = Buffer()
    b.directories["equipment_type"].values.add("Насос ЦНС")
    b.directories["brand"].values.add("WARMAN")
    return b


def _coded_part(buffer, **row_kw):
    row = buffer.submit(_row(**row_kw))
    return buffer.resolve_as_new(row.id, OWNER, name_ru="Вал ротора",
                                 keywords=["вал", "shaft"])


class TestSubmission:
    def test_row_without_raw_folder_is_returned_to_author(self, buffer):
        """Р-13 п.4.2: строка без ссылки на сырьё не разбирается — назад автору."""
        row = buffer.submit(_row(raw_folder_url=None))
        assert row.state == "RETURNED"
        assert "Р-13 п.4.2" in row.returned_reason

    def test_returned_row_comes_back_after_resubmit_with_raw(self, buffer):
        row = buffer.submit(_row(raw_folder_url=None))
        buffer.resubmit(row.id, "nextcloud://raw/1041")
        assert row.state == "PENDING" and row.returned_reason is None

    def test_free_text_in_directory_field_is_refused(self, buffer):
        """Р-03 п.2: свободный ввод в справочное поле — ошибка, а не запись."""
        with pytest.raises(BufferError, match="свободный ввод запрещён"):
            buffer.submit(_row(brand="сименс какой-то"))

    def test_unknown_becomes_service_value_not_emptiness(self, buffer):
        """Р-13 п.5.1: неизвестность — служебное значение справочника."""
        row = buffer.submit(_row(brand=None, equipment_type=None))
        assert row.brand == SERVICE_BRAND
        assert row.equipment_type == SERVICE_EQUIPMENT

    def test_unplanned_row_requires_who_handed_it_over(self, buffer):
        """Р-13 п.5.3: «подброшенная» позиция фиксируется с указанием, кто передал."""
        with pytest.raises(BufferError, match="Р-13 п.5.3"):
            buffer.submit(_row(unplanned=True))
        row = buffer.submit(_row(unplanned=True, unplanned_by="мастер цеха Иванов"))
        assert row.state == "PENDING"

    def test_on_hold_is_not_a_buffer_state(self, buffer):
        """Р-13 п.5.4: «На стопе» — статус реестра; в буфере такого состояния нет."""
        with pytest.raises(BufferError, match="Недопустимое состояние"):
            buffer.submit(_row(state="ON_HOLD"))


class TestResolution:
    def test_only_registry_owner_resolves(self, buffer):
        """Р-13 п.2: разбор буфера — исключительное право владельца реестра."""
        row = buffer.submit(_row())
        with pytest.raises(BufferError, match="владелец реестра"):
            buffer.resolve_as_new(row.id, ENGINEER, name_ru="Вал", keywords=["вал"])

    def test_codes_are_monotonic_and_never_reused(self, buffer):
        """Р-03 п.3: KV-D-NNNNN сквозной; способа освободить код не существует."""
        p1 = _coded_part(buffer)
        p2 = _coded_part(buffer, temp_folder_name="З-1042_WR-116")
        assert (p1.kv_code, p2.kv_code) == ("KV-D-00001", "KV-D-00002")
        assert not hasattr(buffer.ledger, "release")

    def test_rejected_part_still_gets_code_and_record(self, buffer):
        """Р-03 п.3, п.5: забракованная позиция получает код — запись
        предотвращает повторный замер той же детали."""
        row = buffer.submit(_row())
        part = buffer.resolve_as_rejected(row.id, OWNER, name_ru="Кожух",
                                          reject_reason="нет маркировки")
        assert part.kv_code == "KV-D-00001"
        assert part.status_rkd == RKD_REJECTED
        assert row.state == "CLOSED_REJECTED"
        # следующий код продолжает нумерацию, а не занимает «освободившийся»
        assert _coded_part(buffer, temp_folder_name="x_y").kv_code == "KV-D-00002"

    def test_reject_reason_must_come_from_directory(self, buffer):
        row = buffer.submit(_row())
        with pytest.raises(BufferError, match="свободный ввод запрещён"):
            buffer.resolve_as_rejected(row.id, OWNER, name_ru="Кожух",
                                       reject_reason="просто не хочется")

    def test_duplicate_resolution_references_issued_code_only(self, buffer):
        part = _coded_part(buffer)
        row = buffer.submit(_row(temp_folder_name="З-1043_WR-115"))
        buffer.resolve_as_duplicate(row.id, OWNER, part.kv_code)
        assert row.state == "CLOSED_DUPLICATE" and row.duplicate_of == part.kv_code
        row2 = buffer.submit(_row(temp_folder_name="З-1044_WR-999"))
        with pytest.raises(BufferError, match="не выдавался"):
            buffer.resolve_as_duplicate(row2.id, OWNER, "KV-D-99999")

    def test_keywords_are_mandatory(self, buffer):
        """Р-04 п.5: без ключевых слов деталь не найти через год — поле обязательно."""
        row = buffer.submit(_row())
        with pytest.raises(BufferError, match="Р-04 п.5"):
            buffer.resolve_as_new(row.id, OWNER, name_ru="Вал", keywords=[])

    def test_closed_row_cannot_be_resolved_twice(self, buffer):
        row = buffer.submit(_row())
        buffer.resolve_as_new(row.id, OWNER, name_ru="Вал", keywords=["вал"])
        with pytest.raises(BufferError, match="уже закрыта"):
            buffer.resolve_as_new(row.id, OWNER, name_ru="Вал", keywords=["вал"])

    def test_dedup_candidates_by_article_and_by_brand_plus_name(self, buffer):
        part = _coded_part(buffer, article_norm="wr115")
        row = buffer.submit(_row(temp_folder_name="a_b", article_norm="wr115"))
        assert buffer.dedup_candidates(row.id) == [part]
        row2 = buffer.submit(_row(temp_folder_name="c_d",
                                  name_hint="вал ротора насоса"))
        assert part in buffer.dedup_candidates(row2.id)


class TestStaleRows:
    def test_stale_counts_business_days_not_calendar(self):
        """Р-13 п.6: 10 РАБОЧИХ дней. 14 календарных с двумя выходными
        подряд — ровно 10 рабочих, ещё не просрочка; 15-й календарный — уже да."""
        cal = WorkCalendar()
        buffer = Buffer(calendar=cal)
        monday = dt.date(2026, 7, 6)
        buffer.submit(_row(created_on=monday))
        assert buffer.stale_rows(monday + dt.timedelta(days=14)) == []
        assert len(buffer.stale_rows(monday + dt.timedelta(days=15))) == 1

    def test_holidays_extend_the_deadline(self):
        cal = WorkCalendar(holidays={dt.date(2026, 7, 13)})
        buffer = Buffer(calendar=cal)
        monday = dt.date(2026, 7, 6)
        buffer.submit(_row(created_on=monday))
        # праздник в середине сдвигает 11-й рабочий день на сутки позже
        assert buffer.stale_rows(monday + dt.timedelta(days=15)) == []
        assert len(buffer.stale_rows(monday + dt.timedelta(days=16))) == 1

    def test_closed_rows_are_never_stale(self, buffer):
        row = buffer.submit(_row(created_on=dt.date(2026, 1, 12)))
        buffer.resolve_as_new(row.id, OWNER, name_ru="Вал", keywords=["вал"])
        assert buffer.stale_rows(dt.date(2026, 7, 1)) == []


class TestRkdGates:
    def test_chain_moves_one_step_forward_only(self, buffer):
        part = _coded_part(buffer)
        with pytest.raises(BufferError, match="по цепочке"):
            advance_rkd(part, "КД")
        advance_rkd(part, "Замер")
        assert part.status_rkd == "Замер"

    def test_unknown_equipment_blocks_progress_past_measurement(self, buffer):
        """Р-13 п.5.2: без узла/среды нельзя выбирать материал и допуски —
        дальше «Замер» не пускаем, но сам замер разрешён."""
        part = _coded_part(buffer, equipment_type=None)
        assert part.equipment_type == SERVICE_EQUIPMENT
        advance_rkd(part, "Замер")                      # замер разрешён
        part.status_measure = "Принят"
        with pytest.raises(BufferError, match="Р-13 п.5.2"):
            advance_rkd(part, "Моделирование")

    def test_modeling_requires_accepted_measurement_protocol(self, buffer):
        """Р-03 п.5: «Замер → Моделирование» — только после приёмки протокола."""
        part = _coded_part(buffer, equipment_type="Насос ЦНС")
        advance_rkd(part, "Замер")
        with pytest.raises(BufferError, match="Р-03 п.5"):
            advance_rkd(part, "Моделирование")
        for status in ("В работе", "На проверке", "Принят"):
            advance_measure(part, status)
        advance_rkd(part, "Моделирование")
        assert part.status_rkd == "Моделирование"

    def test_hold_requires_condition_and_owner(self, buffer):
        """Р-13 п.5.4: «На стопе» без условия снятия и владельца — не статус,
        а способ потерять позицию."""
        part = _coded_part(buffer)
        with pytest.raises(BufferError, match="Р-13 п.5.4"):
            advance_rkd(part, RKD_ON_HOLD)
        advance_rkd(part, RKD_ON_HOLD,
                    stop_condition="появится заказ", stop_owner="u-kam")
        assert part.status_rkd == RKD_ON_HOLD

    def test_release_from_hold_returns_to_chain_and_clears_condition(self, buffer):
        part = _coded_part(buffer)
        advance_rkd(part, "Замер")
        advance_rkd(part, RKD_ON_HOLD, stop_condition="заказ", stop_owner="u-kam")
        advance_rkd(part, "Замер")
        assert part.status_rkd == "Замер"
        assert part.stop_condition is None and part.stop_owner is None

    def test_rejected_is_terminal(self, buffer):
        part = _coded_part(buffer)
        advance_rkd(part, RKD_REJECTED, reject_reason="нет смысла замерять")
        with pytest.raises(BufferError, match="терминальный"):
            advance_rkd(part, "Замер")

    def test_measure_axis_is_independent(self, buffer):
        """R-5: ось «Замер» движется независимо от «РКД»."""
        part = _coded_part(buffer)
        advance_measure(part, "В работе")
        assert (part.status_measure, part.status_rkd) == ("В работе", "Заявка")
        with pytest.raises(BufferError, match="Замер»:"):
            advance_measure(part, "Принят")  # тоже только по шагу


class TestFolderNaming:
    def test_planned_and_unplanned_patterns(self):
        """Р-13 п.3: {№ заказа}_{артикул} для плановых, {дата}_POSNN_{имя} для
        внеплановых."""
        assert temp_folder_name(order_no="З-1041", article="WR-115") == "З-1041_WR-115"
        assert temp_folder_name(date=dt.date(2026, 7, 28), pos=3,
                                name="втулка") == "2026-07-28_POS03_втулка"
        with pytest.raises(BufferError):
            temp_folder_name(order_no="З-1041")
