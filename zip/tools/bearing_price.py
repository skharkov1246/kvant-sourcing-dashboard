# -*- coding: utf-8 -*-
"""Параметрическая модель себестоимости и цены: биметаллический подшипник
и цельнобронзовая втулка. Китай EXW, серия 50-500 шт/год.

Модель считает снизу вверх по переделам, а не подгоняет под чужой прайс.
Все ставки вынесены в константы и подписаны — их можно оспорить построчно.
Цены на металл — параметры: при обновлении котировок пересчитывается всё.
"""

# ── котировки металла, USD/кг (база модели; обновляются из рыночных данных) ──
# Котировки LME cash на 01.09.2026, подтверждены двумя независимыми
# источниками (metaltorg.ru и tradingeconomics/westmetall), сходимость <0,1%.
METAL = {
    "Cu": 14.33,  # медь — 14 325 $/т
    "Sn": 55.32,  # олово — 55 318 $/т, главный драйвер цены бронзы
    "Pb": 1.98,   # свинец — 1 981 $/т
    "Ni": 16.79,  # никель — 16 790 $/т
    "P": 5.0,     # фосфор (лигатура), доля мала
}
FX_USD_RUB = 86.3793   # курс ЦБ РФ на 01.09.2026
FX_EUR_RUB = 100.5714
STEEL_40X = 1.20      # круг 40Х, USD/кг
INGOT_MARKUP = 1.18   # переплав чушки: угар, флюсы, маржа литейщика

# Шихта. ТЗ требует сертификат с химсоставом и подтверждение марки — это
# фактически первичная (или строго контролируемая) шихта. Многие литейщики
# считают цену от ВТОРИЧНОЙ бронзы (лом), она дешевле на 35-45%, но состав
# по олову и примесям плавает и марку подтвердить нечем. Разрыв в цене
# между этими двумя сценариями и есть типичная причина «подозрительно
# дешёвого» предложения.
CHARGE = {"первичная": 1.00, "вторичная (лом)": 0.62}

# ── марки бронзы: массовые доли ──
ALLOYS = {
    "CuSn8Ni2":   {"Cu": 0.90, "Sn": 0.08, "Ni": 0.02},
    "CuSn10Pb10": {"Cu": 0.80, "Sn": 0.10, "Pb": 0.10},
    "CuSn10P":    {"Cu": 0.89, "Sn": 0.10, "P":  0.01},
}

# ── ставки переделов (Китай, USD) ──
RATE_CNC = 9.0        # станко-час токарного ЧПУ
RATE_GRIND = 14.0     # станко-час внутришлифовального/хонинговального
RATE_CAST = 45.0      # час центробежной машины с оператором и печью
RATE_HT = 2.2         # термообработка, USD/кг садки
SCRAP_RETURN = 0.55   # возврат стружки бронзы, доля от цены металла
QC_UZK = 3.5          # УЗК одной детали
QC_SPECTRO = 25.0     # спектральный анализ на плавку
QC_SHEAR = 120.0      # испытание на сдвиг по ISO 4386-3, на партию
TOOLING = 900.0       # оснастка ЦБЛ на типоразмер
OVERHEAD = 1.28       # накладные завода
MARGIN = 1.22         # прибыль поставщика
DEFECT = 1.06         # брак и переделка


def alloy_price(grade, charge="первичная"):
    """USD/кг готовой чушки заданной марки при выбранном типе шихты."""
    comp = ALLOYS[grade]
    raw = sum(METAL[el] * frac for el, frac in comp.items())
    return raw * INGOT_MARKUP * CHARGE[charge]


def bimetal(steel_kg, bronze_kg, batch, grade="CuSn8Ni2", charge="первичная"):
    """Изделие 1: стальная основа 40Х + слой бронзы центробежным литьём."""
    br = alloy_price(grade, charge)
    # заготовка: сталь +30%, бронза +80% (слой льют с большим припуском под расточку)
    steel_blank, bronze_blank = steel_kg * 1.30, bronze_kg * 1.80
    m_steel = steel_blank * STEEL_40X
    m_bronze = bronze_blank * br - (bronze_blank - bronze_kg) * br * SCRAP_RETURN
    # переделы
    t_cast = 0.22 + bronze_blank * 0.10          # час на заливку
    t_turn = 0.30 + (steel_kg + bronze_kg) * 0.16
    t_grind = 0.18 + (steel_kg + bronze_kg) * 0.06
    p_cast = t_cast * RATE_CAST
    p_turn = t_turn * RATE_CNC
    p_grind = t_grind * RATE_GRIND
    p_ht = (steel_blank) * RATE_HT               # улучшение основы под 20-25 HRC
    p_qc = QC_UZK + (QC_SPECTRO + QC_SHEAR) / batch
    p_tool = TOOLING / batch
    direct = m_steel + m_bronze + p_cast + p_turn + p_grind + p_ht + p_qc + p_tool
    cost = direct * OVERHEAD * DEFECT
    return {
        "материал сталь": m_steel, "материал бронза": m_bronze,
        "центробежное литьё": p_cast, "токарная ЧПУ": p_turn,
        "шлифовка": p_grind, "термообработка": p_ht,
        "контроль (УЗК+спектр+сдвиг)": p_qc, "оснастка на партию": p_tool,
        "себестоимость": cost, "цена EXW": cost * MARGIN,
    }


def bronze_bush(bronze_kg, batch, grade="CuSn10Pb10", charge="первичная"):
    """Изделие 2: цельнобронзовая втулка центробежного литья."""
    br = alloy_price(grade, charge)
    blank = bronze_kg * 1.40
    m = blank * br - (blank - bronze_kg) * br * SCRAP_RETURN
    t_cast = 0.15 + blank * 0.06
    t_turn = 0.25 + bronze_kg * 0.13
    t_grind = 0.12 + bronze_kg * 0.05
    p_cast = t_cast * RATE_CAST
    p_turn = t_turn * RATE_CNC
    p_grind = t_grind * RATE_GRIND
    p_qc = QC_SPECTRO / batch
    p_tool = (TOOLING * 0.5) / batch             # оснастка ЦБЛ проще, чем под биметалл
    direct = m + p_cast + p_turn + p_grind + p_qc + p_tool
    cost = direct * OVERHEAD * DEFECT
    return {
        "материал бронза": m, "центробежное литьё": p_cast,
        "токарная ЧПУ": p_turn, "шлифовка": p_grind,
        "контроль (спектр)": p_qc, "оснастка на партию": p_tool,
        "себестоимость": cost, "цена EXW": cost * MARGIN,
    }


# опорные типоразмеры: код, габарит, сталь кг, бронза кг (изд.1), бронза кг (изд.2)
SIZES = [
    ("Р1", "60 × 45 × 60", 0.37, 0.24, 0.65),
    ("Р2", "100 × 80 × 100", 1.61, 0.69, 2.49),
    ("Р3", "160 × 130 × 150", 6.57, 1.65, 9.02),
]
BATCHES = [50, 200, 500]

if __name__ == "__main__":
    for g in ALLOYS:
        print(f"чушка {g:11} = ${alloy_price(g):.2f}/кг")
    print()
    for code, dim, st_kg, br1, br2 in SIZES:
        for b in BATCHES:
            a1 = bimetal(st_kg, br1, b)["цена EXW"]
            a2 = bimetal(st_kg, br1, b, charge="вторичная (лом)")["цена EXW"]
            c1 = bronze_bush(br2, b)["цена EXW"]
            c2 = bronze_bush(br2, b, charge="вторичная (лом)")["цена EXW"]
            print(f"{code} {dim:16} партия {b:3}: биметалл ${a1:7.2f} "
                  f"(на ломе ${a2:6.2f}) | втулка ${c1:7.2f} (на ломе ${c2:6.2f})")
        print()
