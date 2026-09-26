#!/usr/bin/env python3
"""Дилеры разведки брендов, которых нет в реестре компаний, → реестр. Вхолостую по умолчанию.

ЗАЧЕМ. Разрешение владельца 26.09.2026: завести в реестр поставщиков дилеров из
разведки брендов, которых там нет. Замер library/dealer_link.py (прогон
36274214934): из 371 записи dealers с сильным ключом 186, сведено 63, «с ключом,
но в реестре нет» — 124. Пока такой дилер вне реестра, роль предложения
узнаёт его только по имени, а сорсер не видит его среди компаний.

КОГО ЗАВОДИТ — правило то же, что у замера (dealer_link, сопоставитель
supplier_link): запись с сильным ключом — домен сайта с доказательством
(domain_source) или ИНН с верной контрольной суммой, — которую замер НЕ свёл:
ни одна сущность реестра не носит этот домен или ИНН. Не заводятся:
  · сведённые (компания уже есть);
  · спорные — ключи ведут в две сущности, домен против налогового номера,
    домен придержан как площадка;
  · записи, делящие ключ со сведённой или спорной записью: сведённая доменом
    запись может нести ИНН, которого в реестре нет, и новая сущность с ним
    сделала бы её спорной;
  · группы, в которых ключ носят РАЗНЫЕ имена (один сайт у двух юрлиц,
    например у дочерних обществ бренда в разных странах) или разные ИНН, или
    разные правовые формы: юрлица не склеиваются по предположению;
  · домен самого бренда (метка до зоны — ключ бренда): он опознал бы бренд, а
    не дилера;
  · домен почтового хостинга или площадки (свой закрытый список ниже — вторая
    проверка поверх общий_домен сопоставителя, эталон не из того же правила).
Записи одного ключа (один дилер у двух брендов) — одна сущность.

ЧТО ПИШЕТ — тем же путём, что сведение реестров (load_supplier_master.записать):
  sup_entity         — новая сущность, resolution = candidate (человек не
                       подтверждал), note «разведка брендов: <вид> (<бренды>)»;
                       страница /suppliers показывает note графой «Чем слито»;
  sup_number_registry — вечный номер KV-S-NNNNNN-C, следующий после наибольшего;
  sup_identifier     — domain / inn (source «разведка брендов», evidence —
                       ссылка-доказательство, status stated), alias и legal
                       (как у сведения: следующий прогон сведения опознает
                       компанию, а не выдаст ей второй номер);
  sup_research_dealer — происхождение: запись разведки, бренд, вид, страна,
                       ссылка, ключ прогона (library/supabase/research_dealers_schema.sql).
Каждая строка несёт run_id. Одна транзакция: гейты не сошлись до записи или
после неё — откат своей транзакции, в базе ничего не остаётся.

ПОСЛЕ ЗАПИСИ — ПРОВЕРКА ТЕМ ЖЕ ЗАМЕРОМ, внутри той же транзакции: реестр
читается заново из базы (supplier_link.читать_реестр) и сводится с дилерами
(dealer_link.сопоставить). Каждая заведённая запись обязана выйти «сведено» в
свою сущность, споров не прибавиться. Холостой прогон считает то же в памяти
(добавить_в_реестр) — два способа, Python против SQL.

ОТКАТ — пометкой (правило 5), одной командой по ключу прогона: строки
sup_research_dealer получают rolled_back_at, признаки прогона — status
rejected (их не читает ни сопоставитель, ни страница), сущность, у которой
других действующих признаков нет, — status inactive. Номер остаётся выданным
навсегда (регламент реестра): повторная запись возвращает той же компании тот
же номер, а не новый.

В ЖУРНАЛ — ТОЛЬКО АГРЕГАТЫ (правило 17): числа, виды, ключи брендов (константы
справочника разведки). Ни имени дилера, ни домена, ни ИНН, ни номера KV.

ОДНОЙ ЧАСТЬЮ, И ЭТО РЕШЕНИЕ: сотни записей из файлов и три выборки реестра,
секунды; спор «ключ ведёт в две сущности» считается только по реестру целиком.
Битрикс не читается.

    SUPABASE_DB_URL=… python library/load_research_dealers.py                    # вхолостую
    SUPABASE_DB_URL=… APPLY=1 RUN_ID=dealers-1 python library/load_research_dealers.py
    SUPABASE_DB_URL=… ROLLBACK_RUN_ID=dealers-1 python library/load_research_dealers.py
"""
from __future__ import annotations

import collections
import os
import re
import sys
import time
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

from supplier_registry_overlap import общий_домен  # noqa: E402

from library import dealer_link as dl  # noqa: E402
from library import supplier_link as sl  # noqa: E402
from library.load_supplier_master import ЕДИНОЛИЧНЫЕ, номер, норма  # noqa: E402

ИСТОЧНИК = "разведка брендов"
ВИДЫ = dl.ВИДЫ

ГЕЙТЫ = {
    # Разрешение владельца — на дилеров разведки, которых нет в реестре: замер
    # 26.09.2026 дал 124 записи с ключом вне реестра. Больше двухсот — значит,
    # реестр прочитался не весь (или не та база), и заводить поверх нельзя.
    "макс_к_заведению": 200,
    # Среди заводимых — ни одного спора: ни после записи (замер из базы), ни в
    # памяти. Любой спор значит, что правило отбора разошлось с сопоставителем.
    "макс_доля_споров": 0.0,
}

# Почтовые хостинги и площадки — закрытый список, НЕЗАВИСИМЫЙ от общий_домен
# сопоставителя (правило 1: эталон не может быть производным от правила).
# Узел совпал или оканчивается на «.<узел>» — провал гейта.
ПЛОЩАДКИ = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com",
    "icloud.com", "aol.com", "proton.me", "protonmail.com", "gmx.de", "gmx.net", "web.de",
    "mail.ru", "bk.ru", "inbox.ru", "list.ru", "yandex.ru", "yandex.com", "ya.ru",
    "rambler.ru", "qq.com", "163.com", "126.com", "sina.com", "foxmail.com",
    "alibaba.com", "1688.com", "aliexpress.com", "aliexpress.ru", "made-in-china.com",
    "globalsources.com", "ec21.com", "tradekey.com", "tradeatlas.com", "indiamart.com",
    "tradeindia.com", "ebay.com", "ebay.de", "amazon.com", "amazon.de", "avito.ru",
    "ozon.ru", "wildberries.ru", "pulscen.ru", "tiu.ru", "satu.kz", "prom.ua",
    "blizko.ru", "flagma.ru", "all.biz", "kompass.com", "europages.com",
    "linkedin.com", "facebook.com", "vk.com", "instagram.com", "t.me", "youtube.com",
    "wordpress.com", "wixsite.com", "tilda.ws", "sites.google.com", "mtrehber.com",
})

# Разбор страны: «Россия (по ценам в рублях)», «Турция, склад в Стамбуле» →
# «Россия», «Турция». Для sup_entity.country — первая часть без пояснений.
_ЧАСТЬ_СТРАНЫ = re.compile(r"[,;/]")


def площадка(h: str) -> bool:
    h = (h or "").lower().strip(".")
    return bool(h) and (общий_домен(h) or any(h == x or h.endswith("." + x) for x in ПЛОЩАДКИ))


def страна_записи(v) -> str:
    return _ЧАСТЬ_СТРАНЫ.split(dl.без_пояснений(v))[0].strip()


def домен_бренда(h: str, бренд: str) -> bool:
    """Метка домена до зоны — ключ бренда: «epiroc.com» у бренда epiroc."""
    метки = (h or "").split(".")
    return len(метки) >= 2 and норма(метки[-2]) == норма(бренд)


class Группа(NamedTuple):
    """Записи разведки одного ключа — одна будущая сущность реестра."""
    дилеры: tuple            # dl.Дилер
    домены: frozenset
    инн: frozenset
    имя: str                 # для показа: без пояснений в скобках
    страна: str
    формы: frozenset

    @property
    def бренды(self) -> list[str]:
        return sorted({x.oem_key for x in self.дилеры})

    @property
    def виды(self) -> list[str]:
        есть = {x.вид for x in self.дилеры}
        return [в for в in ВИДЫ if в in есть]

    def note(self) -> str:
        return f"{ИСТОЧНИК}: {', '.join(self.виды)} ({', '.join(self.бренды)})"


class Отбор(NamedTuple):
    группы: list
    отсеяно: collections.Counter    # причина → записей


def _корень(родитель: dict, i: int) -> int:
    while родитель[i] != i:
        родитель[i] = родитель[родитель[i]]
        i = родитель[i]
    return i


def отобрать(список: list, итог: sl.Итог, сырые: dict) -> Отбор:
    """Кого заводить. сырые — {id записи: словарь dealers разведки} (имя, страна).

    Причины отсева считаются по записям, каждая запись — одной причиной
    (первой сработавшей, от сильной к слабой)."""
    отсеяно: collections.Counter = collections.Counter()
    с_ключом, годные = [], set()
    for x in список:
        р = x.разведка
        if not (р.сайты or р.налоги):
            отсеяно["без сильного ключа"] += 1
            continue
        с_ключом.append(x)
        if р.id in итог.связь:
            отсеяно["уже в реестре (сведено)"] += 1
        elif р.id in итог.спор_две:
            отсеяно["спор: ключи ведут в разные сущности"] += 1
        elif р.id in итог.спор_налог:
            отсеяно["спор: домен против налогового номера"] += 1
        elif р.id in итог.придержано:
            отсеяно["придержано: домен носят много имён"] += 1
        else:
            годные.add(р.id)

    # Записи одного ключа — одна компания: объединение по домену и ИНН. По ВСЕМ
    # записям с ключом, а не только годным: запись, уже сведённая доменом, может
    # нести ИНН, которого в реестре нет. Заведи его новой сущности — и она
    # станет спорной (ключи в две сущности). Поймано проверкой «после» на
    # выдуманном корпусе.
    родитель = {x.разведка.id: x.разведка.id for x in с_ключом}
    по_ключу: dict = {}
    for x in с_ключом:
        for к in [("d", d) for d in x.разведка.сайты] + sorted(x.разведка.налоги):
            if к in по_ключу:
                a, b = _корень(родитель, по_ключу[к]), _корень(родитель, x.разведка.id)
                if a != b:
                    родитель[b] = a
            else:
                по_ключу[к] = x.разведка.id
    состав = collections.defaultdict(list)
    for x in с_ключом:
        состав[_корень(родитель, x.разведка.id)].append(x)
    for к in list(состав):
        члены = состав[к]
        свои = [x for x in члены if x.разведка.id in годные]
        if свои and len(свои) < len(члены):
            отсеяно["ключ общий со сведённой или спорной записью"] += len(свои)
        if len(свои) < len(члены):
            del состав[к]

    группы = []
    for _, члены in sorted(состав.items()):
        домены = frozenset().union(*(x.разведка.сайты for x in члены))
        инн = frozenset(v for x in члены for _, v in x.разведка.налоги)
        имена = {x.разведка.имя for x in члены}
        формы_ = [x.разведка.формы for x in члены if x.разведка.формы]
        причина = ""
        if any(площадка(d) for d in домены):
            причина = "домен почты или площадки"
        elif any(домен_бренда(d, x.oem_key) for d in домены for x in члены):
            причина = "домен самого бренда"
        elif len(имена) > 1 or "" in имена:
            причина = "один ключ — разные имена"
        elif len(инн) > 1:
            причина = "один ключ — разные ИНН"
        elif len({f for ф in формы_ for f in ф}) > 1:
            причина = "разные правовые формы"
        if причина:
            отсеяно[причина] += len(члены)
            continue
        показ = collections.Counter(dl.без_пояснений(сырые[x.разведка.id].get("company"))
                                    for x in члены)
        имя = sorted(показ.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))[0][0]
        стр = collections.Counter(страна_записи(сырые[x.разведка.id].get("country")) for x in члены)
        страна = sorted(((n, s) for s, n in стр.items() if s), key=lambda t: (-t[0], t[1]))
        группы.append(Группа(
            дилеры=tuple(sorted(члены, key=lambda x: x.разведка.id)), домены=домены, инн=инн,
            имя=имя, страна=страна[0][1] if страна else "",
            формы=frozenset(f for ф in формы_ for f in ф)))
    return Отбор(группы, отсеяно)


def доказательство(сырая: dict) -> str:
    """Ссылка-доказательство записи: domain_source, иначе первая из sources."""
    if str(сырая.get("domain_source") or "").strip():
        return str(сырая["domain_source"]).strip()
    for s in сырая.get("sources") or []:
        if str(s or "").strip():
            return str(s).strip()
    return ""


# ── Проверка в памяти (холостой прогон) ─────────────────────────────────────

def добавить_в_реестр(р: sl.Реестр, sid: str, г: Группа) -> None:
    """Заведённая группа — в реестр, собранный в памяти, тем же разбором, что
    собрать_реестр делает над строками базы."""
    р.корень[sid] = sid
    for d in г.домены:
        р.по_домену[d].add(sid)
        р.домены[sid].add(d)
    for n in г.инн:
        р.по_налогу[("inn", n)].add(sid)
        р.налоги[sid].add(("inn", n))
    sl._имя(р, sid, г.имя)
    for x in г.дилеры:
        р.по_имени[x.разведка.имя].add(sid)
    р.формы[sid] |= set(г.формы)


def проверить_после(список: list, итог_до: sl.Итог, итог_после: sl.Итог,
                    назначено: dict) -> list[str]:
    """Провалы проверки «после записи»: назначено — {id записи: sup_id}."""
    провалы = []
    мимо = [i for i, sid in назначено.items() if итог_после.связь.get(i) != sid]
    if мимо:
        провалы.append(f"заведённых записей, не сведённых в свою сущность: {len(мимо)}")
    споров_до = len(итог_до.спор_две | итог_до.спор_налог | итог_до.придержано)
    споров_после = len(итог_после.спор_две | итог_после.спор_налог | итог_после.придержано)
    if споров_после > споров_до:
        провалы.append(f"споров прибавилось: {споров_до} → {споров_после}")
    потеряно = [i for i, sid in итог_до.связь.items() if итог_после.связь.get(i) != sid]
    if потеряно:
        провалы.append(f"прежних связей потеряно или сменило сущность: {len(потеряно)}")
    return провалы


def гейты(отбор: Отбор, сырые: dict) -> list[str]:
    """Гейты до записи. Возвращает список провалов."""
    провалы = []
    n = len(отбор.группы)
    if n > ГЕЙТЫ["макс_к_заведению"]:
        провалы.append(f"к заведению {n} — больше порога {ГЕЙТЫ['макс_к_заведению']}")
    # Спор внутри группы, пересчитанный заново, а не взятый из отбора.
    спорных = sum(1 for г in отбор.группы
                  if len({x.разведка.имя for x in г.дилеры}) != 1 or len(г.инн) > 1
                  or not (г.домены or г.инн))
    if n and спорных / n > ГЕЙТЫ["макс_доля_споров"]:
        провалы.append(f"спорных групп {спорных} из {n}")
    площадок = sum(1 for г in отбор.группы for d in г.домены
                   if any(d == x or d.endswith("." + x) for x in ПЛОЩАДКИ) or общий_домен(d))
    if площадок:
        провалы.append(f"доменов почты или площадки среди заводимых: {площадок}")
    без_док = sum(1 for г in отбор.группы for x in г.дилеры
                  if not доказательство(сырые[x.разведка.id]))
    if без_док:
        провалы.append(f"записей без ссылки-доказательства: {без_док}")
    return провалы


# ── Печать (только агрегаты) ────────────────────────────────────────────────

def печать(список: list, итог: sl.Итог, отбор: Отбор) -> None:
    записей = sum(len(г.дилеры) for г in отбор.группы)
    print("ДИЛЕРЫ РАЗВЕДКИ БРЕНДОВ → РЕЕСТР КОМПАНИЙ (заведение отсутствующих)")
    print(f"  записей dealers:                       {len(список)}")
    print(f"  сведено с реестром (компания есть):     {len(итог.связь)}")
    print(f"  К ЗАВЕДЕНИЮ: сущностей                  {len(отбор.группы)}  (записей разведки {записей})")
    print(f"    по ключу: домен {sum(1 for г in отбор.группы if г.домены)}, "
          f"ИНН {sum(1 for г in отбор.группы if г.инн)}")
    print("    по виду (записей):")
    виды = collections.Counter(x.вид for г in отбор.группы for x in г.дилеры)
    for в in ВИДЫ:
        print(f"      {в:34} {виды.get(в, 0)}")
    бренды = collections.Counter(x.oem_key for г in отбор.группы for x in г.дилеры)
    print(f"    по брендам (записей), брендов {len(бренды)}:")
    for k, n in sorted(бренды.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"      {k:34} {n}")
    print(f"    сущностей с несколькими брендами:     {sum(1 for г in отбор.группы if len(г.бренды) > 1)}")
    print(f"    со страной:                           {sum(1 for г in отбор.группы if г.страна)}")
    print("  ОТСЕЯНО (записей) — почему:")
    for причина, n in sorted(отбор.отсеяно.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"    {причина:40} {n}")


# ── База ────────────────────────────────────────────────────────────────────

def _прежние(cur) -> dict:
    """Откаченные сущности этого загрузчика: (вид признака, ключ) → {sup_id}.
    Повторная запись после отката возвращает компании её номер."""
    if not sl.есть(cur, "sup_research_dealer"):
        return {}
    cur.execute("""
        select i.kind, i.value_norm, i.sup_id
          from sup_identifier i
         where i.source = %s and i.status = 'rejected' and i.kind in ('domain', 'inn')
           and i.sup_id in (select sup_id from sup_research_dealer where rolled_back_at is not null)
           and i.sup_id not in (select sup_id from sup_research_dealer where rolled_back_at is null)""",
                (ИСТОЧНИК,))
    out: dict = collections.defaultdict(set)
    for k, v, sid in cur.fetchall():
        out[(k, v)].add(sid)
    return out


def признаки(г: Группа, сырые: dict) -> list[tuple[str, str, str, str]]:
    """(kind, value, evidence, status) — как load_supplier_master.признаки."""
    док = {}
    for x in г.дилеры:
        for d in x.разведка.сайты:
            док.setdefault(d, доказательство(сырые[x.разведка.id]))
    out = [("domain", d, док.get(d, ""), "stated") for d in sorted(г.домены)]
    out += [("inn", n, "", "stated") for n in sorted(г.инн)]
    out += [("legal", f, "", "stated") for f in sorted(г.формы)]
    out += [("alias", n, "", "stated") for n in sorted({x.разведка.имя for x in г.дилеры} - {""})]
    return out


def записать(cur, отбор: Отбор, сырые: dict, run_id: str) -> tuple[dict, dict]:
    """Запись на готовом курсоре (транзакцию ведёт вызывающий).
    → ({id записи: sup_id}, счётчики)."""
    import psycopg2.extras

    # Номер выдаётся по наибольшему: второй выдающий в ту же минуту получил бы
    # тот же номер. Блокировка — до конца транзакции.
    cur.execute("lock table sup_number_registry in exclusive mode")
    cur.execute("lock table sup_identifier in share row exclusive mode")
    cur.execute("select coalesce(max(seq), 0) from sup_number_registry")
    следующий = cur.fetchone()[0]
    cur.execute("select kind, value_norm, sup_id from sup_identifier where status <> 'rejected'")
    занято: dict = collections.defaultdict(set)
    for k, v, sid in cur.fetchall():
        занято[(k, v)].add(sid)
    прежние = _прежние(cur)

    назначено: dict = {}
    сущности, реестр, строки_признаков, происхождение, вернуть = [], [], [], [], []
    сч = collections.Counter()
    for г in отбор.группы:
        мои = признаки(г, сырые)
        # Признак, который уже носит другая сущность, — не наш: отбор это
        # исключил, и повтор здесь значит, что реестр изменился между чтением
        # и записью. Такая группа не пишется вовсе.
        if any(k in ЕДИНОЛИЧНЫЕ and занято.get((k, норма(v))) for k, v, _, _ in мои):
            сч["пропущено: ключ занят к моменту записи"] += 1
            continue
        старые = set().union(*(прежние.get((k, норма(v)), set()) for k, v, _, _ in мои
                              if k in ("domain", "inn")))
        if len(старые) == 1:
            sid = next(iter(старые))
            вернуть.append(sid)
            сч["номер возвращён после отката"] += 1
        else:
            следующий += 1
            sid = номер(следующий)
            сущности.append((sid, "legal", г.имя, г.страна or None, "candidate", г.note()))
            реестр.append((sid, следующий, run_id))
            сч["выдано новых номеров"] += 1
        for k, v, док, status in мои:
            строки_признаков.append((sid, k, v, норма(v), ИСТОЧНИК, док or None, status, run_id))
            занято[(k, норма(v))].add(sid)
        for x in г.дилеры:
            сыр = сырые[x.разведка.id]
            назначено[x.разведка.id] = sid
            ключи = [("domain", d) for d in sorted(x.разведка.сайты)] + \
                    [("inn", n) for _, n in sorted(x.разведка.налоги)]
            for kk, kv in ключи:
                происхождение.append((sid, x.oem_key, x.номер, dl.без_пояснений(сыр.get("company")) or "—",
                                      x.вид, страна_записи(сыр.get("country")) or None, kk, kv,
                                      доказательство(сыр) or None, run_id))

    if сущности:
        psycopg2.extras.execute_values(cur, """
            insert into sup_entity (id, kind, display_name, country, resolution, note)
            values %s""", сущности, page_size=500)
        psycopg2.extras.execute_values(cur, """
            insert into sup_number_registry (sup_id, seq, run_id) values %s""",
            реестр, page_size=500)
    if вернуть:
        cur.execute("update sup_entity set status = 'active', updated_at = now() "
                    "where id = any(%s) and status = 'inactive'", (вернуть,))
    if строки_признаков:
        # Откаченный признак того же загрузчика возвращается в строй этим же
        # прогоном; чужой (другого источника) не трогается.
        psycopg2.extras.execute_values(cur, """
            insert into sup_identifier
              (sup_id, kind, value, value_norm, source, evidence, status, run_id)
            values %s
            on conflict (sup_id, kind, value_norm) do update
               set status = excluded.status, run_id = excluded.run_id,
                   evidence = excluded.evidence
             where sup_identifier.status = 'rejected'
               and sup_identifier.source = excluded.source""",
            строки_признаков, page_size=1000)
    if происхождение:
        psycopg2.extras.execute_values(cur, """
            insert into sup_research_dealer
              (sup_id, oem_key, dealer_no, company, kind, country, key_kind, key_value,
               evidence, run_id)
            values %s""", происхождение, page_size=1000)
    сч["строк признаков"] = len(строки_признаков)
    сч["строк происхождения"] = len(происхождение)
    return назначено, сч


def откатить(cur, run_id: str) -> collections.Counter:
    """Откат пометкой: происхождение — rolled_back_at, признаки прогона —
    rejected, сущность без других действующих признаков — inactive."""
    сч = collections.Counter()
    cur.execute("update sup_research_dealer set rolled_back_at = now() "
                "where run_id = %s and rolled_back_at is null returning sup_id", (run_id,))
    сущности = sorted({r[0] for r in cur.fetchall()})
    сч["строк происхождения помечено"] = cur.rowcount
    cur.execute("update sup_identifier set status = 'rejected' "
                "where run_id = %s and source = %s and status <> 'rejected'", (run_id, ИСТОЧНИК))
    сч["признаков отклонено"] = cur.rowcount
    if сущности:
        cur.execute("""
            update sup_entity e set status = 'inactive', updated_at = now()
             where e.id = any(%s) and e.status = 'active'
               and not exists (select 1 from sup_identifier i
                                where i.sup_id = e.id and i.status <> 'rejected')
               and not exists (select 1 from sup_research_dealer r
                                where r.sup_id = e.id and r.rolled_back_at is null)""",
                    (сущности,))
        сч["сущностей выведено из строя"] = cur.rowcount
    return сч


def прогон(conn, разведка: list[dict], *, apply: bool = False, run_id: str = "",
           rollback: str = "") -> int:
    """Весь прогон на готовом соединении: откат, замер или замер с записью."""
    with conn.cursor() as cur:
        if rollback:
            if not sl.есть(cur, "sup_research_dealer"):
                print("таблицы sup_research_dealer нет — откатывать нечего", file=sys.stderr)
                return 3
            сч = откатить(cur, rollback)
            if not сч["строк происхождения помечено"]:
                conn.rollback()
                print("ни одной действующей строки с таким ключом — ключ набран неверно?",
                      file=sys.stderr)
                return 3
            conn.commit()
            print(f"ОТКАТ прогона {rollback} (пометкой):")
            for k, n in сч.items():
                print(f"  {k:36} {n}")
            print("Номера остаются выданными; повторная запись вернёт их тем же компаниям.")
            return 0
        for опора in ("sup_entity", "sup_identifier", "sup_number_registry"):
            if not sl.есть(cur, опора):
                print(f"нет таблицы {опора} — заводить некуда", file=sys.stderr)
                return 2
        if apply and not sl.есть(cur, "sup_research_dealer"):
            print("запись запрошена, но таблицы sup_research_dealer нет — примените "
                  "library/supabase/research_dealers_schema.sql", file=sys.stderr)
            return 2

        список = dl.дилеры(разведка)
        сырые = _сырые(разведка)
        р, _ = sl.читать_реестр(cur)
        итог = dl.сопоставить(список, р)
        отбор = отобрать(список, итог, сырые)
        печать(список, итог, отбор)

        # Проверка в памяти: реестр плюс заводимые — замер обязан свести их все.
        р_память, _ = sl.читать_реестр(cur)
        назначено_память = {}
        for n, г in enumerate(отбор.группы):
            sid = f"new-{n}"
            добавить_в_реестр(р_память, sid, г)
            for x in г.дилеры:
                назначено_память[x.разведка.id] = sid
        итог_память = dl.сопоставить(список, р_память)
        провалы = гейты(отбор, сырые) + проверить_после(список, итог, итог_память, назначено_память)
        print(f"  после заведения сведено станет (в памяти): {len(итог_память.связь)} "
              f"из {len(список)}")
        print()
        if провалы:
            print("ГЕЙТЫ НЕ СОШЛИСЬ:")
            for p in провалы:
                print(f"  ✗ {p}")
            print("Запись отменена — так и задумано: правило меряют до применения.")
            conn.rollback()
            return 1
        print("гейты до записи: сошлись все")
        if not apply:
            conn.rollback()
            print("Прогон ВХОЛОСТУЮ: в базу ничего не записано.")
            return 0
        if not отбор.группы:
            conn.rollback()
            print("Заводить некого: все дилеры с ключом уже в реестре или отсеяны.")
            return 0

        назначено, сч = записать(cur, отбор, сырые, run_id)
        # Проверка после записи — из базы, в этой же транзакции.
        р_после, _ = sl.читать_реестр(cur)
        итог_после = dl.сопоставить(список, р_после)
        провалы = проверить_после(список, итог, итог_после, назначено)
        if провалы:
            conn.rollback()
            print("ПРОВЕРКА ПОСЛЕ ЗАПИСИ НЕ СОШЛАСЬ — транзакция откачена, в базе ничего:")
            for p in провалы:
                print(f"  ✗ {p}")
            return 1
        conn.commit()
    print(f"\nЗАПИСАНО, ключ прогона {run_id}")
    for k, n in сч.items():
        print(f"  {k:36} {n}")
    print(f"  сведено после записи (из базы):      {len(итог_после.связь)} из {len(список)}")
    print(f"Откат: ROLLBACK_RUN_ID={run_id} (пометкой, не удалением).")
    return 0


def _сырые(разведка: list[dict]) -> dict:
    """{id записи в dl.дилеры: словарь dealers} — тем же обходом, что dl.дилеры."""
    out = {}
    for d in разведка:
        if not d.get("oem_key"):
            continue
        for x in d.get("dealers") or []:
            if isinstance(x, dict):
                out[len(out)] = x
    return out


def main() -> int:
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url:
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    apply = os.environ.get("APPLY", "") not in ("", "0", "false")
    rollback = os.environ.get("ROLLBACK_RUN_ID", "").strip()
    run_id = os.environ.get("RUN_ID", "").strip() or f"dealers-{int(time.time())}"

    import psycopg2
    # statement_timeout и lock_timeout — в строке подключения (правила 9 и 12).
    conn = psycopg2.connect(url, connect_timeout=20,
                            options="-c statement_timeout=300000 -c lock_timeout=30000")
    try:
        if not apply and not rollback:
            conn.set_session(readonly=True)
        return прогон(conn, dl.читать_файлы(), apply=apply, run_id=run_id, rollback=rollback)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
