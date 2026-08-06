"""Страница «Реестр деталей»: архив виден контролёру, закрыт от оператора."""

from tests.conftest import OPERATOR, REVIEWER


def test_reviewer_sees_registry(client):
    r = client.get("/review/parts", headers=REVIEWER)
    assert r.status_code == 200
    assert "Реестр деталей" in r.text
    # реальная позиция из архива
    assert "KSB" in r.text


def test_search_filters_rows(client):
    r = client.get("/review/parts", params={"q": "flowserve"}, headers=REVIEWER)
    assert r.status_code == 200
    assert "Flowserve" in r.text
    assert "WITTEY" not in r.text


def test_operator_is_rejected(client):
    assert client.get("/review/parts", headers=OPERATOR).status_code == 403
