"""Суточный скан чатов сделок → снимок реакции коммерсантов.

Запускается РАЗ В СУТКИ (полночь МСК) отдельным джобом — НЕ при каждой пересборке дашборда.
Копит дневные снимки (последние 14 дн); дашборд читает их и показывает среднее за неделю.
Выборку текста сообщений для ручной/Opus-оценки дозаписывает в /tmp/rep_samples.json —
только на раннере, в репозиторий и в журнал она не попадает.

СНИМОК В ДВУХ ЧАСТЯХ (19.10.2026). Репозиторий публичный (CLAUDE.md, правило 5), а снимок
до этого дня держал в открытом файле названия сделок («ждут ответа») и оценки сотрудников
моделью. Теперь:
  • data/chat_snapshot.json — ПУБЛИЧНАЯ часть: только числа по сотруднику (uid Битрикса)
    за день, ровно те, из которых reps._load_react_snapshot считает недельное среднее.
    Поля — белым списком (reps.REACT_PUBLIC): новое поле скана само в файл не попадёт;
  • KV Cloudflare, ключ chat:v1 — ЗАКРЫТАЯ часть: список «ждут ответа» по дням (номер
    чата, номер и название сделки, возраст) и оценки (assessment). Пишет только этот
    прогон; читает сборка дашборда (deploy.yml → --fetch-private). Воркер портала этот
    ключ не читает.

ПЕРЕНОС. Первый прогон после смены схемы снимает закрытые поля с прежнего публичного файла
и кладёт их в KV. Порядок записи: KV → перечитать и сверить → публичный файл. Не записался
или не сошёлся KV — публичный файл не трогается и прогон красный: так ничего не теряется.

В ЖУРНАЛ — ТОЛЬКО ЧИСЛА (правило 17): ни фамилий, ни uid, ни номеров и названий сделок.

Использование:
  python chat_scan.py                          # скан, запись в KV и в файл
  python chat_scan.py --dry-run                # скан и счётчики, ничего не записывает
  python chat_scan.py --fetch-private ПУТЬ     # закрытая часть из KV в файл (сборка дашборда)
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import statistics
from pathlib import Path

import reps as reps_mod

ROOT = Path(__file__).parent
SNAP = ROOT / "data" / "chat_snapshot.json"
SAMPLES = Path("/tmp/rep_samples.json")
KV_KEY = "chat:v1"
KEEP_DAYS = 14
PUBLIC_SCHEMA = 2
PRIVATE_VERSION = 1
# Тот же предел, что у остальных снимков в KV (publish_suppliers.MAX_BYTES).
MAX_BYTES = 8 * 1024 * 1024

_DAY = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_UID = re.compile(r"\d{1,12}\Z")


class ScanError(Exception):
    """Наружу выходит только постоянный код, без данных."""


def _msk_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=3)


# ── Разделение снимка ────────────────────────────────────────────────────────

def _is_num(v) -> bool:
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))


def public_record(r: dict) -> dict:
    """Запись сотрудника за день → только числа из белого списка (и те же — в разрезах)."""
    out = {k: r[k] for k in reps_mod.REACT_PUBLIC if k in r and _is_num(r[k])}
    for part in ("presale", "real"):
        sub = r.get(part)
        if isinstance(sub, dict):
            out[part] = {k: sub[k] for k in reps_mod.REACT_PUBLIC if k in sub and _is_num(sub[k])}
    return out


def private_record(r: dict) -> dict | None:
    """Закрытое из записи за день — список «ждут ответа». Пустой список не хранится."""
    w = r.get("waiting")
    return {"waiting": w} if isinstance(w, list) and w else None


def split_snapshot(snap: dict) -> tuple[dict, dict]:
    """Снимок любой схемы → (публичная часть, закрытая часть).

    Для файла новой схемы закрытая часть пуста; для прежнего файла в ней всё, что
    надо перенести в KV: «ждут ответа» по дням и оценки."""
    pub_days, priv_days = {}, {}
    for day, emps in (snap.get("days") or {}).items():
        if not isinstance(emps, dict):
            continue
        pub_days[day] = {}
        for uid, r in emps.items():
            if not isinstance(r, dict):
                continue
            pub_days[day][str(uid)] = public_record(r)
            p = private_record(r)
            if p:
                priv_days.setdefault(day, {})[str(uid)] = p
    assess = {str(u): a for u, a in (snap.get("assessment") or {}).items() if isinstance(a, dict)}
    pub = {"schema": PUBLIC_SCHEMA, "days": pub_days, "updated": snap.get("updated")}
    priv = {"version": PRIVATE_VERSION, "days": priv_days, "assessment": assess}
    return pub, priv


def compose_private(old: dict | None, moved: dict, day: str, today: dict, keep: list[str]) -> dict:
    """Новая закрытая часть: прежняя из KV + перенесённая из файла + сегодняшний день.

    Дни — ровно те, что остались в публичном файле (keep). День заменяется целиком,
    как и в публичном файле: перезапуск скана в тот же день не оставит вчерашних
    «ждут ответа» у того, кому уже ответили. Перенесённое из файла главнее KV по дню
    (из него же посчитаны публичные числа этого дня); оценка из KV главнее
    перенесённой — после переноса хранилище оценок одно, KV."""
    old = old or {}
    days = {**(old.get("days") or {}), **(moved.get("days") or {})}
    days[day] = today
    days = {d: days[d] for d in keep if days.get(d)}
    assess = {**(moved.get("assessment") or {}), **(old.get("assessment") or {})}
    return {"version": PRIVATE_VERSION, "updated": _msk_now().isoformat(timespec="seconds"),
            "days": days, "assessment": assess}


def check_public(pub: dict) -> None:
    """Публичная часть: даты, uid и числа — больше ничего. Иначе запись не делается."""
    for day, emps in pub.get("days", {}).items():
        if not _DAY.match(str(day)) or not isinstance(emps, dict):
            raise ScanError("PUBLIC_BAD_DAY")
        for uid, r in emps.items():
            if not _UID.match(str(uid)) or not isinstance(r, dict):
                raise ScanError("PUBLIC_BAD_UID")
            for k, v in r.items():
                if k in ("presale", "real") and isinstance(v, dict):
                    if not all(_is_num(x) for x in v.values()):
                        raise ScanError("PUBLIC_HAS_TEXT")
                elif k not in reps_mod.REACT_PUBLIC or not _is_num(v):
                    raise ScanError("PUBLIC_HAS_TEXT")
    if "assessment" in pub:
        raise ScanError("PUBLIC_HAS_ASSESSMENT")


def check_nothing_lost(moved: dict, priv: dict, keep: list[str], day: str) -> None:
    """Всё перенесённое из файла (в пределах хранимых дней) — в закрытой части дословно."""
    for d, emps in (moved.get("days") or {}).items():
        if d not in keep or d == day:
            continue
        for uid, rec in emps.items():
            if ((priv["days"].get(d) or {}).get(uid)) != rec:
                raise ScanError("MOVE_LOST_WAITING")
    for uid in moved.get("assessment") or {}:
        if uid not in priv["assessment"]:
            raise ScanError("MOVE_LOST_ASSESSMENT")


def counts(priv: dict) -> tuple[int, int, int]:
    """(дней, записей «ждут ответа», оценок) — для журнала и сверки."""
    days = priv.get("days") or {}
    waits = sum(len(rec.get("waiting") or []) for emps in days.values() for rec in emps.values())
    return len(days), waits, len(priv.get("assessment") or {})


def journal(day: str, today_pub: dict, n_samples: int, pub: dict) -> list[str]:
    """Строки журнала о скане — только числа по всем сотрудникам вместе."""
    vals = [r for r in today_pub.values()]
    s = lambda k: sum(int(r.get(k) or 0) for r in vals)
    med = [r["medMin"] for r in vals if r.get("medMin") is not None]
    return [
        f"  ✓ снимок за {day}: {len(vals)} сотрудников, дней в истории {len(pub['days'])}",
        f"    всего: упоминаний {s('mentions')}, ответил сам {s('self')}, закрыл коллега {s('coll')}, "
        f"повисло {s('none')}; висит {s('waitN')} (>сут {s('waitGt1d')}, >3 сут {s('waitGt3d')})",
        f"    медиана реакции по сотрудникам: {round(statistics.median(med)) if med else '—'} мин "
        f"(у {len(med)} из {len(vals)}); реплик в выборке для оценки {n_samples}",
    ]


# ── Закрытое хранилище: KV Cloudflare ────────────────────────────────────────

class KVStore:
    """Один ключ KV (chat:v1). Клиент — общий с публикаторами (publish_suppliers.Cloudflare):
    та же привязка, что читает портал, закрытый список ключей, повтор только для чтения."""

    def __init__(self, cf, namespace, delays):
        self.cf, self.ns, self.delays = cf, namespace, delays

    def get(self) -> dict | None:
        raw = self.cf.get(self.ns, KV_KEY)
        if raw is None:
            return None
        try:
            val = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ScanError("KV_BAD_JSON") from None
        if not isinstance(val, dict) or val.get("version") != PRIVATE_VERSION:
            raise ScanError("KV_BAD_VERSION")
        return val

    def put(self, raw: bytes) -> None:
        """Запись и сверка чтением: значение в KV — байт в байт то, что записали."""
        self.cf.put(self.ns, KV_KEY, raw)
        for delay in self.delays:
            if delay:
                self.cf.sleep(delay)
            if self.cf.get(self.ns, KV_KEY) == raw:
                return
        raise ScanError("KV_READBACK_MISMATCH")


def kv_store() -> KVStore:
    spec = importlib.util.spec_from_file_location(
        "kvant_publish_suppliers", ROOT / "scripts" / "publish_suppliers.py")
    ps = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ps)

    class CloudflareChat(ps.Cloudflare):
        КЛЮЧИ = (KV_KEY,)

    try:
        cf = CloudflareChat(os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""), os.environ.get("CLOUDFLARE_API_TOKEN", ""))
        return KVStore(cf, cf.namespace(), ps.READBACK_DELAYS)
    except ps.PublishError as e:
        raise ScanError(str(e)) from None


def _has_kv_secrets() -> bool:
    return bool(os.environ.get("CLOUDFLARE_ACCOUNT_ID") and os.environ.get("CLOUDFLARE_API_TOKEN"))


def _kv_call(fn, *args):
    """Ошибки клиента KV (его PublishError) → свой код, без данных."""
    try:
        return fn(*args)
    except ScanError:
        raise
    except Exception as e:
        if type(e).__name__ == "PublishError":
            raise ScanError(str(e)) from None
        raise


# ── Прогоны ─────────────────────────────────────────────────────────────────

def fetch_private(path: Path, store) -> int:
    """Закрытая часть из KV → файл для сборки дашборда (reps._load_chat_private)."""
    priv = _kv_call(store.get)
    if priv is None:
        print(f"закрытой части в KV ({KV_KEY}) ещё нет — дашборд соберётся на числах публичного снимка")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(priv, ensure_ascii=False), encoding="utf-8")
    d, w, a = counts(priv)
    print(f"закрытая часть снимка чатов прочитана: дней {d}, записей «ждут ответа» {w}, оценок {a}")
    return 0


def _scan_bitrix():
    import config
    from bitrix_client import BitrixClient
    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    return reps_mod.scan_chat_reaction(client, collect_samples=True)


def scan(*, dry: bool, store=None, scanner=None, snap_path: Path = SNAP, samples_path: Path = SAMPLES) -> int:
    print("• Скан чатов сделок (реакция + выборка текста)…")
    react, rep_ids, unames = (scanner or _scan_bitrix)()
    uid2label = {u: lb for lb, u in rep_ids.items()}

    day = _msk_now().date().isoformat()
    today_pub, today_priv, samples_out = {}, {}, {}
    for uid, r in react.items():
        uid = str(uid)
        s = r.pop("samples", [])
        samples_out[uid] = {"label": uid2label.get(uid, uid), "name": unames.get(uid, uid), "samples": s}
        today_pub[uid] = public_record(r)
        p = private_record(r)
        if p:
            today_priv[uid] = p

    try:
        file_snap = json.loads(snap_path.read_text(encoding="utf-8"))
    except Exception:
        file_snap = {"days": {}}
    pub, moved = split_snapshot(file_snap)
    pub["days"][day] = today_pub
    keep = sorted(pub["days"])[-KEEP_DAYS:]
    pub["days"] = {d: pub["days"][d] for d in keep}
    pub["updated"] = _msk_now().isoformat(timespec="seconds")
    pub["private"] = {"store": "Cloudflare KV", "key": KV_KEY}
    check_public(pub)

    if store is None and (not dry or _has_kv_secrets()):
        store = kv_store()          # без ключей запись невозможна — kv_store() упадёт с кодом
    old = _kv_call(store.get) if store is not None else None
    priv = compose_private(old, moved, day, today_priv, keep)
    check_nothing_lost(moved, priv, keep, day)
    raw = json.dumps(priv, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ScanError("PRIVATE_TOO_LARGE")

    for line in journal(day, today_pub, sum(len(v["samples"]) for v in samples_out.values()), pub):
        print(line)
    md, mw, ma = counts(moved)
    if md or ma:
        print(f"  перенос из публичного файла: дней {md}, записей «ждут ответа» {mw}, оценок {ma}")
    d, w, a = counts(priv)
    print(f"  закрытая часть ({KV_KEY}): дней {d}, записей «ждут ответа» {w}, оценок {a}, размер {len(raw)} Б; "
          f"в KV до записи: {'нет' if old is None else 'есть'}")

    samples_path.write_text(json.dumps(samples_out, ensure_ascii=False, indent=1), encoding="utf-8")
    if dry:
        print("  вхолостую: ни KV, ни data/chat_snapshot.json не записаны")
        return 0

    _kv_call(store.put, raw)        # сначала закрытая часть, со сверкой чтением
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    snap_path.write_text(json.dumps(pub, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  ✓ KV {KV_KEY} записан и сверен чтением; публичный снимок — только числа")
    return 0


def main(argv=None, **kw) -> int:
    ap = argparse.ArgumentParser(description="Суточный скан чатов сделок")
    ap.add_argument("--dry-run", action="store_true", help="скан и счётчики, без записи в KV и в файл")
    ap.add_argument("--fetch-private", metavar="ПУТЬ", help="закрытую часть из KV — в файл, и выйти")
    a = ap.parse_args(argv)
    try:
        if a.fetch_private:
            return fetch_private(Path(a.fetch_private), kw.get("store") or kv_store())
        return scan(dry=a.dry_run, **kw)
    except ScanError as e:
        print(f"::error::снимок чатов: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
