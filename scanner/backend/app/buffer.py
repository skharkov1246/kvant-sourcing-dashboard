"""Буфер «Очередь на добавление» и гейты реестра — исполняемая половина Р-13.

Зеркало `schema_ri.sql`: то, что там выражено CHECK-ами, триггерами и REVOKE,
здесь существует как проверяемая в памяти доменная логика. Правило то же, что
для интерпретатора протокола: менять обе половины одновременно.

Инварианты (номера — из шапки schema_ri.sql):
  R-1  код KV-D-NNNNN выдаётся один раз и навсегда, в т.ч. забракованным (Р-03 п.3)
  R-2  ни одна позиция не попадает в реестр минуя буфер, разбирает только
       владелец реестра (Р-13 п.2, п.4)
  R-3  свободный ввод в справочные поля запрещён; неизвестность — служебное
       значение справочника, а не пустота (Р-03 п.2, Р-13 п.5.1)
  R-4  браковка не удаляет запись — она предотвращает повторный замер (Р-03 п.5)
  R-5  оси «Замер» и «РКД» независимы, переходы гейтуются (Р-02 п.9.3, Р-03 п.5)
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from dataclasses import dataclass, field

# ─────────────────────────────────────────────────────────────────────────────
# Словарь домена
# ─────────────────────────────────────────────────────────────────────────────

SERVICE_BRAND = "БРЕНД НЕ ОПРЕДЕЛЁН"
SERVICE_EQUIPMENT = "УСТАНОВКА НЕ ОПРЕДЕЛЕНА"

#: Основная траектория оси «РКД» (Р-03 п.5) — движение только вперёд по цепочке.
RKD_CHAIN = (
    "Заявка", "Замер", "Моделирование", "КД",
    "Изготовление образца", "Испытания", "Освоено", "Архив",
)
#: Приостановка вне цепочки: обязательное условие снятия + владелец (Р-13 п.5.4).
RKD_ON_HOLD = "На стопе (нет продажи)"
#: Терминал вне цепочки: код и запись сохраняются (Р-03 п.5).
RKD_REJECTED = "Забраковано"

MEASURE_STATUSES = ("Не начато", "В работе", "На проверке", "Принят")

REJECT_REASONS = (
    "нет маркировки",
    "нет смысла замерять",
    "экономически нецелесообразно",
    "технически нереализуемо",
)

#: Состояния строки буфера. «На стопе» здесь НЕТ намеренно: стоп — статус
#: реестра, держать позицию в буфере запрещено (Р-13 п.5.4).
BUFFER_STATES = (
    "PENDING", "RETURNED", "NEEDS_INFO",
    "CLOSED_CODED", "CLOSED_DUPLICATE", "CLOSED_REJECTED",
)
OPEN_STATES = ("PENDING", "RETURNED", "NEEDS_INFO")

#: Строка без движения дольше этого срока попадает в повестку созвона (Р-13 п.6).
STALE_BUSINESS_DAYS = 10

KV_CODE_RE = re.compile(r"^KV-D-[0-9]{5}$")


class BufferError(ValueError):
    """Нарушение регламента, которое сервер обязан отклонить, а не записать."""


# ─────────────────────────────────────────────────────────────────────────────
# Справочники: значения только отсюда (Р-03 разд. 6)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Directory:
    """Справочник с закрытым перечнем значений.

    `service` — служебные значения вроде «БРЕНД НЕ ОПРЕДЕЛЁН»: легальный ответ
    «не знаю», который не блокирует позицию (Р-13 п.5.1).
    """

    code: str
    values: set[str] = field(default_factory=set)
    service: set[str] = field(default_factory=set)

    def validate(self, value: str | None, *, default_service: str | None = None) -> str:
        if value is None:
            if default_service is None:
                raise BufferError(f"Справочник {self.code}: значение обязательно")
            return default_service
        if value not in self.values and value not in self.service:
            raise BufferError(
                f"Справочник {self.code}: «{value}» не значение справочника — "
                f"свободный ввод запрещён (Р-03 п.2)"
            )
        return value


def default_directories() -> dict[str, Directory]:
    return {
        "brand": Directory("brand", values=set(), service={SERVICE_BRAND}),
        "equipment_type": Directory("equipment_type", values=set(), service={SERVICE_EQUIPMENT}),
        "reject_reason": Directory("reject_reason", values=set(REJECT_REASONS)),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Выдача кодов: монотонная, невозвратная (Р-03 п.3)
# ─────────────────────────────────────────────────────────────────────────────

class CodeLedger:
    """KV-D-NNNNN. Метода «освободить код» нет и не будет — код, выданный
    ошибочной или забракованной записи, остаётся занятым навсегда."""

    def __init__(self) -> None:
        self._issued: list[tuple[str, str]] = []  # (kv_code, issued_by)
        self._next = 1

    def issue(self, issued_by: str) -> str:
        code = f"KV-D-{self._next:05d}"
        self._next += 1
        self._issued.append((code, issued_by))
        return code

    def is_issued(self, kv_code: str) -> bool:
        return any(c == kv_code for c, _ in self._issued)

    @property
    def issued_codes(self) -> list[str]:
        return [c for c, _ in self._issued]


# ─────────────────────────────────────────────────────────────────────────────
# Рабочий календарь: сроки регламентов — в рабочих днях
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WorkCalendar:
    """По умолчанию пн–пт; праздники и перенесённые дни задаются явно."""

    holidays: set[dt.date] = field(default_factory=set)
    extra_working: set[dt.date] = field(default_factory=set)  # рабочие субботы переносов

    def is_working(self, d: dt.date) -> bool:
        if d in self.extra_working:
            return True
        if d in self.holidays:
            return False
        return d.weekday() < 5

    def business_days_since(self, since: dt.date, until: dt.date) -> int:
        """Зеркало SQL-функции: считает дни в интервале (since, until]."""
        days = 0
        d = since
        while d < until:
            d += dt.timedelta(days=1)
            if self.is_working(d):
                days += 1
        return days

    def add_business_days(self, start: dt.date, n: int) -> dt.date:
        """Дедлайн через n рабочих дней: сроки регламентов (10/15 р.д. у
        служебных исходов kv_no_id) считаются по календарю, а не по календарным
        дням — иначе задача, выданная в пятницу, «горит» все выходные."""
        d = start
        added = 0
        while added < n:
            d += dt.timedelta(days=1)
            if self.is_working(d):
                added += 1
        return d


# ─────────────────────────────────────────────────────────────────────────────
# Сущности
# ─────────────────────────────────────────────────────────────────────────────

def temp_folder_name(*, order_no: str | None = None, article: str | None = None,
                     date: dt.date | None = None, pos: int | None = None,
                     name: str | None = None) -> str:
    """Имя временной папки строки буфера (Р-13 п.3):
    плановая — `{№ заказа}_{артикул}`, внеплановая — `{дата}_POSNN_{имя}`."""
    if order_no and article:
        return f"{order_no}_{article}"
    if date and pos is not None and name:
        return f"{date.isoformat()}_POS{pos:02d}_{name}"
    raise BufferError(
        "Имя папки: нужны либо (№ заказа, артикул), либо (дата, № позиции, имя)"
    )


@dataclass
class BufferRow:
    temp_folder_name: str
    added_by: str
    raw_folder_url: str | None = None
    brand: str = SERVICE_BRAND
    equipment_type: str = SERVICE_EQUIPMENT
    article_norm: str | None = None
    name_hint: str | None = None
    unplanned: bool = False
    unplanned_by: str | None = None
    #: Источник строки: staff — штатный замерщик, crowd — краудскан
    #: (docs/15 §15.6). Пометка обязана пережить путь до реестра: классы
    #: точности крауда C/D, строка не должна прикидываться штатным замером.
    source: str = "staff"
    created_on: dt.date = dt.date(2026, 1, 1)
    state: str = "PENDING"
    returned_reason: str | None = None
    needs_info_assignee: str | None = None
    needs_info_due: dt.date | None = None
    resolution_kv_code: str | None = None
    duplicate_of: str | None = None
    resolved_by: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class PartRecord:
    kv_code: str
    name_ru: str
    brand: str
    equipment_type: str
    keywords: list[str]
    origin_buffer_row_id: str
    article_norm: str | None = None
    category: str | None = None
    status_measure: str = "Не начато"
    status_rkd: str = "Заявка"
    reject_reason: str | None = None
    stop_condition: str | None = None
    stop_owner: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Буфер (Р-13 разд. 4–6)
# ─────────────────────────────────────────────────────────────────────────────

class Buffer:
    """Единственный путь в реестр. Разбирает строки только владелец реестра —
    это доменное правило, а не проверка UI (Р-13 п.2)."""

    def __init__(self, directories: dict[str, Directory] | None = None,
                 calendar: WorkCalendar | None = None) -> None:
        self.directories = directories or default_directories()
        self.calendar = calendar or WorkCalendar()
        self.ledger = CodeLedger()
        self.rows: dict[str, BufferRow] = {}
        self.registry: dict[str, PartRecord] = {}  # kv_code → запись реестра

    # — подача строк —

    def submit(self, row: BufferRow) -> BufferRow:
        if row.state not in BUFFER_STATES:
            raise BufferError(f"Недопустимое состояние строки буфера: {row.state}")
        row.brand = self.directories["brand"].validate(
            row.brand, default_service=SERVICE_BRAND)
        row.equipment_type = self.directories["equipment_type"].validate(
            row.equipment_type, default_service=SERVICE_EQUIPMENT)
        if row.unplanned and not row.unplanned_by:
            raise BufferError(
                "Внеплановая позиция: обязательно «кто передал» (Р-13 п.5.3)")
        if row.raw_folder_url is None:
            # Строка без сырья — не работа, а обещание: сразу назад автору.
            row.state = "RETURNED"
            row.returned_reason = "нет ссылки на папку с сырьём (Р-13 п.4.2)"
        else:
            row.state = "PENDING"
        self.rows[row.id] = row
        return row

    def resubmit(self, row_id: str, raw_folder_url: str) -> BufferRow:
        row = self._open_row(row_id)
        row.raw_folder_url = raw_folder_url
        row.state = "PENDING"
        row.returned_reason = None
        return row

    # — разбор (только владелец реестра) —

    def request_info(self, row_id: str, actor: "Actor", assignee: str,
                     due: dt.date) -> BufferRow:
        self._require_owner(actor)
        row = self._open_row(row_id)
        row.state = "NEEDS_INFO"
        row.needs_info_assignee = assignee
        row.needs_info_due = due
        return row

    def dedup_candidates(self, row_id: str) -> list[PartRecord]:
        """Кандидаты в дубликаты перед выдачей кода (Р-13 п.4.3):
        совпадение нормализованного артикула либо (бренд + похожее имя)."""
        row = self.rows[row_id]
        found: list[PartRecord] = []
        for part in self.registry.values():
            if row.article_norm and part.article_norm == row.article_norm:
                found.append(part)
                continue
            if (row.name_hint and part.brand == row.brand
                    and _similar(row.name_hint, part.name_ru)):
                found.append(part)
        return found

    def resolve_as_new(self, row_id: str, actor: "Actor", *, name_ru: str,
                       keywords: list[str], category: str | None = None) -> PartRecord:
        self._require_owner(actor)
        row = self._open_row(row_id)
        if row.raw_folder_url is None:
            raise BufferError("Строка без сырья не разбирается (Р-13 п.4.2)")
        if not keywords:
            raise BufferError(
                "«Ключевые слова/синонимы» — обязательное поле (Р-04 п.5)")
        part = PartRecord(
            kv_code=self.ledger.issue(actor.user_id),
            name_ru=name_ru,
            brand=row.brand,
            equipment_type=row.equipment_type,
            keywords=keywords,
            article_norm=row.article_norm,
            category=category,
            origin_buffer_row_id=row.id,
        )
        self.registry[part.kv_code] = part
        self._close(row, "CLOSED_CODED", actor, resolution_kv_code=part.kv_code)
        return part

    def resolve_as_duplicate(self, row_id: str, actor: "Actor",
                             kv_code: str) -> BufferRow:
        self._require_owner(actor)
        row = self._open_row(row_id)
        if not self.ledger.is_issued(kv_code):
            raise BufferError(f"Код {kv_code} не выдавался — дубликатом быть не может")
        self._close(row, "CLOSED_DUPLICATE", actor, duplicate_of=kv_code)
        return row

    def resolve_as_rejected(self, row_id: str, actor: "Actor", *, name_ru: str,
                            reject_reason: str) -> PartRecord:
        """Браковка ВСЁ РАВНО выдаёт код и создаёт запись: она предотвращает
        повторный замер той же детали через год (Р-03 п.3, п.5)."""
        self._require_owner(actor)
        row = self._open_row(row_id)
        reason = self.directories["reject_reason"].validate(reject_reason)
        part = PartRecord(
            kv_code=self.ledger.issue(actor.user_id),
            name_ru=name_ru,
            brand=row.brand,
            equipment_type=row.equipment_type,
            keywords=[name_ru],
            article_norm=row.article_norm,
            origin_buffer_row_id=row.id,
            status_rkd=RKD_REJECTED,
            reject_reason=reason,
        )
        self.registry[part.kv_code] = part
        self._close(row, "CLOSED_REJECTED", actor, resolution_kv_code=part.kv_code)
        return part

    # — просрочка (Р-13 п.6) —

    def stale_rows(self, today: dt.date) -> list[BufferRow]:
        return [
            r for r in self.rows.values()
            if r.state in OPEN_STATES
            and self.calendar.business_days_since(r.created_on, today) > STALE_BUSINESS_DAYS
        ]

    # — внутреннее —

    def _require_owner(self, actor: "Actor") -> None:
        if actor.role != "registry_owner":
            raise BufferError(
                "Разбор буфера и выдача кодов — только владелец реестра (Р-13 п.2)")

    def _open_row(self, row_id: str) -> BufferRow:
        row = self.rows.get(row_id)
        if row is None:
            raise BufferError(f"Строка {row_id} не найдена")
        if row.state.startswith("CLOSED"):
            raise BufferError(f"Строка {row_id} уже закрыта ({row.state})")
        return row

    def _close(self, row: BufferRow, state: str, actor: "Actor", *,
               resolution_kv_code: str | None = None,
               duplicate_of: str | None = None) -> None:
        row.state = state
        row.resolved_by = actor.user_id
        row.resolution_kv_code = resolution_kv_code
        row.duplicate_of = duplicate_of


@dataclass
class Actor:
    user_id: str
    role: str  # registry_owner | engineer | operator | manager


def buffer_row_from_crowd(submission, contributor, *, created_on: dt.date,
                          pos: int, name: str) -> BufferRow:
    """Принятый крауд-скан рождает строку буфера (docs/15 §15.6).

    Дальше — штатный путь Р-13: разбирает владелец реестра, код выдаётся
    как обычно. Краудскан по определению внеплановый (Р-13 п.5.3, «кто
    передал» — участник), источник сырья — сам сабмишен, пометка source
    не даёт строке прикидываться штатным замером. Не-принятый скан строкой
    не становится: деньги и реестр входят через одну и ту же дверь приёмки.
    """
    if submission.state != "ACCEPTED":
        raise BufferError(
            f"В буфер попадают только принятые крауд-сканы "
            f"(сабмишен в состоянии {submission.state})")
    claim = submission.supplier_claim or {}
    return BufferRow(
        temp_folder_name=temp_folder_name(date=created_on, pos=pos, name=name),
        added_by=f"crowd:{contributor.id}",
        raw_folder_url=f"crowd://submissions/{submission.id}",
        name_hint=name,
        article_norm=claim.get("article"),
        unplanned=True,
        unplanned_by=contributor.display_name,
        created_on=created_on,
        source="crowd",
    )


def _similar(a: str, b: str) -> bool:
    """Дешёвое приближение trigram-похожести из schema_ri.sql — для памяти
    достаточно совпадения нормализованных слов больше чем наполовину."""
    wa, wb = set(a.lower().split()), set(b.lower().split())
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= 0.5


# ─────────────────────────────────────────────────────────────────────────────
# Гейты статусов реестра (Р-02 п.9.3, Р-03 п.5, Р-13 п.5.2, п.5.4)
# ─────────────────────────────────────────────────────────────────────────────

def advance_rkd(part: PartRecord, target: str, *,
                stop_condition: str | None = None,
                stop_owner: str | None = None,
                reject_reason: str | None = None,
                directories: dict[str, Directory] | None = None) -> PartRecord:
    """Переход по оси «РКД». Движение по цепочке — только вперёд и по шагу;
    «На стопе» и «Забраковано» — выходы вне цепочки со своими требованиями."""
    dirs = directories or default_directories()

    if part.status_rkd == RKD_REJECTED:
        raise BufferError("«Забраковано» — терминальный статус (Р-03 п.5)")

    if target == RKD_ON_HOLD:
        if not stop_condition or not stop_owner:
            raise BufferError(
                "«На стопе»: обязательны условие снятия и владелец (Р-13 п.5.4)")
        part.stop_condition, part.stop_owner = stop_condition, stop_owner
        part.status_rkd = target
        return part

    if target == RKD_REJECTED:
        reason = dirs["reject_reason"].validate(reject_reason)
        part.reject_reason = reason
        part.status_rkd = target
        return part

    if target not in RKD_CHAIN:
        raise BufferError(f"Неизвестный статус РКД: {target}")

    current = part.status_rkd
    if current == RKD_ON_HOLD:
        # Снятие со стопа возвращает в цепочку — на любую уже достигнутую позицию.
        part.stop_condition = part.stop_owner = None
    else:
        if RKD_CHAIN.index(target) != RKD_CHAIN.index(current) + 1:
            raise BufferError(
                f"Ось «РКД» движется по цепочке: {current} → {target} запрещён")

    # Гейт Р-13 п.5.2: без установки нельзя выбирать материал и допуски —
    # дальше «Замера» не продвигаемся. Сам замер и фото при этом разрешены.
    if (RKD_CHAIN.index(target) > RKD_CHAIN.index("Замер")
            and part.equipment_type == SERVICE_EQUIPMENT):
        raise BufferError(
            "УСТАНОВКА НЕ ОПРЕДЕЛЕНА: статус РКД не продвигается дальше "
            "«Замер» (Р-13 п.5.2)")

    # Гейт Р-03 п.5: моделирование начинается только по принятому протоколу замера.
    if target == "Моделирование" and part.status_measure != "Принят":
        raise BufferError(
            "«Замер → Моделирование» — только после приёмки протокола замера (Р-03 п.5)")

    part.status_rkd = target
    return part


def advance_measure(part: PartRecord, target: str) -> PartRecord:
    """Ось «Замер» независима от «РКД» (R-5) — движение вперёд по своей цепочке."""
    if target not in MEASURE_STATUSES:
        raise BufferError(f"Неизвестный статус замера: {target}")
    if MEASURE_STATUSES.index(target) != MEASURE_STATUSES.index(part.status_measure) + 1:
        raise BufferError(
            f"Ось «Замер»: {part.status_measure} → {target} запрещён")
    part.status_measure = target
    return part
