"""Свод предложений поставщиков за неделю (scripts/weekly_offers.py).

Что проверяется и почему:

1. ОКНО. Дни UTC, обе границы включительно; конец — полночь ПОСЛЕ последнего
   дня. Ошибка на границе даёт «потерянное воскресенье» или чужой понедельник.
2. ДВА СРЕЗА. «Поступило» — дата предложения (КП, письмо, карточка) в окне;
   «обработано» — processed_at в окне, с разделением «впервые / повторно» по
   самой ранней строке спроса файла. Переразобранный старый КП обязан попасть во
   второй срез и НЕ попасть в первый; поздно обработанный свежий КП — наоборот.
3. НИЧЕГО ИЗ ДАННЫХ В ЖУРНАЛЕ. Журнал прогона публичный (CLAUDE.md, правило 17):
   ни компаний, ни брендов, ни кодов, ни номеров карточек и сделок, ни почт, ни
   имён файлов. Корпус нарочно набит узнаваемыми строками, и ни одна не должна
   дойти до печати.
4. ПОДРОБНОСТИ — ТОЛЬКО С КЛЮЧОМ И ТОЛЬКО ОТКРЫТЫМ. Без ключа файл не пишется
   вовсе; закрытый ключ во входе — отказ.
5. ШИФРОВАНИЕ. Шаг прогона берётся из самого workflow и исполняется: открытый
   файл стёрт, в папке артефакта только *.enc, и они расшифровываются закрытым
   ключом пары, сгенерированной здесь же.
6. ПРОГОН. Job подключён, входы идут в окружение как есть, шаг их не подменяет.

Корпус придуман (правило 18); компании, бренды и номера — выдуманные.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
нужна_база = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")
нужен_openssl = pytest.mark.skipif(not shutil.which("openssl"), reason="нет openssl")
WORKFLOW = ROOT / ".github" / "workflows" / "library-stats.yml"


def _модуль(имя, путь):
    spec = importlib.util.spec_from_file_location(имя, путь)
    м = importlib.util.module_from_spec(spec)
    sys.modules[имя] = м
    spec.loader.exec_module(м)
    return м


wo = _модуль("weekly_offers_t", ROOT / "scripts" / "weekly_offers.py")


# ── Окно ─────────────────────────────────────────────────────────────────────

def test_окно_по_умолчанию_семь_дней_кончая_сегодня():
    о = wo.окно_из_env({}, сегодня=date(2026, 9, 26))
    assert (о.с, о.по, о.дней) == (date(2026, 9, 20), date(2026, 9, 26), 7)
    assert о.начало == datetime(2026, 9, 20, tzinfo=timezone.utc)
    assert о.конец == datetime(2026, 9, 27, tzinfo=timezone.utc)


def test_окно_явными_датами_и_полуявное():
    сегодня = date(2026, 9, 26)
    о = wo.окно_из_env({"FROM": "2026-09-14", "TO": "2026-09-20", "DAYS": "3"}, сегодня)
    assert (о.с, о.по) == (date(2026, 9, 14), date(2026, 9, 20))   # DAYS не действует
    о = wo.окно_из_env({"FROM": "2026-09-24"}, сегодня)
    assert (о.с, о.по) == (date(2026, 9, 24), сегодня)
    о = wo.окно_из_env({"TO": "2026-09-10", "DAYS": "14"}, сегодня)
    assert (о.с, о.по) == (date(2026, 8, 28), date(2026, 9, 10))
    # Пустые входы прогона приходят пустыми строками, а не отсутствием.
    о = wo.окно_из_env({"DAYS": "", "FROM": "", "TO": ""}, сегодня)
    assert о.дней == 7


@pytest.mark.parametrize("env", [
    {"DAYS": "неделя"}, {"DAYS": "0"}, {"DAYS": "400"}, {"FROM": "20.09.2026"},
    {"FROM": "2026-09-21", "TO": "2026-09-20"}, {"FROM": "2024-01-01", "TO": "2026-01-01"},
])
def test_окно_ошибки_входа_говорят_словами(env):
    with pytest.raises(ValueError):
        wo.окно_из_env(env, date(2026, 9, 26))


def test_границы_окна():
    о = wo.Окно(date(2026, 9, 14), date(2026, 9, 20))
    assert о.где(date(2026, 9, 13)) == wo.ДО_ОКНА
    assert о.где(date(2026, 9, 14)) == wo.В_ОКНЕ
    assert о.где(date(2026, 9, 20)) == wo.В_ОКНЕ
    assert о.где(date(2026, 9, 21)) == wo.ПОСЛЕ_ОКНА
    assert о.где(None) == wo.НЕ_ЗАПИСАНА
    утс = timezone.utc
    assert о.содержит_момент(datetime(2026, 9, 20, 23, 59, 59, tzinfo=утс))
    assert not о.содержит_момент(datetime(2026, 9, 21, 0, 0, tzinfo=утс))
    assert not о.содержит_момент(datetime(2026, 9, 13, 23, 59, 59, tzinfo=утс))
    assert not о.содержит_момент(None)
    assert [x[0] for x in wo.группы_дней(о)][:2] == ["пн 14.09", "вт 15.09"]
    длинное = wo.Окно(date(2026, 8, 1), date(2026, 9, 20))
    assert all("-W" in x[0] for x in wo.группы_дней(длинное))


# ── Дата поступления и источник ──────────────────────────────────────────────

def _предложение(источник, **кв):
    return wo.Предложение(file_id=кв.pop("file_id", "f"), источник=источник, владелец=кв.pop("владелец", "1"),
                          поле=None, статус="разобран", причина=None, вид=None, позиций=1,
                          обработан=None, **кв)


def test_дата_поступления_по_убыванию_точности():
    qd = wo.quote_date
    п = _предложение(wo.ИСТ_ПИСЬМА, даты={qd.КАРТОЧКА: date(2026, 9, 16)})
    wo.выбрать_дату(п, None)
    assert (п.дата_поступления, п.откуда_дата) == (date(2026, 9, 16), wo.ДАТА_CRM)
    п = _предложение(wo.ИСТ_КАРТОЧКИ, даты={qd.КАРТОЧКА: date(2026, 9, 14)})
    wo.выбрать_дату(п, None)
    assert п.откуда_дата == wo.ДАТА_КАРТОЧКИ and not п.дата_по_карточке
    п = _предложение(wo.ИСТ_КАРТОЧКИ, даты={qd.ДОКУМЕНТ: date(2026, 9, 1), qd.ПИСЬМО: date(2026, 9, 3)})
    wo.выбрать_дату(п, date(2026, 8, 1))
    assert (п.дата_поступления, п.откуда_дата) == (date(2026, 9, 3), wo.ДАТА_ПИСЬМА)
    # Своих строк нет — дата карточки по другому её файлу, с пометкой.
    п = _предложение(wo.ИСТ_КАРТОЧКИ)
    wo.выбрать_дату(п, date(2026, 9, 14))
    assert (п.откуда_дата, п.дата_по_карточке) == (wo.ДАТА_КАРТОЧКИ, True)
    # У письма и сделки дата чужой карточки не берётся.
    п = _предложение(wo.ИСТ_СДЕЛКИ)
    wo.выбрать_дату(п, date(2026, 9, 14))
    assert (п.дата_поступления, п.откуда_дата) == (None, wo.НЕТ_ДАТЫ)


def test_источник_файла():
    assert wo.источник_файла("поле запроса", "ufCrm18_1700698211875", None, None) == wo.ИСТ_КАРТОЧКИ
    # Наш «Request file» — не предложение.
    assert wo.источник_файла("поле запроса", "ufCrm18_1727423346", None, None) is None
    assert wo.источник_файла("письмо поставщика", "письмо 1", "поставщик", None) == wo.ИСТ_ПИСЬМА
    assert wo.источник_файла("поле сделки", "x", "поставщик", None) == wo.ИСТ_СДЕЛКИ
    assert wo.источник_файла("поле сделки", "x", "заказчик", None) is None
    assert wo.источник_файла("поле сделки", "x", None, "Offer from supplier(s)") == wo.ИСТ_СДЕЛКИ
    assert wo.источник_файла("письмо лида", "письмо 1", "заказчик", None) is None


def test_поля_кп_те_же_что_у_индексатора():
    """Индексатор собирает вложения карточек по quote_coverage.ПОЛЯ_КП; замер
    берёт поля из карты папок. Разойдись они — замер считал бы не те файлы."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import quote_coverage as qc
    assert set(wo.ПОЛЯ_КП) == set(qc.ПОЛЯ_КП)
    assert qc.ПОЛЕ_ЗАПРОСА not in wo.ПОЛЯ_КП


def test_причина_в_журнале_без_данных():
    т = wo.причина_для_журнала("disk.file.get: ACCESS_DENIED; urlMachine 403 для secret@example.test "
                              "«Смета_Ромашка.xlsx» https://portal.example/x?t=1")
    for кусок in ("secret@", "Ромашка", "https", "403"):
        assert кусок not in т, кусок
    assert "ACCESS_DENIED" in т
    assert wo.причина_для_журнала("строк 12 · с признаками позиции 3") == "строк # · с признаками позиции #"
    assert wo.причина_для_журнала(None) == "(без причины)"
    assert wo.валюта_для_журнала("eur") == "EUR"
    assert wo.валюта_для_журнала("Ромашка") == "(прочая)"


# ── Ключ ─────────────────────────────────────────────────────────────────────

def _пара(папка: Path) -> tuple[Path, str]:
    закрытый = папка / "private.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                    "-out", str(закрытый)], check=True, capture_output=True)
    открытый = subprocess.run(["openssl", "pkey", "-in", str(закрытый), "-pubout"],
                              check=True, capture_output=True).stdout
    return закрытый, base64.b64encode(открытый).decode()


@нужен_openssl
def test_ключ_открытый_закрытый_и_мусор(tmp_path):
    закрытый, открытый_b64 = _пара(tmp_path)
    assert wo.проверить_ключ(открытый_b64)[0] is True
    # Длина входа: RSA-2048 — около 600 знаков, RSA-4096 — около 1 100; предел
    # входов workflow_dispatch — 65 535 знаков на все входы.
    assert len(открытый_b64) < 2000
    писать, сообщение = wo.проверить_ключ(base64.b64encode(закрытый.read_bytes()).decode())
    assert not писать and "::error::" in сообщение and "ЗАКРЫТЫЙ" in сообщение
    assert wo.проверить_ключ("")[0] is False
    assert wo.проверить_ключ("не base64 вовсе")[0] is False
    assert wo.проверить_ключ(base64.b64encode(b"hello").decode())[0] is False


# ── Живой запрос на PostgreSQL с настоящими схемами ─────────────────────────

СХЕМА = "тест_недельных_предложений"
ФАЙЛЫ = ("schema.sql", "schema_junk.sql", "suppliers_schema.sql", "brands_schema.sql")

# КОРПУС. Окно — 14…20.09.2026 (пн…вс). Считано руками:
#
#  карточка 9900771 (компания 5550001 «ООО Ромашка Трейд», бренд карточки 7):
#   F1 разобран, 4 поз., 3 цены EUR с датой КП 15.09 (одна низкой уверенности) — поступило И обработано, впервые
#   F2 разобран, 5 поз., цен нет (текст, шапки нет) — дата карточки 14.09 по F3 — поступило И обработано, впервые
#   F3 разобран, 2 поз., 1 цена USD, дата «карточка: создана» 14.09, строки спроса с 01.08 — оба среза, повторно
#  карточка 9900772 (строк цены нет): F4 пусто, F5 не скачался — только обработано; F6 — наш Request file, не в счёт
#  карточка 9900773: F7 — КП от 20.08, переразобран 19.09 (2 цены RUB) — только обработано, повторно;
#                    F8 — КП от 16.09, обработан 25.09 — только поступило
#  карточка 9900774: F9 — всё до окна, не в счёт вовсе
#  письма: M1 компания 5550002 «Zentrix GmbH», 2 цены CNY, письмо легло в CRM 16.09 — оба среза;
#          M2 тело письма контакта, без спецификации — только обработано
#  сделки: D1 «Offer from supplier(s)», 6 поз. — только обработано; D2 спецификация заказчика — не в счёт;
#          D3 сторона не записана, но название поля — поставщика; формат не читаем — только обработано;
#          D4 сторона не записана, название поля — заказчика: отсеивается как не предложение
#  шум: строка цены чужого потока на F4 с датой в окне и выведенная строка на F2 — не в счёт;
#       строка цены файла, которого нет в lib_files, — не в счёт, но видна числом.
КОРПУС = """
insert into sup_entity (id, kind, display_name, status) values
  ('KV-S-000001-8', 'legal', 'ООО Ромашка Трейд', 'active'),
  ('KV-S-000002-6', 'legal', 'Zentrix GmbH', 'active');
insert into sup_identifier (sup_id, kind, value, value_norm, source, status, run_id) values
  ('KV-S-000001-8', 'bitrix', 'ID 5550001', '5550001', 't', 'stated', 'r'),
  ('KV-S-000002-6', 'bitrix', 'ID 5550002', '5550002', 't', 'stated', 'r');

insert into lib_files (file_id, deal_id, origin, field, field_title, side, kind, status, reason,
                       chars, rows_found, processed_at, parse_path, header_found) values
  ('фид-КП-АЛЬФА-1',   '9900771', 'поле запроса', 'ufCrm18_1700698211875', 'КП поставщика', 'поставщик',
   'xlsx/docx', 'разобран', null, 5000, 4, '2026-09-15 10:00+00', 'таблица', true),
  ('фид-КП-БЕТА-2',    '9900771', 'поле запроса', 'ufCrm18_1731179998', 'Offer from supplier', 'поставщик',
   'pdf', 'разобран', null, 9000, 5, '2026-09-16 09:00+00', 'текст', false),
  ('фид-КП-ГАММА-3',   '9900771', 'поле запроса', 'ufCrm18_1703712059311', 'Processed offer', 'поставщик',
   'xlsx/docx', 'разобран', null, 1000, 2, '2026-09-14 08:00+00', 'таблица', true),
  ('фид-КП-ДЕЛЬТА-4',  '9900772', 'поле запроса', 'ufCrm18_1700698211875', 'КП поставщика', 'поставщик',
   'pdf', 'пусто', 'нет текстового слоя', 0, 0, '2026-09-17 12:00+00', null, null),
  ('фид-КП-ЭПСИЛОН-5', '9900772', 'поле запроса', 'ufCrm18_1731179998', 'Offer from supplier', 'поставщик',
   null, 'не скачался', 'disk.file.get: ACCESS_DENIED; urlMachine 403 для secret@example.test', null, 0,
   '2026-09-18 12:00+00', null, null),
  ('фид-ЗАПРОС-НАШ-6', '9900772', 'поле запроса', 'ufCrm18_1727423346', 'Request file', 'заказчик',
   'pdf', 'разобран', null, 100, 3, '2026-09-18 13:00+00', 'текст', false),
  ('фид-КП-СТАРЫЙ-7',  '9900773', 'поле запроса', 'ufCrm18_1700698211875', 'КП поставщика', 'поставщик',
   'xlsx/docx', 'разобран', null, 3000, 3, '2026-09-19 07:00+00', 'таблица', true),
  ('фид-КП-ПОЗДНИЙ-8', '9900773', 'поле запроса', 'ufCrm18_1700698211875', 'КП поставщика', 'поставщик',
   'xlsx/docx', 'разобран', null, 2000, 1, '2026-09-25 07:00+00', 'таблица', true),
  ('фид-КП-ВНЕ-9',     '9900774', 'поле запроса', 'ufCrm18_1700698211875', 'КП поставщика', 'поставщик',
   'xlsx/docx', 'разобран', null, 2000, 1, '2026-09-01 07:00+00', 'таблица', true),
  ('mail:880001', 'C5550002', 'письмо поставщика', 'письмо 7700123', 'входящее письмо', 'поставщик',
   'xlsx/docx', 'разобран', null, 800, 2, '2026-09-16 15:00+00', 'таблица', true),
  ('mail-body:7700124', 'K6660001', 'письмо поставщика', 'письмо 7700124', 'входящее письмо', 'поставщик',
   'прочее', 'текст без спецификации', 'в тексте письма нет строк с признаками позиции (строк 12)', 300, 0,
   '2026-09-18 16:00+00', 'текст', null),
  ('фид-СДЕЛКА-10', '4440001', 'поле сделки', 'ufCrm_ТАЙНОЕ_ПОЛЕ', 'Offer from supplier(s)', 'поставщик',
   'xlsx/docx', 'разобран', null, 5000, 6, '2026-09-17 11:00+00', 'таблица', true),
  ('фид-СДЕЛКА-11', '4440001', 'поле сделки', 'ufCrm_ТАЙНОЕ_ДРУГОЕ', 'Техническая спецификация', 'заказчик',
   'xlsx/docx', 'разобран', null, 5000, 50, '2026-09-17 11:00+00', 'таблица', true),
  ('фид-СДЕЛКА-12', '4440002', 'поле сделки', 'ufCrm_ТАЙНОЕ_ТРЕТЬЕ', 'Offer from supplier(s)', null,
   'xlsx/docx', 'формат не читаем', 'BadZipFile', 0, 0, '2026-09-18 11:00+00', null, null),
  ('фид-СДЕЛКА-14', '4440003', 'поле сделки', 'ufCrm_ТАЙНОЕ_ЧЕТВЁРТОЕ', 'Техническая спецификация', null,
   'xlsx/docx', 'разобран', null, 5000, 9, '2026-09-18 11:00+00', 'таблица', true);

insert into lib_prices (source_url, feed, source, rfq_id, rfq_company, oem, rfq_brands, part_number, item_name,
                        price, currency, qty, qty_unit, total, confidence, price_date, price_date_src) values
  ('фид-КП-АЛЬФА-1', 'разбор КП', 'КП', '9900771', '5550001', 'Zentrix', '7', 'ZX-ТАЙНА-101',
   'Подшипник секретный Альфа', 100, 'EUR', 2, 'шт', 200, 'med', '2026-09-15', 'документ'),
  ('фид-КП-АЛЬФА-1', 'разбор КП', 'КП', '9900771', '5550001', null, '7', 'ZX-ТАЙНА-102',
   'Втулка секретная', 50, 'EUR', 1, 'шт', null, 'low', '2026-09-15', 'документ'),
  ('фид-КП-АЛЬФА-1', 'разбор КП', 'КП', '9900771', '5550001', null, '7', 'ZX-ТАЙНА-103',
   'Кольцо секретное ' || repeat('очень длинное наименование ', 10), 30, 'EUR', 3, 'шт', 90, 'med',
   '2026-09-15', 'документ'),
  ('фид-КП-ГАММА-3', 'разбор КП', 'КП', '9900771', '5550001', null, '7', 'ZX-ТАЙНА-104',
   'Уплотнение секретное', 12.5, 'USD', 4, 'шт', 50, 'med', '2026-09-14', 'карточка: создана'),
  ('фид-КП-СТАРЫЙ-7', 'разбор КП', 'КП', '9900773', null, null, null, 'ZX-ТАЙНА-105',
   'Вал секретный', 1000, 'RUB', 1, 'шт', 1000, 'med', '2026-08-20', 'документ'),
  ('фид-КП-СТАРЫЙ-7', 'разбор КП', 'КП', '9900773', null, null, null, 'ZX-ТАЙНА-106',
   'Шестерня секретная', 2000, 'RUB', 1, 'шт', 2000, 'med', '2026-08-20', 'документ'),
  ('фид-КП-ПОЗДНИЙ-8', 'разбор КП', 'КП', '9900773', null, null, null, 'ZX-ТАЙНА-107',
   'Муфта секретная', 77, 'EUR', 1, 'шт', 77, 'med', '2026-09-16', 'документ'),
  ('фид-КП-ВНЕ-9', 'разбор КП', 'КП', '9900774', null, null, null, 'ZX-ТАЙНА-108',
   'Болт секретный', 5, 'EUR', 1, 'шт', 5, 'med', '2026-09-01', 'документ'),
  ('mail:880001', 'письмо поставщика', 'КП из письма', 'C5550002', '5550002', 'Zentrix', null, 'ZX-ТАЙНА-109',
   'Корпус секретный', 10, 'CNY', 1, 'шт', 10, 'med', '2026-09-16', 'карточка: создана'),
  ('mail:880001', 'письмо поставщика', 'КП из письма', 'C5550002', '5550002', 'Zentrix', null, 'ZX-ТАЙНА-110',
   'Крышка секретная', 20, 'CNY', 1, 'шт', 20, 'med', '2026-09-16', 'карточка: создана'),
  ('фид-КП-ДЕЛЬТА-4', 'прайс', 'прайс', null, '5550009', 'Kvarcton', null, 'ZX-ТАЙНА-111',
   'Чужой поток', 1, 'EUR', 1, 'шт', 1, 'med', '2026-09-17', null),
  ('фид-КП-БЕТА-2', 'разбор КП: выведено', 'распознавание скана', '9900771', '5550001', null, null,
   'ZX-ТАЙНА-112', 'Выведенная строка', 1, 'EUR', 1, 'шт', 1, 'med', '2026-09-16', 'документ'),
  ('фид-ПРИЗРАК-13', 'разбор КП', 'КП', '9900775', '5550001', null, null, 'ZX-ТАЙНА-113',
   'Строка без файла', 3, 'EUR', 1, 'шт', 3, 'med', '2026-09-17', 'документ');

insert into lib_demand (deal_id, item_name, oem, part_number, source, source_file, created_at)
select '9900771', 'позиция ' || g, case when g = 1 then 'Zentrix' end, 'ZX-ТАЙНА-2' || g, 'КП', 'фид-КП-АЛЬФА-1',
       '2026-09-15 10:00+00' from generate_series(1, 4) g;
insert into lib_demand (deal_id, item_name, oem, part_number, source, source_file, created_at)
select '9900771', 'позиция ' || g, case when g = 1 then 'Polarmax' end, null, 'КП', 'фид-КП-БЕТА-2',
       '2026-09-16 09:00+00' from generate_series(1, 5) g;
insert into lib_demand (deal_id, item_name, source, source_file, created_at)
select '9900771', 'позиция ' || g, 'КП', 'фид-КП-ГАММА-3', '2026-08-01 08:00+00' from generate_series(1, 2) g;
insert into lib_demand (deal_id, item_name, source, source_file, created_at)
select '9900773', 'позиция ' || g, 'КП', 'фид-КП-СТАРЫЙ-7', '2026-08-20 07:00+00' from generate_series(1, 3) g;
insert into lib_demand (deal_id, item_name, source, source_file, created_at)
select '9900773', 'позиция', 'КП', 'фид-КП-ПОЗДНИЙ-8', '2026-09-25 07:00+00';
insert into lib_demand (deal_id, item_name, oem, source, source_file, created_at)
select 'C5550002', 'позиция ' || g, 'Zentrix', 'письмо поставщика', 'mail:880001', '2026-09-16 15:00+00'
  from generate_series(1, 2) g;
insert into lib_demand (deal_id, item_name, source, source_file, created_at)
select '4440001', 'позиция ' || g, 'поле сделки', 'фид-СДЕЛКА-10', '2026-09-17 11:00+00' from generate_series(1, 6) g;
"""

# Всё, что есть в корпусе и не должно дойти до журнала.
УТЕЧКИ = ("Ромашка", "Zentrix", "Polarmax", "Kvarcton", "ZX-ТАЙНА", "секрет", "9900771", "9900772",
          "9900773", "5550001", "5550002", "4440001", "6660001", "880001", "7700123", "7700124", "фид-",
          "mail:", "mail-body", "KV-S-", "ТАЙНОЕ", "secret@", "example.test", "позиция ", "ПРИЗРАК",
          "9900775", "4440003")


@pytest.fixture()
def база(monkeypatch):
    import psycopg2
    from psycopg2.extensions import parse_dsn

    from library import offer_role as orl
    from tests import test_library_schema_sql as helpers
    tor = _модуль("test_offer_role_t2", ROOT / "tests" / "test_offer_role.py")
    р = orl.собрать(tor.СЛОВАРЬ, tor.РЯДЫ, tor.РАЗВЕДКА)
    monkeypatch.setattr(orl, "реестр_файлов", lambda доп=(): р)

    assert parse_dsn(DSN)["dbname"] == "library_sql_test"
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'drop schema if exists "{СХЕМА}" cascade')
        cur.execute(f'create schema "{СХЕМА}"')
        cur.execute(f'set search_path to "{СХЕМА}"')
        for имя in ФАЙЛЫ:
            for оператор in helpers.операторы((ROOT / "library" / "supabase" / имя).read_text(encoding="utf-8")):
                cur.execute(оператор)
        cur.execute(КОРПУС)
    # Замер — отдельным соединением ТОЛЬКО ДЛЯ ЧТЕНИЯ, как в прогоне: любая
    # попытка записи уронит тест.
    чтение = psycopg2.connect(DSN, options=f"-c search_path={СХЕМА}")
    чтение.set_session(readonly=True)
    try:
        yield чтение
    finally:
        чтение.close()
        with conn.cursor() as cur:
            cur.execute(f'drop schema if exists "{СХЕМА}" cascade')
        conn.close()


ОКНО = wo.Окно(date(2026, 9, 14), date(2026, 9, 20))


def _прогон(чтение, env) -> tuple[int, str]:
    вывод = io.StringIO()
    with чтение.cursor() as cur, redirect_stdout(вывод):
        код = wo.выполнить(cur, ОКНО, env)
    return код, вывод.getvalue()


@нужна_база
def test_два_среза_разделены(база):
    with база.cursor() as cur, redirect_stdout(io.StringIO()):
        итог = wo.собрать(cur, ОКНО)
    поступило = {п.file_id for п in итог.поступило}
    обработано = {п.file_id for п in итог.обработано}
    assert поступило == {"фид-КП-АЛЬФА-1", "фид-КП-БЕТА-2", "фид-КП-ГАММА-3", "фид-КП-ПОЗДНИЙ-8", "mail:880001"}
    assert обработано == {"фид-КП-АЛЬФА-1", "фид-КП-БЕТА-2", "фид-КП-ГАММА-3", "фид-КП-ДЕЛЬТА-4",
                          "фид-КП-ЭПСИЛОН-5", "фид-КП-СТАРЫЙ-7", "mail:880001", "mail-body:7700124",
                          "фид-СДЕЛКА-10", "фид-СДЕЛКА-12"}
    п = {x.file_id: x for x in итог.замер.предложения.values()}
    assert "фид-ЗАПРОС-НАШ-6" not in п and "фид-СДЕЛКА-11" not in п and "фид-КП-ВНЕ-9" not in п
    assert итог.замер.вне_полей_кп == 1
    assert итог.замер.не_поставщика == 1                # D4: спецификация заказчика
    assert итог.замер.без_записи_файла == 1             # строка цены без записи lib_files
    assert "фид-СДЕЛКА-14" not in п and "фид-ПРИЗРАК-13" not in п
    assert п["фид-КП-АЛЬФА-1"].откуда_дата == wo.ДАТА_КП
    assert (п["фид-КП-БЕТА-2"].откуда_дата, п["фид-КП-БЕТА-2"].дата_по_карточке) == (wo.ДАТА_КАРТОЧКИ, True)
    assert п["mail:880001"].откуда_дата == wo.ДАТА_CRM
    assert п["фид-КП-ДЕЛЬТА-4"].откуда_дата == wo.НЕТ_ДАТЫ
    # Переразобранный старый КП: дата до окна, обработан в окне, повторно.
    assert итог.до_окна_обработаны == {wo.ДАТА_КП: 1}
    assert [x.первичность(ОКНО) for x in итог.обработано].count(wo.ВПЕРВЫЕ) == 4
    assert п["фид-КП-СТАРЫЙ-7"].первичность(ОКНО) == wo.ПОВТОРНО
    assert п["фид-КП-ГАММА-3"].первичность(ОКНО) == wo.ПОВТОРНО
    assert п["фид-КП-ДЕЛЬТА-4"].первичность(ОКНО) == wo.НЕ_УСТАНОВИТЬ
    # Чужой поток и выведенная строка в счёт не идут.
    assert п["фид-КП-ДЕЛЬТА-4"].строк_цены == 0 and п["фид-КП-БЕТА-2"].строк_цены == 0
    # Компания карточки — по строкам цены любого её файла.
    assert п["фид-КП-БЕТА-2"].компании == {"5550001"}
    assert п["mail:880001"].компании == {"5550002"}
    assert п["mail-body:7700124"].компании == set()

    своды = wo.своды_по_источнику(итог.обработано, ОКНО, итог.замер.компании_реестра)
    в = своды["всего"]
    assert (в.файлов, в.с_позициями, в.позиций, в.с_ценой, в.строк_цены) == (10, 6, 22, 4, 8)
    assert в.статусы == {"разобран": 6, "пусто (нет текста)": 1, "не скачался": 1,
                         "без спецификации": 1, "формат не читаем": 1}
    assert в.валюты == {"EUR": 3, "USD": 1, "RUB": 2, "CNY": 2}
    assert (в.с_поставщиком, в.низкая, в.с_датой, в.дата_из_кп) == (6, 1, 8, 5)
    assert (len(в.компании), len(в.сущности), len(в.бренды), len(в.бренды_карточек), len(в.карточки)) == \
        (2, 2, 2, 1, 3)
    assert в.тел_писем == 1
    assert (своды["карточки"].файлов, своды["письма"].файлов, своды["сделки"].файлов) == (6, 2, 2)
    assert (своды["карточки"].пишущих_с_ценой, своды["карточки"].пишущих) == (3, 6)
    assert своды["сделки"].пишущих == 0
    assert в.немые == {("текст", "НЕ найдена"): 1}
    assert sum(в.почему_нет.values()) == 4
    from library import offer_role as orl
    assert своды["карточки"].роли == {orl.ТРЕЙДЕР: 2, orl.НЕ_ОПРЕДЕЛЕНО: 4}
    assert своды["письма"].роли == {orl.ПРЯМОЕ: 1, orl.НЕ_ОПРЕДЕЛЕНО: 1}

    с1 = wo.своды_по_источнику(итог.поступило, ОКНО, итог.замер.компании_реестра)["всего"]
    assert (с1.файлов, с1.с_ценой, с1.строк_цены) == (5, 4, 7)
    по_дням = wo.своды_по_дням(итог.обработано, ОКНО, итог.замер.компании_реестра,
                               lambda x: x.обработан.astimezone(timezone.utc).date())
    assert [по_дням[м].файлов for м in по_дням] == [1, 1, 2, 2, 3, 1, 0, 10]


@нужна_база
def test_журнал_без_данных_и_без_подробностей(база, tmp_path):
    путь = tmp_path / "detail.json"
    код, текст = _прогон(база, {"DETAIL_OUT": str(путь)})
    assert код == 0
    for кусок in УТЕЧКИ:
        assert кусок not in текст, f"в журнал попало: {кусок!r}"
    assert "СРЕЗ 1. ПОСТУПИЛО В ОКНЕ" in текст and "СРЕЗ 2. ОБРАБОТАНО НАМИ В ОКНЕ" in текст
    assert "завышена ПО ПОСТРОЕНИЮ" in текст
    assert "ACCESS_DENIED" in текст                     # константа портала — можно
    assert "подробная часть не пишется" in текст
    assert not путь.exists()


@нужна_база
@нужен_openssl
def test_подробности_только_с_открытым_ключом(база, tmp_path):
    закрытый, открытый_b64 = _пара(tmp_path)
    путь = tmp_path / "detail.json"
    код, текст = _прогон(база, {"DETAIL_OUT": str(путь),
                                "PUBKEY_B64": base64.b64encode(закрытый.read_bytes()).decode()})
    assert код == 0 and not путь.exists() and "::error::" in текст

    код, текст = _прогон(база, {"DETAIL_OUT": str(путь), "PUBKEY_B64": открытый_b64})
    assert код == 0 and путь.exists()
    assert oct(путь.stat().st_mode & 0o777) == oct(0o600)
    for кусок in УТЕЧКИ:
        assert кусок not in текст, f"в журнал попало: {кусок!r}"
    данные = json.loads(путь.read_text(encoding="utf-8"))
    assert данные["окно"] == {"с": "2026-09-14", "по": "2026-09-20", "дней": 7, "пояс": "UTC",
                              "границы": "включительно"}
    по_файлу = {x["file_id"]: x for x in данные["предложения"]}
    assert данные["предложений"] == len(по_файлу) == 11
    a = по_файлу["фид-КП-АЛЬФА-1"]
    assert a["карточка"] == "9900771" and a["срезы"] == ["поступило в окне", "обработано в окне"]
    assert a["компании"] == [{"ключ_портала": "5550001", "реестр": "KV-S-000001-8", "имя": "ООО Ромашка Трейд"}]
    # Сумма — только названная в КП; цена на количество не домножалась.
    assert a["суммы"]["EUR"] == {"строк": 3, "итог_по_КП": 290.0, "строк_с_итогом": 2,
                                 "сумма_цен_за_единицу": 180.0}
    assert len(a["строки_цены"]) == 3 and a["строки_цены"][0]["код"].startswith("ZX-ТАЙНА-10")
    assert max(len(x["наименование"]) for x in a["строки_цены"]) <= 120
    assert "Zentrix" in a["бренды"] and a["бренды_карточки_СП176"] == ["7"]
    assert a["роль"]["role"] == "трейдер"
    assert по_файлу["фид-КП-БЕТА-2"]["поступило"]["по_другому_файлу_карточки"] is True
    assert по_файлу["фид-КП-ПОЗДНИЙ-8"]["срезы"] == ["поступило в окне"]
    assert по_файлу["фид-КП-СТАРЫЙ-7"]["срезы"] == ["обработано в окне"]
    assert по_файлу["фид-КП-СТАРЫЙ-7"]["обработано"]["первичность"] == wo.ПОВТОРНО
    assert по_файлу["mail:880001"]["владелец_письма"] == "C5550002"
    assert по_файлу["mail:880001"]["роль"]["role"] == "прямое"
    assert по_файлу["фид-СДЕЛКА-10"]["сделка"] == "4440001" and по_файлу["фид-СДЕЛКА-10"]["роль"] is None


@нужна_база
def test_без_необязательных_колонок_замер_не_падает(база):
    """Инструмент чтения не падает на колонке, которой в базе ещё нет: источник
    даты квотации снят — дата остаётся, источник «не записан», оговорка печатается."""
    import psycopg2
    conn = psycopg2.connect(DSN, options=f"-c search_path={СХЕМА}")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("alter table lib_prices drop column price_date_src cascade")
    conn.close()
    код, текст = _прогон(база, {})
    assert код == 0
    assert "price_date_src в базе нет" in текст
    with база.cursor() as cur, redirect_stdout(io.StringIO()):
        итог = wo.собрать(cur, ОКНО)
    assert {п.откуда_дата for п in итог.поступило} == {wo.ДАТА_БЕЗ_ИСТОЧНИКА}


# ── Прогон ───────────────────────────────────────────────────────────────────

def _шаги():
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return wf, wf["jobs"]["run"]["steps"]


def test_job_подключён_и_входы_не_прибиты():
    wf, шаги = _шаги()
    входы = wf[True]["workflow_dispatch"]["inputs"]
    assert "weekly-offers" in входы["job"]["options"]
    assert len(входы) <= 25                                  # предел GitHub
    for имя in ("days", "date_from", "date_to", "pubkey"):
        assert входы[имя]["type"] == "string", имя
    assert входы["days"]["default"] == "7" and входы["pubkey"]["default"] == ""
    шаг = next(s for s in шаги if s.get("if") == "inputs.job == 'weekly-offers'")
    # Входы — в окружение как есть; строка запуска ничего не подменяет.
    assert шаг["run"].strip() == "python scripts/weekly_offers.py"
    env = шаг["env"]
    assert env["DAYS"] == "${{ inputs.days }}"
    assert env["FROM"] == "${{ inputs.date_from }}"
    assert env["TO"] == "${{ inputs.date_to }}"
    assert env["PUBKEY_B64"] == "${{ inputs.pubkey }}"
    assert env["SUPABASE_DB_URL"] == "${{ secrets.SUPABASE_DB_URL }}"
    # Открытый файл — вне рабочей папки: загрузка артефакта его не видит.
    assert env["DETAIL_OUT"].startswith("/tmp/")
    assert "schedule" not in wf[True]


def test_в_артефакт_только_зашифрованное():
    _, шаги = _шаги()
    шифр = next(s for s in шаги if s.get("name") == "Шифрование подробной части")
    загрузка = next(s for s in шаги if s.get("name") == "Подробная часть — только зашифрованной")
    for s in (шифр, загрузка):
        assert "always()" in s["if"] and "inputs.pubkey != ''" in s["if"]
    основной = next(s for s in шаги if s.get("if") == "inputs.job == 'weekly-offers'")
    assert шифр["env"]["DETAIL"] == основной["env"]["DETAIL_OUT"]
    assert загрузка["uses"].startswith("actions/upload-artifact@")
    assert загрузка["with"]["path"] == шифр["env"]["ENC_DIR"] + "/*.enc"
    assert загрузка["with"]["retention-days"] == 1
    for нужно in ("rsa_padding_mode:oaep", "-pbkdf2", "aes-256-cbc", "openssl rand", "shred"):
        assert нужно in шифр["run"], нужно
    # Шифрование — после замера и до загрузки.
    индексы = [шаги.index(s) for s in (основной, шифр, загрузка)]
    assert индексы == sorted(индексы)


def _шаг_шифрования(env: dict) -> subprocess.CompletedProcess:
    _, шаги = _шаги()
    шифр = next(s for s in шаги if s.get("name") == "Шифрование подробной части")
    return subprocess.run(["bash", "-c", шифр["run"]], env={"PATH": os.environ["PATH"], **env},
                          capture_output=True, text=True)


@нужен_openssl
def test_шифрование_и_расшифровка_парой(tmp_path):
    закрытый, открытый_b64 = _пара(tmp_path)
    открытый_файл = tmp_path / "detail.json"
    данные = {"предложения": [{"file_id": "фид-КП-АЛЬФА-1", "компания": "ООО Ромашка Трейд", "цена": 100.5}]}
    открытый_файл.write_text(json.dumps(данные, ensure_ascii=False), encoding="utf-8")
    папка = tmp_path / "enc"
    итог = _шаг_шифрования({"PUBKEY_B64": открытый_b64, "DETAIL": str(открытый_файл), "ENC_DIR": str(папка)})
    assert итог.returncode == 0, итог.stderr
    assert not открытый_файл.exists(), "открытый файл должен быть стёрт до загрузки"
    файлы = sorted(p.name for p in папка.iterdir())
    assert файлы == ["weekly_offers_detail.json.enc", "weekly_offers_key.enc"]
    assert b"\xd0\xa0\xd0\xbe\xd0\xbc" not in (папка / "weekly_offers_detail.json.enc").read_bytes()  # «Ром»
    # Расшифровка — ровно командами из комментария шага.
    ключ = tmp_path / "key.txt"
    subprocess.run(["openssl", "pkeyutl", "-decrypt", "-inkey", str(закрытый), "-pkeyopt",
                    "rsa_padding_mode:oaep", "-in", str(папка / "weekly_offers_key.enc"), "-out", str(ключ)],
                   check=True, capture_output=True)
    выход = tmp_path / "out.json"
    subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-md", "sha256",
                    "-in", str(папка / "weekly_offers_detail.json.enc"), "-out", str(выход),
                    "-pass", f"file:{ключ}"], check=True, capture_output=True)
    assert json.loads(выход.read_text(encoding="utf-8")) == данные


@нужен_openssl
def test_шифрование_с_негодным_ключом_стирает_открытый_файл(tmp_path):
    открытый_файл = tmp_path / "detail.json"
    открытый_файл.write_text('{"x": 1}', encoding="utf-8")
    папка = tmp_path / "enc"
    итог = _шаг_шифрования({"PUBKEY_B64": base64.b64encode(b"not a key").decode(),
                            "DETAIL": str(открытый_файл), "ENC_DIR": str(папка)})
    assert итог.returncode != 0
    assert not открытый_файл.exists()
    assert not папка.exists() or not any(папка.iterdir())
    # Подробной части нет — шаг спокойно выходит.
    итог = _шаг_шифрования({"PUBKEY_B64": "x", "DETAIL": str(tmp_path / "нет.json"), "ENC_DIR": str(папка)})
    assert итог.returncode == 0 and "шифровать нечего" in итог.stdout
