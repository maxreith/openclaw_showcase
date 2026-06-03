"""Tests for the Mensa am Hofgarten menu scraper."""

import pytest

from src.hofgarten.client import (
    MensaItem,
    _clean_meal_name,
    _parse_meals,
    _parse_price,
    get_menu,
)

SAMPLE_HTML = """\
<div class="container d-flex flex-wrap">
    <div class="menus-custom my-5">
        <h2 style="color: #000;">
            Erstelle dein Menü: <span class="menus-green">Hauptgericht</span>
        </h2>
    </div>
    <div class="menus__container mode-row">
        <h2>Hauptgericht</h2>
        <div class="menus__results row">
            <div class="col-12 col-lg-6">
                <div class="menus__result additive-40 additive-45">
                    <div class="card">
                        <div class="card-body">
                            <h5>
                                Frühlingsrolle mit Gemüsefüllung (VEGAN)
                            </h5>
                            <div class="popover-content">
                                <div>
                                    <strong>Allergene</strong>
                                    <p>Gluten (40)</p>
                                    <p>Soja (45)</p>
                                </div>
                                <div>
                                    <strong>Zusatzstoffe</strong>
                                    <p>konserviert (2)</p>
                                    <p>mit Antioxidationsmittel (3)</p>
                                </div>
                            </div>
                            <table class="menus__result__prices">
                                <tr><th>Stud.</th><td>2,90 €</td></tr>
                                <tr><th>Bed.</th><td>3,75 €</td></tr>
                                <tr><th>Gast</th><td>6,10 €</td></tr>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
            <div class="col-12 col-lg-6">
                <div class="menus__result">
                    <div class="card">
                        <div class="card-body">
                            <h5>Frikadelle auf Möhren</h5>
                            <div class="popover-content">
                                <div>
                                    <strong>Allergene</strong>
                                    <p>Gluten (40)</p>
                                </div>
                            </div>
                            <table class="menus__result__prices">
                                <tr><th>Stud.</th><td>3,00 €</td></tr>
                                <tr><th>Bed.</th><td>4,00 €</td></tr>
                                <tr><th>Gast</th><td>6,20 €</td></tr>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        <h2>Suppe &amp; Eintopf</h2>
        <div class="menus__results row">
            <div class="col-12 col-lg-6">
                <div class="menus__result">
                    <div class="card">
                        <div class="card-body">
                            <h5>Tomatensuppe (VEGAN)</h5>
                            <table class="menus__result__prices">
                                <tr><th>Stud.</th><td>0,95 €</td></tr>
                                <tr><th>Bed.</th><td>1,50 €</td></tr>
                                <tr><th>Gast</th><td>2,00 €</td></tr>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>
"""


class TestParseMeals:
    @pytest.fixture()
    def meals(self):
        return _parse_meals(SAMPLE_HTML)

    def test_count(self, meals):
        assert len(meals) == 3

    def test_first_meal_name(self, meals):
        assert meals[0].name == "Frühlingsrolle mit Gemüsefüllung"

    def test_first_meal_is_vegan(self, meals):
        assert meals[0].is_vegan is True

    def test_first_meal_prices(self, meals):
        assert meals[0].price_student == 2.90
        assert meals[0].price_staff == 3.75
        assert meals[0].price_guest == 6.10

    def test_first_meal_allergens(self, meals):
        assert meals[0].allergens == ["Gluten (40)", "Soja (45)"]

    def test_first_meal_additives(self, meals):
        assert meals[0].additives == ["konserviert (2)", "mit Antioxidationsmittel (3)"]

    def test_second_meal_not_vegan(self, meals):
        assert meals[1].is_vegan is False
        assert meals[1].name == "Frikadelle auf Möhren"

    def test_second_meal_no_additives(self, meals):
        assert meals[1].additives == []
        assert meals[1].allergens == ["Gluten (40)"]

    def test_category_hauptgericht(self, meals):
        assert meals[0].category == "Hauptgericht"
        assert meals[1].category == "Hauptgericht"

    def test_category_with_ampersand(self, meals):
        assert meals[2].category == "Suppe & Eintopf"

    def test_soup_is_vegan(self, meals):
        assert meals[2].name == "Tomatensuppe"
        assert meals[2].is_vegan is True

    def test_soup_prices(self, meals):
        assert meals[2].price_student == 0.95

    def test_skips_intro_header(self, meals):
        categories = {m.category for m in meals}
        assert all("Erstelle" not in c for c in categories)

    def test_day_left_empty(self, meals):
        assert all(m.day == "" for m in meals)


class TestCleanMealName:
    def test_strips_vegan_suffix(self):
        name, is_vegan = _clean_meal_name("Frühlingsrolle (VEGAN)")
        assert name == "Frühlingsrolle"
        assert is_vegan is True

    def test_non_vegan(self):
        name, is_vegan = _clean_meal_name("Schnitzel")
        assert name == "Schnitzel"
        assert is_vegan is False

    def test_normalizes_whitespace(self):
        name, _ = _clean_meal_name("  Reis   mit   Gemüse  ")
        assert name == "Reis mit Gemüse"

    def test_vegan_with_extra_whitespace(self):
        name, is_vegan = _clean_meal_name("  Tofu  (VEGAN)  ")
        assert name == "Tofu"
        assert is_vegan is True

    def test_vegan_in_middle(self):
        name, is_vegan = _clean_meal_name("Gemüse (VEGAN) Curry")
        assert name == "Gemüse Curry"
        assert is_vegan is True


class TestParsePrice:
    def test_standard_price(self):
        assert _parse_price("2,90 €") == 2.90

    def test_no_space_before_euro(self):
        assert _parse_price("3,75€") == 3.75

    def test_extra_whitespace(self):
        assert _parse_price("  6,10 €  ") == 6.10

    def test_single_digit_cents(self):
        assert _parse_price("0,95 €") == 0.95


class TestGetMenu:
    @pytest.fixture(scope="class")
    def menu_df(self):
        return get_menu(canteen_id=3)

    def test_not_empty(self, menu_df):
        assert len(menu_df) > 0

    def test_columns(self, menu_df):
        expected = {
            "day", "category", "name", "is_vegan",
            "price_student", "price_staff", "price_guest",
            "allergens", "additives", "week_label",
        }
        assert set(menu_df.columns) == expected

    def test_categories_present(self, menu_df):
        categories = set(menu_df["category"])
        assert "Hauptgericht" in categories

    def test_no_empty_names(self, menu_df):
        assert all(menu_df["name"].str.strip() != "")

    def test_positive_student_prices(self, menu_df):
        assert (menu_df["price_student"] > 0).all()

    def test_days_are_german(self, menu_df):
        valid_days = {"Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"}
        assert set(menu_df["day"]).issubset(valid_days)

    def test_has_week_label(self, menu_df):
        assert all(menu_df["week_label"].str.contains("–"))
