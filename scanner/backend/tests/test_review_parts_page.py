"""Страница «Реестр деталей»: архив виден контролёру, закрыт от оператора."""

from tests.conftest import OPERATOR, REVIEWER


def test_reviewer_sees_registry(client):
    r = client.get("/review/parts", headers=REVIEWER)
    assert r.status_code == 200
    assert "База деталей" in r.text
    # реальная позиция из архива
    assert "KSB" in r.text


def test_search_filters_rows(client):
    r = client.get("/review/parts", params={"q": "flowserve"}, headers=REVIEWER)
    assert r.status_code == 200
    assert "Flowserve" in r.text
    assert "WITTEY" not in r.text


def test_operator_is_rejected(client):
    assert client.get("/review/parts", headers=OPERATOR).status_code == 403


def test_completeness_filter_and_task_creation(client):
    # Фильтр дыр: без замеров — почти весь архив (у 4 из 102 замеры есть)
    r = client.get("/review/parts", params={"gap": "замеры"}, headers=REVIEWER)
    assert r.status_code == 200

    # Карточка позиции открывается и предлагает задание
    card = client.get("/review/parts/item", params={"n": "1"}, headers=REVIEWER)
    assert card.status_code == 200
    assert "досъёмку" in card.text

    # Кнопка рождает задание, повторное нажатие не плодит дубликат
    r1 = client.post("/review/parts/item/task", params={"n": "1"}, headers=REVIEWER,
                     follow_redirects=False)
    assert r1.status_code == 303
    r2 = client.post("/review/parts/item/task", params={"n": "1"}, headers=REVIEWER,
                     follow_redirects=False)
    assert r2.status_code == 303
    tasks = client.get("/v1/tasks", headers=OPERATOR).json()["items"]
    reg = [t for t in tasks if t.get("external_ref") == "reg-1"]
    assert len(reg) == 1
    assert "доснять и домерить" in str(reg[0]["subject"])
