"""Зонд v44: дотянемся ли мы до 335 непрочитанных вложений — и что именно мешает.

Опись входящих КП (`gt/data/bitrix_tkp_index.json`) говорит по 335 файлам одно и то
же: «Диск не отдал ссылку (нет скоупа disk или файла нет)». Это не измерение, а две
гипотезы в одной строке, и выбор между ними меняет исполнителя: отсутствующее право —
одна галочка владельца в портале, неверный идентификатор — правка кода у меня. Из 335
файлов 323 — входящие предложения поставщиков, а по прочитанным входящим цена нашлась
в 779 файлах из 1000, так что цена вопроса не нулевая.

Зонд отвечает на четыре вопроса по порядку:
  1. есть ли у вебхука право `disk` (и админская ли учётка);
  2. отвечает ли Диск вообще хоть на один вызов;
  3. что именно возвращают `disk.file.get` и `disk.attachedObject.get` по нашим же
     непрочитанным идентификаторам — выборка берётся из описи, а не набирается руками;
  4. есть ли обходной путь без Диска: какие ключи вообще приходят в файловом объекте
     дела и отдаёт ли хоть один из них байты файла, а не страницу входа.

ПЕЧАТАЮТСЯ ТОЛЬКО АГРЕГАТЫ: названия прав, имена методов, коды ошибок, имена ключей,
счётчики. Ни одного идентификатора файла, дела или сделки, ни имени файла, ни ссылки —
ссылка несёт одноразовый токен, а журнал прогона публичный. Ничего не пишется.

Прежние выпуски зонда: v19/v20 — состав СП-166, v43 — права на механические правки
гигиены. Зонд разовый: каждый выпуск отвечает на вопрос своего дня.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bitrix_client import BitrixClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "gt/data/bitrix_tkp_index.json"
FAIL = "Диск не отдал ссылку"
SAMPLE = int(os.getenv("PROBE_SAMPLE") or 12)

#: ключи файлового объекта, которые могли бы отдать байты без Диска
URL_KEYS = ("urlMachine", "DOWNLOAD_URL", "downloadUrl", "urlDownload", "url", "URL")
#: подписи начала файла — по ним видно, файл нам отдали или страницу входа
MAGIC = ((b"%PDF", "pdf"), (b"PK\x03\x04", "zip/xlsx/docx"), (b"\xd0\xcf\x11\xe0", "старый office"),
         (b"\x89PNG", "png"), (b"\xff\xd8\xff", "jpeg"), (b"<!DO", "html"), (b"<htm", "html"),
         (b"<HTM", "html"), (b"{", "json"))


#: итог зонда — отдельной чистой функцией, чтобы вывод нельзя было разогнать
#: с измерением: ровно один разбор случая, и он под тестом. Прогон 18.09.2026
#: показал, зачем это нужно: первая редакция считала обходом само НАЛИЧИЕ
#: http-ссылки в файловом объекте и объявила обход возможным, хотя ссылка
#: отдала text/html — страницу входа. Наличие ссылки и отдача байтов — разные
#: измерения, и решает второе.
VERDICTS = {
    "нет права": (
        "Право disk вебхуку НЕ выдано. Это и есть причина по всем непрочитанным файлам,",
        "а не отсутствие файлов: иначе неудача не была бы поголовной. Действие владельца —",
        "портал → Разработчикам → вебхук → отметить «disk» → сохранить. Мой код менять не",
        "нужно: путь disk.file.get уже написан и на файлах с правом работает."),
    "доступ закрыт учётной записи": (
        "Право disk ЕСТЬ, Диск отвечает, но по нашим файлам возвращает «доступ запрещён».",
        "Значит не хватает не права вебхука, а прав его СОТРУДНИКА на эти файлы: вложения",
        "писем лежат на личном диске того, кто письмо получил, и посторонний их не видит.",
        "Учётка вебхука при этом не администратор. Действие владельца — либо сделать эту",
        "учётку администратором, либо выдать ей доступ к диску сотрудников, чьи письма",
        "разбираются. Правка кода не поможет: отказ приходит от прав, а не от метода."),
    "право есть, путь мой": (
        "Право есть и ссылку Диск отдаёт. Значит виноват не доступ, а мой путь до байтов,",
        "и правка за мной: раздел 3 показывает, каким методом и по какому идентификатору",
        "файл берётся."),
    "обход без диска": (
        "Диск байтов не даёт, зато ссылка из файлового объекта отдала НЕ страницу, а файл.",
        "Обход Диска возможен без новых прав — качать по этой ссылке."),
    "ссылка ведёт на страницу входа": (
        "Ссылка в файловом объекте есть, но отдаёт text/html — страницу входа, а не файл.",
        "Обхода нет: серверный клиент сессии не имеет. Остаётся доступ к Диску."),
    "тупик": (
        "Ни Диск, ни ссылки в объекте байтов не дают. Дальше — только доступ к Диску.",),
    "не измерено": (
        "Права не прочитались, и ссылку никто не отдал: зонд ничего не измерил.",
        "Это отказ измерения, а не ответ — перезапустить."),
}


def verdict(rights: list[str], gave_url: int, url_keys: int, *,
            denied: int = 0, url_file: int = 0) -> str:
    """Какой из случаев мы наблюдали. Порядок разбора — от дешёвого действия.

    denied   — сколько наших файлов Диск закрыл отказом доступа;
    url_keys — сколько http-ссылок нашлось в файловых объектах;
    url_file — по скольким из них пришли байты ФАЙЛА, а не страница.
    """
    if not rights:
        return "не измерено"
    if "disk" not in rights:
        return "нет права"
    if gave_url:
        return "право есть, путь мой"
    if url_file:
        return "обход без диска"
    if denied:
        return "доступ закрыт учётной записи"
    if url_keys:
        return "ссылка ведёт на страницу входа"
    return "тупик"


def head(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78, flush=True)


def safe(c: BitrixClient, method: str, params: dict | None = None):
    """Вызов, который не роняет зонд. Возвращает (результат, краткая ошибка).

    Текст ошибки клиента — «метод: КОД описание», без адреса и без токена (см.
    bitrix_client.BitrixClient.call_envelope). Обрезаем до 120 знаков и всё равно
    печатаем только в агрегате.
    """
    try:
        return c.call(method, params or {}, retries=1), ""
    except Exception as e:                                   # noqa: BLE001 — зонд
        return None, f"{type(e).__name__}: {str(e)[:120]}"


def code_of(err: str) -> str:
    """Код ошибки Bitrix из текста исключения: «метод: КОД описание» → КОД."""
    m = re.search(r":\s*([A-Z_]{3,40})", err or "")
    return m.group(1) if m else (err.split(":")[0] if err else "без ошибки")


def unread(doc: dict) -> list[dict]:
    """Непрочитанные вложения из описи — те самые, на которых стоит гипотеза."""
    out = []
    for payload in (doc.get("scopes") or {}).values():
        for it in payload.get("inventory") or []:
            if FAIL in str(it.get("download") or "") and it.get("file_id"):
                out.append(it)
    return out


def shape(o: dict) -> tuple:
    """Форма файлового объекта: имена ключей. Значения не печатаются никогда."""
    return tuple(sorted(str(k) for k in o.keys()))


def sniff(url: str) -> tuple[str, bool]:
    """Что лежит по ссылке: файл или страница входа. Печатается только вид и объём."""
    try:
        r = requests.get(url, timeout=25, stream=True)
    except Exception as e:                                   # noqa: BLE001 — зонд
        return f"сеть: {type(e).__name__}", False
    try:
        chunk = next(r.iter_content(4096), b"") or b""
    except Exception:                                        # noqa: BLE001 — зонд
        chunk = b""
    r.close()
    kind = next((n for sig, n in MAGIC if chunk.startswith(sig)), "неопознанное начало")
    ct = str(r.headers.get("content-type") or "")[:40]
    size = str(r.headers.get("content-length") or "?")
    # файлом считаем только опознанный бинарный формат: html и json — это ответ
    # портала о том, что нас не пустили, а не вложение
    is_file = kind not in ("html", "json", "неопознанное начало")
    return f"HTTP {r.status_code} · {ct} · {size} б · начало: {kind}", is_file


def main() -> int:
    c = BitrixClient(os.environ["BITRIX_WEBHOOK_URL"])

    head("1. ПРАВА ВЕБХУКА: ЕСТЬ ЛИ СРЕДИ НИХ disk")
    scope, err = safe(c, "scope")
    rights = sorted(str(x).lower() for x in scope) if isinstance(scope, list) else []
    print("выданные права:", ", ".join(rights) if rights else f"не получены ({err})")
    print("право disk:", "ЕСТЬ" if "disk" in rights else "НЕТ" if rights else "не измерено")
    prof, err = safe(c, "profile")
    if isinstance(prof, dict):
        print(f"учётка вебхука: администратор — {'да' if prof.get('ADMIN') else 'НЕТ'}")
    else:
        print(f"профиль не получен ({err})")

    head("2. ОТВЕЧАЕТ ЛИ ДИСК ХОТЬ НА ЧТО-НИБУДЬ")
    for m, p in (("disk.storage.getlist", {}), ("disk.folder.getchildren", {"id": 1})):
        res, err = safe(c, m, p)
        if err:
            print(f"  {m:<28} ошибка {code_of(err)}")
        elif isinstance(res, list):
            print(f"  {m:<28} ответил, записей: {len(res)}")
        else:
            print(f"  {m:<28} ответил: {type(res).__name__}")

    head("3. ЧТО ОТВЕЧАЮТ МЕТОДЫ ДИСКА ПО НАШИМ ЖЕ НЕПРОЧИТАННЫМ ФАЙЛАМ")
    if not INDEX.exists():
        print("описи нет на диске — сравнивать не с чем")
        return 1
    doc = json.loads(INDEX.read_text(encoding="utf-8"))
    rows = unread(doc)
    print(f"непрочитанных вложений в описи: {len(rows)} · в выборку зонда: "
          f"{min(SAMPLE, len(rows))} (шагом через весь список, не первые подряд)")
    step = max(1, len(rows) // max(1, SAMPLE))
    pick = rows[::step][:SAMPLE]
    per_method: dict[str, Counter] = {}
    gave_url = 0
    denied = 0
    for it in pick:
        fid = str(it["file_id"])
        for m in ("disk.file.get", "disk.attachedObject.get"):
            res, err = safe(c, m, {"id": fid})
            cnt = per_method.setdefault(m, Counter())
            if err:
                c_err = code_of(err)
                cnt[c_err] += 1
                if c_err == "ACCESS_DENIED":
                    denied += 1
            elif isinstance(res, dict) and res.get("DOWNLOAD_URL"):
                cnt["отдал ссылку"] += 1
                gave_url += 1
            elif isinstance(res, dict):
                cnt["ответил без ссылки: " + ",".join(shape(res)[:6])] += 1
            else:
                cnt[f"ответил {type(res).__name__}"] += 1
    for m, cnt in per_method.items():
        print(f"  {m}")
        for k, n in cnt.most_common():
            print(f"      {n:3d} × {k}")
    print(f"ссылку на скачивание получили: {gave_url} из {len(pick) * 2} вызовов")

    head("4. ЕСТЬ ЛИ ПУТЬ БЕЗ ДИСКА: ФОРМА ФАЙЛОВОГО ОБЪЕКТА У ДЕЛА")
    acts = []
    for it in pick:
        m = re.match(r"дело (\d+)", str(it.get("origin") or ""))
        if m:
            acts.append(int(m.group(1)))
    acts = sorted(set(acts))
    print(f"дел в выборке: {len(acts)} (идентификаторы не печатаются)")
    shapes: Counter = Counter()
    url_keys: Counter = Counter()
    probed = 0
    url_file = 0
    for aid in acts:
        res, err = safe(c, "crm.activity.get", {"id": aid})
        if err or not isinstance(res, dict):
            shapes[f"дело не прочиталось: {code_of(err)}"] += 1
            continue
        files = res.get("FILES")
        items = list(files.values()) if isinstance(files, dict) else (files or [])
        if not items:
            shapes["у дела нет FILES"] += 1
            continue
        for o in items:
            if not isinstance(o, dict):
                shapes[f"элемент FILES не словарь: {type(o).__name__}"] += 1
                continue
            shapes[" · ".join(shape(o))] += 1
            for k in URL_KEYS:
                v = o.get(k)
                if isinstance(v, str) and v.startswith("http"):
                    url_keys[k] += 1
                    if probed < 3:                    # по одной пробе на ключ, не больше трёх
                        probed += 1
                        told, ok = sniff(v)
                        url_file += int(ok)
                        print(f"  проба ключа {k}: {told}")
    print("формы файлового объекта (имена ключей, значения не печатаются):")
    for k, n in shapes.most_common(8):
        print(f"      {n:3d} × {k}")
    print("ключи с http-ссылкой:", dict(url_keys) or "ни одного")

    head("5. ИТОГ")
    print(f"отказов доступа по нашим файлам: {denied} · ссылок отдало файл: {url_file}")
    case = verdict(rights, gave_url, sum(url_keys.values()), denied=denied, url_file=url_file)
    print(f"случай: {case}")
    for line in VERDICTS[case]:
        print(line)

    print("\nГОТОВО")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
