"""Клиент Bitrix24 REST поверх входящего вебхука.

Возможности:
  * устойчивые вызовы с ретраями и обработкой лимита запросов;
  * быстрая постраничная выгрузка больших списков (ID-based fast method);
  * кэш справочников: пользователи, стадии, воронки, enum-поля сделок.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
from typing import Any, Iterable

import requests


class BitrixError(RuntimeError):
    pass


class BitrixNetworkError(requests.RequestException):
    """Сетевой сбой вызова без адреса вебхука в тексте.

    Наследует RequestException, чтобы прежние `except requests.RequestException`
    ловили его как раньше."""


# Путь вебхука — это токен доступа ко всему CRM. Текст сетевого исключения
# requests его цитирует («Max retries exceeded with url: /rest/<id>/<токен>/…»),
# а журнал Actions публичен; GitHub прячет только строку секрета целиком, а не
# её части. Поэтому ни одно сообщение клиента не несёт пути, и main.py пропускает
# текст любой ошибки через ту же чистку. Найдено проверкой PR #402, 23.09.2026.
_ПУТЬ_ВЕБХУКА = re.compile(r"/rest/[^\s'\")]+")


def без_вебхука(text: Any) -> str:
    """Текст без пути вебхука: /rest/<id>/<токен>/… → /rest/***."""
    return _ПУТЬ_ВЕБХУКА.sub("/rest/***", str(text))


class _Снимок:
    """Ответ портала, прочитанный из снимка: ровно те поля, что читает клиент."""
    status_code = 200

    def __init__(self, data: Any):
        self._data = data

    def json(self) -> Any:
        return self._data

    @property
    def text(self) -> str:
        return json.dumps(self._data, ensure_ascii=False)


#: Коды ошибок Bitrix, которые лечатся повтором (транзиентные).
#: INTERNAL_SERVER_ERROR добавлен по разбору падения сборки 19.08.2026
#: (crm.activity.list уронил весь 12-минутный прогон деплоя).
RETRYABLE_BITRIX_ERRORS = frozenset({
    "QUERY_LIMIT_EXCEEDED",     # превышена частота запросов (ведро переполнено)
    "OPERATION_TIME_LIMIT",     # исчерпано время работы метода за 10 минут
    "INTERNAL_SERVER_ERROR",    # внутренняя ошибка портала
    "ERROR_CORE",               # ядро Bitrix отдало ошибку
    "OVERLOAD_LIMIT",           # портал перегружен
})
#: Отказы по лимитам портала: лечатся только временем и меряются бюджетом
#: ожидания, а не числом попыток.
ЛИМИТНЫЕ_ОШИБКИ = frozenset({"QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT", "OVERLOAD_LIMIT"})


# ─────────────────────────────────────────────────────────────────────────────
# БЮДЖЕТ ПОРТАЛА — ОДНА ТОЧКА ПРАВДЫ (CLAUDE.md, «Битрикс не перегружать»).
#
# Документация Битрикс24, «Ограничения REST API»
# (https://apidocs.bitrix24.ru/limits.html), сверено 24.09.2026. Лимитов ДВА,
# и оба считаются на вебхук в пределах портала, то есть общие для ВСЕХ наших
# прогонов — у них один секрет BITRIX_WEBHOOK_URL:
#
#   1. Частота — «дырявое ведро». Каждый запрос добавляет единицу в счётчик,
#      счётчик убывает на Y = 2 в секунду; при переполнении (X = 50, у
#      «Энтерпрайза» 250) запрос отбивается: HTTP 503, QUERY_LIMIT_EXCEEDED.
#      Устойчиво держится не больше двух запросов в секунду на ВЕСЬ портал.
#   2. Время работы метода. Накопленное время выполнения КАЖДОГО метода за
#      скользящие 10 минут не выше 420 с; сверх — HTTP 429, OPERATION_TIME_LIMIT,
#      и метод заблокирован, пока не выпадет старая минута. Ответ несёт
#      time.operating (накоплено) и time.operating_reset_at (когда выпадет
#      старейшая минута). Заголовка Retry-After документация не обещает.
#
# 24.09.2026 холостой переразбор сделок упал в 44 частях из 50 с HTTP 429 на
# crm.deal.list — это второй лимит, не первый: пятьдесят частей читали список
# смещением с подсчётом total, и время метода кончилось за две минуты. А клиент
# ждал не дольше минуты и падал раньше, чем метод разблокировался.
#
# Скачивание по urlMachine — тоже REST: ссылка ведёт на метод
# crm.controller.item.getFile того же вебхука. Значит, закачка файла — такой же
# запрос в ведро и в счёт времени метода, и идёт через ту же очередь.
ПОРТАЛ_УТЕЧКА_В_С = 2.0          # Y: на столько убывает счётчик ведра в секунду
ПОРТАЛ_ВЕДРО = 50                # X: порог ведра (не «Энтерпрайз»)
ПОРТАЛ_ВРЕМЯ_МЕТОДА_С = 420.0    # время метода за скользящие 10 минут
#: Запросов в секунду на ВЕСЬ портал по умолчанию: три четверти утечки ведра.
#: Запас — на прогоны, идущие одновременно (деплой дашборда раз в шесть часов,
#: ночной скан чатов), и на запросы людей из приложений портала.
RPS_ПО_УМОЛЧАНИЮ = 1.5
#: Доля времени метода, с которой клиент сам притормаживает, не дожидаясь 429.
ДОЛЯ_ВРЕМЕНИ_ТОРМОЗ = 0.6
#: Бюджет ожидания одного вызова на ЛИМИТАХ портала, секунд. Метод,
#: заблокированный по времени, освобождается до десяти минут; часть разбора,
#: которой некуда спешить, лучше прождёт полчаса, чем упадёт.
ЖДАТЬ_ЛИМИТ_С = 1800.0
#: Пауза на лимите: первая и наибольшая, секунд.
ЛИМИТ_ПАУЗА_С = 5.0
ЛИМИТ_ПАУЗА_МАКС_С = 180.0
#: Ожидание по operating_reset_at/Retry-After не длиннее десяти минут: дольше
#: время метода не держится по устройству лимита.
ЛИМИТ_ПАУЗА_ПО_ПОРТАЛУ_МАКС_С = 600.0


def _число(env: str, умолчание: float) -> float:
    try:
        return float(os.getenv(env) or умолчание)
    except ValueError:
        print(f"::warning::{env}={os.getenv(env)!r} не число — беру {умолчание}",
              file=sys.stderr, flush=True)
        return умолчание


def бюджет_портала() -> tuple[float, int]:
    """(запросов в секунду на весь портал, сколько наших процессов делят его).

    BITRIX_RPS — бюджет на портал, а не на процесс. BITRIX_PARALLEL — сколько
    процессов этого прогона читают портал одновременно: прогон частями передаёт
    min(частей, max-parallel). Бюджет выше утечки ведра не бывает — такое
    значение урезается ВСЛУХ, а не молча (правило «шаг не подменяет вход молча»).
    """
    rps = _число("BITRIX_RPS", RPS_ПО_УМОЛЧАНИЮ)
    if rps <= 0:
        rps = RPS_ПО_УМОЛЧАНИЮ
    if rps > ПОРТАЛ_УТЕЧКА_В_С:
        print(f"::warning::BITRIX_RPS={rps:g} выше утечки ведра портала "
              f"({ПОРТАЛ_УТЕЧКА_В_С:g}/с) — урезано до {ПОРТАЛ_УТЕЧКА_В_С:g}",
              file=sys.stderr, flush=True)
        rps = ПОРТАЛ_УТЕЧКА_В_С
    parallel = max(1, int(_число("BITRIX_PARALLEL", 1)))
    return rps, parallel


def интервал_портала(rps: float | None = None, parallel: int | None = None) -> float:
    """Пауза между запросами ОДНОГО процесса: parallel / rps.

    Двенадцать частей при бюджете 1,5 запроса в секунду — по восемь секунд между
    запросами каждой: вместе те же полтора в секунду, сколько бы частей ни шло.
    """
    if rps is None or parallel is None:
        r, p = бюджет_портала()
        rps = r if rps is None else rps
        parallel = p if parallel is None else parallel
    return max(1, int(parallel)) / float(rps)


# Нагрузка процесса на портал — для одной строки в журнале в конце прогона.
# Только агрегаты: числа запросов и отказов, ни адресов, ни имён (правило 17).
_НАГРУЗКА: dict[str, float] = {"запросов": 0, "файлов": 0, "отказ_503": 0,
                               "отказ_429": 0, "ждали_с": 0.0, "тормоз_времени": 0}
_НАГРУЗКА_ЗАМОК = threading.Lock()


def _учесть(ключ: str, на: float = 1) -> None:
    with _НАГРУЗКА_ЗАМОК:
        _НАГРУЗКА[ключ] = _НАГРУЗКА.get(ключ, 0) + на


def сводка_нагрузки() -> str:
    н = dict(_НАГРУЗКА)
    rps, par = бюджет_портала()
    return (f"портал: запросов {int(н['запросов'])} (из них файлов {int(н['файлов'])})"
            f" · отказов по частоте (503) {int(н['отказ_503'])}"
            f" · по времени метода (429) {int(н['отказ_429'])}"
            f" · торможений по времени метода {int(н['тормоз_времени'])}"
            f" · ждали {н['ждали_с']:.0f} с"
            f" · бюджет {rps:g}/с на портал, процессов {par}")


@atexit.register
def _сводка_при_выходе() -> None:
    # Печатается и у упавшего прогона: сколько отказов он видел перед смертью —
    # ровно то, что нужно разбору падения.
    if _НАГРУЗКА["запросов"]:
        print(сводка_нагрузки(), file=sys.stderr, flush=True)


class BitrixLimitError(BitrixError):
    """Портал держал лимит дольше бюджета ожидания."""


class BitrixClient:
    def __init__(self, webhook_url: str, *, timeout: int = 30, min_interval: float | None = None,
                 retries: int | None = None, backoff_base: float = 0.5, backoff_max: float = 32.0):
        self.base = webhook_url.rstrip("/") + "/"
        self.timeout = timeout
        # Пауза между запросами — из общего бюджета портала, а не своим числом:
        # у каждого прогона своё число давало сумму выше лимита (CLAUDE.md,
        # «Битрикс не перегружать»). Явное значение оставлено для проверок.
        self.min_interval = интервал_портала() if min_interval is None else min_interval
        self.retries = int(os.getenv("BITRIX_RETRIES") or retries or 6)
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.limit_wait_budget = _число("BITRIX_WAIT_BUDGET", ЖДАТЬ_ЛИМИТ_С)
        self.limit_pause = ЛИМИТ_ПАУЗА_С
        self.limit_pause_max = ЛИМИТ_ПАУЗА_МАКС_С
        self.retry_count = 0              # счётчик повторов за прогон (для сводки сборки)
        # Замедление после отказа по лимиту: пауза между запросами умножается,
        # затем плавно возвращается. Так два прогона, случайно сошедшиеся на
        # портале, сами расходятся по бюджету, не зная друг о друге.
        self._замедление = 1.0
        self._session = requests.Session()
        self._lock = threading.Lock()
        self._last_call = 0.0
        # ленивые кэши
        self._users: dict[str, str] | None = None
        self._stages: dict[str, str] | None = None
        self._categories: dict[str, str] | None = None
        self._uf: dict[str, dict] | None = None
        self._departments: list[dict] | None = None
        self._user_depts: dict[str, str] | None = None
        # Снимок портала для живой сверки (.github/workflows/live-check.yml):
        # сборка прода записывает ответы, сборка правки читает их же, и обе
        # считаются на одних данных. Без снимка вторая сборка шла минутами позже,
        # и карточка, переназначенная за это время, выглядела сдвигом от правки.
        # Промах (правка спросила то, чего прод не спрашивал) идёт в портал живьём
        # и считается: по счётчику видно, была ли сверка точной.
        self.snapshot_dir = os.getenv("BITRIX_SNAPSHOT_DIR") or ""
        self.snapshot_mode = (os.getenv("BITRIX_SNAPSHOT_MODE") or "").strip().lower()
        self.snapshot_saved = self.snapshot_hits = self.snapshot_misses = 0

    def snapshot_stats(self) -> dict:
        return {"mode": self.snapshot_mode if self.snapshot_dir else "",
                "saved": self.snapshot_saved, "hits": self.snapshot_hits,
                "misses": self.snapshot_misses}

    def _post(self, method: str, payload: dict):
        """Один HTTP-вызов метода — единственное место, где клиент ходит в сеть.
        Здесь снимок и здесь же сетевая ошибка теряет адрес вебхука."""
        key = ""
        if self.snapshot_dir and self.snapshot_mode in ("record", "replay"):
            raw = json.dumps([method, payload], sort_keys=True, ensure_ascii=False, default=str)
            key = os.path.join(self.snapshot_dir, hashlib.sha256(raw.encode("utf-8")).hexdigest() + ".json")
            if self.snapshot_mode == "replay":
                try:
                    with open(key, encoding="utf-8") as fh:
                        data = json.load(fh)
                    self.snapshot_hits += 1
                    return _Снимок(data)
                except FileNotFoundError:
                    self.snapshot_misses += 1
        self._throttle()
        _учесть("запросов")
        try:
            r = self._session.post(self.base + method + ".json", json=payload, timeout=self.timeout)
        except requests.RequestException as e:
            raise BitrixNetworkError(f"{method}: сеть: {e.__class__.__name__}") from None
        if key and self.snapshot_mode == "record" and r.status_code == 200:
            try:
                data = r.json()
            except ValueError:
                data = None
            # пишется только годный ответ: сбой портала в снимке повторился бы
            # в сборке правки и выглядел бы её ошибкой
            if isinstance(data, (dict, list)) and not (isinstance(data, dict) and data.get("error")):
                os.makedirs(self.snapshot_dir, exist_ok=True)
                with open(key + ".tmp", "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False)
                os.replace(key + ".tmp", key)
                self.snapshot_saved += 1
        return r

    # ----------------------------------------------------------------- low level
    def call_envelope(self, method: str, params: dict | None = None, *, retries: int | None = None) -> dict:
        """Полный ответ Bitrix ({result, next, total}) с повторами.

        Сбои двух родов, и счёт у них разный:
          * ЛИМИТЫ портала — HTTP 429 и 503, QUERY_LIMIT_EXCEEDED,
            OPERATION_TIME_LIMIT, OVERLOAD_LIMIT. Лечатся только временем, поэтому
            меряются бюджетом ОЖИДАНИЯ (BITRIX_WAIT_BUDGET, по умолчанию 30 мин),
            а не числом попыток: пауза растёт 5 → 180 с, а у блокировки по времени
            метода — до отметки operating_reset_at из ответа;
          * прочие сбои — сеть, 5xx, не-JSON, внутренние ошибки портала —
            прежние `retries` попыток с паузой 0,5 → 32 с.
        """
        retries = self.retries if retries is None else retries
        payload = params or {}
        last_err: Exception | None = None
        попытка = 0          # прочие сбои
        лимит = 0            # отказы по лимиту
        ждали = 0.0          # сколько этот вызов уже прождал на лимитах
        while попытка < retries:
            try:
                r = self._post(method, payload)
            except requests.RequestException as e:                      # сеть/таймаут
                last_err = e
                self._backoff(попытка, method, без_вебхука(e).removeprefix(f"{method}: "))
                попытка += 1
                continue
            try:
                data = r.json()
            except ValueError:                                          # HTML вместо JSON
                data = None
            err = str(data.get("error") or "") if isinstance(data, dict) else ""
            if r.status_code in (429, 503) or err in ЛИМИТНЫЕ_ОШИБКИ:
                причина = f"HTTP {r.status_code}" + (f" {err}" if err else "")
                last_err = BitrixLimitError(f"{method}: {причина}")
                пауза = self.limit_delay(лимит, r, data)
                if ждали + пауза > self.limit_wait_budget:
                    raise BitrixLimitError(
                        f"{method}: портал держит лимит дольше бюджета ожидания "
                        f"{self.limit_wait_budget:.0f} с ({причина}, отказов {лимит + 1})")
                self._limit_sleep(пауза, method, причина, r.status_code, err)
                ждали += пауза
                лимит += 1
                continue
            if r.status_code >= 500:
                last_err = BitrixError(f"{method}: HTTP {r.status_code}")
                self._backoff(попытка, method, f"HTTP {r.status_code}")
                попытка += 1
                continue
            if data is None:
                last_err = BitrixError(f"{method}: не JSON-ответ (HTTP {r.status_code}): {r.text[:200]}")
                self._backoff(попытка, method, f"не JSON (HTTP {r.status_code})")
                попытка += 1
                continue
            if err:
                desc = data.get("error_description", "")
                if err in RETRYABLE_BITRIX_ERRORS:
                    last_err = BitrixError(f"{method}: {err} {desc}")
                    self._backoff(попытка, method, err)
                    попытка += 1
                    continue
                raise BitrixError(f"{method}: {err} {desc}")            # неустранимая ошибка
            self._после_успеха(r, data)
            return data if isinstance(data, dict) else {"result": data}
        raise BitrixError(f"{method}: не удалось выполнить за {retries} попыток ({без_вебхука(last_err)})")

    def call(self, method: str, params: dict | None = None, *, retries: int | None = None) -> Any:
        data = self.call_envelope(method, params, retries=retries)
        return data.get("result")

    # ----------------------------------------------------------------- лимиты
    def limit_delay(self, n: int, r: Any = None, data: Any = None) -> float:
        """Пауза перед повтором после n-го отказа по лимиту, секунд.

        Растёт от ЛИМИТ_ПАУЗА_С вдвое до ЛИМИТ_ПАУЗА_МАКС_С. Если портал сказал,
        когда станет можно (Retry-After или time.operating_reset_at у блокировки
        по времени метода), ждём до этой отметки, но не дольше десяти минут.
        """
        пауза = min(self.limit_pause * (2 ** n), self.limit_pause_max)
        пауза *= 0.75 + random.random() * 0.5                          # джиттер ±25 %
        заголовки = getattr(r, "headers", None) or {}
        try:
            ra = float(заголовки.get("Retry-After") or 0)
        except (TypeError, ValueError, AttributeError):
            ra = 0.0
        сброс = 0.0
        if isinstance(data, dict):
            t = data.get("time") if isinstance(data.get("time"), dict) else {}
            try:
                сброс = float(t.get("operating_reset_at") or 0) - time.time()
            except (TypeError, ValueError):
                сброс = 0.0
        по_порталу = max(ra, сброс + 1 if сброс > 0 else 0)
        if по_порталу > 0:
            пауза = max(пауза, min(по_порталу, ЛИМИТ_ПАУЗА_ПО_ПОРТАЛУ_МАКС_С))
        return пауза

    def _limit_sleep(self, пауза: float, method: str, причина: str, код: int, err: str) -> None:
        self.retry_count += 1
        _учесть("отказ_429" if (код == 429 or err == "OPERATION_TIME_LIMIT") else "отказ_503")
        _учесть("ждали_с", пауза)
        with self._lock:
            self._замедление = min(self._замедление * 2, 16.0)
        print(f"  ⏸ лимит портала {method}: {причина} — пауза {пауза:.0f} с",
              file=sys.stderr, flush=True)
        time.sleep(пауза)

    def wait_limit(self, n: int, ждали: float, method: str, r: Any = None) -> float | None:
        """Для чужого цикла повторов (закачка файла): пауза по n-му отказу.

        Возвращает сколько прождано, или None, если бюджет ожидания исчерпан и
        ждать дальше не надо.
        """
        пауза = self.limit_delay(n, r)
        if ждали + пауза > self.limit_wait_budget:
            return None
        код = int(getattr(r, "status_code", 0) or 0)
        self._limit_sleep(пауза, method, f"HTTP {код}", код, "")
        return пауза

    def before_request(self, kind: str = "файл") -> None:
        """Очередь и учёт для запроса мимо _post — закачки по urlMachine.

        urlMachine — это REST-метод crm.controller.item.getFile того же
        вебхука: закачка стоит в той же очереди, что и вызовы методов."""
        self._throttle()
        _учесть("запросов")
        if kind == "файл":
            _учесть("файлов")

    def _после_успеха(self, r: Any, data: Any) -> None:
        """Плавный возврат скорости и тормоз по времени метода до отказа.

        time.operating — накопленное время метода за 10 минут, общее у всех наших
        процессов на этом вебхуке. Каждый процесс видит его в своём ответе и
        притормаживает сам: согласования между частями не нужно."""
        with self._lock:
            self._замедление = max(1.0, self._замедление * 0.95)
        if isinstance(r, _Снимок) or not isinstance(data, dict):
            return                          # ответ из снимка: портал не трогали
        t = data.get("time")
        if not isinstance(t, dict):
            return
        try:
            накоплено = float(t.get("operating") or 0)
            сброс = float(t.get("operating_reset_at") or 0) - time.time()
        except (TypeError, ValueError):
            return
        доля = накоплено / ПОРТАЛ_ВРЕМЯ_МЕТОДА_С
        if доля < ДОЛЯ_ВРЕМЕНИ_ТОРМОЗ or сброс <= 0:
            return
        # чем ближе к пределу, тем дольше: с 60 % — до минуты, с 85 % — до сброса
        пауза = min(сброс + 1, 60.0 if доля < 0.85 else ЛИМИТ_ПАУЗА_ПО_ПОРТАЛУ_МАКС_С)
        _учесть("тормоз_времени")
        _учесть("ждали_с", пауза)
        print(f"  ⏸ время метода {доля:.0%} от предела портала — пауза {пауза:.0f} с",
              file=sys.stderr, flush=True)
        time.sleep(пауза)

    def _backoff(self, attempt: int, method: str, reason: str) -> None:
        """Экспоненциальная пауза с джиттером; каждый повтор виден в логах CI."""
        delay = min(self.backoff_base * (2 ** attempt), self.backoff_max)
        delay *= 0.75 + random.random() * 0.5                            # джиттер ±25 %
        self.retry_count += 1
        print(f"  ↻ повтор {method}: {reason} — пауза {delay:.1f} с", file=sys.stderr, flush=True)
        time.sleep(delay)

    def _throttle(self) -> None:
        with self._lock:
            интервал = self.min_interval * self._замедление
            dt = time.monotonic() - self._last_call
            if dt < интервал:
                time.sleep(интервал - dt)
            self._last_call = time.monotonic()

    # ----------------------------------------------------------------- listing
    def list_paged(self, method: str, params: dict | None = None, *, max_items: int | None = None) -> list[dict]:
        """Классическая постраничная выгрузка (start += 50)."""
        params = dict(params or {})
        out: list[dict] = []
        start = 0
        while True:
            params["start"] = start
            # через call_envelope: лимиты портала пережидаются, как у всех вызовов
            data = self.call_envelope(method, params)
            chunk = data.get("result") or []
            if isinstance(chunk, dict):  # некоторые методы возвращают dict
                chunk = list(chunk.values())
            out.extend(chunk)
            if max_items and len(out) >= max_items:
                return out[:max_items]
            nxt = data.get("next")
            if not nxt:
                break
            start = nxt
        return out

    def list_deals_fast(
        self,
        *,
        filter: dict | None = None,
        select: Iterable[str] | None = None,
        order_field: str = "ID",
        max_items: int | None = None,
    ) -> list[dict]:
        """Быстрая выгрузка сделок методом ID > last_id, start=-1 (без подсчёта total)."""
        select = list(select or ["*", "UF_*"])
        base_filter = dict(filter or {})
        out: list[dict] = []
        last_id = 0
        while True:
            f = dict(base_filter)
            f[">ID"] = last_id
            params = {"order": {"ID": "ASC"}, "filter": f, "select": select, "start": -1}
            chunk = self.call("crm.deal.list", params) or []
            if not chunk:
                break
            out.extend(chunk)
            last_id = int(chunk[-1]["ID"])
            if max_items and len(out) >= max_items:
                return out[:max_items]
            if len(chunk) < 50:
                break
        return out

    def count_deals(self, filter: dict | None = None) -> int:
        return self.count("crm.deal.list", filter)

    def count(self, method: str, filter: dict | None = None) -> int:
        """Общее число записей list-метода: читает поле total из ответа.
        (call() возвращает только result-массив без total, поэтому считаем отдельным сырым запросом.)"""
        # Прежде — свои четыре повтора по 0,5–2,8 с против лимита, который держится
        # до десяти минут. Теперь общий путь; исчерпав его, как и прежде, ноль.
        try:
            data = self.call_envelope(method, {"filter": filter or {}, "select": ["ID"], "start": 0})
        except (BitrixError, requests.RequestException):
            return 0
        return int((data or {}).get("total") or 0)

    def stage_first_entry(self, entity_type_id: int, category_id: int, since: str) -> dict[str, str]:
        """Момент ПЕРВОГО входа сущности в указанную воронку (category_id) — из истории стадий.
        crm.stagehistory.list возвращает result={items:[...]} + next; пагинируем сами.
        Для сделок (entityTypeId=2) воронка 0 — это воронка реализации (победа).
        Возвращает {OWNER_ID(str): дата YYYY-MM-DD первого входа}, начиная с even `since`."""
        params = {
            "entityTypeId": entity_type_id,
            "filter": {"CATEGORY_ID": category_id, ">=CREATED_TIME": since},
            "select": ["OWNER_ID", "CREATED_TIME", "STAGE_ID"],
            "order": {"CREATED_TIME": "ASC"},
        }
        first: dict[str, str] = {}
        start = 0
        while True:
            params["start"] = start
            # через call_envelope: ретраи транзиентных сбоев + сохранение поля next.
            # Раньше сетевая ошибка делала break и молча возвращала частичный результат.
            data = self.call_envelope("crm.stagehistory.list", params)
            res = (data or {}).get("result") or {}
            items = res.get("items") if isinstance(res, dict) else res
            items = items or []
            for x in items:                       # ASC по CREATED_TIME → первый встреченный = самый ранний
                did = str(x.get("OWNER_ID"))
                if did and did not in first:
                    first[did] = str(x.get("CREATED_TIME"))[:10]
            nxt = data.get("next")
            if not nxt or not items:
                break
            start = nxt
        return first

    def stage_history(self, entity_type_id: int, *, category_id: int | None = None,
                      since: str | None = None) -> dict[str, list[tuple[str, str]]]:
        """Полная история стадий: {OWNER_ID: [(STAGE_ID, CREATED_TIME), …]} — ПЕРВЫЙ вход
        в каждую стадию, с полным временем (ISO), в хронологическом порядке.
        Для замера скорости переходов (сделки кат.0, заказы СП-172 и т.п.)."""
        params: dict = {
            "entityTypeId": entity_type_id,
            "select": ["OWNER_ID", "CREATED_TIME", "STAGE_ID"],
            "order": {"CREATED_TIME": "ASC"},
            "filter": {},
        }
        if category_id is not None:
            params["filter"]["CATEGORY_ID"] = category_id
        if since:
            params["filter"][">=CREATED_TIME"] = since
        hist: dict[str, list[tuple[str, str]]] = {}
        seen: dict[str, set] = {}
        start = 0
        while True:
            params["start"] = start
            # общий путь повторов: прежний цикл долбил лимит каждые 0,7 с без конца
            data = self.call_envelope("crm.stagehistory.list", params)
            res = (data or {}).get("result") or {}
            items = res.get("items") if isinstance(res, dict) else res
            items = items or []
            for x in items:
                oid = str(x.get("OWNER_ID"))
                st = str(x.get("STAGE_ID") or "")
                if not oid or not st:
                    continue
                if st not in seen.setdefault(oid, set()):   # ASC → первый встреченный вход в стадию
                    seen[oid].add(st)
                    hist.setdefault(oid, []).append((st, str(x.get("CREATED_TIME") or "")))
            nxt = data.get("next")
            if not nxt or not items:
                break
            start = nxt
        return hist

    def deal_stages_cat(self, category_id: int = 0) -> dict[str, str]:
        """Упорядоченный (по SORT) справочник стадий воронки сделок категории:
        {STAGE_ID: имя}. Для кат.0 ENTITY_ID='DEAL_STAGE', иначе 'DEAL_STAGE_<cat>'."""
        ent = "DEAL_STAGE" if int(category_id) == 0 else f"DEAL_STAGE_{category_id}"
        m: dict[str, str] = {}
        for s in self.list_paged("crm.status.list", {"filter": {"ENTITY_ID": ent}, "order": {"SORT": "ASC"}}):
            m[s["STATUS_ID"]] = s.get("NAME") or s["STATUS_ID"]
        return m

    def deal_stages_process(self, category_id: int = 0) -> dict[str, str]:
        """Только РАБОЧИЕ стадии воронки (без успеха и без причин проигрыша).

        В кат.0 после «Сделка успешна» идут корзины проигрыша — «Политика»,
        «Не прошли по цене», «Проблемы с документами» и т.п. Это не шаги процесса,
        и в измерениях скорости они дают бессмысленные медианы."""
        ent = "DEAL_STAGE" if int(category_id) == 0 else f"DEAL_STAGE_{category_id}"
        m: dict[str, str] = {}
        for s in self.list_paged("crm.status.list", {"filter": {"ENTITY_ID": ent}, "order": {"SORT": "ASC"}}):
            if str(s.get("SEMANTICS") or "").upper() in ("F", "S"):
                continue
            m[s["STATUS_ID"]] = s.get("NAME") or s["STATUS_ID"]
        return m

    def deal_stage_meta(self) -> dict[str, dict]:
        """Все стадии всех воронок сделок: {STAGE_ID: {name, sem, sort, cat}}.

        `sem` — семантика стадии: 'F' проигрыш, 'S' успех, 'P' в работе. Прежние
        методы её выбрасывали, а для разбора проигрышей она и есть главное: имена
        F-стадий («Не прошли по цене», «Пост-щик не ответил») — единственная
        причина проигрыша, которую портал хранит машинно, отдельного поля нет.
        `sort` даёт порядок стадии в воронке, то есть глубину отвала без истории.
        """
        m: dict[str, dict] = {}
        for s in self.list_paged("crm.status.list", {"order": {"SORT": "ASC"}}):
            ent = str(s.get("ENTITY_ID") or "")
            if not ent.startswith("DEAL_STAGE"):
                continue
            cat = ent.split("_")[-1] if ent != "DEAL_STAGE" else "0"
            m[s["STATUS_ID"]] = {
                "name": s.get("NAME") or s["STATUS_ID"],
                "sem": str(s.get("SEMANTICS") or "P").upper(),
                "sort": int(s.get("SORT") or 0),
                "cat": cat,
            }
        return m

    # ----------------------------------------------------------------- smart-process items
    def list_items(
        self,
        entity_type_id: int,
        *,
        filter: dict | None = None,
        select: Iterable[str] | None = None,
        max_items: int | None = None,
    ) -> list[dict]:
        """Быстрая выгрузка записей смарт-процесса (crm.item.list) методом id > last_id."""
        select = list(select or ["*"])
        base = dict(filter or {})
        out: list[dict] = []
        last = 0
        while True:
            f = dict(base)
            f[">id"] = last
            res = self.call(
                "crm.item.list",
                {"entityTypeId": entity_type_id, "order": {"id": "ASC"}, "filter": f, "select": select, "start": -1},
            )
            items = (res or {}).get("items", []) if isinstance(res, dict) else []
            if not items:
                break
            out.extend(items)
            last = int(items[-1]["id"])
            if max_items and len(out) >= max_items:
                return out[:max_items]
            if len(items) < 50:
                break
        return out

    def spa_stages(self, entity_type_id: int, category_id: int) -> dict[str, str]:
        """{stageId: name} для воронки смарт-процесса."""
        ent = f"DYNAMIC_{entity_type_id}_STAGE_{category_id}"
        m: dict[str, str] = {}
        for s in self.list_paged("crm.status.list", {"filter": {"ENTITY_ID": ent}, "order": {"SORT": "ASC"}}):
            m[s["STATUS_ID"]] = s.get("NAME") or s["STATUS_ID"]
        return m

    # ----------------------------------------------------------------- departments
    def departments(self) -> list[dict]:
        if self._departments is None:
            self._departments = self.list_paged("department.get", {})
        return self._departments

    def dept_member_ids(self, dept_id: int | str, *, include_children: bool = True) -> set[str]:
        """ID пользователей отдела (по UF_DEPARTMENT), включая дочерние отделы."""
        deps = self.departments()
        ids = {str(dept_id)}
        if include_children:
            changed = True
            while changed:
                changed = False
                for d in deps:
                    if str(d.get("PARENT")) in ids and str(d["ID"]) not in ids:
                        ids.add(str(d["ID"]))
                        changed = True
        members: set[str] = set()
        for did in ids:
            for u in self.list_paged("user.get", {"FILTER": {"UF_DEPARTMENT": int(did)}}):
                members.add(str(u["ID"]))
        return members

    # ----------------------------------------------------------------- deals (для покрытия и цепочки ТКП)
    def deals_in_period(self, date_from: str, date_to: str, *, select: Iterable[str] | None = None) -> list[dict]:
        select = list(select or ["ID", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "DATE_CREATE", "ASSIGNED_BY_ID"])
        return self.list_deals_fast(
            filter={">=DATE_CREATE": date_from, "<=DATE_CREATE": date_to}, select=select
        )

    def deals_by_ids(self, ids: Iterable, *, select: Iterable[str] | None = None, chunk: int = 50) -> dict[str, dict]:
        select = list(select or ["ID", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID"])
        clean = [int(i) for i in ids if i]
        out: dict[str, dict] = {}
        for i in range(0, len(clean), chunk):
            part = clean[i : i + chunk]
            res = self.call("crm.deal.list", {"filter": {"@ID": part}, "select": select, "start": -1}) or []
            for d in res:
                out[str(d["ID"])] = d
        return out

    def companies_by_ids(self, ids: Iterable, *, chunk: int = 50) -> dict[str, str]:
        clean = sorted({int(i) for i in ids if str(i).isdigit()})
        out: dict[str, str] = {}
        for i in range(0, len(clean), chunk):
            part = clean[i : i + chunk]
            res = self.call("crm.company.list", {"filter": {"@ID": part}, "select": ["ID", "TITLE"], "start": -1}) or []
            for c in res:
                out[str(c["ID"])] = c.get("TITLE") or f"company#{c['ID']}"
        return out

    def contacts_by_ids(self, ids: Iterable, *, chunk: int = 50) -> dict[str, str]:
        clean = sorted({int(i) for i in ids if str(i).isdigit()})
        out: dict[str, str] = {}
        for i in range(0, len(clean), chunk):
            part = clean[i : i + chunk]
            res = self.call("crm.contact.list", {"filter": {"@ID": part}, "select": ["ID", "NAME", "LAST_NAME"], "start": -1}) or []
            for c in res:
                nm = " ".join(x for x in [c.get("NAME"), c.get("LAST_NAME")] if x).strip()
                out[str(c["ID"])] = nm or f"contact#{c['ID']}"
        return out

    # ----------------------------------------------------------------- per-deal data
    def get_comments(self, deal_id: int | str) -> list[dict]:
        return self.list_paged(
            "crm.timeline.comment.list",
            {"filter": {"ENTITY_ID": int(deal_id), "ENTITY_TYPE": "deal"}, "order": {"CREATED": "ASC"}},
        )

    def get_activities(self, deal_id: int | str) -> list[dict]:
        return self.list_paged(
            "crm.activity.list",
            {
                "filter": {"OWNER_TYPE_ID": 2, "OWNER_ID": int(deal_id)},
                "order": {"CREATED": "ASC"},
                "select": ["ID", "SUBJECT", "TYPE_ID", "DESCRIPTION", "DIRECTION", "COMPLETED", "CREATED"],
            },
        )

    def get_product_rows(self, deal_id: int | str) -> list[dict]:
        return self.call("crm.deal.productrows.get", {"id": int(deal_id)}) or []

    # ----------------------------------------------------------------- deal chat (IM)
    def deal_chat_id(self, deal_id: int | str) -> str | None:
        """ID IM-чата сделки (entity_type=CRM, entity_id=DEAL|<id>)."""
        ch = self.call("im.chat.get", {"ENTITY_TYPE": "CRM", "ENTITY_ID": f"DEAL|{deal_id}"})
        if isinstance(ch, dict):
            return str(ch.get("ID") or ch.get("id") or "") or None
        return None

    def chat_messages(self, chat_id: str | int, *, limit: int = 50) -> list[dict]:
        r = self.call("im.dialog.messages.get", {"DIALOG_ID": f"chat{chat_id}", "LIMIT": limit})
        return (r or {}).get("messages", []) if isinstance(r, dict) else []

    def deal_chat_messages(self, deal_id: int | str, *, limit: int = 50) -> list[dict]:
        """Сообщения чата сделки (как есть; author_id<=0 — системные/боты)."""
        cid = self.deal_chat_id(deal_id)
        return self.chat_messages(cid, limit=limit) if cid else []

    # ----------------------------------------------------------------- reference maps (cached)
    def users(self) -> dict[str, str]:
        if self._users is None:
            self._load_users()
        return self._users

    def user_dept_names(self) -> dict[str, str]:
        """Пользователь → название его подразделения.

        Нужно, чтобы отличать запросы, заведённые сорсингом, от заведённых кем-то
        ещё: одного признака «в отделе 172 или нет» мало — владельцу нужно видеть,
        какое именно подразделение грузит очередь. Если человек числится в нескольких
        подразделениях, берём первое: в портале это основное место работы.
        """
        if self._user_depts is None:
            self._load_users()
        return self._user_depts

    def _load_users(self) -> None:
        """Одна выгрузка user.get на обе карты: имена и подразделения."""
        dep_names = {str(d.get("ID")): str(d.get("NAME") or f"подразделение #{d.get('ID')}")
                     for d in self.departments()}
        names: dict[str, str] = {}
        depts: dict[str, str] = {}
        for u in self.list_paged("user.get", {}):
            uid = str(u.get("ID"))
            name = " ".join(x for x in [u.get("NAME"), u.get("LAST_NAME")] if x).strip() or f"user#{uid}"
            pos = u.get("WORK_POSITION")
            names[uid] = f"{name} ({pos})" if pos else name
            raw = u.get("UF_DEPARTMENT") or []
            if not isinstance(raw, list):
                raw = [raw]
            got = [dep_names[str(x)] for x in raw if str(x) in dep_names]
            depts[uid] = got[0] if got else ""
        self._users = names
        self._user_depts = depts

    def user_name(self, uid: Any) -> str:
        if uid in (None, "", 0, "0"):
            return ""
        return self.users().get(str(uid), f"user#{uid}")

    def categories(self) -> dict[str, str]:
        if self._categories is None:
            m = {"0": "Общая"}
            for c in self.call("crm.dealcategory.list", {"select": ["ID", "NAME"]}) or []:
                m[str(c["ID"])] = c.get("NAME") or f"cat#{c['ID']}"
            self._categories = m
        return self._categories

    def stages(self) -> dict[str, str]:
        if self._stages is None:
            m: dict[str, str] = {}
            for s in self.list_paged("crm.status.list", {"order": {"SORT": "ASC"}}):
                if str(s.get("ENTITY_ID", "")).startswith("DEAL_STAGE"):
                    m[s["STATUS_ID"]] = s.get("NAME") or s["STATUS_ID"]
            self._stages = m
        return self._stages

    def userfields(self) -> dict[str, dict]:
        """{FIELD_NAME: {"type": USER_TYPE_ID, "enum": {item_id: value}}}"""
        if self._uf is None:
            m: dict[str, dict] = {}
            for f in self.list_paged("crm.deal.userfield.list", {"order": {"ID": "ASC"}}):
                name = f.get("FIELD_NAME")
                if not name:
                    continue
                enum = {str(i["ID"]): i.get("VALUE") for i in (f.get("LIST") or [])}
                m[name] = {"type": f.get("USER_TYPE_ID"), "enum": enum}
            self._uf = m
        return self._uf

    def warm_reference_caches(self) -> None:
        self.users(); self.categories(); self.stages(); self.userfields()
