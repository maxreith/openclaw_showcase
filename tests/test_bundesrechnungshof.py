"""Tests for the Catering Günther menu scraper."""

import pytest

from src.bundesrechnungshof.client import (
    MenuItem,
    _extract_price,
    _merge_continuation_lines,
    _parse_cell,
    _parse_pdf,
    _split_into_dishes,
)


class TestExtractPrice:
    def test_simple_price(self):
        name, price = _extract_price("Schweineschnitzel 3,80 €")
        assert name == "Schweineschnitzel"
        assert price == 3.80

    def test_price_with_msc(self):
        name, price = _extract_price("Buntbarsch 7,95 € MSC")
        assert name == "Buntbarsch"
        assert price == 7.95

    def test_no_price(self):
        name, price = _extract_price("Vegetarische Pizza")
        assert name == "Vegetarische Pizza"
        assert price is None

    def test_size_variant_kl_gr_with_euro(self):
        name, price = _extract_price("Pommes-Frites Kl. 2,50 € / Gr. 3,00 €")
        assert name == "Pommes-Frites"
        assert price == 2.50

    def test_size_variant_kl_gr_without_euro(self):
        name, price = _extract_price("Bratkartoffeln Kl. 2,50 / Gr. 3,00")
        assert name == "Bratkartoffeln"
        assert price == 2.50

    def test_size_variant_klein_gross(self):
        name, price = _extract_price("Tagessuppe Klein 1,90 € Groß 3,80 €")
        assert name == "Tagessuppe"
        assert price == 1.90


class TestMergeContinuationLines:
    def test_price_only_merges_up(self):
        lines = ["Spinat-Kichererbsen-Eintopf", "3,80 €"]
        assert _merge_continuation_lines(lines) == [
            "Spinat-Kichererbsen-Eintopf 3,80 €"
        ]

    def test_size_indicator_merges_up(self):
        lines = ["Pommes-Frites", "Kl. 2,50 € / Gr. 3,00 €"]
        assert _merge_continuation_lines(lines) == [
            "Pommes-Frites Kl. 2,50 € / Gr. 3,00 €"
        ]

    def test_preposition_merges_when_no_price(self):
        lines = ["Vegetarische Kohlroulade", "mit Kartoffelstampf,", "dazu Gemüse-Bechamelsauce", "4,95 €"]
        assert _merge_continuation_lines(lines) == [
            "Vegetarische Kohlroulade mit Kartoffelstampf, dazu Gemüse-Bechamelsauce 4,95 €"
        ]

    def test_preposition_does_not_merge_after_price(self):
        lines = ["Spinat-Kichererbsen-Eintopf", "3,80 €", "dazu eine Bockwurst", "5,50 €"]
        result = _merge_continuation_lines(lines)
        assert result == [
            "Spinat-Kichererbsen-Eintopf 3,80 €",
            "dazu eine Bockwurst 5,50 €",
        ]

    def test_priceless_prefix_merges_down(self):
        lines = ["Vegetarische", "Kohlroulade", "mit Kartoffelstampf", "4,95 €"]
        result = _merge_continuation_lines(lines)
        assert "Vegetarische" in result[0]
        assert "Kohlroulade" in result[0]

    def test_multiple_size_items(self):
        lines = [
            "Pommes-Frites",
            "Kl. 2,50 € / Gr. 3,00 €",
            "Bratkartoffeln",
            "Kl. 2,50 / Gr. 3,00",
            "Gemüseteller",
            "Kl. 2,50 € / Gr. 3,50 €",
        ]
        result = _merge_continuation_lines(lines)
        assert len(result) == 3
        assert "Pommes-Frites" in result[0]
        assert "Bratkartoffeln" in result[1]
        assert "Gemüseteller" in result[2]


class TestSplitIntoDishes:
    def test_single_dish(self):
        assert _split_into_dishes("Schweineschnitzel 3,80 €") == [
            "Schweineschnitzel 3,80 €"
        ]

    def test_multiple_dishes(self):
        text = "Schweineschnitzel 3,80 € Hausmacher Frikadelle 2,50 €"
        result = _split_into_dishes(text)
        assert len(result) == 2
        assert "Schweineschnitzel" in result[0]
        assert "Hausmacher Frikadelle" in result[1]

    def test_size_variant_stays_together(self):
        text = "Pommes-Frites Kl. 2,50 € / Gr. 3,00 €"
        result = _split_into_dishes(text)
        assert len(result) == 1


class TestParseCell:
    def test_simple_cell(self):
        items = _parse_cell("Schweineschnitzel\n3,80 €", "Montag", "Imbiss")
        assert len(items) == 1
        assert items[0].name == "Schweineschnitzel"
        assert items[0].price_eur == 3.80
        assert items[0].day == "Montag"

    def test_siehe_aushang_returns_empty(self):
        assert _parse_cell("-Siehe Aushang-", "Montag", "Tagestipp") == []

    def test_strips_tagestipp_prefix(self):
        items = _parse_cell("Tagestipp\nPizza-Salami\n7,45 €", "Mittwoch", "Nachhaltig")
        assert len(items) >= 1
        assert "Tagestipp" not in items[0].name

    def test_info_only_filtered(self):
        items = _parse_cell("Alle Essen\nauch to Go!", "Freitag", "Imbiss")
        assert items == []

    def test_multi_dish_cell(self):
        cell = "Schweineschnitzel 3,80 €\nHausmacher Frikadelle 2,50 €\nRostbratwurst 2,50 €"
        items = _parse_cell(cell, "Montag", "Imbiss")
        assert len(items) == 3
        names = [i.name for i in items]
        assert "Schweineschnitzel" in names
        assert "Hausmacher Frikadelle" in names
        assert "Rostbratwurst" in names


class TestParsePdfIntegration:
    @pytest.fixture(scope="class")
    def menu_items(self):
        """Download and parse the live PDF once for all integration tests."""
        import httpx
        from src.bundesrechnungshof.client import HEADERS, _find_pdf_url

        pdf_url = _find_pdf_url()
        resp = httpx.get(pdf_url, headers=HEADERS, follow_redirects=True, timeout=15)
        return _parse_pdf(resp.content)

    def test_has_items(self, menu_items):
        assert len(menu_items) > 0

    def test_all_items_have_day(self, menu_items):
        for item in menu_items:
            assert item.day in ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]

    def test_all_items_have_category(self, menu_items):
        for item in menu_items:
            assert item.category

    def test_all_items_have_name(self, menu_items):
        for item in menu_items:
            assert item.name

    def test_categories_present(self, menu_items):
        categories = {item.category for item in menu_items}
        assert any("Stammessen" in c for c in categories)
        assert "Essen 2" in categories

    def test_no_info_text_in_names(self, menu_items):
        for item in menu_items:
            assert "Alle Essen" not in item.name
            assert "Alle Komponenten" not in item.name
            assert "auch to Go" not in item.name
