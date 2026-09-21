"""Себестоимость ввоза: правило одно на весь портал, проверяем его арифметику."""
from library import landed as L


def test_коэффициент_ввоза_собирается_из_стека():
    assert L.import_factor() == 1.16


def test_поршень_считается_как_в_смете():
    # Interstate-McBee 4309253, $863,61, курс ЦБ 17.09.2026
    assert L.landed(863.61)["rub"] == 84391


def test_разложение_сходится_с_итогом():
    # итог считается неокруглённым, разложение округляется для показа:
    # расхождение допускается только на копейки округления
    r = L.landed(1000.0)
    assert abs(r["base_rub"] + sum(r["parts"].values()) - r["rub"]) <= 2
    assert set(r["parts"]) == {"freight", "duty", "broker"}


def test_курс_сделки_перебивает_курс_из_данных():
    assert L.landed(100.0, fx=100.0)["rub"] == 11600


def test_своя_пошлина_меняет_только_пошлину():
    base = L.landed(100.0)
    other = L.landed(100.0, duty=0.10)
    assert other["parts"]["duty"] > base["parts"]["duty"]
    assert other["parts"]["freight"] == base["parts"]["freight"]


def test_разрыв_с_розницей_измеряется_числом():
    assert L.retail_gap(863.61, 380000) == 4.5


def test_ндс_не_входит_в_себестоимость():
    cfg = L.config()
    assert cfg["vat"]["in_cost"] is False
