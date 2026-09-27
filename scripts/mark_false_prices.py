#!/usr/bin/env python3
"""Пометка ложных строк цены в lib_prices: холостой замер, запись, откат.

ЗАЧЕМ. Недельный свод 21–27.09.2026 (прогон 36281598639): из 83 проверенных
строк цены 20 — не цены. «PAYMENT TERMS 20 × 1.0 USD», «Page 1 1 × 1.0»,
телефон, номер CIN, «Quotation Validity: Valid for 30 days», «Total Amount
(RMB): 143,880 × 1.0», «Items 1 & 2: Approx. 3–4 months», строки служебной
записки «Вывод: КП … дороже …» — почти все с низкой уверенностью, ценой 1,0 и
суммой, равной количеству. Такие строки дают карточке кода цену, которой нет,
и поставщику — предложение, которого он не делал.

ПРАВИЛО ОДНО НА ЗАПИСЬ И НА ПОМЕТКУ. Строку судит quotes.ложная_цена через
price_store.причина_отказа — ту же функцию, которой price_store.отобрать
снимает ложные строки при записи (разбор, переразбор, распознавание), когда
отбор при записи включён (FALSE_PRICE_RULE=1; по умолчанию выключен, пока этот
холостой замер не просмотрен — правило 3). Поля те же: колонки lib_prices. Причины — закрытый список quotes.ПРИЧИНЫ_ЛОЖНОЙ;
строку с признаком позиции (код, единица, стандарт, типоразмер, словарь) не
обвиняет ничто (правило 7 CLAUDE.md).

ПОМЕТКА, А НЕ УДАЛЕНИЕ (правило 5). Обвинённая строка ложится в таблицу-спутник
lib_price_junk с ключом прогона (правило 6); сама lib_prices не меняется.
Страницы читают вид lib_prices_live — он пометки и исключает. Откат — удаление
пометок прогона по run_id, секунды.

ПОРЯДОК (правило 3): сначала холостой замер, запись — только при пройденных
гейтах, иначе отменяется сама.

    SUPABASE_DB_URL=… python scripts/mark_false_prices.py                  # замер
    SUPABASE_DB_URL=… APPLY=1 python scripts/mark_false_prices.py          # запись
    SUPABASE_DB_URL=… REVERT=<run_id> python scripts/mark_false_prices.py  # откат

Входы: PARTS — частей обхода по диапазону id (по умолчанию 10, правило
дробления), WINDOW — строк за одно чтение. Пороги гейтов (не контракт —
контракт сами гейты): MAX_FEED_SHARE (0,30), MASS_SHARE (0,5), MASS_ROWS (5).

ГЕЙТЫ ЗАПИСИ
  1 · доля обвинённых в каждом потоке (feed) не выше MAX_FEED_SHARE;
  2 · в запись не идёт ни один файл с таблицей (lib_files.parse_path =
      «таблица»), обвинённый массово: не меньше MASS_ROWS строк и не меньше
      MASS_SHARE его строк цены — у таблицы с шапкой правило срабатывать почти
      не должно. MASS_HOLD (по умолчанию включён) такие файлы ОТКЛАДЫВАЕТ: их
      строки не помечаются, журнал раскладывает их по причинам без номеров, и
      запись идёт по остальным. MASS_HOLD=0 — прежнее поведение: гейт
      отменяет запись целиком. Замер 27.09.2026: 9 таких файлов на 64 915 строк;
  3 · структурный инвариант: у обвинённой строки нет единицы в своей колонке
      при количестве — строго 0 (единица — защитный признак);
  4 · сверка правила: причина канонического пути (price_store) совпадает с
      причиной, разложенной замером на «обвинение» и «защиту», — строго 0
      расхождений.
ПОСЛЕ ЗАПИСИ, в той же транзакции, — сверка Python против SQL: ни одна
обвинённая строка не видна в lib_prices_live, и ни одна необвинённая (и не
помеченная раньше) не пропала из него. Расхождение — откат транзакции.

ПРАВИЛО 0 — ЧИСЛОМ: сколько файлов теряют хоть одну строку цены и сколько —
ВСЕ, по потокам и по пути разбора файла. И защищённые: сколько строк правило
обвинило бы, не будь у них признака позиции, и каким признаком каждая спасена.

В журнал — только агрегаты и константы кода (правило 17): ни наименований, ни
кодов, ни номеров карточек и файлов. SAMPLE_TO=<файл> на своей машине пишет
номера (id) до 200 обвинённых строк — для просмотра глазами; в Actions запрещён.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from library import price_store, quotes  # noqa: E402

# Замер меряет само правило, а не переключатель записи (FALSE_PRICE_RULE).
ПРАВИЛО = quotes.ЛОЖНАЯ_ЦЕНА_ВЕРСИЯ
ПОТОКИ = (price_store.FEED, price_store.FEED_ПИСЬМА)
ПУТЬ_ТАБЛИЦА = "таблица"
НЕТ_ПУТИ = "(не указан)"

# statement_timeout — в строке подключения, а не через SET (правило 9).
OPTIONS = "-c statement_timeout=900000 -c idle_in_transaction_session_timeout=600000"


def _целое(имя: str, умолчание: int) -> int:
    try:
        return max(1, int(os.environ.get(имя, "") or умолчание))
    except ValueError:
        return умолчание


def _доля(имя: str, умолчание: float) -> float:
    try:
        return float(os.environ.get(имя, "") or умолчание)
    except ValueError:
        return умолчание


APPLY = os.environ.get("APPLY", "") not in ("", "0", "false")
REVERT = os.environ.get("REVERT", "").strip()
PARTS = _целое("PARTS", 10)
WINDOW = _целое("WINDOW", 20000)
MAX_FEED_SHARE = _доля("MAX_FEED_SHARE", 0.30)
MASS_SHARE = _доля("MASS_SHARE", 0.5)
MASS_ROWS = _целое("MASS_ROWS", 5)
#: Массово обвинённый файл с таблицей (гейт 2) не помечается, а откладывается
#: на разбор глазами: у таблицы с шапкой правило срабатывать почти не должно, и
#: сплошное обвинение говорит скорее о сдвиге колонок, чем о ложных строках.
#: Правило 7: ошибочная защита стоит полноты, ошибочное обвинение — спроса.
MASS_HOLD = os.environ.get("MASS_HOLD", "1").strip().lower() not in ("0", "false", "no")
SAMPLE_TO = os.environ.get("SAMPLE_TO", "").strip()

# Колонки строки цены, которые читает правило. Обязательные — без них судить
# нечего; остальные спрашиваются у базы и без колонки читаются как null
# (инструмент чтения не падает на миграции, которой ещё нет).
ОБЯЗАТЕЛЬНЫЕ = ("id", "feed", "item_name", "price")
НЕОБЯЗАТЕЛЬНЫЕ = {"source": "text", "source_url": "text", "part_number": "text",
                  "qty_unit": "text", "qty": "numeric", "total": "numeric",
                  "note": "text"}
КОЛОНКИ_ЧТЕНИЯ = ОБЯЗАТЕЛЬНЫЕ + tuple(НЕОБЯЗАТЕЛЬНЫЕ)

КОЛОНКИ_БАЗЫ = ("select table_name, column_name from information_schema.columns"
                " where table_name = any(%s) and table_schema = any(current_schemas(false))")
ГРАНИЦЫ = "select min(id), max(id), count(*) from lib_prices where feed = any(%s)"
ПУТИ_ФАЙЛОВ = ("select f.file_id, f.parse_path from lib_files f"
               " where f.file_id in (select distinct source_url from lib_prices"
               "                      where feed = any(%s) and source_url is not null)")
ПОМЕЧЕНЫ = ("select j.price_id from lib_price_junk j join lib_prices p on p.id = j.price_id"
            " where j.revoked_at is null and p.feed = any(%s)")
ЗАПИСАТЬ = ("insert into lib_price_junk (price_id, rule, run_id, reason)"
            " select v.price_id::bigint, v.rule, v.run_id, v.reason"
            "   from (values %s) as v(price_id, rule, run_id, reason)"
            "  where exists (select 1 from lib_prices p where p.id = v.price_id::bigint)"
            " on conflict (price_id) do nothing")
ОТКАТИТЬ = "delete from lib_price_junk where run_id = %s"
ЖУРНАЛ = ("insert into lib_mark_runs (run_id, rule, mode, params, rows_total, rows_marked,"
          " files_total, files_marked, note) values (%s, %s, 'разметка', %s::jsonb,"
          " %s, %s, %s, %s, %s)")
ЖУРНАЛ_КОНЕЦ = "update lib_mark_runs set finished_at = now(), rows_marked = %s where run_id = %s"
ЖУРНАЛ_ОТКАТ = ("update lib_mark_runs set reverted_at = now(), reverted_reason = %s"
                " where run_id = %s")
# Сверка Python против SQL — по номерам строк, а не по счётчикам: ночной разбор
# может в это время дописывать и снимать строки, и счётчики разошлись бы по
# причине, к правилу не относящейся.
ВИДНЫ_В_ЖИВЫХ = "select count(*) from lib_prices_live where id = any(%s)"
ПРОПАЛИ_ИЗ_ЖИВЫХ = ("select count(*) from lib_prices p where p.id = any(%s)"
                    " and not exists (select 1 from lib_prices_live l where l.id = p.id)")
ПОМЕЧЕНО_ПРОГОНОМ = "select count(*) from lib_price_junk where run_id = %s"
ПАЧКА = 10000


def num(v, w: int = 10) -> str:
    return f"{v:,}".replace(",", " ").rjust(w)


def pct(a, b) -> str:
    return f"{a / b * 100:.1f}%" if b else "—"


def блок(заголовок: str) -> None:
    print(f"\n=== {заголовок} ===", flush=True)


def путь_строки(note) -> str:
    """Путь разбора самой строки: текстовая тройка или колонки таблицы."""
    return "текст" if quotes.ОГОВОРКА_ТЕКСТА in str(note or "") else "таблица"


def судить(поля: dict) -> tuple[str | None, str | None, frozenset]:
    """(причина, причина без защиты, признаки позиции) одной строки цены.

    причина — канонический путь записи (price_store.причина_отказа); вторые две
    — раскладка замера: что обвинило бы строку и чем она защищена. Гейт 4
    сверяет, что раскладка сходится с каноническим ответом."""
    причина = price_store.причина_отказа(поля)
    кол, цена = поля.get("qty"), поля.get("price")
    сумма = quotes.сумма_строки(кол, цена, поля.get("total"), поля.get("note"))
    без_защиты = quotes.причина_без_защиты(поля.get("item_name"), кол, цена, сумма)
    признаки = frozenset()
    if без_защиты:
        признаки = frozenset(quotes.признаки_позиции(
            поля.get("item_name"), поля.get("part_number"), поля.get("qty_unit"), кол,
            (quotes._число(кол), quotes._число(цена), сумма)))
    return причина, без_защиты, признаки


def прочтение_текста(поля: dict) -> str:
    """Что дало бы нынешнее правило текстовой строке: для замера перестановки."""
    # Отбор — явно, мимо переключателя записи (FALSE_PRICE_RULE): замер меряет
    # само правило, а не то, включено ли оно при записи.
    р = quotes.цена_из_текста(str(поля.get("item_name") or ""), отбор=True)
    if р is None:
        return "не взяло бы"
    кол, цена = quotes._число(поля.get("qty")), quotes._число(поля.get("price"))
    if кол is not None and цена is not None and abs(р["qty"] - кол) < 0.005 \
            and abs(р["price"] - цена) < 0.00005:
        return "то же прочтение"
    if quotes.ОГОВОРКА_ПЕРЕСТАВЛЕНО in (р.get("note") or ""):
        return "переставило бы цену и количество"
    return "прочтение иное"


@dataclass
class Файл:
    поток: str
    путь: str
    строк: int = 0
    обвинено: int = 0
    защищено: int = 0
    причины: Counter = field(default_factory=Counter)   # причина → обвинено строк


@dataclass
class Замер:
    """Счётчики одного прохода. Только числа и константы кода."""
    пути_файлов: dict = field(default_factory=dict)       # file_id → parse_path
    помечены_раньше: set = field(default_factory=set)     # id с действующей пометкой
    строк: Counter = field(default_factory=Counter)        # поток → строк
    обвинено: Counter = field(default_factory=Counter)     # поток → обвинено
    по_причинам: Counter = field(default_factory=Counter)
    причина_путь: Counter = field(default_factory=Counter)  # (причина, путь строки)
    защищено: Counter = field(default_factory=Counter)     # причина → спасено
    признаки: Counter = field(default_factory=Counter)     # признак → спасённых
    вырождена_цена: Counter = field(default_factory=Counter)  # «ровно 1» / «меньше 1»
    перечитано: Counter = field(default_factory=Counter)
    перечитано_обвинённых: Counter = field(default_factory=Counter)
    файлы: dict = field(default_factory=dict)
    с_единицей: int = 0                  # гейт 3
    расхождений: int = 0                 # гейт 4
    обвинённые: list = field(default_factory=list)       # (id, причина) — к записи
    необвинённые: list = field(default_factory=list)     # id — к сверке после записи
    уже: int = 0
    помечены_не_обвинены: int = 0      # пометка действует, а правило строку не обвиняет
    файл_обвинённой: dict = field(default_factory=dict)  # id → file_id (отложенные файлы)

    def учесть(self, поля: dict) -> None:
        поток = поля.get("feed") or "(нет)"
        файл_id = поля.get("source_url") or ""
        путь_файла = self.пути_файлов.get(файл_id) or НЕТ_ПУТИ
        ф = self.файлы.get(файл_id)
        if ф is None:
            ф = self.файлы[файл_id] = Файл(поток, путь_файла)
        ф.строк += 1
        self.строк[поток] += 1
        причина, без_защиты, признаки = судить(поля)
        if (причина or None) != (без_защиты if not признаки else None):
            self.расхождений += 1
        путь = путь_строки(поля.get("note"))
        if путь == "текст":
            п = прочтение_текста(поля)
            self.перечитано[п] += 1
            if причина:
                self.перечитано_обвинённых[п] += 1
        if без_защиты and признаки:
            ф.защищено += 1
            self.защищено[без_защиты] += 1
            for пр in признаки:
                self.признаки[пр] += 1
        if not причина:
            if поля["id"] in self.помечены_раньше:
                self.помечены_не_обвинены += 1
            else:
                self.необвинённые.append(поля["id"])
            return
        ф.обвинено += 1
        ф.причины[причина] += 1
        self.файл_обвинённой[поля["id"]] = файл_id
        self.обвинено[поток] += 1
        self.по_причинам[причина] += 1
        self.причина_путь[(причина, путь)] += 1
        if причина == quotes.ПРИЧИНА_ВЫРОЖДЕНА:
            цена = quotes._число(поля.get("price")) or 0.0
            self.вырождена_цена["ровно 1" if abs(цена - 1) < 0.005 else "меньше 1"] += 1
        if str(поля.get("qty_unit") or "").strip() and поля.get("qty") is not None:
            self.с_единицей += 1
        if поля["id"] in self.помечены_раньше:
            self.уже += 1
        self.обвинённые.append((поля["id"], причина))

    # ── гейты ────────────────────────────────────────────────────────────────
    def массовые_файлы(self, доля: float = None, строк: int = None) -> list:
        """file_id файлов с таблицей, обвинённых массово (гейт 2)."""
        доля = MASS_SHARE if доля is None else доля
        строк = MASS_ROWS if строк is None else строк
        return [к for к, ф in self.файлы.items()
                if ф.путь == ПУТЬ_ТАБЛИЦА and ф.обвинено >= строк
                and ф.обвинено >= доля * ф.строк]

    def массовые(self, доля: float = None, строк: int = None) -> int:
        """Сколько файлов с таблицей обвинено массово (гейт 2, сетка порогов)."""
        return len(self.массовые_файлы(доля, строк))

    def отложено(self) -> set:
        """Файлы, чьи обвинённые строки не помечаются (MASS_HOLD)."""
        return set(self.массовые_файлы()) if MASS_HOLD else set()

    def к_записи(self) -> list:
        """(id, причина) обвинённых строк, которые пойдут в пометки: без
        отложенных файлов. Один список на запись и на сверку после неё."""
        отл = self.отложено()
        if not отл:
            return list(self.обвинённые)
        return [(i, п) for i, п in self.обвинённые if self.файл_обвинённой.get(i) not in отл]

    def гейты(self) -> list[tuple[str, bool, str]]:
        out = []
        for поток in sorted(self.строк):
            д = self.обвинено[поток] / self.строк[поток] if self.строк[поток] else 0.0
            out.append((f"1 · доля обвинённых, поток «{поток}»", д <= MAX_FEED_SHARE,
                        f"{д * 100:.2f}% (порог {MAX_FEED_SHARE * 100:.0f}%)"))
        м = len(set(self.массовые_файлы()) - self.отложено())
        out.append(("2 · массово обвинённых таблиц к записи", м == 0,
                    f"{м} (порог: от {MASS_ROWS} строк и от {MASS_SHARE * 100:.0f}% файла;"
                    f" отложено {len(self.отложено())})"))
        out.append(("3 · обвинено при единице в колонке", self.с_единицей == 0,
                    f"{self.с_единицей} (строго 0)"))
        out.append(("4 · расхождений правила записи и замера", self.расхождений == 0,
                    f"{self.расхождений} (строго 0)"))
        return out

    # ── журнал ───────────────────────────────────────────────────────────────
    def печать(self) -> bool:
        блок("объём")
        print(f"  {'поток':28}{'строк':>10}{'файлов':>10}{'обвинено':>10}{'доля':>9}")
        по_файлам = Counter(ф.поток for ф in self.файлы.values())
        for поток in sorted(self.строк):
            print(f"  {поток:28}{num(self.строк[поток])}{num(по_файлам[поток])}"
                  f"{num(self.обвинено[поток])}{pct(self.обвинено[поток], self.строк[поток]):>9}")

        блок("обвинено по причинам (закрытый список quotes.ПРИЧИНЫ_ЛОЖНОЙ)")
        print(f"  {'причина':28}{'всего':>10}{'текст':>10}{'таблица':>10}")
        for причина in quotes.ПРИЧИНЫ_ЛОЖНОЙ:
            print(f"  {причина:28}{num(self.по_причинам[причина])}"
                  f"{num(self.причина_путь[(причина, 'текст')])}"
                  f"{num(self.причина_путь[(причина, 'таблица')])}")
        if self.вырождена_цена:
            print("  вырожденная тройка по цене: " + " · ".join(
                f"{к} {n}" for к, n in sorted(self.вырождена_цена.items())))
        print(f"  уже помечено раньше (пометка действует): {self.уже}")
        print(f"  помечено раньше, но правило больше не обвиняет: {self.помечены_не_обвинены}"
              " (снимаются откатом своего прогона)")
        к_записи = self.к_записи()
        print(f"  к записи новых пометок: {sum(1 for i, _п in к_записи if i not in self.помечены_раньше)}"
              f" · отложено вместе с массовыми файлами: {len(self.обвинённые) - len(к_записи)}")

        блок("защищено — обвинило бы, не будь признака позиции (правило 7)")
        print(f"  строк спасено защитой: {sum(self.защищено.values())}")
        for причина in quotes.ПРИЧИНЫ_ЛОЖНОЙ:
            if self.защищено[причина]:
                print(f"    от «{причина}»{num(self.защищено[причина], 36 - len(причина))}")
        print("  каким признаком (строка бывает защищена несколькими):")
        for пр, n in sorted(self.признаки.items(), key=lambda x: (-x[1], x[0])):
            print(f"    {пр:26}{num(n)}")

        блок("правило 0 — у скольких файлов стало хуже")
        строк_путь = Counter()
        хуже = Counter()
        всё = Counter()
        for ф in self.файлы.values():
            к = (ф.поток, ф.путь)
            строк_путь[к] += 1
            if ф.обвинено:
                хуже[к] += 1
            if ф.обвинено and ф.обвинено == ф.строк:
                всё[к] += 1
        print(f"  {'поток · путь файла':44}{'файлов':>9}{'теряют строки':>15}{'теряют ВСЕ':>12}")
        for к in sorted(строк_путь):
            print(f"  {к[0] + ' · ' + к[1]:44}{num(строк_путь[к], 9)}{num(хуже[к], 15)}"
                  f"{num(всё[к], 12)}")
        print(f"  итого: теряют строки {sum(хуже.values())} файлов,"
              f" теряют все цены {sum(всё.values())}")
        # Распределение доли обвинённых по файлам с таблицей: на нём видно, где
        # сидит порог гейта 2, и что двигать, если он сработал.
        корзины = Counter()
        for ф in self.файлы.values():
            if ф.путь != ПУТЬ_ТАБЛИЦА or not ф.обвинено:
                continue
            д = ф.обвинено / ф.строк
            корзины["100%" if д >= 1 else "50–99%" if д >= 0.5 else
                    "25–49%" if д >= 0.25 else "до 25%"] += 1
        print("  файлы с таблицей по доле обвинённых строк: " + (" · ".join(
            f"{к} {корзины[к]}" for к in ("до 25%", "25–49%", "50–99%", "100%")
            if корзины[к]) or "ни одного"))

        блок("сетка гейта 2 — файлов с таблицей, обвинённых массово")
        print(f"  {'от строк':>9}{'доля 25%':>10}{'доля 50%':>10}{'доля 75%':>10}{'доля 100%':>11}")
        for строк in (1, 3, 5, 10, 20):
            print(f"  {строк:>9}" + "".join(
                f"{self.массовые(д, строк):>{10 if д < 1 else 11}}" for д in (0.25, 0.5, 0.75, 1.0)))

        массовые = self.массовые_файлы()
        блок(f"массово обвинённые файлы с таблицей: {len(массовые)} — "
             + ("ОТЛОЖЕНЫ, пометка не пишется (MASS_HOLD)" if MASS_HOLD else "идут в запись"))
        print("  (номер — порядковый в этом журнале, не номер файла; причины — закрытый список)")
        for n, к in enumerate(sorted(массовые, key=lambda к: (-self.файлы[к].обвинено,
                                                              self.файлы[к].поток)), 1):
            ф = self.файлы[к]
            причины = ", ".join(f"{п} {c}" for п, c in ф.причины.most_common())
            print(f"  {n:>3}. {ф.поток} · строк {ф.строк} · обвинено {ф.обвинено}"
                  f" · защищено {ф.защищено} · {причины}")

        # Чем именно обвинены файлы, теряющие ВСЕ цены: по главной причине файла.
        главные = Counter()
        for ф in self.файлы.values():
            if ф.обвинено and ф.обвинено == ф.строк:
                главные[(ф.поток, ф.путь, ф.причины.most_common(1)[0][0])] += 1
        блок("файлы, теряющие ВСЕ цены, — по главной причине файла")
        for (поток, путь, причина), n in sorted(главные.items(), key=lambda x: (-x[1], x[0])):
            print(f"  {поток + ' · ' + путь + ' · ' + причина:64}{num(n, 6)}")

        блок("текстовые строки, перечитанные нынешним правилом")
        print("  (перестановка цены и количества — quotes.прочтение_тройки; записанное")
        print("   исправит переразбор файла, пометка его не трогает)")
        for к in ("то же прочтение", "переставило бы цену и количество", "прочтение иное",
                  "не взяло бы"):
            print(f"    {к:36}{num(self.перечитано[к])}   из них обвинено"
                  f"{num(self.перечитано_обвинённых[к], 8)}")

        блок("СВОДКА ГЕЙТОВ")
        все = True
        for имя, ок, значение in self.гейты():
            все = все and ок
            print(f"  {'ПРОЙДЕН   ' if ок else 'НЕ ПРОЙДЕН'}  {имя:48}{значение}")
        return все


# ─────────────────────────────────────────────────────────────────────────────
# База

def колонки_базы(cur) -> dict[str, set[str]]:
    cur.execute(КОЛОНКИ_БАЗЫ, (["lib_prices", "lib_price_junk", "lib_files",
                               "lib_mark_runs"],))
    out: dict[str, set[str]] = defaultdict(set)
    for таблица, колонка in cur.fetchall():
        out[таблица].add(колонка)
    return out


def запрос_чтения(есть: set[str]) -> str:
    нет = [к for к in ОБЯЗАТЕЛЬНЫЕ if к not in есть]
    if нет:
        raise SystemExit(f"в lib_prices нет колонок {', '.join(нет)} — судить нечего")
    поля = list(ОБЯЗАТЕЛЬНЫЕ) + [к if к in есть else f"null::{тип} as {к}"
                                 for к, тип in НЕОБЯЗАТЕЛЬНЫЕ.items()]
    return (f"select {', '.join(поля)} from lib_prices"
            " where feed = any(%s) and id >= %s and id < %s and id > %s"
            " order by id limit %s")


def части(первый: int, последний: int, n: int) -> list[tuple[int, int]]:
    """Диапазоны id [от, до) — n частей, покрывающих [первый, последний]."""
    if первый is None or последний is None:
        return []
    шаг = max(1, -(-(последний - первый + 1) // n))
    return [(от, min(от + шаг, последний + 1))
            for от in range(первый, последний + 1, шаг)]


def прочитать(cur, замер: Замер, есть: dict[str, set[str]]) -> None:
    cur.execute(ГРАНИЦЫ, (list(ПОТОКИ),))
    первый, последний, всего = cur.fetchone()
    print(f"строк цены в потоках {', '.join(ПОТОКИ)}: {num(всего or 0, 0)}"
          f" · частей обхода: {PARTS}", flush=True)
    if "parse_path" in есть.get("lib_files", ()):
        cur.execute(ПУТИ_ФАЙЛОВ, (list(ПОТОКИ),))
        замер.пути_файлов = {f: (p or НЕТ_ПУТИ) for f, p in cur.fetchall()}
    else:
        print("  в lib_files нет колонки parse_path — путь файла не известен, гейт 2 пуст")
    if есть.get("lib_price_junk"):
        cur.execute(ПОМЕЧЕНЫ, (list(ПОТОКИ),))
        замер.помечены_раньше = {r[0] for r in cur.fetchall()}
    sql = запрос_чтения(есть["lib_prices"])
    имена = list(ОБЯЗАТЕЛЬНЫЕ) + list(НЕОБЯЗАТЕЛЬНЫЕ)
    for k, (от, до) in enumerate(части(первый, последний, PARTS), 1):
        последний_id, прочитано = от - 1, 0
        while True:
            cur.execute(sql, (list(ПОТОКИ), от, до, последний_id, WINDOW))
            пачка = cur.fetchall()
            if not пачка:
                break
            for r in пачка:
                поля = dict(zip(имена, r))
                замер.учесть(поля)
                последний_id = поля["id"]
            прочитано += len(пачка)
        print(f"  часть {k} из {PARTS}: строк {num(прочитано, 0)}", flush=True)


def _пачки(ids: list) -> list[list]:
    return [ids[i:i + ПАЧКА] for i in range(0, len(ids), ПАЧКА)]


def сверить(cur, замер: Замер, run_id: str, записано: int) -> list[str]:
    """Python против SQL после записи, в той же транзакции. Пусто — сошлось."""
    ошибки = []
    cur.execute(ПОМЕЧЕНО_ПРОГОНОМ, (run_id,))
    у_прогона = cur.fetchone()[0]
    if у_прогона != записано:
        ошибки.append(f"пометок прогона в базе {у_прогона}, вставлено {записано}")
    видны = 0
    for пачка in _пачки([i for i, _ in замер.к_записи()]):
        cur.execute(ВИДНЫ_В_ЖИВЫХ, (пачка,))
        видны += cur.fetchone()[0]
    if видны:
        ошибки.append(f"обвинённых строк видно в lib_prices_live: {видны}")
    пропали = 0
    for пачка in _пачки(замер.необвинённые):
        cur.execute(ПРОПАЛИ_ИЗ_ЖИВЫХ, (пачка,))
        пропали += cur.fetchone()[0]
    if пропали:
        ошибки.append(f"необвинённых строк пропало из lib_prices_live: {пропали}")
    return ошибки


class СверкаНеСошлась(Exception):
    """Сверка Python ↔ SQL после записи разошлась; транзакция уже откатана."""


def записать(conn, замер: Замер, run_id: str, execute_values) -> int:
    """Пометки и журнал одной транзакцией со сверкой; расхождение — откат."""
    новые = [(i, ПРАВИЛО, run_id, п) for i, п in замер.к_записи()
             if i not in замер.помечены_раньше]
    параметры = json.dumps({"parts": PARTS, "max_feed_share": MAX_FEED_SHARE,
                            "mass_share": MASS_SHARE, "mass_rows": MASS_ROWS,
                            "mass_hold": MASS_HOLD, "held_files": len(замер.отложено()),
                            "feeds": list(ПОТОКИ), "threshold": quotes.ПОРОГ_ВЫРОЖДЕНИЯ,
                            "reasons": dict(замер.по_причинам)}, ensure_ascii=False)
    записано = 0
    with conn.cursor() as cur:
        cur.execute(ЖУРНАЛ, (run_id, ПРАВИЛО, параметры, sum(замер.строк.values()),
                             len(новые), len(замер.файлы),
                             sum(1 for ф in замер.файлы.values() if ф.обвинено),
                             "lib_price_junk: ложные строки цены"))
        for пачка in _пачки(новые):
            # Одна пачка — один оператор: у execute_values с несколькими
            # страницами rowcount говорит только о последней, и счёт записанного
            # разошёлся бы со сверкой на ровном месте.
            execute_values(cur, ЗАПИСАТЬ, пачка, page_size=len(пачка))
            записано += cur.rowcount
        ошибки = сверить(cur, замер, run_id, записано)
        if ошибки:
            conn.rollback()
            raise СверкаНеСошлась(ошибки)
        cur.execute(ЖУРНАЛ_КОНЕЦ, (записано, run_id))
    conn.commit()
    return записано


def откатить(conn, run_id: str) -> int:
    with conn.cursor() as cur:
        cur.execute(ОТКАТИТЬ, (run_id,))
        снято = cur.rowcount
        cur.execute(ЖУРНАЛ_ОТКАТ, ("откат пометок ложных строк цены", run_id))
        cur.execute(ПОМЕЧЕНО_ПРОГОНОМ, (run_id,))
        осталось = cur.fetchone()[0]
        if осталось:
            conn.rollback()
            raise SystemExit(f"откат не сошёлся: у прогона осталось пометок {осталось}")
    conn.commit()
    return снято


def выборка(замер: Замер, путь: str) -> None:
    """Номера до 200 обвинённых строк — только на своей машине (правило 17)."""
    if os.environ.get("GITHUB_ACTIONS"):
        print("SAMPLE_TO в Actions запрещён: журнал публичный", file=sys.stderr)
        return
    with open(путь, "w", encoding="utf-8") as f:
        f.write("\n".join(f"{i}\t{п}" for i, п in замер.обвинённые[:200]))
    print(f"  номера {min(200, len(замер.обвинённые))} обвинённых строк записаны в {путь}")


def выполнить(conn, apply: bool = False, revert: str = "", run_id: str | None = None) -> int:
    """Замер, запись или откат на готовом соединении. Код возврата: 0 — сделано,
    1 — запись отменена (гейты или сверка), 2 — нет схемы.

    Отдельно от main, чтобы проверка на настоящем PostgreSQL шла той же дорогой,
    что прогон, — в своей схеме базы (tests/test_false_prices_sql.py)."""
    import psycopg2.extras

    with conn.cursor() as cur:
        есть = колонки_базы(cur)
    conn.rollback()
    if revert:
        if not есть.get("lib_price_junk"):
            print("таблицы lib_price_junk нет — снимать нечего", file=sys.stderr)
            return 2
        снято = откатить(conn, revert)
        print(f"снято пометок ложных строк цены: {снято} (прогон {revert})")
        return 0

    run_id = run_id or ПРАВИЛО + "-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    print(f"правило: {ПРАВИЛО} · прогон: {run_id}")
    print(f"режим: {'ЗАПИСЬ ПОМЕТОК' if apply else 'холостой, без записи'}")
    print(f"пороги гейтов: доля потока ≤ {MAX_FEED_SHARE} · массово — от {MASS_ROWS}"
          f" строк и от {MASS_SHARE} файла · вырожденная цена ≤ {quotes.ПОРОГ_ВЫРОЖДЕНИЯ}",
          flush=True)
    if not есть.get("lib_price_junk"):
        print("::warning::таблицы lib_price_junk нет — пометок раньше не было, записать"
              " нельзя; примените library/supabase/schema_junk.sql")
    замер = Замер()
    with conn.cursor() as cur:
        прочитать(cur, замер, есть)
    conn.rollback()
    гейты = замер.печать()
    if SAMPLE_TO:
        выборка(замер, SAMPLE_TO)

    if not apply:
        print("\nхолостой прогон — в базе ничего не изменилось. Для записи: APPLY=1")
        return 0
    if not гейты:
        print("\nЗАПИСЬ ОТМЕНЕНА: не пройдены гейты. Сначала разбор причин, потом запись.",
              file=sys.stderr)
        return 1
    if not есть.get("lib_price_junk") or not есть.get("lib_mark_runs"):
        print("ЗАПИСЬ НЕВОЗМОЖНА: нет lib_price_junk или lib_mark_runs — примените"
              " library/supabase/schema_junk.sql", file=sys.stderr)
        return 2
    try:
        записано = записать(conn, замер, run_id, psycopg2.extras.execute_values)
    except СверкаНеСошлась as e:
        for о in e.args[0]:
            print(f"::error::сверка Python ↔ SQL: {о}", file=sys.stderr)
        print("ЗАПИСЬ ОТМЕНЕНА: сверка после записи не сошлась, транзакция откатана —"
              " в базе ничего не изменилось", file=sys.stderr)
        return 1
    print(f"\n✓ помечено {записано} строк цены, прогон {run_id}; сверка Python ↔ SQL сошлась")
    print(f"  откат: REVERT={run_id} python scripts/mark_false_prices.py")
    return 0


def main() -> int:
    url = os.environ.get("SUPABASE_DB_URL", "")
    if not url:
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2

    conn = psycopg2.connect(url, connect_timeout=20, options=OPTIONS)
    # Замер — только чтение: сессия это гарантирует, а не обещает.
    conn.set_session(readonly=not (APPLY or REVERT))
    try:
        return выполнить(conn, APPLY, REVERT)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
