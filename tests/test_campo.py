"""Tests for the Mensa Campo (Poppelsdorf) menu scraper."""

import pytest

from src.campo.client import CANTEEN_ID, CATEGORIES_WANTED, get_menu


class TestGetMenu:
    @pytest.fixture(scope="class")
    def menu_df(self):
        return get_menu()

    def test_canteen_id_is_two(self):
        assert CANTEEN_ID == 2

    def test_categories_wanted(self):
        assert CATEGORIES_WANTED == frozenset(["Unser Spezial", "Hauptgericht", "Suppe & Eintopf"])

    def test_not_empty(self, menu_df):
        assert len(menu_df) > 0

    def test_columns(self, menu_df):
        expected = {
            "day", "category", "name", "is_vegan",
            "price_student", "price_staff", "price_guest",
            "allergens", "additives", "week_label",
        }
        assert set(menu_df.columns) == expected

    def test_has_spezial(self, menu_df):
        assert "Unser Spezial" in set(menu_df["category"])

    def test_has_hauptgericht(self, menu_df):
        assert "Hauptgericht" in set(menu_df["category"])

    def test_has_suppe(self, menu_df):
        assert "Suppe & Eintopf" in set(menu_df["category"])

    def test_no_empty_names(self, menu_df):
        assert all(menu_df["name"].str.strip() != "")

    def test_positive_student_prices(self, menu_df):
        assert (menu_df["price_student"] > 0).all()

    def test_days_are_german(self, menu_df):
        valid_days = {"Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"}
        assert set(menu_df["day"]).issubset(valid_days)

    def test_has_week_label(self, menu_df):
        assert all(menu_df["week_label"].str.contains("–"))
