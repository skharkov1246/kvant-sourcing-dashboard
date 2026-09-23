"""Таблица со скана по координатам слов: колонки не сливаются, строки не рвутся.

ЗАЧЕМ. Распознавание отдавало плоский текст, и таблица со скана теряла колонки:
наименование, количество, цена и сумма шли одной строкой, а цена бралась только
арифметикой. library/ocr_table.py восстанавливает колонки по координатам слов из
вывода `tesseract … tsv`.

Главные тесты — на разборе TSV: вывод tesseract здесь придуман руками, это текст,
и tesseract для них не нужен. Ловушки, каждая из которых проверена отдельно:

1. Автоматический режим (--psm 3 и 1) выдаёт таблицу КОЛОНКА ЗА КОЛОНКОЙ, и
   строки tesseract — не строки таблицы.
2. Перекос листа: строка шириной 1300 px уходит вниз больше высоты слова.
3. Пробел после одинакового первого слова («Подшипник …» во всех строках)
   стоит на одном месте и притворяется жёлобом.
4. Пробел в тысячах («12 400,00») — не граница колонки.
5. Проза поперёк таблицы режется в обрывки, если её резать по колонкам.
6. Цена по центру ячейки в две строки перекрывает обе строки наименования.
7. Лист на боку: tesseract отдаёт координаты в кадре исходной картинки.

Тесты, которым нужны tesseract и poppler, пропускаются без них. Корпуса придуманы
(CLAUDE.md, правило 18).
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "library"))

import ocr_table  # noqa: E402

# ---------------------------------------------------------------- придуманный TSV
#
# Размеры — как у tesseract на листе 200 dpi: рамка слова 20 px, знак 11 px,
# пробел 10 px (0,5 высоты — так в замере на настоящем выводе).
Н, ЗНАК, ПРОБЕЛ = 20, 11, 10


def ширина(текст: str) -> int:
    части = текст.split(" ")
    return sum(ЗНАК * len(ч) for ч in части) + ПРОБЕЛ * (len(части) - 1)


def слова(текст: str, x: float, y: float, наклон: float = 0.0) -> list[list]:
    """Ячейка → слова [x, y, w, h, текст]; наклон — сдвиг вниз на пиксель ширины."""
    out = []
    for ч in текст.split(" "):
        w = ЗНАК * len(ч)
        out.append([x, y + x * наклон, w, Н, ч])
        x += w + ПРОБЕЛ
    return out


# Колонки: (левый край или правый край, выравнивание). Числа — вправо, как в КП.
КОЛОНКИ = [(100, "l"), (160, "l"), (560, "l"), (860, "r"), (900, "l"), (1180, "r"),
           (1420, "r")]
ШАПКА = ("№", "Наименование", "Артикул", "Кол-во", "Ед.", "Цена, руб.", "Сумма, руб.")
ПОЗИЦИИ = [
    ("1", "Подшипник роликовый", "22315 EK", "4", "шт", "312,50", "1 250,00"),
    ("2", "Уплотнение торцевое", "TRZ-4471/2", "10", "шт", "85,00", "850,00"),
    ("3", "Кольцо дистанционное", "6205-2RS", "2", "шт", "47,25", "94,50"),
    ("4", "Вал ротора в сборе", "NM-125/07", "1", "шт", "12 400,00", "12 400,00"),
]
ЗАГОЛОВОК = "КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ № 114 от 18.09.2026"
ПЕРЕНОС = "с муфтой"
УСЛОВИЯ = "Условия поставки: DAP Москва. Оплата 100% предоплата."


def ячейки_строки(значения, колонки=КОЛОНКИ) -> list[tuple[float, str]]:
    out = []
    for (край, выр), текст in zip(колонки, значения):
        if текст:
            out.append((край - ширина(текст) if выр == "r" else край, текст))
    return out


def лист(позиции=ПОЗИЦИИ, шапка=ШАПКА, колонки=КОЛОНКИ) -> list[tuple[float, list]]:
    """Видимые строки листа: (y, [(x, текст ячейки), …])."""
    строки = [(100, [(100, ЗАГОЛОВОК)]), (200, ячейки_строки(шапка, колонки))]
    y = 240
    for п in позиции:
        строки.append((y, ячейки_строки(п, колонки)))
        y += 40
    строки.append((y - 16, [(160, ПЕРЕНОС)]))       # перенос наименования последней позиции
    строки.append((y + 40, [(100, УСЛОВИЯ)]))
    return строки


ОЖИДАНИЕ = ([[ЗАГОЛОВОК] + [""] * 6, list(ШАПКА)] + [list(п) for п in ПОЗИЦИИ]
            + [["", ПЕРЕНОС, "", "", "", "", ""], [УСЛОВИЯ] + [""] * 6])


def в_tsv(строки, режим: str = "6", наклон: float = 0.0, conf: dict | None = None,
          поворот: int = 0, W: int = 1600, H: int = 1000, с_шапкой: bool = True,
          со_страницей: bool = True) -> str:
    """Видимые строки → вывод tesseract tsv.

    режим "6": строка листа — строка tesseract (блочный режим).
    режим "3": каждая ячейка — свой блок, и вывод идёт колонка за колонкой, как в
    автоматическом режиме: сначала все номера, потом все наименования.
    поворот: кадр картинки, в котором tesseract --psm 1 отдаёт координаты листа,
    лежащего на боку (ниже — обратное преобразование, выписанное независимо от
    модуля)."""
    conf = conf or {}
    записи = []                                    # (блок, строка, слова)
    for i, (y, ячейки) in enumerate(строки):
        if режим == "6":
            записи.append((1, i + 1, [с for x, т in ячейки for с in слова(т, x, y, наклон)]))
        else:
            for j, (x, т) in enumerate(ячейки):
                блок = j + 1 if len(ячейки) > 1 else 50 + i
                записи.append((блок, i + 1, слова(т, x, y, наклон)))
    записи.sort(key=lambda з: (з[0], з[1]))
    iw, ih = (H, W) if поворот in (90, 270) else (W, H)
    out = []
    имена = ocr_table.ИМЕНА if со_страницей else ocr_table.БЕЗ_СТРАНИЦЫ
    if с_шапкой:
        out.append("\t".join(имена))

    def строка(уровень, блок, абзац, линия, номер, x, y, w, h, c, текст):
        поля = [уровень, 1, блок, абзац, линия, номер, x, y, w, h, c, текст]
        if not со_страницей:
            del поля[1]
        out.append("\t".join(str(п) for п in поля))

    строка(1, 0, 0, 0, 0, 0, 0, iw, ih, -1, "")
    for блок, линия, сл in записи:
        строка(4, блок, 1, линия, 0, 0, 0, 0, 0, -1, "")
        for номер, (x, y, w, h, т) in enumerate(сл, 1):
            if поворот == 270:        # лист повёрнут по часовой: текст течёт вниз
                x, y, w, h = H - y - h, x, h, w
            elif поворот == 90:       # против часовой: текст течёт вверх
                x, y, w, h = y, W - x - w, h, w
            elif поворот == 180:
                x, y = W - x - w, H - y - h
            строка(5, блок, 1, линия, номер, round(x), round(y), round(w), round(h),
                   conf.get(т, 95.5), т)
    return "\n".join(out) + "\n"


# ================================================================ разбор TSV

def test_колонки_по_координатам_блочный_режим():
    """Основной случай: каждая колонка — своя ячейка, заголовок и условия целы.

    Здесь же ловушка 4: «1 250,00» и «12 400,00» остаются одной ячейкой, хотя в
    них пробел, — он уже порога поля."""
    assert ocr_table.строки_по_координатам(в_tsv(лист())) == ОЖИДАНИЕ


def test_колонка_за_колонкой_собирается_в_те_же_строки():
    """Ловушка 1: в автоматическом режиме вывод идёт колонка за колонкой. Строки
    tesseract здесь — отдельные ячейки, и строка листа собирается по вертикали."""
    tsv = в_tsv(лист(), режим="3")
    # Убеждаемся, что корпус и правда «колонками»: первые слова вывода — номера.
    первые = [s.split("\t")[-1] for s in tsv.splitlines() if s.startswith("5\t")][:4]
    assert первые == ["№", "1", "2", "3"]
    assert ocr_table.строки_по_координатам(tsv) == ОЖИДАНИЕ


@pytest.mark.parametrize("режим", ["3", "6"])
def test_перекос_листа_не_рвёт_строки(режим):
    """Ловушка 2: наклон 0,02 (1,1°) уводит правый край строки на 28 px вниз —
    больше высоты слова. Сравнение с ближайшей ячейкой, а не с началом строки,
    держит строку целой."""
    assert ocr_table.строки_по_координатам(в_tsv(лист(), режим=режим, наклон=0.02)) \
        == ОЖИДАНИЕ


def test_одинаковое_первое_слово_не_режет_наименование():
    """Ловушка 3: «Подшипник …» во всех строках — пробел после первого слова на
    одном месте. Шапка «Товар» короче и его не закрывает. Жёлоб по словам
    разрезал бы наименование надвое; по полям пробел занят."""
    позиции = [("1", "Подшипник 22315 EK", "A-1", "4", "шт", "312,50", "1 250,00"),
               ("2", "Подшипник 6205-2RS", "A-2", "10", "шт", "85,00", "850,00"),
               ("3", "Подшипник 7312 BECBP", "A-3", "2", "шт", "47,25", "94,50")]
    шапка = ("№", "Товар", "Код", "Кол-во", "Ед.", "Цена, руб.", "Сумма, руб.")
    rows = ocr_table.строки_по_координатам(в_tsv(лист(позиции, шапка)))
    assert [r[1] for r in rows[2:5]] == ["Подшипник 22315 EK", "Подшипник 6205-2RS",
                                         "Подшипник 7312 BECBP"]
    assert all(len(r) == 7 for r in rows)


def test_проза_одной_ячейкой_а_перенос_в_своей_колонке():
    """Ловушка 5: условия поставки поперёк таблицы — одной ячейкой, а перенос
    наименования, лежащий внутри своей колонки, — в колонку наименования."""
    rows = ocr_table.строки_по_координатам(в_tsv(лист()))
    assert rows[-1] == [УСЛОВИЯ] + [""] * 6
    assert rows[-2] == ["", ПЕРЕНОС, "", "", "", "", ""]
    assert rows[0] == [ЗАГОЛОВОК] + [""] * 6


def test_цена_по_центру_двух_строк_уходит_к_первой():
    """Ловушка 6: наименование в две строки, номер, количество и цена выровнены
    по центру ячейки и перекрывают каждую строку на 0,4 высоты. При пороге
    перекрытия в половину цена стала бы отдельной строкой без наименования."""
    колонки = [(100, "l"), (160, "l"), (860, "r"), (1180, "r")]
    строки = [(200, ячейки_строки(("№", "Наименование", "Кол-во", "Цена"), колонки)),
              (240, [(160, "Подшипник")]),
              (252, [(100, "1")] + ячейки_строки(("", "", "4", "312,50"), колонки)[0:]),
              (264, [(160, "двухрядный")]),
              (300, ячейки_строки(("2", "Кольцо", "2", "47,25"), колонки))]
    rows = ocr_table.строки_по_координатам(в_tsv(строки, режим="3"))
    assert rows == [["№", "Наименование", "Кол-во", "Цена"],
                    ["1", "Подшипник", "4", "312,50"],
                    ["", "двухрядный", "", ""],
                    ["2", "Кольцо", "2", "47,25"]]


def test_линейки_таблицы_разделяют_узкие_колонки_и_исчезают():
    """В таблице с линейками ячейки стоят в 3 px друг от друга — уже любого
    порога. Граница видна только по глифу «|», которым tesseract читает линейку,
    отдельным словом или прилипшим к соседу («шт|»)."""
    # Промежутки между ячейками 13 px — меньше порога поля (0,75 × 20 = 15 px):
    # без линеек строка — одно поле, и таблицы нет вовсе.
    def ряд(a, b, c, d, прилип=False):
        return [(100, a), (156, "|"), (168, b), (180, "|"),
                (192, c + ("|" if прилип else "")), *([] if прилип else [(215, "|")]),
                (227, d)]
    строки = [(200, ряд("22315", "4", "шт", "312,50")),
              (240, ряд("62052", "2", "кг", "047,25", прилип=True)),
              (280, ряд("73120", "9", "шт", "850,00"))]
    rows = ocr_table.строки_по_координатам(в_tsv(строки))
    assert rows == [["22315", "4", "шт", "312,50"], ["62052", "2", "кг", "047,25"],
                    ["73120", "9", "шт", "850,00"]]
    без_линеек = [(y, [(x, т.strip("|")) for x, т in я if т != "|"]) for y, я in строки]
    assert ocr_table.строки_по_координатам(в_tsv(без_линеек)) == []


@pytest.mark.parametrize("поворот", [90, 180, 270])
def test_лист_на_боку_возвращается_в_прямой_кадр(поворот):
    """Ловушка 7: --psm 1 читает лист на боку верно, но координаты отдаёт в кадре
    исходной картинки — строки там идут столбцами. Кадр возвращается по порядку
    слов в строке, и таблица выходит та же, что у прямого листа."""
    таблица = ocr_table.разобрать_tsv(в_tsv(лист(), поворот=поворот))
    assert таблица.rows == ОЖИДАНИЕ
    assert таблица.поворот == поворот
    assert ocr_table.разобрать_tsv(в_tsv(лист())).поворот == 0


def test_уверенность_отмечена_а_текст_ячейки_не_тронут():
    """Слабое слово отмечается рядом, а не пометкой в тексте: «312,50?» уже не
    число, и цена потерялась бы из-за знака, поставленного ради осторожности."""
    таблица = ocr_table.разобрать_tsv(в_tsv(лист(), conf={"312,50": 41.0,
                                                          "роликовый": 50.0}))
    assert таблица.rows == ОЖИДАНИЕ
    assert таблица.слабых == 2
    assert таблица.слабые() == [(2, 1), (2, 5)]
    assert таблица.уверенность[2][5] == 41.0
    # Ячейка из двух слов: наименьшая, а не средняя (72,75 прошла бы порог).
    assert таблица.уверенность[2][1] == 50.0
    assert таблица.уверенность[3][1] == 95.5
    assert таблица.уверенность[0][1] is None          # пустая ячейка
    ожидаемая = (95.5 * (таблица.слов - 2) + 41.0 + 50.0) / таблица.слов
    assert abs(таблица.средняя - ожидаемая) < 1e-9
    assert "уверенность 94" in таблица.сводка()
    assert "слабых слов 2 из" in таблица.сводка()


@pytest.mark.parametrize("с_шапкой,со_страницей", [(False, True), (False, False),
                                                   (True, False)])
def test_формат_без_шапки_и_без_номера_страницы(с_шапкой, со_страницей):
    """Одиннадцать полей без номера страницы (так формат описан в постановке) и
    двенадцать без строки шапки разбираются так же, как вывод tesseract 5."""
    tsv = в_tsv(лист(), с_шапкой=с_шапкой, со_страницей=со_страницей)
    assert ocr_table.строки_по_координатам(tsv) == ОЖИДАНИЕ


def test_текст_идёт_видимыми_строками_поля_через_два_пробела():
    """Плоский текст собирается тоже по координатам. В автоматическом режиме
    колонки больше не идут друг за другом, а количество и цена разделены двумя
    пробелами: одиночный пробел перед тремя цифрами quotes читает как разделитель
    тысяч, и «10 100,00» склеилось бы в десять тысяч сто."""
    текст = ocr_table.разобрать_tsv(в_tsv(лист(), режим="3")).text
    строки = текст.splitlines()
    assert строки[0] == ЗАГОЛОВОК
    assert строки[3] == "2  Уплотнение торцевое  TRZ-4471/2  10  шт  85,00  850,00"
    assert строки[-1] == УСЛОВИЯ


def test_проза_без_таблицы_даёт_текст_но_не_строки():
    """Письмо без таблицы: строк нет — честный ответ, и текст остаётся для
    прежнего текстового пути."""
    строки = [(100, [(100, "Добрый день. Направляем предложение по запросу.")]),
              (140, [(100, "С уважением, отдел продаж.")])]
    таблица = ocr_table.разобрать_tsv(в_tsv(строки))
    assert таблица.rows == []
    assert таблица.text.splitlines()[1] == "С уважением, отдел продаж."
    assert "таблицы нет" in таблица.сводка()


# Шапка «Цена за ед., руб.» выровнена влево и свисает над колонкой единицы:
# ловушка 2 из pdftable. Строгий разрез склеивает «шт» с ценой.
СВИСАЕТ = [(100, "l"), (160, "l"), (560, "l"), (860, "r"), (900, "l"), (940, "l"),
           (1420, "r")]


def _свисающая_шапка() -> str:
    строки = [(200, ячейки_строки(("№", "Наименование", "Артикул", "Кол-во", "Ед.",
                                   "Цена за ед., руб.", "Сумма, руб."), СВИСАЕТ))]
    y = 240
    for п in ПОЗИЦИИ:
        строки.append((y, ячейки_строки(п)))
        y += 40
    return в_tsv(строки)


def test_лестница_допусков_выбирает_ступень_через_годится():
    """Строгая ступень склеивает «шт» с ценой; проверка вызывающего это видит,
    и берётся следующая ступень, где жёлоб занят одной шапкой."""
    tsv = _свисающая_шапка()
    строгая = ocr_table.разобрать_tsv(tsv)
    assert строгая.допуск == 0
    assert строгая.rows[1][4] == "шт 312,50"

    def годится(rows):
        return any(r[4] == "шт" for r in rows)
    мягкая = ocr_table.разобрать_tsv(tsv, годится)
    assert мягкая.допуск == 1
    assert мягкая.rows[1][4:] == ["шт", "312,50", "1 250,00"]
    # Свисающая шапка режется границей на 1002 px (середина жёлоба 922–1082).
    # Слово «за» (994–1016) лежит поперёк неё и идёт по ЦЕНТРУ — в колонку цены;
    # по левому краю оно ушло бы к единице измерения.
    assert мягкая.rows[0][4:6] == ["Ед. Цена", "за ед., руб."]


def test_выступ_одной_строки_слева_не_колонка():
    """На мягкой ступени отрезок, занятый одной строкой, считается жёлобом. Если
    одна строка начинается левее прочих («10.» против «9.»), отступ перед
    таблицей стал бы жёлобом — и появилась бы пустая первая колонка."""
    def поле(x0, x1):
        return [ocr_table.Слово(x0, 0, x1 - x0, 20, 95.0, "т")]
    блок = [[поле(40, 110), поле(200, 260), поле(400, 460)]] + \
        [[поле(100, 120), поле(200, 260), поле(400, 460)] for _ in range(3)]
    assert ocr_table._границы(блок, 1, 20) == [160.0, 330.0]
    assert ocr_table._границы(блок, 0, 20) == [160.0, 330.0]


def test_проверка_вызывающего_не_роняет_разбор():
    """Проверка, которая бросает, — это «не годится», а не падение разбора."""
    def годится(rows):
        raise RuntimeError("проверка сломана")
    таблица = ocr_table.разобрать_tsv(в_tsv(лист()), годится)
    assert таблица.rows == ОЖИДАНИЕ and таблица.допуск == 0


@pytest.mark.parametrize("мусор", [
    "", "\n\n", "level\tpage_num", "мусор\tмусор\tмусор",
    "5\t1\t1\t1\t1\t1\tx\ty\tw\th\t95\tслово",
    "5\t1\t1\t1\t1\t1\t10\t10\t-5\t20\t95\tслово",
    "5\t1\t1\t1\t1\t1\t10\t10\tnan\t20\t95\tслово",
    "\x00\xff\t\t\t",
    "level\tleft\ttop",
    None,
])
def test_мусор_на_входе_не_роняет(мусор):
    assert ocr_table.строки_по_координатам(мусор) == []
    assert isinstance(ocr_table.разобрать_tsv(мусор), ocr_table.Таблица)


def test_одно_слово_не_таблица():
    tsv = в_tsv([(100, [(100, "Счёт")])])
    таблица = ocr_table.разобрать_tsv(tsv)
    assert таблица.rows == [] and таблица.text == "Счёт" and таблица.слов == 1


# ================================================================ картинка без бинарников

def png(w: int, h: int, цвет: int = 0, черезстрочный: int = 0) -> bytes:
    """Настоящий PNG w×h, белый. Рисуется здесь: Pillow в зависимостях нет.
    цвет — тип PNG: 0 серый, 2 RGB, 3 палитра, 6 RGBA."""
    каналов = {0: 1, 2: 3, 3: 1, 6: 4}[цвет]
    сырьё = b"".join(b"\x00" + b"\xff" * (w * каналов) for _ in range(h))

    def кусок(тип: bytes, данные: bytes) -> bytes:
        return (struct.pack(">I", len(данные)) + тип + данные
                + struct.pack(">I", zlib.crc32(тип + данные) & 0xFFFFFFFF))
    палитра = кусок(b"PLTE", b"\xff\xff\xff\x00\x00\x00") if цвет == 3 else b""
    return (b"\x89PNG\r\n\x1a\n"
            + кусок(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, цвет, 0, 0, черезстрочный))
            + палитра + кусок(b"IDAT", zlib.compress(сырьё)) + кусок(b"IEND", b""))


class Машина:
    """Подмена subprocess.run: что ответил бы tesseract и что у него спросили."""

    def __init__(self, osd=b"Rotate: 0\nOrientation confidence: 11.0\n", tsv="",
                 падение=None, падение_чтения=None):
        self.osd, self.tsv, self.вызовы = osd, tsv, []
        self.падение, self.падение_чтения = падение, падение_чтения

    def __call__(self, argv, capture_output=True, timeout=None, env=None):
        self.вызовы.append((list(argv), env))
        if self.падение:
            raise self.падение
        if self.падение_чтения and "tessedit_create_tsv=1" in argv:
            raise self.падение_чтения
        if argv[0] == "pdftoppm":                        # пишет <база>.png
            self.pdf = Path(argv[-2]).read_bytes()
            Path(argv[-1] + ".png").write_bytes(png(4, 4))
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if argv[0] != "tesseract":                       # ImageMagick
            Path(argv[-1]).write_bytes(png(4, 4))
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if argv[argv.index("--psm") + 1] == "0":
            return subprocess.CompletedProcess(argv, 0, self.osd, b"")
        return subprocess.CompletedProcess(argv, 0, self.tsv.encode("utf-8"), b"")

    def режимы(self):
        return [a[a.index("--psm") + 1] for a, _ in self.вызовы if a[0] == "tesseract"]


@pytest.fixture
def картинка(tmp_path):
    p = tmp_path / "скан.png"
    p.write_bytes(png(1600, 1200))
    return str(p)


@pytest.fixture
def без_инструментов(monkeypatch):
    monkeypatch.setattr(ocr_table, "_pillow", lambda: None)
    monkeypatch.setattr(ocr_table.shutil, "which", lambda имя: None)


def test_не_картинка_не_доходит_до_tesseract(tmp_path, monkeypatch):
    """Не опознанный как картинка файл tesseract читает как СПИСОК ПУТЕЙ и лезет
    распознавать то, на что указывают строки. Такой файл отсекается до вызова."""
    p = tmp_path / "список.txt"
    p.write_text("/etc/passwd\n")
    машина = Машина()
    monkeypatch.setattr(ocr_table.subprocess, "run", машина)
    rows, text, reason = ocr_table.распознать_таблицу(str(p))
    assert (rows, text) == ([], "") and reason == "не картинка: формат не опознан"
    assert машина.вызовы == []
    assert ocr_table.распознать_таблицу(str(tmp_path / "нет.png"))[2] == "файл не найден"


@pytest.mark.parametrize("падение,причина", [
    (FileNotFoundError(), "tesseract не установлен"),
    (subprocess.TimeoutExpired("tesseract", 1), "таймаут распознавания"),
    (PermissionError(), "сбой запуска: PermissionError"),
])
def test_отказ_окружения_назван_как_в_ocr(картинка, monkeypatch, падение, причина):
    """Причины отказа окружения — дословно из ocr.ПРИЧИНЫ_ОКРУЖЕНИЯ: по началу
    строки ocr.py оставляет файл в очереди, а не выбрасывает навсегда. После
    таймаута второй попытки нет — она тоже не уложится."""
    машина = Машина(падение=падение)
    monkeypatch.setattr(ocr_table.subprocess, "run", машина)
    rows, text, reason = ocr_table.распознать_таблицу(картинка)
    assert rows == [] and text == ""
    assert reason.startswith(причина)
    assert len(машина.вызовы) == 1


def test_таймаут_чтения_без_второй_попытки(картинка, monkeypatch):
    """Ориентация определилась, а само чтение не уложилось: запасной режим не
    запускается — он тоже не уложится, а время части уйдёт вдвое."""
    машина = Машина(падение_чтения=subprocess.TimeoutExpired("tesseract", 1))
    monkeypatch.setattr(ocr_table.subprocess, "run", машина)
    rows, text, reason = ocr_table.распознать_таблицу(картинка)
    assert (rows, text, reason) == ([], "", "таймаут распознавания")
    assert машина.режимы() == ["0", ocr_table.PSM_MAIN]


def test_потоки_делятся_как_в_ocr(картинка, monkeypatch):
    """Каждый запуск tesseract — с OMP_THREAD_LIMIT: без него четыре процесса по
    четыре потока на четырёх ядрах не дочитывают лист и за 400 с."""
    машина = Машина(tsv=в_tsv(лист()))
    monkeypatch.setattr(ocr_table.subprocess, "run", машина)
    ocr_table.распознать_таблицу(картинка)
    assert машина.вызовы and all(env["OMP_THREAD_LIMIT"] == ocr_table.OMP
                                 for _, env in машина.вызовы)
    своя = {"OMP_THREAD_LIMIT": "3", "PATH": "/usr/bin"}
    ocr_table.распознать_таблицу(картинка, env=своя)
    assert машина.вызовы[-1][1] is своя


def test_прямой_лист_читается_основным_режимом(картинка, monkeypatch):
    машина = Машина(tsv=в_tsv(лист()))
    monkeypatch.setattr(ocr_table.subprocess, "run", машина)
    rows, text, reason = ocr_table.распознать_таблицу(картинка)
    assert rows == ОЖИДАНИЕ
    assert машина.режимы() == ["0", ocr_table.PSM_MAIN]
    assert "tessedit_create_tsv=1" in машина.вызовы[-1][0]
    assert reason.startswith("уверенность 96; слабых слов 0 из")
    assert text.splitlines()[0] == ЗАГОЛОВОК


ОСД_270 = b"Orientation in degrees: 90\nRotate: 270\nOrientation confidence: 9.50\n"


def test_лист_на_боку_без_инструментов_читается_режимом_с_ориентацией(
        картинка, monkeypatch, без_инструментов):
    """Повернуть нечем — последний запас: режим 1 поворачивает сам, а кадр слов
    возвращается по самим словам."""
    машина = Машина(osd=ОСД_270, tsv=в_tsv(лист(), режим="3", поворот=270))
    monkeypatch.setattr(ocr_table.subprocess, "run", машина)
    rows, _text, reason = ocr_table.распознать_таблицу(картинка)
    assert машина.режимы() == ["0", "1"]
    assert rows == ОЖИДАНИЕ
    assert "повернуть нечем (нет Pillow, ImageMagick и pdftoppm)" in reason
    assert "кадр слов повёрнут на 270°" in reason


def test_лист_на_боку_поворачивается_картинкой_через_poppler(картинка, monkeypatch):
    """Pillow и ImageMagick нет (как в прогоне), poppler есть: картинка уходит в
    PDF со страницей /Rotate 270, повёрнутая читается основным режимом."""
    monkeypatch.setattr(ocr_table, "_pillow", lambda: None)
    monkeypatch.setattr(ocr_table.shutil, "which",
                        lambda имя: "/usr/bin/pdftoppm" if имя == "pdftoppm" else None)
    машина = Машина(osd=ОСД_270, tsv=в_tsv(лист()))
    monkeypatch.setattr(ocr_table.subprocess, "run", машина)
    rows, _text, reason = ocr_table.распознать_таблицу(картинка)
    assert rows == ОЖИДАНИЕ
    assert машина.режимы() == ["0", ocr_table.PSM_MAIN]
    assert b"/Rotate 270" in машина.pdf and b"/FlateDecode" in машина.pdf
    вызов_pdftoppm = next(a for a, _ in машина.вызовы if a[0] == "pdftoppm")
    assert вызов_pdftoppm[1:3] == ["-r", "72"]           # поворот без увеличения
    повёрнутый = вызов_pdftoppm[-1] + ".png"
    assert машина.вызовы[-1][0][1] == повёрнутый
    assert "лист на боку: повёрнут на 270° (poppler)" in reason


def test_слабый_OSD_лист_не_поворачивает(картинка, monkeypatch):
    """Калибровка: все ошибочные ответы OSD — с уверенностью ниже 1. Такой ответ
    не принимается, лист читается как прямой, и это названо."""
    машина = Машина(osd=b"Rotate: 180\nOrientation confidence: 0.90\n", tsv=в_tsv(лист()))
    monkeypatch.setattr(ocr_table.subprocess, "run", машина)
    rows, _text, reason = ocr_table.распознать_таблицу(картинка)
    assert машина.режимы() == ["0", ocr_table.PSM_MAIN]
    assert rows == ОЖИДАНИЕ
    assert "OSD предлагал поворот 180°" in reason


def test_пустой_ответ_уходит_в_запасной_режим(картинка, monkeypatch):
    машина = Машина(tsv="")
    monkeypatch.setattr(ocr_table.subprocess, "run", машина)
    rows, text, reason = ocr_table.распознать_таблицу(картинка)
    assert машина.режимы() == ["0", ocr_table.PSM_MAIN, ocr_table.PSM_RETRY]
    assert (rows, text) == ([], "") and reason == "текста не найдено"


def test_мелкая_без_инструментов_читается_как_есть_и_это_названо(tmp_path, monkeypatch,
                                                                 без_инструментов):
    p = tmp_path / "мелкая.png"
    p.write_bytes(png(700, 495))
    машина = Машина(tsv=в_tsv(лист()))
    monkeypatch.setattr(ocr_table.subprocess, "run", машина)
    rows, _text, reason = ocr_table.распознать_таблицу(str(p))
    assert rows == ОЖИДАНИЕ
    assert "мелкая 700×495: увеличить нечем (нет Pillow, ImageMagick и pdftoppm)" in reason
    assert all(a[1] == str(p) for a, _ in машина.вызовы)


def test_мелкая_увеличивается_imagemagick(tmp_path, monkeypatch):
    """Короткая сторона 495 → множитель 3 (до 1485). ImageMagick получает свой
    предел потоков: он тоже собран с OpenMP."""
    p = tmp_path / "мелкая.png"
    p.write_bytes(png(700, 495))
    monkeypatch.setattr(ocr_table, "_pillow", lambda: None)
    monkeypatch.setattr(ocr_table.shutil, "which",
                        lambda имя: "/usr/bin/convert" if имя == "convert" else None)
    машина = Машина(tsv=в_tsv(лист()))
    monkeypatch.setattr(ocr_table.subprocess, "run", машина)
    _rows, _text, reason = ocr_table.распознать_таблицу(str(p))
    (увеличение, env), *распознавание = машина.вызовы
    assert увеличение[:4] == ["/usr/bin/convert", str(p), "-resize", "300%"]
    assert env["MAGICK_THREAD_LIMIT"] == env["OMP_THREAD_LIMIT"]
    assert all(a[1] == увеличение[-1] for a, _ in распознавание)
    assert "мелкая 700×495: увеличено ×3 (ImageMagick)" in reason


def test_pdf_из_картинки_без_распаковки(tmp_path):
    """PNG и JPEG идут в PDF как есть; то, что без распаковки не завернуть, —
    отказ с причиной, а не битый PDF."""
    def файл(имя, байты):
        p = tmp_path / имя
        p.write_bytes(байты)
        return str(p)
    pdf, почему = ocr_table.pdf_из_картинки(файл("g.png", png(30, 20)), 90)
    assert почему == "" and b"/Rotate 90" in pdf and b"/DeviceGray" in pdf
    assert b"/MediaBox [0 0 30 20]" in pdf and b"/Columns 30" in pdf
    pdf, _ = ocr_table.pdf_из_картинки(файл("p.png", png(30, 20, цвет=3)))
    assert b"/Indexed /DeviceRGB 1 <ffffff000000>" in pdf
    pdf, _ = ocr_table.pdf_из_картинки(файл("c.png", png(30, 20, цвет=2)), 180)
    assert b"/DeviceRGB" in pdf and b"/Colors 3" in pdf and b"/Rotate 180" in pdf
    assert ocr_table.pdf_из_картинки(файл("a.png", png(30, 20, цвет=6))) \
        == (None, "PNG с прозрачностью")
    assert ocr_table.pdf_из_картинки(файл("i.png", png(30, 20, черезстрочный=1))) \
        == (None, "черезстрочный PNG")
    jpeg = (b"\xff\xd8\xff\xc0" + struct.pack(">HBHHB", 11, 8, 120, 160, 3) + b"\x00" * 9
            + b"\xff\xd9")
    pdf, _ = ocr_table.pdf_из_картинки(файл("a.jpg", jpeg), 270)
    assert b"/DCTDecode /ColorSpace /DeviceRGB" in pdf and b"/Width 160 /Height 120" in pdf
    assert ocr_table.pdf_из_картинки(файл("a.gif", b"GIF89a" + b"\x00" * 20))[0] is None


class ПоддельныйPillow:
    """Изображает PIL.Image: записывает, что с картинкой делали."""
    class Transpose:
        ROTATE_90, ROTATE_180, ROTATE_270 = "против часовой 90", "180", "против часовой 270"

    class Resampling:
        LANCZOS = "lanczos"

    def __init__(self):
        self.действия = []

    def open(self, путь):
        журнал = self.действия

        class Кадр:
            width, height, mode, n_frames = 700, 495, "RGB", 1

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def resize(self, размер, фильтр):
                журнал.append(("resize", размер, фильтр))
                return self

            def transpose(self, как):
                журнал.append(("transpose", как))
                return self

            def save(self, куда):
                журнал.append(("save", куда))
                Path(куда).write_bytes(png(4, 4))
        return Кадр()


@pytest.mark.parametrize("поворот,как", [(90, "против часовой 270"), (180, "180"),
                                         (270, "против часовой 90")])
def test_pillow_крутит_в_ту_же_сторону_что_OSD(tmp_path, monkeypatch, поворот, как):
    """OSD говорит «Rotate» по часовой, Pillow крутит против часовой: 90 по
    часовой — это ROTATE_270. Перепутанная сторона дала бы лист вверх ногами."""
    pil = ПоддельныйPillow()
    monkeypatch.setattr(ocr_table, "_pillow", lambda: pil)
    новый, чем = ocr_table._подготовить(str(tmp_path / "a.png"), str(tmp_path), 3, поворот,
                                        {}, 60)
    assert чем == "Pillow" and Path(новый).exists()
    assert pil.действия[:2] == [("resize", (2100, 1485), "lanczos"), ("transpose", как)]


def test_множитель_увеличения():
    assert ocr_table._множитель((827, 1169)) == 2      # A4 при 100 dpi
    assert ocr_table._множитель((400, 300)) == 4       # потолок — четыре
    assert ocr_table._множитель((1000, 1400)) == 1     # не мелкая
    assert ocr_table._множитель((999, 30_000)) == 1    # полоса чека: предел пикселей


def test_формат_и_размер_по_заголовку(tmp_path):
    def файл(имя, байты):
        p = tmp_path / имя
        p.write_bytes(байты)
        return str(p)
    assert ocr_table.вид_и_размер(файл("a.png", png(37, 21))) == ("png", (37, 21))
    assert ocr_table.вид_и_размер(файл("a.gif", b"GIF89a" + struct.pack("<HH", 640, 480)
                                       + b"\x00" * 20)) == ("gif", (640, 480))
    bmp = b"BM" + b"\x00" * 16 + struct.pack("<ii", 800, -600) + b"\x00" * 10
    assert ocr_table.вид_и_размер(файл("a.bmp", bmp)) == ("bmp", (800, 600))
    tiff = (b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 2)
            + struct.pack("<HHI", 256, 3, 1) + struct.pack("<HH", 1700, 0)
            + struct.pack("<HHI", 257, 4, 1) + struct.pack("<I", 2200) + b"\x00" * 4)
    assert ocr_table.вид_и_размер(файл("a.tif", tiff)) == ("tiff", (1700, 2200))
    jpeg = (b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 4) + b"\x00\x00"
            + b"\xff\xc0" + struct.pack(">H", 11) + b"\x08" + struct.pack(">HH", 900, 1200)
            + b"\x00" * 10)
    assert ocr_table.вид_и_размер(файл("a.jpg", jpeg)) == ("jpeg", (1200, 900))
    assert ocr_table.вид_и_размер(файл("a.pgm", "P5\n# придумано\n320 200\n255\n".encode())) \
        == ("pnm", (320, 200))
    assert ocr_table.вид_и_размер(файл("a.txt", b"%PDF-1.4 not an image")) == (None, None)
    assert ocr_table.вид_и_размер(str(tmp_path / "нет")) == (None, None)


# ================================================================ настоящий tesseract

нужен_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None or shutil.which("pdftoppm") is None,
    reason="нет tesseract или pdftoppm: настоящий скан нарисовать и прочитать нечем")

# Ширина знаков Helvetica в тысячных кегля: нужна, чтобы выровнять числа вправо.
_ШИРИНА_HELV = {**{ц: 556 for ц in "0123456789"}, ",": 278, ".": 278, " ": 278}


def мини_pdf(ячейки: list[tuple[float, float, str, str]], поворот: int = 0,
             кегль: int = 11) -> bytes:
    """Одностраничный PDF: каждая ячейка на своей координате (x, y, текст, выравн.).

    Колонки ставятся координатой, а не пробелами: выравнивание пробелами в
    пропорциональном шрифте колонок не даёт, и тест проверял бы не то."""
    части = ["BT", f"/F1 {кегль} Tf"]
    for x, y, т, выр in ячейки:
        if выр == "r":
            x -= sum(_ШИРИНА_HELV.get(ч, 556) for ч in т) * кегль / 1000
        части.append(f"1 0 0 1 {x:.2f} {y} Tm ({т}) Tj")
    части.append("ET")
    поток = "\n".join(части).encode("latin-1", "replace")
    объекты = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] /Rotate %d "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>" % поворот,
        b"<< /Length %d >>\nstream\n" % len(поток) + поток + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    смещения = []
    for i, o in enumerate(объекты, 1):
        смещения.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    начало = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(объекты) + 1)
    for off in смещения:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(объекты) + 1, начало))
    return bytes(out)


# Английская шапка — КП чаще китайские, а шрифт PDF без вложения кириллицы не
# нарисует. Колонки в пунктах; числа выровнены вправо.
КП_КОЛОНКИ = [(40, "l"), (70, "l"), (260, "l"), (380, "r"), (400, "l"), (540, "r"),
              (660, "r")]
КП_ШАПКА = ("No", "Description", "Part No", "Qty", "Unit", "Price, USD", "Amount, USD")
КП_ПОЗИЦИИ = [("1", "Roller bearing", "22315 EK", "4", "pcs", "312,50", "1 250,00"),
              ("2", "Mechanical seal", "TRZ-4471/2", "10", "pcs", "85,00", "850,00"),
              ("3", "Spacer ring", "6205-2RS", "2", "pcs", "47,25", "94,50"),
              ("4", "Rotor shaft assembly", "NM-125/07", "1", "pcs", "12 400,00",
               "12 400,00")]


def скан_кп(tmp_path: Path, поворот: int = 0, dpi: int = 200, формат: str = "png") -> str:
    ячейки = [(40, 540, "QUOTATION No. 114 dated 18.09.2026", "l")]
    for i, ряд in enumerate([КП_ШАПКА] + КП_ПОЗИЦИИ):
        y = 500 - i * 18
        for (x, выр), т in zip(КП_КОЛОНКИ, ряд):
            # Шапка числовых колонок стоит вправо, как и числа под ней.
            ячейки.append((x, y, т, выр))
    ячейки.append((40, 390, "Terms of delivery: DAP Moscow. Payment 100% in advance.", "l"))
    pdf = tmp_path / f"kp{поворот}.pdf"
    pdf.write_bytes(мини_pdf(ячейки, поворот))
    subprocess.run(["pdftoppm", "-r", str(dpi), f"-{формат}", "-singlefile", str(pdf),
                    str(tmp_path / f"kp{поворот}")], check=True, capture_output=True,
                   timeout=60)
    return str(tmp_path / f"kp{поворот}.{'jpg' if формат == 'jpeg' else формат}")


def _строка_с(rows, артикул):
    р = next((r for r in rows if any(c.startswith(артикул) for c in r)), None)
    assert р is not None, f"строки с {артикул} нет: {rows}"
    return р


def _проверить_кп(rows):
    assert rows, "таблица не восстановлена"
    assert len(rows[0]) == 7, rows
    р = _строка_с(rows, "22315")
    assert р[2].startswith("22315") and р[5] == "312,50" and р[6] == "1 250,00", р
    # Одиночная цифра количества — то, что терял режим --psm 1 на листе на боку.
    assert р[3] == "4", р
    р = _строка_с(rows, "NM-125")
    assert р[5] == "12 400,00" and р[6] == "12 400,00", р


@нужен_tesseract
def test_настоящий_скан_колонки_и_шапка_для_разбора(tmp_path):
    """Цепочка целиком: картинка → tesseract tsv → колонки → ворота шапки
    indexer.header_map и колонки цены quotes — те же, что у xlsx и PDF."""
    rows, text, reason = ocr_table.распознать_таблицу(скан_кп(tmp_path), lang="eng",
                                                      timeout=90)
    _проверить_кп(rows)
    assert "Terms of delivery" in text and reason.startswith("уверенность")
    try:
        import indexer
        import quotes
    except ImportError as e:
        pytest.skip(f"indexer без своих зависимостей не импортируется: {e}")
    hi, cols = indexer.header_map(rows)
    assert hi >= 0, "шапка не опознана воротами разбора"
    assert {"item_name", "qty"} <= set(cols)
    цк = quotes.колонки_цены(rows[hi])
    assert "price" in цк and "total" in цк and цк["price"] != цк["total"]


@нужен_tesseract
@pytest.mark.parametrize("поворот", [90, 180, 270])
def test_настоящий_скан_на_боку(tmp_path, поворот):
    rows, _text, reason = ocr_table.распознать_таблицу(скан_кп(tmp_path, поворот),
                                                       lang="eng", timeout=90)
    _проверить_кп(rows)
    assert f"лист на боку: повёрнут на {(360 - поворот) % 360}°" in reason


@нужен_tesseract
def test_настоящий_jpeg_на_боку(tmp_path):
    """Фотография с телефона — JPEG, и лист на ней часто лежит на боку. JPEG идёт
    в PDF-обёртку как есть (/DCTDecode), без распаковки."""
    rows, _text, reason = ocr_table.распознать_таблицу(
        скан_кп(tmp_path, 90, формат="jpeg"), lang="eng", timeout=90)
    _проверить_кп(rows)
    assert "лист на боку: повёрнут на 270°" in reason


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="нет pdftoppm")
def test_poppler_поворачивает_и_увеличивает_настоящую_картинку(tmp_path, monkeypatch):
    """Поворот и увеличение через PDF-обёртку дают картинку нужного размера:
    повёрнутую — со сторонами наоборот, увеличенную — ровно в k раз."""
    monkeypatch.setattr(ocr_table, "_pillow", lambda: None)
    monkeypatch.setattr(ocr_table.shutil, "which",
                        lambda имя: "/usr/bin/pdftoppm" if имя == "pdftoppm" else None)
    исходная = tmp_path / "лист.png"
    исходная.write_bytes(png(300, 200, цвет=2))
    новый, чем = ocr_table._подготовить(str(исходная), str(tmp_path), 1, 90,
                                        ocr_table.СРЕДА, 60)
    assert чем == "poppler" and ocr_table.вид_и_размер(новый) == ("png", (200, 300))
    новый, чем = ocr_table._подготовить(str(исходная), str(tmp_path), 3, 0,
                                        ocr_table.СРЕДА, 60)
    assert ocr_table.вид_и_размер(новый) == ("png", (900, 600))


@нужен_tesseract
def test_настоящая_мелкая_картинка_названа(tmp_path):
    """60 dpi — 702×496: мелкая. Увеличена, если есть чем, иначе это названо."""
    _rows, _text, reason = ocr_table.распознать_таблицу(скан_кп(tmp_path, dpi=60),
                                                        lang="eng", timeout=90)
    assert "мелкая 702×496: увеличено ×3" in reason
