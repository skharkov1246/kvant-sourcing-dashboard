#!/usr/bin/env python3
"""Финальная экономика сделки: файлы «Экономика проекта» открытых сделок реализации →
закрытая база → свод для вкладки «Реализация».

РЕШЕНИЕ ВЛАДЕЛЬЦА 23.10.2026 (DECISIONS.md): финальная экономика — из файла «Экономика
проекта» на общем диске Яндекс 360 (ключ Диска аккаунта компании), 30 файлов в день, по
одному, от новых сделок к старым; читается по шаблону, без модели (scripts/econ_template.py).
Продажа — переторжка, если её столбцы заполнены, иначе «Наше КП»; закупка — оффер
поставщика в евро по курсам файла; «Сумма» сделки сверяется с файлом в валюте подачи и
без НДС (OPPORTUNITY − TAX_VALUE против нетто-продажи файла; отношение около 1,20 или 1,22
при пустом налоге — «≈ с НДС», отдельным счётом, не расхождение: до решения владельца;
«Сумма», равная «Нашему КП» файла с переторжкой, — «до переторжки», тоже отдельно); наценка
показывается как «наценка над поставщиком», не как маржа. Путь к файлу проверен
зондом v56 (scripts/probe_econ_files.py), код ссылки — оттуда же.

РЕЖИМЫ
  --mode dry (по умолчанию) — сделки из портала, очередь, остаток предела суток (база —
      только на чтение, если строка подключения есть). Ни одного запроса к Диску, база
      не трогается.
  --mode live — по очереди: попытка суток в базе (econ_file_take_slot) → ссылка → файл →
      шаблон → строка в econ_file_reads с ключом прогона.

ССЫЛКА → ФАЙЛ. Поле сделки contracts.ECON_LINK — короткая ссылка clck.ru. Она
раскрывается БЕЗ перехода (один шаг) и ведёт на страницу проверки ссылок Яндекса
(sba.yandex.ru/redirect?url=…); её параметр url — https://disk.yandex.ru/edit/disk/<путь>.
Путь — «vd/<метка>/disk/<путь внутри>» (общий диск Яндекс 360, общий_диск). Скачивание —
virtual-disks/resources/download?path=vd:<метка>:disk:/<путь внутри> с ключом прогона
(YADISK_TOKEN), затем GET выданной ссылки с тем же ключом.

ОЧЕРЕДЬ. Открытые сделки реализации (кат. 0, семантика P, не «Оплата получена») со
ссылкой. Первыми — сделки, у которых «Сумма» менялась после последнего сверенного чтения
(сверка сегодня — по полям сделки, уже прочитанным для очереди, — не та, что записал
прогон чтения; dealctx.econ_changed): карточка такой сделки сверку отложила до перечтения,
и ждать ПОВТОР_ДНЕЙ ей нельзя; если после сверенного чтения уже была попытка — в общий
черёд. Затем ни разу не читанные — от новых по DATE_CREATE к старым; затем читанные
раньше ПОВТОР_ДНЕЙ назад — от давно читанных. Неудачная попытка — тоже чтение: ссылка,
которая не открывается никогда, не стоит первой каждый день. Чтением считаются только
строки прогонов, прошедших гейт (econ_file_runs): упавший прогон сделку не «прочитал».
Новая версия правил (METHOD_VER, правка econ_template): сделка, чьё последнее чтение не
«сверено» и сделано версией старше нынешней, — снова в черёд ни разу не читанных (от новых
сделок к старым): файл, не прочитанный прежними правилами, нынешние могут прочесть, и ждать
ПОВТОР_ДНЕЙ ему незачем. Прочтён нынешней версией — обычный черёд. Сбой доставки (ссылка,
диск, тип, закачка) от правил шаблона не зависит: поднята лишь версия econ_template — он
ждёт обычного черёда, место в пределе суток не тратится.

ПРЕДЕЛ. Не больше ПРЕДЕЛ_СУТОК файлов за сутки по Москве — каждая попытка, удачная или
нет. Предел держит база: econ_file_take_slot под замком считает попытки суток и
дописывает новую, только если есть место, — два прогона разом его не удвоят. Файлы — по
одному, пауза между ними не меньше ПАУЗА_С.

ЧАСТИ (распоряжение 22.09.2026): первые --limit сделок очереди режутся на --parts смежных
отрезков (10, 25 или 50) и идут подряд в одном процессе; каждая строка — своя транзакция.
Портал читается один раз, до частей (два запроса: стадии и сделки).

ДВЕ ПРОВЕРКИ ФАЙЛА — в econ_template; числа (продажа, закупка, наценка) пишутся в строку
только у статуса «сверено» — у прочих их нет и в базе (ограничение econ_file_reads_num_chk).
Сбой чтения или записи одной сделки (исключение разбора, отказ базы принять строку) прогон
не роняет: у сделки пишется строка «сбой чтения» без чисел — попытка потрачена, сделка
уходит в конец очереди и не держит её каждую ночь. База не принимает и её или не выдаёт
попытку суток — стоп, код 1.
ГЕЙТЫ ПРОГОНА (правило «сначала меряют, потом применяют»):
  • «шаблон другой» у больше ГЕЙТ_ШАБЛОН_ДОЛЯ открытых файлов (от ГЕЙТ_ОТ) — шаблон
    сменился: чтение останавливается (предел суток не тратится впустую), код 1;
  • у сверенных строк медиана |сумма сделки / продажа файла − 1| больше ГЕЙТ_СУММЫ (от
    ГЕЙТ_ОТ, сделки без суммы не в счёт) — разбор, скорее всего, берёт не тот столбец или
    не ту валюту: прогон сам помечает свои строки (voided_at), код 1.
РЕШЕНИЕ ГЕЙТА — В БАЗЕ: гейт сумм пройден — прогон пишет себя в econ_file_runs (passed_at),
и только строки таких прогонов видит вид econ_file_latest, а с ним свод для вкладки (и
ночной свод chat-context.yml). Прогон упал, его сняли или он не дошёл до гейта — его строк
во вкладке нет, хотя они и не помечены.

В ЖУРНАЛ — ТОЛЬКО АГРЕГАТЫ (CLAUDE.md, правило 17): ни номеров сделок, ни ссылок,
путей, меток диска, имён файлов, ни сумм и наценок — только счётчики по статусам и
по расхождению с суммой сделки; ссылки — видом (clck.ru, disk.yandex, другой), без домена:
ссылка на портал клиента — его имя. Ошибка — классом, без текста. У несверенных файлов —
счёт по (статус, где, вид значения): вид — из закрытого списка econ_template.вид_значения
(пусто, число, ошибка формулы, текст-валюта:<код>, длина текста корзиной, счёт слов словаря
шаблона), сам текст ячейки и его слова в журнал не идут — в ячейках бывают имена клиентов.
У нераспознанной D4 (v5) — признаки листа счётом по файлам (econ_template.зонд_валюты:
адрес:код валюты, таблицы курсов, подписи итогов, AE, полосы курса итогов) и пути чтения
счётом: откуда валюта подачи, чем сверен курс, сдвиг шаблона — те же закрытые списки, что
пишутся в строку базы (sub_cur_src, rate_src, col_shift — правило 16).

    python scripts/econ_files.py                     # dry, до 30 сделок
    python scripts/econ_files.py --mode live --limit 30 --parts 10
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import contracts  # noqa: E402  (поле ссылки ECON_LINK, стадия «Оплата получена»)
import dealctx  # noqa: E402  (сверка «Суммы» сделки с файлом — та же, что у сборки)
import econ_template as et  # noqa: E402

METHOD_VER = f"econ_files:v3.{et.ВЕРСИЯ}"
КАТЕГОРИЯ_РЕАЛИЗАЦИИ = 0
ПРЕДЕЛ_СУТОК = 30             # решение владельца 23.10.2026: не больше 30 файлов в сутки по Москве
ПОВТОР_ДНЕЙ = 14              # читанное раньше — перечитать не чаще
ПАУЗА_С = 2.0                 # между файлами
ЧАСТИ = (10, 25, 50)          # распоряжение 22.09.2026: минимум 10
СРОК_МИН = 20                 # дальше новых файлов не брать: job ограничен 30 минутами
МАКС_БАЙТ = 30 * 1024 * 1024
ТАЙМАУТ_С = 30
API_СКАЧАТЬ = "https://cloud-api.yandex.net/v1/disk/virtual-disks/resources/download?path="
# гейты прогона — таблицей (правило 4)
ГЕЙТ_ОТ = 5
ГЕЙТ_ШАБЛОН_ДОЛЯ = 0.5
ГЕЙТ_СУММЫ = 0.5

# сбои доставки файла — дополняют статусы шаблона до закрытого списка базы
НЕ_РАСКРЫЛАСЬ = "ссылка не раскрылась"
НЕ_ОБЩИЙ = "не общий диск"
НЕ_XLSX = "не xlsx"
НЕ_СКАЧАЛСЯ = "не скачался"
СБОЙ = "сбой чтения"          # исключение разбора или отказ базы принять строку — наш сбой, не файла
СТАТУСЫ = et.СТАТУСЫ_ФАЙЛА + (НЕ_РАСКРЫЛАСЬ, НЕ_ОБЩИЙ, НЕ_XLSX, НЕ_СКАЧАЛСЯ, СБОЙ)
#: сбои, которые не заменяют прежнего чтения в econ_file_latest (сеть, наш код)
НЕ_ЧТЕНИЕ = (НЕ_РАСКРЫЛАСЬ, НЕ_СКАЧАЛСЯ, СБОЙ)
#: исходы, что зависят от правил шаблона (econ_template): статусы разбора и сбой разбора.
#: Сбои доставки (ссылка, диск, тип, закачка) — нет: их перечитывает лишь версия econ_files.
ОТ_ШАБЛОНА = et.СТАТУСЫ_ФАЙЛА + (СБОЙ,)

# TAX_VALUE — НДС в «Сумме»: сверка с файлом — без НДС (OPPORTUNITY − TAX_VALUE)
ПОЛЯ_СДЕЛКИ = ["ID", "DATE_CREATE", "STAGE_ID", "STAGE_SEMANTIC_ID", "CATEGORY_ID", "OPPORTUNITY",
               "TAX_VALUE", "CURRENCY_ID", contracts.ECON_LINK]


# --------------------------------------------------------------------------- портал
def вид_ссылки(ссылка: str) -> str:
    """Вид ссылки для журнала — без домена: ссылка на портал клиента в журнале — его имя."""
    try:
        хост = (urllib.parse.urlparse(ссылка).hostname or "").lower()
    except ValueError:
        return "другой"
    if хост == "clck.ru" or хост.endswith(".clck.ru"):
        return "clck.ru"
    if хост.startswith("disk.yandex.") or ".disk.yandex." in хост:
        return "disk.yandex"
    return "другой"


def кандидаты(сделки: list[dict], стадии: dict[str, dict], счёт: Counter) -> list[dict]:
    """Открытые сделки реализации со ссылкой на файл. Открытость — по семантике стадии
    из справочника (запасной — поле сделки); «Оплата получена» — успех, не читается."""
    out = []
    for d in сделки:
        if str(d.get("CATEGORY_ID", КАТЕГОРИЯ_РЕАЛИЗАЦИИ)) != str(КАТЕГОРИЯ_РЕАЛИЗАЦИИ):
            continue
        m = стадии.get(str(d.get("STAGE_ID") or "")) or {}
        sem = str(m.get("sem") or d.get("STAGE_SEMANTIC_ID") or "").upper()
        if sem != "P" or contracts.PAID_RX.search(str(m.get("name") or "")):
            счёт["закрыта"] += 1
            continue
        счёт["открытых"] += 1
        ссылка = str(d.get(contracts.ECON_LINK) or "").strip()
        if not ссылка.lower().startswith("http"):
            счёт["без ссылки"] += 1
            continue
        счёт["ссылка " + вид_ссылки(ссылка)] += 1
        out.append(d)
    return out


def выбрать(client, счёт: Counter) -> list[dict]:
    """Портал — один раз на прогон: справочник стадий и открытые сделки воронки."""
    стадии = client.deal_stage_meta()
    сделки = client.list_deals_fast(filter={"CATEGORY_ID": КАТЕГОРИЯ_РЕАЛИЗАЦИИ, "STAGE_SEMANTIC_ID": "P"},
                                    select=ПОЛЯ_СДЕЛКИ)
    return кандидаты(сделки, стадии, счёт)


def _время(v) -> dt.datetime | None:
    if isinstance(v, dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)
    try:
        t = dt.datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=dealctx.MSK)


def сумма_менялась(сделка: dict, с: dict) -> bool:
    """«Сумма» сделки (без НДС) против продажи файла сегодня — не та сверка, что записал
    прогон чтения (с: сверка_на_чтение). Тот же расчёт и признак, что у сборки."""
    r, why = dealctx.econ_ratio(сделка.get("OPPORTUNITY"), et.валюта(str(сделка.get("CURRENCY_ID") or "")) or "",
                                с, tax=сделка.get("TAX_VALUE"))
    return dealctx.econ_changed(round(r, 6) if r is not None else None, why, с.get("ratio"), с.get("note"))


def _версия(method_ver) -> tuple[int, int] | None:
    """«econ_files:v3.econ_template:v4» → (3, 4); иная запись — None."""
    м = re.fullmatch(r"econ_files:v(\d+)\.econ_template:v(\d+)", str(method_ver or ""))
    return (int(м.group(1)), int(м.group(2))) if м else None


def прежняя_версия(method_ver, статус: str | None = None) -> bool:
    """Строка прочитана правилами старше нынешних (METHOD_VER). Неразборчивая версия — тоже
    прежняя: нынешними правилами файл не читан. Версия новее нынешней (прогон с ветки) — нет.
    статус — сбой доставки (не из ОТ_ШАБЛОНА): прежняя, только если старше версия econ_files —
    правила шаблона доставку не меняют."""
    их, наша = _версия(method_ver), _версия(METHOD_VER)
    if их is None:
        return True
    if статус is not None and статус not in ОТ_ШАБЛОНА:
        return их[0] < наша[0]
    return их < наша


def очередь(канд: list[dict], прочитано: dict[int, dt.datetime], сейчас: dt.datetime,
            счёт: Counter | None = None, сверка: dict[int, dict] | None = None,
            последние: dict[int, tuple[str, str]] | None = None) -> list[dict]:
    """Первыми — «Сумма» менялась после сверенного чтения (сверка — сверка_на_чтение) и
    попытки после него не было; затем ни разу не читанные — от новых сделок к старым, и с ними
    те, чьё последнее чтение (последние: статус и версия правил) не «сверено» и сделано
    версией старше нынешней (сбой доставки — версией econ_files старше нынешней); затем
    читанные раньше ПОВТОР_ДНЕЙ — от давно читанных;
    читанные недавно — не берутся."""
    счёт = счёт if счёт is not None else Counter()
    сверка = сверка or {}
    последние = последние or {}
    менялись, новые, давние = [], [], []
    for d in канд:
        когда = прочитано.get(int(d["ID"]))
        с = сверка.get(int(d["ID"]))
        посл = последние.get(int(d["ID"]))
        if когда is not None and с is not None and когда <= с["at"] and сумма_менялась(d, с):
            менялись.append((когда, d))
        elif когда is None:
            новые.append(d)
        elif посл is not None and посл[0] != et.СВЕРЕНО and прежняя_версия(посл[1], посл[0]):
            новые.append(d)
            счёт["прежняя версия"] += 1
        elif сейчас - когда >= dt.timedelta(days=ПОВТОР_ДНЕЙ):
            давние.append((когда, d))
        else:
            счёт["свежих"] += 1
    счёт["сумма менялась"] += len(менялись)
    счёт["ни разу"] += len(новые)
    счёт["перечитать"] += len(давние)
    новые.sort(key=lambda d: (_время(d.get("DATE_CREATE")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
                              int(d["ID"])), reverse=True)
    давние.sort(key=lambda x: (x[0], int(x[1]["ID"])))
    менялись.sort(key=lambda x: (x[0], int(x[1]["ID"])))
    return [d for _к, d in менялись] + новые + [d for _к, d in давние]


def нарезать(список: list, частей: int) -> list[list]:
    """Ровно частей СМЕЖНЫХ отрезков, размеры различаются не больше чем на один."""
    n = len(список)
    return [список[i * n // частей:(i + 1) * n // частей] for i in range(частей)]


# --------------------------------------------------------------------------- Диск
class _НетПерехода(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Диск:
    """Короткая ссылка и Диск Яндекса. В журнал — только коды ответов (коды)."""

    def __init__(self, ключ: str):
        self.ключ = ключ
        self.коды: Counter = Counter()

    def раскрыть(self, url: str) -> str:
        """Куда ведёт короткая ссылка — один шаг, без перехода (как зонд v56)."""
        op = urllib.request.build_opener(_НетПерехода())
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "kvant-econ"})
        try:
            with op.open(req, timeout=ТАЙМАУТ_С) as r:
                return r.geturl()
        except urllib.error.HTTPError as e:
            return e.headers.get("Location") or ""
        except Exception as e:                                   # noqa: BLE001
            self.коды[f"ссылка: {type(e).__name__}"] += 1
            return ""

    def получить(self, url: str) -> bytes | None:
        """Тело ответа с ключом Диска или None; код — в коды. Текст ошибки API не берётся:
        в описании бывает сам ключ."""
        req = urllib.request.Request(url, headers={"User-Agent": "kvant-econ", "Authorization": f"OAuth {self.ключ}"})
        try:
            with urllib.request.urlopen(req, timeout=ТАЙМАУТ_С) as r:
                self.коды[f"Диск: {r.status}"] += 1
                тело = r.read(МАКС_БАЙТ + 1)
                return тело if len(тело) <= МАКС_БАЙТ else None
        except urllib.error.HTTPError as e:
            self.коды[f"Диск: {e.code}"] += 1
            return None
        except Exception as e:                                   # noqa: BLE001
            self.коды[f"Диск: {type(e).__name__}"] += 1
            return None


def общий_диск(путь: str) -> tuple[str, str] | None:
    """Путь файла на общем диске Яндекс 360 → (метка, путь внутри диска) или None.
    Принимает «vd/<метка>/disk/<путь>», «vd/<метка>/<путь>» и «vd:<метка>:disk:/<путь>»
    (зонд v56, scripts/probe_econ_files.общий_диск)."""
    м = re.match(r"^vd[:/]([A-Za-z0-9_-]+)[:/](?:disk:?/)?(.*)$", путь.strip("/"))
    return (м.group(1), м.group(2)) if м and м.group(2) else None


def найти_файл(ссылка: str, диск) -> tuple[str | None, str | None, str | None]:
    """Ссылка поля → (статус сбоя или None, путь для API Диска, путь внутри диска)."""
    цель = ссылка.strip()
    if "clck.ru" in urllib.parse.urlparse(цель).netloc.lower():
        цель = диск.раскрыть(цель).strip()
    пу = urllib.parse.urlparse(цель)
    if "sba.yandex" in пу.netloc.lower():
        цель = ((urllib.parse.parse_qs(пу.query).get("url") or [""])[0]).strip()
        пу = urllib.parse.urlparse(цель)
    if "disk.yandex" not in пу.netloc.lower():
        return НЕ_РАСКРЫЛАСЬ, None, None
    части = [x for x in пу.path.split("/") if x]
    if части[:2] not in (["edit", "disk"], ["client", "disk"]):
        return НЕ_ОБЩИЙ, None, None
    об = общий_диск("/".join(urllib.parse.unquote(x) for x in части[2:]))
    if not об:
        return НЕ_ОБЩИЙ, None, None
    метка, внутри = об
    if not внутри.lower().endswith((".xlsx", ".xlsm")):
        return НЕ_XLSX, None, внутри
    return None, f"vd:{метка}:disk:/{внутри}", внутри


def скачать(путь_api: str, диск) -> bytes | None:
    raw = диск.получить(API_СКАЧАТЬ + urllib.parse.quote(путь_api, safe=""))
    try:
        href = json.loads(raw).get("href") if raw else None
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        href = None
    # ключ Диска уходит со ссылкой (так качает зонд v56) — только на хост Яндекса
    хост = urllib.parse.urlparse(str(href or "")).hostname or ""
    if not str(href or "").startswith("https://") or not хост.endswith((".yandex.ru", ".yandex.net")):
        return None
    return диск.получить(str(href))


def ключ_файла(внутри: str | None) -> str | None:
    """sha256 пути внутри диска — сам путь не хранится нигде."""
    return hashlib.sha256(внутри.encode("utf-8")).hexdigest() if внутри else None


# --------------------------------------------------------------------------- строка
def строка(run_id: str, сделка: dict, статус: str, file_key: str | None, итог: et.Итог | None = None) -> dict:
    """Строка econ_file_reads. Числа файла и сверка с «Суммой» сделки (без НДС: OPPORTUNITY −
    TAX_VALUE) — только у сверенного файла: несверенный числа не несёт (дефект 3 ревизии —
    отрицательная «продажа» неправдоподобного файла роняла запись). tax_empty — не колонка
    базы (База.КОЛОНКИ его не пишет): налога в сделке нет — для счёта «≈ с НДС» в журнале."""
    и = итог or et.Итог(статус)
    свер = статус == et.СВЕРЕНО and и.сверено
    deal_cur = et.валюта(str(сделка.get("CURRENCY_ID") or "")) or None
    ratio, note = None, None
    if свер:
        r, why = dealctx.econ_ratio(сделка.get("OPPORTUNITY"), deal_cur or "",
                                    {"sale": и.sale_sub, "cur": и.sub_cur, "saleEur": и.sale_eur, "fx": и.fx},
                                    tax=сделка.get("TAX_VALUE"))
        ratio, note = (round(r, 6) if r is not None else None), (why or None)

    def число(v):
        return v if свер else None
    return {"run_id": run_id, "method_ver": METHOD_VER, "deal_id": int(сделка["ID"]), "file_key": file_key,
            "status": статус, "sub_cur": и.sub_cur, "sale_sub": число(и.sale_sub), "sale_eur": число(и.sale_eur),
            "purchase_eur": число(и.purchase_eur), "markup": число(и.markup), "rebid": и.rebid,
            "items": и.items or None,
            "coef_median": и.coef_median, "totals_dev": и.totals_dev, "bad_lines": и.bad_lines,
            "fx": (и.fx or None) if свер else None, "deal_cur": deal_cur, "bitrix_ratio": ratio,
            "bitrix_note": note, "kp_sale_sub": число(и.kp_sale_sub) if и.rebid else None,
            "why": и.why or None, "tax_empty": not dealctx._num(сделка.get("TAX_VALUE")),
            # v5 (правило 16 — храни, почему получилось значение): откуда валюта подачи, чем
            # сверен её курс, на сколько столбцов сдвинут шаблон — закрытые списки шаблона
            "sub_cur_src": и.sub_src if и.sub_cur and и.sub_src in et.ИСТОЧНИКИ_ВАЛЮТЫ else None,
            "rate_src": и.rate_src if свер and и.rate_src in et.ИСТОЧНИКИ_КУРСА else None,
            "col_shift": и.сдвиг if и.сдвиг in (0, *et.СДВИГИ) else None,
            # вид значения там, где не сошлось (econ_template.вид_значения) — только журналу
            "shape": (и.вид or None) if not свер else None,
            # признаки листа у нераспознанной D4 (econ_template.зонд_валюты) — только журналу
            "probe": (list(и.зонд) or None) if not свер else None}


def прочитать_сделку(run_id: str, сделка: dict, диск) -> dict:
    статус, путь_api, внутри = найти_файл(str(сделка.get(contracts.ECON_LINK) or ""), диск)
    file_key = ключ_файла(внутри)
    if статус:
        return строка(run_id, сделка, статус, file_key)
    b = скачать(путь_api, диск)
    if not b:
        return строка(run_id, сделка, НЕ_СКАЧАЛСЯ, file_key)
    итог = et.разобрать(b)
    return строка(run_id, сделка, итог.status, file_key, итог)


# --------------------------------------------------------------------------- база
class База:
    """Закрытая база Supabase. statement_timeout и lock_timeout — в строке подключения
    (правила 9 и 12): SET откатывается с транзакцией."""

    КОЛОНКИ = ("run_id", "method_ver", "deal_id", "file_key", "status", "sub_cur", "sale_sub", "sale_eur",
               "purchase_eur", "markup", "rebid", "items", "coef_median", "totals_dev", "bad_lines", "fx",
               "deal_cur", "bitrix_ratio", "bitrix_note", "kp_sale_sub", "why", "sub_cur_src", "rate_src",
               "col_shift")

    def __init__(self, dsn: str, схема: str | None = None, только_чтение: bool = False):
        import psycopg2
        опции = "-c statement_timeout=60000 -c lock_timeout=10000"
        if схема:
            опции += f" -c search_path={схема}"
        self.с = psycopg2.connect(dsn, options=опции)
        if только_чтение:
            self.с.set_session(readonly=True)

    def _одно(self, sql: str, p=()):
        with self.с, self.с.cursor() as c:
            c.execute(sql, p)
            return c.fetchone()

    def нет_таблиц(self) -> list[str]:
        r = self._одно("select to_regclass('econ_file_reads') is null, to_regclass('econ_file_attempts') is null, "
                       "to_regclass('econ_file_runs') is null, "
                       "to_regprocedure('econ_file_take_slot(text, bigint, integer)') is null")
        return [имя for имя, нет in zip(("econ_file_reads", "econ_file_attempts", "econ_file_runs",
                                         "econ_file_take_slot"), r) if нет]

    def прочитано(self) -> dict[int, dt.datetime]:
        """Последнее непомеченное чтение каждой сделки (и неудачное — тоже чтение) — только
        из прогонов, прошедших гейт: строки упавшего или снятого прогона вкладка не видит
        (econ_file_latest), и сделку они не «прочитали» — иначе она ждала бы ПОВТОР_ДНЕЙ."""
        with self.с, self.с.cursor() as c:
            c.execute("select r.deal_id, max(r.read_at) from econ_file_reads r "
                      "join econ_file_runs g on g.run_id = r.run_id "
                      "where r.voided_at is null group by r.deal_id")
            return {int(d): t for d, t in c.fetchall()}

    def последние(self) -> dict[int, tuple[str, str]]:
        """Статус и версия правил последнего чтения каждой сделки — из тех же строк, что
        прочитано(): непомеченные, только прогонов, прошедших гейт."""
        with self.с, self.с.cursor() as c:
            c.execute("select distinct on (r.deal_id) r.deal_id, r.status, r.method_ver from econ_file_reads r "
                      "join econ_file_runs g on g.run_id = r.run_id "
                      "where r.voided_at is null order by r.deal_id, r.read_at desc, r.run_id desc")
            return {int(d): (str(s), str(v)) for d, s, v in c.fetchall()}

    def сверка_на_чтение(self) -> dict[int, dict]:
        """Сделки, чьё последнее чтение во вкладке (econ_file_latest) сверено: продажа и курсы
        файла и сверка с «Суммой» на день чтения — по ним очередь видит «Сумму», изменённую
        после чтения."""
        with self.с, self.с.cursor() as c:
            c.execute("select deal_id, read_at, sale_sub, sub_cur, sale_eur, fx, bitrix_ratio, bitrix_note "
                      "from econ_file_latest where status = %s", (et.СВЕРЕНО,))
            return {int(d): {"at": t, "sale": float(sale), "cur": cur, "saleEur": float(se),
                             "fx": {str(k): float(v) for k, v in (fx or {}).items()},
                             "ratio": float(ratio) if ratio is not None else None, "note": note}
                    for d, t, sale, cur, se, fx, ratio, note in c.fetchall()}

    def взято_сегодня(self) -> int:
        """Попыток за сутки по Москве — тем же счётом, что у econ_file_take_slot."""
        return int(self._одно("select count(*) from econ_file_attempts where at >= "
                              "(date_trunc('day', (now() at time zone 'UTC') + interval '3 hours') "
                              "- interval '3 hours') at time zone 'UTC'")[0])

    def взять(self, run_id: str, deal_id: int, предел: int) -> bool:
        return bool(self._одно("select econ_file_take_slot(%s, %s, %s)", (run_id, deal_id, предел))[0])

    def записать(self, r: dict) -> None:
        import psycopg2.extras
        значения = tuple(psycopg2.extras.Json(r[к]) if к == "fx" and r[к] is not None else r[к] for к in self.КОЛОНКИ)
        with self.с, self.с.cursor() as c:
            c.execute(f"insert into econ_file_reads ({', '.join(self.КОЛОНКИ)}) "
                      f"values ({', '.join(['%s'] * len(self.КОЛОНКИ))}) on conflict (run_id, deal_id) do nothing",
                      значения)

    def пометить_прогон(self, run_id: str) -> int:
        with self.с, self.с.cursor() as c:
            c.execute("update econ_file_reads set voided_at = now() where run_id = %s and voided_at is null",
                      (run_id,))
            return c.rowcount

    def отметить_прогон(self, run_id: str) -> tuple[int, int]:
        """Гейт пройден: прогон — в econ_file_runs, его строки видит econ_file_latest. Счёт
        строк — запросом к базе (правило 2). → (строк, сверено)."""
        with self.с, self.с.cursor() as c:
            c.execute("insert into econ_file_runs (run_id, method_ver, rows_n, verified_n) "
                      "select %s, %s, count(*), count(*) filter (where status = 'сверено') "
                      "  from econ_file_reads where run_id = %s and voided_at is null "
                      "on conflict (run_id) do nothing", (run_id, METHOD_VER, run_id))
            c.execute("select rows_n, verified_n from econ_file_runs where run_id = %s", (run_id,))
            строк, сверено = c.fetchone()
            return int(строк), int(сверено)

    def итог(self, run_id: str) -> Counter:
        """Что записано — запросом по ключу прогона (правило 2), без помеченных."""
        with self.с, self.с.cursor() as c:
            c.execute("select status, count(*) from econ_file_reads where run_id = %s and voided_at is null "
                      "group by status", (run_id,))
            return Counter({s: int(n) for s, n in c.fetchall()})

    def закрыть(self) -> None:
        self.с.close()


# --------------------------------------------------------------------------- прогон
def _доля(a: int, b: int) -> str:
    return f"{a} из {b} ({100 * a / b:.0f} %)" if b else "0 из 0"


НДС_СЧЁТ = "≈ с НДС (×1,20 или ×1,22, налог в сделке пуст)"
ДО_ПЕРЕТОРЖКИ_СЧЁТ = "«Сумма» — «Наше КП» до переторжки"
_СЧЁТ = {dealctx.ECON_OK: f"сходится (±{dealctx.ECON_GAP:.0%})", dealctx.ECON_VAT_LIKE: НДС_СЧЁТ,
         dealctx.ECON_PRE_REBID: ДО_ПЕРЕТОРЖКИ_СЧЁТ, dealctx.ECON_OVER: f"расходится больше {dealctx.ECON_GAP:.0%}"}


def сверка_с_суммой(строки: list[dict]) -> Counter:
    """Сверенные файлы против «Суммы» сделки без НДС — только счётчики, тем же вердиктом, что
    у сборки (dealctx.econ_verdict). «≈ с НДС» — отдельно: замер, сколько сделок держат
    «Сумму» с НДС при пустом TAX_VALUE (до решения владельца это не расхождение); при
    указанном налоге он вычтен, и ×1,20 — расхождение. «До переторжки» — тоже отдельно."""
    n: Counter = Counter()
    for r in строки:
        if r["status"] != et.СВЕРЕНО:
            continue
        if r["bitrix_ratio"] is None:
            n[r["bitrix_note"] or "не сверена"] += 1
            continue
        n[_СЧЁТ[dealctx.econ_verdict(float(r["bitrix_ratio"]), tax_empty=r.get("tax_empty", True),
                                     sale=r.get("sale_sub"), kp_sale=r.get("kp_sale_sub"))]] += 1
    return n


def журнал_видов(строки: list[dict]) -> list[str]:
    """Несверенные файлы счётом по (статус, где, вид значения) — все, и сбои доставки (где и
    вид — «—»). Вид — из закрытого списка econ_template.вид_значения: текста ячейки в нём нет."""
    n = Counter((r["status"], r.get("why") or "—", r.get("shape") or "—") for r in строки
                if r["status"] != et.СВЕРЕНО)
    if not n:
        return []
    return (["несверенные — вид значения там, где не сошлось (статус · где · вид: файлов; без текста ячеек):"]
            + [f"  {с} · {г} · {в}: {v}" for (с, г, в), v in sorted(n.items(), key=lambda x: (-x[1], x[0]))])


def журнал_зонда(строки: list[dict]) -> list[str]:
    """Нераспознанная D4 — признаки листа счётом по файлам (econ_template.зонд_валюты: адрес:код,
    таблицы курсов, подписи итогов, AE, полосы курса итогов). Признак — из адресов шаблона,
    кодов КОДЫ_ВАЛЮТ и слов закрытого списка; признак у файла считается один раз."""
    с_зондом = [r for r in строки if r.get("probe")]
    if not с_зондом:
        return []
    n = Counter(п for r in с_зондом for п in dict.fromkeys(r["probe"]))
    return ([f"D4 без валюты — признаки листа (файлов {len(с_зондом)}; признак: файлов; без текста ячеек и чисел):"]
            + [f"  {п}: {v}" for п, v in sorted(n.items(), key=lambda x: (-x[1], x[0]))])


def журнал_путей(строки: list[dict]) -> str:
    """Откуда валюта подачи и курс, сдвиг шаблона — счётом (закрытые списки шаблона)."""
    прочитано = [r for r in строки if r["status"] in et.СТАТУСЫ_ФАЙЛА]
    свер = [r for r in прочитано if r["status"] == et.СВЕРЕНО]

    def имя(ключ: str, v) -> str:
        return ("без сдвига" if v == 0 else f"{v:+d}") if ключ == "col_shift" else str(v)

    def счёт(ключ: str, rr: list[dict]) -> str:
        c = Counter(имя(ключ, r.get(ключ)) for r in rr if r.get(ключ) is not None)
        return " · ".join(f"{к} {v}" for к, v in sorted(c.items())) or "нет"
    return (f"пути чтения: валюта подачи — {счёт('sub_cur_src', прочитано)} (у сверенных — "
            f"{счёт('sub_cur_src', свер)}); курс подачи у сверенных — {счёт('rate_src', свер)}; "
            f"сдвиг шаблона — {счёт('col_shift', прочитано)} (у сверенных — {счёт('col_shift', свер)})")


def гейт_сумм(строки: list[dict]) -> tuple[bool, str]:
    """Разбор берёт не тот столбец или не ту валюту, если у большинства сверенных файлов
    сумма сделки далеко от продажи файла. Сделки без суммы — не довод."""
    откл = [abs(r["bitrix_ratio"] - 1) for r in строки
            if r["status"] == et.СВЕРЕНО and r["bitrix_ratio"] is not None]
    if len(откл) < ГЕЙТ_ОТ:
        return True, f"гейт сумм: сверенных с суммой {len(откл)} — меньше {ГЕЙТ_ОТ}, не судим"
    м = statistics.median(откл)
    return м <= ГЕЙТ_СУММЫ, f"гейт сумм: медиана |сумма сделки / продажа файла − 1| {'в пределах' if м <= ГЕЙТ_СУММЫ else 'больше'} {ГЕЙТ_СУММЫ:.0%} ({len(откл)} файлов)"


def прогон(режим: str, лимит: int, частей: int, *, портал, run_id: str, база=None, диск=None,
           печать=print, сейчас: dt.datetime | None = None, часы=time.monotonic, спать=time.sleep) -> int:
    """Весь прогон. Портал, база, Диск, часы и сон подставляются (тесты); печать — только
    агрегаты."""
    сейчас = сейчас or dt.datetime.now(dt.timezone.utc)
    печать(f"финальная экономика · режим {режим} · сделок до {лимит} · частей {частей} · ключ прогона {run_id}")
    счёт: Counter = Counter()
    канд = выбрать(портал, счёт)
    виды = " · ".join(f"{к.removeprefix('ссылка ')} {v}" for к, v in счёт.most_common() if к.startswith("ссылка "))
    печать(f"сделки: открытых реализации {счёт['открытых']} (закрытых в выдаче {счёт['закрыта']}); "
           f"со ссылкой «Экономика проекта» {_доля(len(канд), счёт['открытых'])}; вид ссылки: {виды or 'нет'}")
    прочитано = база.прочитано() if база is not None else {}
    сверка = база.сверка_на_чтение() if база is not None else {}
    последние = база.последние() if база is not None else {}
    q = очередь(канд, прочитано, сейчас, счёт, сверка, последние)
    взято = база.взято_сегодня() if база is not None else 0
    остаток = max(0, ПРЕДЕЛ_СУТОК - взято)
    берём = q[:min(лимит, остаток)] if база is not None else q[:лимит]
    печать(f"очередь: «Сумма» менялась после чтения {счёт['сумма менялась']} · "
           f"ни разу не читанных {счёт['ни разу']} (из них не сверены прежней версией правил "
           f"{счёт['прежняя версия']}) · перечитать (старше {ПОВТОР_ДНЕЙ} дн.) "
           f"{счёт['перечитать']} · свежих {счёт['свежих']}; предел суток по Москве: взято {взято} из "
           f"{ПРЕДЕЛ_СУТОК}{'' if база is not None else ' (база не подключена — не знаем)'}; берётся {len(берём)}")
    if режим == "dry":
        печать("холостой прогон: Диск не спрашивался, файлы не качались, база не тронута")
        return 0

    строки: list[dict] = []
    статусы: Counter = Counter()
    сбои: Counter = Counter()
    открыто = шаблон_другой = 0
    стоп = ""
    срок = часы() + СРОК_МИН * 60
    последний: float | None = None
    for k, часть in enumerate(нарезать(берём, частей), 1):
        в_части: Counter = Counter()
        for сделка in часть:
            if стоп:
                break
            if часы() > срок:
                стоп = f"срок {СРОК_МИН} мин вышел"
                break
            # попытка суток — до первого запроса по сделке: считается и неудачная. База её не
            # выдаёт (связь, замок) — стоп, как у отказа записать строку сбоя
            try:
                есть_попытка = база.взять(run_id, int(сделка["ID"]), ПРЕДЕЛ_СУТОК)
            except Exception as e:                               # noqa: BLE001 — класс, без текста
                сбои[f"попытка суток: {type(e).__name__}"] += 1
                стоп = "база"
                break
            if not есть_попытка:
                стоп = "предел суток исчерпан"
                break
            # пауза — от конца прошлого файла: долгая закачка паузу не съедает
            if последний is not None:
                спать(max(0.0, ПАУЗА_С - (часы() - последний)))
            # сбой одной сделки не роняет прогон и не держит очередь (дефект 3 ревизии): у неё
            # строка «сбой чтения» без чисел — попытка потрачена, сделка уходит в конец очереди
            try:
                r = прочитать_сделку(run_id, сделка, диск)
            except Exception as e:                               # noqa: BLE001 — класс, без текста
                сбои[f"чтение: {type(e).__name__}"] += 1
                r = строка(run_id, сделка, СБОЙ, None)
            последний = часы()
            try:
                база.записать(r)
            except Exception as e:                               # noqa: BLE001
                сбои[f"запись: {type(e).__name__}"] += 1
                r = строка(run_id, сделка, СБОЙ, r.get("file_key"))
                try:
                    база.записать(r)
                except Exception as e2:                          # noqa: BLE001
                    сбои[f"запись сбоя: {type(e2).__name__}"] += 1
                    стоп = "база"
                    break
            строки.append(r)
            статусы[r["status"]] += 1
            в_части[r["status"]] += 1
            if r["status"] not in (НЕ_РАСКРЫЛАСЬ, НЕ_ОБЩИЙ, НЕ_XLSX, НЕ_СКАЧАЛСЯ, et.НЕ_ОТКРЫЛСЯ):
                открыто += 1
                шаблон_другой += r["status"] == et.ШАБЛОН_ДРУГОЙ
            if открыто >= ГЕЙТ_ОТ and шаблон_другой / открыто > ГЕЙТ_ШАБЛОН_ДОЛЯ:
                стоп = "шаблон"
        if часть:
            печать(f"  часть {k}/{частей}: сделок {len(часть)} · прочитано {sum(в_части.values())} · "
                   f"сверено {в_части[et.СВЕРЕНО]}")
    печать("файлы: " + (" · ".join(f"{с} {статусы[с]}" for с in СТАТУСЫ if статусы[с]) or "ни одного"))
    где = Counter(r["why"] for r in строки if r["why"])
    if где:
        # адрес ячейки шаблона или имя проверки — по ним видно, что сменилось в шаблоне
        печать("где не сошлось: " + " · ".join(f"{к} {v}" for к, v in где.most_common()))
    for s in журнал_видов(строки):
        печать(s)
    for s in журнал_зонда(строки):
        печать(s)
    if строки:
        печать(журнал_путей(строки))
    печать("сверенные против «Суммы» сделки: " + (" · ".join(f"{к} {v}" for к, v in сверка_с_суммой(строки).items())
                                                 or "нет сверенных"))
    свер = [r for r in строки if r["status"] == et.СВЕРЕНО]
    if свер:
        печать(f"сверенные: переторжка {sum(1 for r in свер if r['rebid'])} из {len(свер)}")
    if диск is not None and getattr(диск, "коды", None):
        печать("коды ответов: " + " · ".join(f"{к} {v}" for к, v in sorted(диск.коды.items())))
    if сбои:
        печать(f"::warning::сбои чтения (у сделки строка «{СБОЙ}», без чисел): "
               + " · ".join(f"{к} {v}" for к, v in sorted(сбои.items())))
    if стоп == "база":
        # база не принимает и строку сбоя (или не выдаёт попытку): гейт не судит, в
        # econ_file_runs прогон не попадёт — его строки не во вкладке и не «прочитаны»
        печать("::error::база отказала (строка сбоя или попытка суток) — чтение остановлено; "
               "строки прогона во вкладку не пойдут, сделки перечитаются следующей ночью")
        return 1
    код = 0
    if стоп == "шаблон":
        печать(f"::error::«шаблон другой» у {_доля(шаблон_другой, открыто)} открытых файлов — шаблон сменился, "
               "чтение остановлено; разбор — scripts/econ_template.py (подписи строки 12)")
        код = 1
    elif стоп:
        печать(f"чтение остановлено: {стоп}")
    годно, журнал_гейта = гейт_сумм(строки)
    печать(журнал_гейта)
    if not годно:
        снято = база.пометить_прогон(run_id)
        печать(f"::error::{журнал_гейта} — прогон пометил свои строки ({снято}), во вкладку они не попадут")
        код = 1
    else:
        # решение гейта — в базе: только строки прошедших прогонов видит econ_file_latest
        строк, сверено = база.отметить_прогон(run_id)
        печать(f"гейт пройден — прогон записан в econ_file_runs (строк {строк}, сверено {сверено}): "
               "его строки пойдут во вкладку")
    итог = база.итог(run_id)
    печать(f"в базе по ключу прогона (без помеченных): строк {sum(итог.values())}, сверено {итог[et.СВЕРЕНО]}")
    return код


def _ключ_прогона() -> str:
    return (os.environ.get("RUN_ID") or "").strip() or "ef-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main(argv: list[str] | None = None, **kw) -> int:
    ap = argparse.ArgumentParser(description="Финальная экономика из файлов «Экономика проекта» (только агрегаты)")
    ap.add_argument("--mode", choices=("dry", "live"), default="dry")
    ap.add_argument("--limit", type=int, default=ПРЕДЕЛ_СУТОК)
    ap.add_argument("--parts", type=int, default=ЧАСТИ[0])
    a = ap.parse_args(argv)
    # предупреждения openpyxl печатают в stderr — публичный журнал — содержимое файла (число с
    # форматом даты, имя листа); econ_template открывает книги без них, здесь — вторая преграда
    warnings.filterwarnings("ignore", module="openpyxl")
    if not 1 <= a.limit <= ПРЕДЕЛ_СУТОК:
        print(f"::error::--limit вне 1…{ПРЕДЕЛ_СУТОК} (предел владельца — {ПРЕДЕЛ_СУТОК} файлов в сутки)")
        return 2
    if a.parts not in ЧАСТИ:
        print(f"::error::--parts — одно из {', '.join(map(str, ЧАСТИ))} (распоряжение 22.09.2026)")
        return 2
    if "портал" not in kw and not os.environ.get("BITRIX_WEBHOOK_URL", "").strip():
        print("::error::нет BITRIX_WEBHOOK_URL")
        return 2
    dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
    ключ = os.environ.get("YADISK_TOKEN", "").strip()
    if a.mode == "live":
        # всё, без чего запись не состоится, — до первого запроса к Диску
        if not ключ and "диск" not in kw:
            print("::error::режим live без YADISK_TOKEN — файлы не скачать")
            return 2
        if not dsn and "база" not in kw:
            print("::error::режим live без SUPABASE_DB_URL — предел суток не проверить, писать некуда")
            return 2
    база = kw.get("база")
    if база is None and dsn:
        база = База(dsn, только_чтение=a.mode == "dry")
        нет = база.нет_таблиц()
        if нет:
            print(f"::{'error' if a.mode == 'live' else 'warning'}::в базе нет {', '.join(нет)}: примените "
                  "library/supabase/econ_files_schema.sql (шаг «Схема в рабочей базе» прогона live или zip-db.yml)")
            база.закрыть()
            if a.mode == "live":
                return 2
            база = None
    портал = kw.get("портал")
    if портал is None:
        import config
        from bitrix_client import BitrixClient
        портал = BitrixClient(config.Settings.load().bitrix_webhook_url)
    try:
        return прогон(a.mode, a.limit, a.parts, портал=портал, run_id=kw.get("run_id") or _ключ_прогона(),
                      база=база, диск=kw.get("диск") or (Диск(ключ) if a.mode == "live" else None),
                      печать=kw.get("печать", print), **({"спать": kw["спать"]} if "спать" in kw else {}))
    finally:
        if база is not None and "база" not in kw:
            база.закрыть()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        # текст исключения в журнал не идёт: у ошибки базы в нём бывают значения строки,
        # у сетевой — адрес; класс и место — достаточно для разбора
        места = []
        tb = e.__traceback__
        while tb:
            места.append(f"{Path(tb.tb_frame.f_code.co_filename).name}:{tb.tb_lineno}")
            tb = tb.tb_next
        код = getattr(e, "pgcode", None)
        print(f"::error::прогон упал: {type(e).__name__}" + (f" (pgcode {код})" if код else "")
              + " · " + " → ".join(места[-4:]))
        raise SystemExit(1)
