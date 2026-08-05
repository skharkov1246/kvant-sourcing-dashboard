"""Многоуровневый бренд: фильтр PALL в насосе FISHER в турбине SIEMENS.

Смысл набора: плоское поле «бренд» врёт всегда — какое из трёх имён в него
ни положи, две трети правды потеряно. Здесь проверяется, что система
держит все имена одной детали как РАЗНЫЕ ПУТИ ЗАКУПКИ одного предмета, а
не как разные предметы, и что спор двух изготовителей уходит человеку.
"""

import pytest

from app.brand_chain import (
    BrandChainError,
    BrandRef,
    InstallationNode,
    conflicting_manufacturers,
    equivalent_articles,
    normalize_article,
    procurement_paths,
    validate_installation,
)
from tests.conftest import CUSTOMER, OPERATOR, REVIEWER


def _ref(brand, role, article=None, confidence="declared", level=None) -> BrandRef:
    return BrandRef(brand=brand, article=article, role=role,
                    confidence=confidence, level=level)


class TestRules:
    def test_article_normalization_merges_the_same_filter(self):
        """«HC 9600-FKS», «hc9600fks», «HC9600 FKS» — один фильтр, а не три
        позиции в базе."""
        forms = ["HC 9600-FKS", "hc9600fks", "HC9600 FKS", "hc-9600-fks"]
        assert len({normalize_article(f) for f in forms}) == 1

    def test_unknown_role_is_refused(self):
        with pytest.raises(BrandChainError, match="роль"):
            _ref("PALL", role="кто-то_там")

    def test_empty_brand_is_refused_service_value_exists(self):
        """Пустой бренд запрещён: для «не знаю» есть служебное значение
        справочника, а не пустая строка (Р-03 п.2)."""
        with pytest.raises(BrandChainError, match="БРЕНД НЕ ОПРЕДЕЛЁН"):
            _ref("   ", role="manufacturer")

    def test_installation_orders_from_equipment_down_to_part(self):
        chain = validate_installation([
            InstallationNode("part", brand="PALL"),
            InstallationNode("equipment", brand="Siemens", designation="SST-300"),
            InstallationNode("assembly", brand="Emerson / Fisher"),
        ])
        assert [n.level for n in chain] == ["equipment", "assembly", "part"]

    def test_missing_middle_level_is_honest_knowledge_not_an_error(self):
        """«Фильтр в турбине Siemens, чей насос неизвестен» — состояние
        знания, а не ошибка ввода: пропуск середины разрешён."""
        chain = validate_installation([
            InstallationNode("equipment", brand="Siemens"),
            InstallationNode("part", brand="PALL"),
        ])
        assert [n.level for n in chain] == ["equipment", "part"]

    def test_chain_without_the_part_itself_is_refused(self):
        with pytest.raises(BrandChainError, match="нет самой детали"):
            validate_installation([InstallationNode("equipment", brand="Siemens")])

    def test_duplicate_level_is_refused(self):
        with pytest.raises(BrandChainError, match="повторяется"):
            validate_installation([
                InstallationNode("part", brand="PALL"),
                InstallationNode("part", brand="Другой"),
            ])


class TestProcurement:
    def test_paths_go_from_manufacturer_to_equipment_owner(self):
        """Порядок — это порядок цены: дешевле у того, кто сделал, дороже
        всего у того, чей шильдик на установке."""
        paths = procurement_paths([
            _ref("Siemens", "equipment_oem", "A5E00123456"),
            _ref("PALL", "manufacturer", "HC9600FKS"),
            _ref("Emerson / Fisher", "assembly_oem", "12-345"),
        ])
        assert [p["brand"] for p in paths] == [
            "PALL", "Emerson / Fisher", "Siemens"]
        assert paths[0]["role_title"] == "изготовитель"

    def test_inferred_path_is_listed_but_not_actionable(self):
        """По догадке OCR заказ не делают — путь виден, но помечен
        неподтверждённым."""
        paths = procurement_paths([
            _ref("PALL", "manufacturer", "HC9600", confidence="inferred"),
            _ref("Siemens", "equipment_oem", "A5E001", confidence="verified"),
        ])
        assert paths[0]["brand"] == "PALL" and paths[0]["actionable"] is False
        assert paths[1]["actionable"] is True

    def test_equivalents_group_article_across_brands(self):
        """Тот же артикул под двумя брендами — семья имён одной детали."""
        eq = equivalent_articles([
            _ref("PALL", "manufacturer", "HC9600FKS"),
            _ref("Дистрибьютор Х", "distributor", "hc 9600 fks"),
            _ref("Siemens", "equipment_oem", "A5E00123456"),
        ])
        assert eq["hc9600fks"] == ["PALL", "Дистрибьютор Х"]

    def test_two_confirmed_manufacturers_is_a_dispute_for_a_human(self):
        """Перемаркировка и смена контрактного завода бывают честными, но
        выбирать бренд молча нельзя — однажды закажут не ту деталь."""
        assert conflicting_manufacturers([
            _ref("PALL", "manufacturer", confidence="verified"),
            _ref("Hydac", "manufacturer", confidence="verified"),
        ]) == ["Hydac", "PALL"]

    def test_declared_versus_inferred_manufacturer_is_not_a_dispute(self):
        assert conflicting_manufacturers([
            _ref("PALL", "manufacturer", confidence="verified"),
            _ref("Hydac", "manufacturer", confidence="inferred"),
        ]) == []


class TestBrandChainApi:
    """Сквозь HTTP на обоих хранилищах: memory задаёт контракт, pg обязан
    быть неотличим."""

    def _siemens_turbine(self, client, item="item-turbine-filter", headers=REVIEWER):
        return client.post(f"/v1/items/{item}/brands", headers=headers, json={
            "brands": [
                {"brand": "PALL", "role": "manufacturer", "article": "HC 9600-FKS",
                 "level": "part"},
                {"brand": "Emerson / Fisher", "role": "assembly_oem",
                 "article": "12-345", "level": "assembly"},
                {"brand": "Siemens", "role": "equipment_oem",
                 "article": "A5E00123456", "level": "equipment"},
            ],
            "installation": [
                {"level": "equipment", "brand": "Siemens", "designation": "SST-300"},
                {"level": "assembly", "brand": "Emerson / Fisher",
                 "designation": "D4 pump", "position": "смазка, поз. 14"},
                {"level": "part", "brand": "PALL", "designation": "фильтроэлемент"},
            ],
        })

    def test_three_levels_become_three_procurement_paths(self, client):
        assert self._siemens_turbine(client).status_code == 201

        doc = client.get("/v1/items/item-turbine-filter/brands",
                         headers=OPERATOR).json()
        assert [p["brand"] for p in doc["procurement_paths"]] == [
            "PALL", "Emerson / Fisher", "Siemens"]
        assert [n["level"] for n in doc["installation"]] == [
            "equipment", "assembly", "part"]
        assert doc["manufacturer_conflict"] == []

    def test_external_declares_reviewer_verifies(self, client):
        """I-7 и здесь: сотрудник заказчика заявляет, контролёр верифицирует —
        уровень доверия клиент назначить не может."""
        client.post("/v1/items/item-ext/brands", headers=CUSTOMER, json={
            "brands": [{"brand": "PALL", "role": "manufacturer", "article": "HC1"}]})
        declared = client.get("/v1/items/item-ext/brands", headers=OPERATOR).json()
        assert declared["procurement_paths"][0]["confidence"] == "declared"

        client.post("/v1/items/item-rev/brands", headers=REVIEWER, json={
            "brands": [{"brand": "PALL", "role": "manufacturer", "article": "HC1"}]})
        verified = client.get("/v1/items/item-rev/brands", headers=OPERATOR).json()
        assert verified["procurement_paths"][0]["confidence"] == "verified"

    def test_repeat_scan_does_not_duplicate_paths(self, client):
        self._siemens_turbine(client, item="item-dup")
        self._siemens_turbine(client, item="item-dup")
        doc = client.get("/v1/items/item-dup/brands", headers=OPERATOR).json()
        assert len(doc["procurement_paths"]) == 3

    def test_search_by_foreign_invoice_article_finds_the_item(self, client):
        """Пришла накладная с «hc9600fks» — находим карточку, а в ней
        изготовителя и остальные имена."""
        self._siemens_turbine(client, item="item-search")
        found = client.get("/v1/search/by-article?article=hc9600fks",
                           headers=OPERATOR).json()
        assert "item-search" in found["items"]

        by_oem = client.get("/v1/search/by-article?article=A5E00123456",
                            headers=OPERATOR).json()
        assert "item-search" in by_oem["items"]

    def test_item_card_carries_both_halves(self, client):
        """Карточка отвечает на оба вопроса снабженца: чем деталь является
        и у кого её купить."""
        self._siemens_turbine(client, item="item-card-brands")
        card = client.get("/v1/items/item-card-brands", headers=OPERATOR).json()
        assert card["procurement_paths"][0]["brand"] == "PALL"
        assert card["installation"][0]["brand"] == "Siemens"

    def test_bad_chain_is_refused_with_reason(self, client):
        r = client.post("/v1/items/item-bad/brands", headers=REVIEWER, json={
            "installation": [{"level": "equipment", "brand": "Siemens"}]})
        assert r.status_code == 422
        assert "нет самой детали" in r.json()["detail"]["message"]
