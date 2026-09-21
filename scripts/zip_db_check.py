"""Проверка, что сайт ЗИП действительно видит базу.

ЗАЧЕМ ЭТО ОТДЕЛЬНЫЙ СКРИПТ. Сайт закрыт Cloudflare Access, и снаружи его
состояние не читается: любой запрос к kvant-zip.pages.dev получает 302 на
страницу входа — и `/`, и `/db/*`. То есть проверить «ожил ли сайт после
обновления ключа» можно было только глазами владельца. 21.09.2026 это стоило
двух заходов: ключ обновили, страница осталась с плашкой «офлайн-копия», и
причину никто не видел.

Проверка идёт с другой стороны — со стороны Cloudflare API и самой Supabase:

1. Переменная SUPABASE_SERVICE_KEY есть в окружении Production проекта.
   Частая ошибка — положить её в Preview или назвать иначе.
2. Последняя выкладка Production несёт эту переменную. Переменные привязываются
   к выкладке: сохранить ключ и не перевыкатить — значит ничего не изменить.
3. Ключ работает. Если переменная лежит открытым текстом, API отдаёт значение,
   и мы спрашиваем им Supabase напрямую тем же путём, каким это делает воркер
   (заголовок apikey, схема public). HTTP 200 — сайт увидит базу. Если
   переменная заведена секретом, значение скрыто — тогда проверяются только
   пункты 1 и 2, и об этом сказано прямо, а не выдано за проверку ключа.

В журнал не печатается ни ключ, ни его часть: репозиторий публичный
(CLAUDE.md, правило 17). Только имена переменных, коды ответов и время выкладки.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

ПРОЕКТ = "kvant-zip"
ПЕРЕМЕННАЯ = "SUPABASE_SERVICE_KEY"
# Тот же источник, что вшит в воркер сайта (zip/site/_worker.js, SUPA_ORIGIN).
# Держать в одном месте нельзя — воркер это JS, — поэтому есть тест на совпадение.
SUPA_ORIGIN = "https://vpjliavuuxjcvtxbthlp.supabase.co"
# Таблица для пробного чтения: нужна только чтобы получить код ответа.
ПРОБА = "/rest/v1/lib_parts?select=part_number&limit=1"
CF = "https://api.cloudflare.com/client/v4"


def _запрос(url: str, заголовки: dict[str, str]) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=заголовки)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:      # код ответа — тоже результат
        return e.code, e.read()


def _cf(путь: str, токен: str) -> dict:
    код, тело = _запрос(CF + путь, {"Authorization": f"Bearer {токен}"})
    if код != 200:
        raise RuntimeError(f"Cloudflare API {путь}: HTTP {код}")
    return json.loads(тело)


def переменные(окружение: dict) -> dict[str, dict]:
    """Переменные окружения выкладки или конфигурации, как их отдаёт API."""
    env = (окружение or {}).get("env_vars") or {}
    return {k: (v or {}) for k, v in env.items()}


def значение(запись: dict) -> str | None:
    """Значение переменной, если оно вообще отдано. У секретов его нет."""
    v = запись.get("value")
    if not isinstance(v, str) or not v or set(v) <= {"*"}:
        return None
    return v


def проверить_ключ(ключ: str) -> tuple[int, str]:
    """Спросить Supabase тем же путём, каким это делает воркер сайта."""
    заголовки = {"apikey": ключ, "Accept-Profile": "public"}
    # Воркер добавляет Bearer только для старых ключей (не sb_secret_).
    if not ключ.startswith("sb_secret_"):
        заголовки["Authorization"] = "Bearer " + ключ
    код, тело = _запрос(SUPA_ORIGIN + ПРОБА, заголовки)
    подсказка = ""
    if код != 200:
        try:                                  # текст ошибки Supabase без данных
            подсказка = str(json.loads(тело).get("message") or "")[:120]
        except Exception:
            подсказка = ""
    return код, подсказка


def main() -> int:
    токен = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    счёт = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if not токен or not счёт:
        print("нет CLOUDFLARE_API_TOKEN или CLOUDFLARE_ACCOUNT_ID — проверять нечем")
        return 2

    беды: list[str] = []

    проект = _cf(f"/accounts/{счёт}/pages/projects/{ПРОЕКТ}", токен)["result"]
    прод = (проект.get("deployment_configs") or {}).get("production") or {}
    в_настройках = переменные(прод)
    print(f"проект {ПРОЕКТ}: переменных в Production — {len(в_настройках)}")
    print("имена: " + (", ".join(sorted(в_настройках)) or "нет ни одной"))

    запись = в_настройках.get(ПЕРЕМЕННАЯ)
    if запись is None:
        беды.append(
            f"в настройках Production нет {ПЕРЕМЕННАЯ} — сайт отдаёт «нет связи с БД». "
            f"Cloudflare → Workers & Pages → {ПРОЕКТ} → Settings → "
            "Variables and Secrets → Production"
        )
    else:
        вид = запись.get("type") or "plain_text"
        print(f"{ПЕРЕМЕННАЯ} в настройках: есть, вид — {вид}")

    выкладки = _cf(
        f"/accounts/{счёт}/pages/projects/{ПРОЕКТ}/deployments"
        "?env=production&per_page=1",
        токен,
    )["result"]
    if not выкладки:
        беды.append("у проекта нет ни одной выкладки Production")
        последняя = {}
    else:
        последняя = выкладки[0]
        этап = (последняя.get("latest_stage") or {}).get("status") or "?"
        print(
            f"последняя выкладка Production: {последняя.get('short_id') or последняя.get('id')} "
            f"от {последняя.get('created_on')}, состояние — {этап}"
        )
        if этап != "success":
            беды.append(f"последняя выкладка Production не удалась (состояние {этап})")
        # У выкладки переменные лежат прямо в env_vars, а не в deployment_configs.
        в_выкладке = переменные(последняя)
        if последняя.get("env_vars") is None:
            print("у выкладки список переменных не отдан API — сверить с настройками нельзя")
        elif ПЕРЕМЕННАЯ not in в_выкладке:
            беды.append(
                f"последняя выкладка не несёт {ПЕРЕМЕННАЯ}: переменная привязывается "
                "к выкладке, поэтому после сохранения ключа нужна новая выкладка "
                "(Actions → «ZIP base deploy» → Run workflow)"
            )
        else:
            print(f"{ПЕРЕМЕННАЯ} в последней выкладке: есть")

    ключ = значение(запись or {})
    if ключ is None:
        print(
            "значение ключа API не отдаёт (заведён секретом) — проверены только "
            "наличие и привязка к выкладке, работоспособность ключа НЕ проверена"
        )
    else:
        код, подсказка = проверить_ключ(ключ)
        print(f"проба Supabase этим ключом: HTTP {код}" + (f" — {подсказка}" if подсказка else ""))
        if код != 200:
            беды.append(
                f"ключ из настроек не читает базу (HTTP {код}) — сайт покажет "
                "«нет связи с БД» даже после выкладки"
            )
        else:
            print("ключ читает базу — сайт увидит данные")

    print()
    if беды:
        for b in беды:
            print(f"::error::{b}")
        return 1
    print("✓ сайт ЗИП настроен на живую базу")
    return 0


if __name__ == "__main__":
    sys.exit(main())
