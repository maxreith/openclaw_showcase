"""Tests for src/lunch_feed.py."""

import datetime
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, "src")

from lunch_feed import (
    FEED_KEYS,
    GERMAN_DAYS,
    SCRAPERS,
    SECTION_TITLES,
    _compact_brh,
    _compact_hofgarten,
    _compact_kirchenpavillon,
    _compact_wolt,
    _compute_wolt_new_price,
    print_feed,
    print_feed_compact,
    refresh_caches,
)


@pytest.fixture()
def sample_df():
    return pd.DataFrame({"name": ["Item A", "Item B"], "price": [5.99, 8.49]})


class TestRefreshCaches:
    def test_all_groups_run(self):
        """All 3 groups are called."""
        with (
            patch("lunch_feed._refresh_wolt_group", return_value=(["  wolt: ok"], [])),
            patch("lunch_feed._refresh_fast_group", return_value=(["  fast: ok"], [])),
        ):
            failed = refresh_caches()
        assert failed == []

    def test_failure_collected(self):
        """Failed scrapers are returned in the failed list."""
        with (
            patch("lunch_feed._refresh_wolt_group", return_value=([], [])),
            patch("lunch_feed._refresh_fast_group", return_value=([], [])),
        ):
            failed = refresh_caches()
        assert failed == []

    def test_status_on_stderr(self, capsys):
        """Status messages are printed to stderr, not stdout."""
        with (
            patch("lunch_feed._refresh_wolt_group", return_value=(["  wolt: ok"], [])),
            patch("lunch_feed._refresh_fast_group", return_value=(["  fast: ok"], [])),
        ):
            refresh_caches()

        captured = capsys.readouterr()
        assert captured.out == ""
        assert "ok" in captured.err


class TestPrintFeed:
    def test_all_sections_present(self, sample_df, capsys):
        """All 4 feed sections appear with correct markers."""
        with patch("lunch_feed.load", return_value=sample_df):
            print_feed(failed=[])

        out = capsys.readouterr().out
        for key in FEED_KEYS:
            assert f"=== {SECTION_TITLES[key]} ===" in out

    def test_only_feed_keys_printed(self, sample_df, capsys):
        """Restaurant listing keys do not appear in output."""
        with patch("lunch_feed.load", return_value=sample_df):
            print_feed(failed=[])

        out = capsys.readouterr().out
        assert "wolt_restaurants" not in out

    def test_data_content_printed(self, sample_df, capsys):
        """DataFrame content appears in the output."""
        with patch("lunch_feed.load", return_value=sample_df):
            print_feed(failed=[])

        out = capsys.readouterr().out
        assert "Item A" in out
        assert "8.49" in out

    def test_failed_key_shows_unavailable(self, capsys):
        """Failed scraper sections show (unavailable)."""
        with patch("lunch_feed.load", return_value=None):
            print_feed(failed=["wolt_deals"])

        out = capsys.readouterr().out
        assert "(unavailable)" in out
        section_start = out.index("=== Wolt Deals ===")
        unavailable_pos = out.index("(unavailable)")
        assert unavailable_pos > section_start

    def test_empty_df_shows_no_data(self, capsys):
        """Empty DataFrame shows (no data)."""
        with patch("lunch_feed.load", return_value=pd.DataFrame()):
            print_feed(failed=[])

        out = capsys.readouterr().out
        assert "(no data)" in out

    def test_missing_cache_shows_no_data(self, capsys):
        """None from cache.load shows (no data)."""
        with patch("lunch_feed.load", return_value=None):
            print_feed(failed=[])

        out = capsys.readouterr().out
        assert "(no data)" in out


@pytest.fixture()
def brh_df():
    return pd.DataFrame({
        "day": ["Montag", "Montag", "Montag", "Montag", "Dienstag"],
        "category": [
            "Stammessen (Gäste zahlen € 0,30 Aufschlag)",
            "Essen 2",
            "Nachhaltig - Tierwohl",
            "Vegan / Vegetarisch / Imbiss",
            "Stammessen (Gäste zahlen € 0,30 Aufschlag)",
        ],
        "name": ["Schnitzel", "Bratwurst", "Bio-Hähnchen", "Falafel", "Suppe"],
        "price_eur": [3.50, 7.45, 6.50, 4.00, 3.50],
        "week_label": ["KW 12"] * 5,
    })


@pytest.fixture()
def hofgarten_df():
    return pd.DataFrame({
        "day": ["Montag", "Montag", "Montag", "Dienstag"],
        "category": ["Hauptgericht", "Suppe & Eintopf", "Dessert", "Hauptgericht"],
        "name": ["Currywurst", "Linsensuppe", "Pudding", "Fisch"],
        "is_vegan": [False, True, False, False],
        "price_student": [2.80, 1.50, 1.00, 3.00],
        "price_staff": [4.00, 2.50, 1.50, 4.50],
        "price_guest": [5.00, 3.50, 2.00, 5.50],
        "allergens": [""] * 4,
        "additives": [""] * 4,
        "week_label": ["KW 12"] * 4,
    })


@pytest.fixture()
def campo_df():
    return pd.DataFrame({
        "day": ["Montag", "Montag", "Montag"],
        "category": ["Unser Spezial", "Hauptgericht", "Suppe & Eintopf"],
        "name": ["Pizza Margherita", "Weizen-Frikadelle", "Kürbissuppe"],
        "is_vegan": [False, True, True],
        "price_student": [4.80, 2.90, 1.20],
        "price_staff": [6.00, 3.75, 1.80],
        "price_guest": [8.00, 5.50, 2.50],
        "allergens": [""] * 3,
        "additives": [""] * 3,
        "week_label": ["KW 15"] * 3,
    })


@pytest.fixture()
def wolt_deals_df():
    return pd.DataFrame({
        "restaurant_id": ["r1", "r2", "r3"],
        "restaurant_name": ["Burger Place", "Pizza Shop", "Sushi Bar"],
        "discount_id": ["d1", "d2", "d3"],
        "deal_title": ["2 for 1", "20% off", "€2 off"],
        "deal_body": ["body1", "body2", "body3"],
        "deal_type": ["two for one", "% off", "item discount"],
        "discount_amount": [0, 0, 200],
        "discount_fraction": [0, 0.2, 0],
        "free_items_buy": [2, 0, 0],
        "free_items_get": [1, 0, 0],
        "min_basket": [0, 10, 0],
        "delivery_methods": ["all", "all", "all"],
        "end_date": ["2026-04-01", "2026-04-01", "2026-04-01"],
        "item_names": ["Cheeseburger", "Margherita", "Salmon Roll"],
        "category_names": ["Burgers", "Pizzas", "Sushi"],
        "item_prices": ["899", "1200", "1500"],
    })


@pytest.fixture()
def kirchenpavillon_df():
    return pd.DataFrame({
        "date": [datetime.date.today()],
        "name": ["Reibekuchen mit Apfelmus"],
        "price_eur": [10.0],
    })



class TestCompactBrh:
    def test_filters_to_today_kept_categories(self, brh_df):
        """Today's Stammessen, Essen 2, and Nachhaltig rows are kept."""
        result = _compact_brh(brh_df, "Montag")
        assert len(result) == 3
        assert list(result.columns) == ["category", "name", "price_eur"]
        assert "Schnitzel" in result["name"].values
        assert "Bratwurst" in result["name"].values
        assert "Bio-Hähnchen" in result["name"].values

    def test_excludes_other_categories(self, brh_df):
        """Vegan / Vegetarisch / Imbiss is excluded."""
        result = _compact_brh(brh_df, "Montag")
        assert "Falafel" not in result["name"].values

    def test_different_day(self, brh_df):
        """Filtering by a different day returns that day's items."""
        result = _compact_brh(brh_df, "Dienstag")
        assert len(result) == 1
        assert result.iloc[0]["name"] == "Suppe"

    def test_no_matches_returns_empty(self, brh_df):
        """A day with no data returns empty DataFrame."""
        result = _compact_brh(brh_df, "Freitag")
        assert result.empty


class TestCompactHofgarten:
    def test_filters_to_today_hauptgericht_and_suppe(self, hofgarten_df):
        """Only today's Hauptgericht and Suppe & Eintopf are kept."""
        result = _compact_hofgarten(hofgarten_df, "Montag")
        assert len(result) == 2
        assert list(result.columns) == ["name", "is_vegan", "price_student"]

    def test_excludes_dessert(self, hofgarten_df):
        """Dessert category is excluded."""
        result = _compact_hofgarten(hofgarten_df, "Montag")
        assert "Pudding" not in result["name"].values

    def test_no_matches_returns_empty(self, hofgarten_df):
        """A day with no data returns empty DataFrame."""
        result = _compact_hofgarten(hofgarten_df, "Samstag")
        assert result.empty


class TestCompactWolt:
    def test_selects_correct_columns(self, wolt_deals_df):
        """The 5 compact columns include new_price instead of item_prices."""
        result = _compact_wolt(wolt_deals_df)
        assert list(result.columns) == [
            "restaurant_name", "deal_title", "deal_type", "item_names", "new_price",
        ]
        assert len(result) == 3

    def test_preserves_all_rows(self, wolt_deals_df):
        """All deal rows are kept."""
        result = _compact_wolt(wolt_deals_df)
        assert "Burger Place" in result["restaurant_name"].values
        assert "Pizza Shop" in result["restaurant_name"].values
        assert "Sushi Bar" in result["restaurant_name"].values

    def test_new_price_two_for_one(self, wolt_deals_df):
        """Two-for-one: new price = base price of one item (what you pay)."""
        result = _compact_wolt(wolt_deals_df)
        tfo_row = result[result["deal_type"] == "two for one"].iloc[0]
        assert tfo_row["new_price"] == 8.99  # pay for 1 item at 899 cents

    def test_new_price_percent_off(self, wolt_deals_df):
        """Percent off: new price = price * (1 - fraction)."""
        result = _compact_wolt(wolt_deals_df)
        pct_row = result[result["deal_type"] == "% off"].iloc[0]
        assert pct_row["new_price"] == 9.60  # 1200 * 0.8 / 100 = 9.60

    def test_new_price_item_discount(self, wolt_deals_df):
        """Item discount: new price = price - amount."""
        result = _compact_wolt(wolt_deals_df)
        item_row = result[result["deal_type"] == "item discount"].iloc[0]
        assert item_row["new_price"] == 13.00  # (1500 - 200) / 100 = 13.00

    def test_category_fallback_when_item_names_empty(self):
        """When item_names is empty, category_names is shown instead."""
        df = pd.DataFrame({
            "restaurant_name": ["Pasta Place"],
            "deal_title": ["Lunch Special"],
            "deal_type": ["% off"],
            "deal_body": [""],
            "discount_amount": [0],
            "discount_fraction": [0.15],
            "free_items_buy": [0],
            "free_items_get": [0],
            "min_basket": [0],
            "delivery_methods": ["all"],
            "end_date": [None],
            "item_names": [""],
            "category_names": ["Pasta & Risotto"],
            "item_prices": ["1000"],
        })
        result = _compact_wolt(df)
        assert result.iloc[0]["item_names"] == "Pasta & Risotto"

    def test_non_empty_item_names_not_overwritten(self, wolt_deals_df):
        """Rows with populated item_names are not replaced by category_names."""
        result = _compact_wolt(wolt_deals_df)
        assert result[result["deal_type"] == "two for one"].iloc[0]["item_names"] == "Cheeseburger"


class TestPrintFeedCompact:
    def test_all_sections_present(self, capsys, brh_df, hofgarten_df, campo_df, kirchenpavillon_df, wolt_deals_df):
        """All compact sections appear."""
        data_map = {
            "brh_menu": brh_df,
            "hofgarten_menu": hofgarten_df,
            "campo_menu": campo_df,
            "kirchenpavillon_menu": kirchenpavillon_df,
            "wolt_deals": wolt_deals_df,
        }
        with patch("lunch_feed.load", side_effect=lambda k: data_map[k]):
            print_feed_compact(failed=[])

        out = capsys.readouterr().out
        assert "=== BRH Menu ===" in out
        assert "=== Hofgarten Menu ===" in out
        assert "=== Mensa Campo ===" in out
        assert "=== Kirchenpavillon ===" in out
        assert "=== Wolt Deals ===" in out

    def test_failed_key_shows_unavailable(self, capsys, brh_df, hofgarten_df, campo_df, kirchenpavillon_df):
        """Failed sections show (unavailable)."""
        data_map = {
            "brh_menu": brh_df,
            "hofgarten_menu": hofgarten_df,
            "campo_menu": campo_df,
            "kirchenpavillon_menu": kirchenpavillon_df,
        }
        with patch("lunch_feed.load", side_effect=lambda k: data_map.get(k)):
            print_feed_compact(failed=["wolt_deals"])

        out = capsys.readouterr().out
        assert "(unavailable)" in out

    def test_compact_has_fewer_columns_than_full(self, capsys, brh_df, hofgarten_df, campo_df, kirchenpavillon_df, wolt_deals_df):
        """Compact output uses markdown tables and excludes columns like discount_id."""
        data_map = {
            "brh_menu": brh_df,
            "hofgarten_menu": hofgarten_df,
            "campo_menu": campo_df,
            "kirchenpavillon_menu": kirchenpavillon_df,
            "wolt_deals": wolt_deals_df,
        }
        with patch("lunch_feed.load", side_effect=lambda k: data_map[k]):
            print_feed_compact(failed=[])

        out = capsys.readouterr().out
        assert "|" in out
        assert "discount_id" not in out
        assert "restaurant_name" in out

    def test_empty_after_filter_shows_no_data(self, capsys, brh_df, hofgarten_df, campo_df, kirchenpavillon_df, wolt_deals_df):
        """When filtering leaves no rows, show (no data)."""
        brh_empty_day = brh_df.copy()
        brh_empty_day["day"] = "Samstag"
        hofgarten_empty_day = hofgarten_df.copy()
        hofgarten_empty_day["day"] = "Samstag"
        campo_empty_day = campo_df.copy()
        campo_empty_day["day"] = "Samstag"
        today = GERMAN_DAYS[datetime.date.today().weekday()]
        if today == "Samstag":
            brh_empty_day["day"] = "Sonntag"
            hofgarten_empty_day["day"] = "Sonntag"
            campo_empty_day["day"] = "Sonntag"
        # Give kirchenpavillon an old date so it also returns empty
        kp_old = kirchenpavillon_df.copy()
        kp_old["date"] = datetime.date(2000, 1, 1)
        data_map = {
            "brh_menu": brh_empty_day,
            "hofgarten_menu": hofgarten_empty_day,
            "campo_menu": campo_empty_day,
            "kirchenpavillon_menu": kp_old,
            "wolt_deals": wolt_deals_df,
        }
        with patch("lunch_feed.load", side_effect=lambda k: data_map[k]):
            print_feed_compact(failed=[])

        out = capsys.readouterr().out
        assert "(no data)" in out


class TestGermanDays:
    def test_monday_is_montag(self):
        assert GERMAN_DAYS[0] == "Montag"

    def test_sunday_is_sonntag(self):
        assert GERMAN_DAYS[6] == "Sonntag"

    def test_seven_days(self):
        assert len(GERMAN_DAYS) == 7
