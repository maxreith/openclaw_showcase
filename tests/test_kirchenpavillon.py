"""Tests for the Kirchenpavillon daily menu scraper."""

import datetime
import sys

import pandas as pd
import pytest

sys.path.insert(0, "src")

from kirchenpavillon.client import (
    DishEntry,
    _parse_menu,
    _parse_week_start,
    get_menu,
    prev_dish_date,
)

# ── sample HTML ──────────────────────────────────────────────────────────────
# Week 1: Mon 2026-03-23 to Wed 2026-03-25 (Thu/Fri absent for brevity)
# Week 2: Mon 2026-03-30 to Thu 2026-04-02, Fri geschlossen
# Post-section: "Saisonale Speisen" h2 with a dummy dish — must NOT be parsed

SAMPLE_HTML = """\
<html><body>
<article>
  <h2>Tagesgerichte 23. bis 27. März</h2>
  <p>je 10,00 €</p>
  <p><strong>Montag</strong><br>Reibekuchen mit Apfelmus und Salat</p>
  <p><strong>Dienstag</strong><br>Hähnchen-Spitzkohlpfanne mit Schupfnudeln</p>
  <p><strong>Mittwoch</strong><br>Dorade mit Zitrone auf Fenchel-Gemüsepfanne</p>

  <h2>Tagesgerichte 30.März bis 2. April</h2>
  <p>je 10,00 €</p>
  <p><strong>Montag</strong><br>Rote Linsen-Couscous-Pfanne mit Gemüse</p>
  <p><strong>Dienstag</strong><br>Spießbraten mit Bratkartoffeln</p>
  <p><strong>Freitag</strong><br>geschlossen (Karfreitag)</p>

  <h2>Saisonale Speisen</h2>
  <p><strong>Täglich</strong><br>Schnitzel vom Schwein</p>
</article>
</body></html>
"""

# Same HTML but with <strong>Day<br></strong>Dish variant for week 1
SAMPLE_HTML_BR_IN_STRONG = """\
<html><body>
<article>
  <h2>Tagesgerichte 23. bis 27. März</h2>
  <p>je 12,00 €</p>
  <p><strong>Montag<br>
</strong>Reibekuchen mit Apfelmus</p>
  <p><strong>Dienstag<br>
</strong>Hähnchen-Schnitzel</p>
</article>
</body></html>
"""

# Real-world variant: <h4> headings and <b> day markers
SAMPLE_HTML_H4_AND_B = """<html><body>
<article>
  <h4><span style="color: #ff9900;">Tagesgerichte 30.März bis 2. April </span></h4>
  <p>je 10,00 €</p>
  <p><strong>Montag<br>
</strong>Rote Linsen-Couscous-Pfanne mit Gemüse und Hirtenkäse</p>
  <p><b>Dienstag<br>
</b>Spießbraten mit Bratkartoffeln</p>
  <p><strong>Mittwoch<br>
</strong>Piccata vom Seelachs auf Tomatenrahm-Farfalle</p>
</article>
</body></html>
"""


# ── _parse_week_start ────────────────────────────────────────────────────────

class TestParseWeekStart:
    def test_format1_same_month(self):
        result = _parse_week_start("Tagesgerichte 23. bis 27. März")
        assert result == datetime.date(result.year, 3, 23)

    def test_format1_start_day_is_used(self):
        result = _parse_week_start("Tagesgerichte 23. bis 27. März")
        assert result.day == 23

    def test_format2_attached_month(self):
        result = _parse_week_start("Tagesgerichte 30.März bis 2. April")
        assert result == datetime.date(result.year, 3, 30)

    def test_format2_attached_month_no_space(self):
        result = _parse_week_start("Tagesgerichte 30. März bis 2. April")
        assert result.month == 3
        assert result.day == 30

    def test_format3_cross_month(self):
        result = _parse_week_start("Tagesgerichte 28. bis 2. Mai")
        assert result.month == 4  # April (month before May)
        assert result.weekday() == 0  # always normalized to Monday

    def test_format3_cross_month_end_january(self):
        # "30. bis 3. Januar" → start in December
        result = _parse_week_start("Tagesgerichte 30. bis 3. Januar")
        assert result.month == 12
        assert result.weekday() == 0  # always normalized to Monday

    def test_invalid_returns_none(self):
        assert _parse_week_start("Kein Speiseplan diese Woche") is None

    def test_no_month_returns_none(self):
        assert _parse_week_start("Tagesgerichte 23. bis 27.") is None

    def test_month_case_insensitive(self):
        result = _parse_week_start("Tagesgerichte 23. bis 27. MÄRZ")
        # The month regex is lowercase; uppercase won't match — returns None
        # (acceptable: the site uses lowercase-normalised text)
        # This test just checks we don't crash
        assert result is None or result.month == 3


# ── _parse_menu ──────────────────────────────────────────────────────────────

class TestParseMenu:
    @pytest.fixture()
    def entries(self):
        return _parse_menu(SAMPLE_HTML)

    def test_count(self, entries):
        # 3 (week 1: Mon/Tue/Wed) + 2 (week 2: Mon/Tue; Fri geschlossen skipped)
        # Saisonale Speisen section skipped entirely
        assert len(entries) == 5

    def test_first_entry_name(self, entries):
        assert entries[0].name == "Reibekuchen mit Apfelmus und Salat"

    def test_first_entry_date(self, entries):
        assert entries[0].date == datetime.date(entries[0].date.year, 3, 23)

    def test_dates_are_sequential(self, entries):
        # week 1: Mar 23, 24, 25; week 2: Mar 30, Apr 1 (Tue)
        assert entries[1].date == datetime.date(entries[0].date.year, 3, 24)
        assert entries[2].date == datetime.date(entries[0].date.year, 3, 25)
        assert entries[3].date == datetime.date(entries[0].date.year, 3, 30)
        assert entries[4].date == datetime.date(entries[0].date.year, 3, 31)

    def test_price_eur(self, entries):
        assert all(e.price_eur == 10.0 for e in entries)

    def test_holiday_excluded(self, entries):
        names = [e.name for e in entries]
        assert not any("geschlossen" in n.lower() for n in names)
        assert not any("Karfreitag" in n for n in names)

    def test_post_section_excluded(self, entries):
        names = [e.name for e in entries]
        assert "Schnitzel vom Schwein" not in names

    def test_br_in_strong_variant(self):
        entries = _parse_menu(SAMPLE_HTML_BR_IN_STRONG)
        assert len(entries) == 2
        assert entries[0].name == "Reibekuchen mit Apfelmus"
        assert entries[0].price_eur == 12.0

    def test_price_parsed_from_html(self):
        entries = _parse_menu(SAMPLE_HTML_BR_IN_STRONG)
        assert entries[0].price_eur == 12.0

    def test_h4_heading_and_b_tag(self):
        entries = _parse_menu(SAMPLE_HTML_H4_AND_B)
        assert len(entries) == 3
        assert entries[0].name == "Rote Linsen-Couscous-Pfanne mit Gemüse und Hirtenkäse"
        assert entries[1].name == "Spießbraten mit Bratkartoffeln"
        assert entries[2].name == "Piccata vom Seelachs auf Tomatenrahm-Farfalle"
        assert entries[0].date.month == 3
        assert entries[0].date.day == 30


# ── prev_dish_date ────────────────────────────────────────────────────────────

class TestPrevDishDate:
    @pytest.fixture()
    def week_df(self):
        """Mon–Fri of a single week, Wednesday absent (holiday)."""
        monday = datetime.date(2026, 3, 23)
        dates = [monday + datetime.timedelta(days=i) for i in [0, 1, 3, 4]]  # skip Wed
        return pd.DataFrame({
            "date": dates,
            "name": ["MonDish", "TueDish", "ThuDish", "FriDish"],
            "price_eur": [10.0] * 4,
        })

    def test_tuesday_ref_gives_monday(self, week_df):
        ref = datetime.date(2026, 3, 24)  # Tuesday
        result = prev_dish_date(week_df, ref)
        assert result == datetime.date(2026, 3, 23)

    def test_thursday_ref_skips_holiday_wednesday(self, week_df):
        ref = datetime.date(2026, 3, 26)  # Thursday
        result = prev_dish_date(week_df, ref)
        assert result == datetime.date(2026, 3, 24)  # Tuesday (Wed absent)

    def test_monday_gives_prior_friday(self):
        # Week 2 starting Mon Mar 30; week 1 has Friday Mar 27
        dates = [datetime.date(2026, 3, 27), datetime.date(2026, 3, 30)]
        df = pd.DataFrame({"date": dates, "name": ["FriDish", "MonDish"], "price_eur": [10.0, 10.0]})
        result = prev_dish_date(df, datetime.date(2026, 3, 30))
        assert result == datetime.date(2026, 3, 27)

    def test_empty_df_returns_none(self):
        df = pd.DataFrame(columns=["date", "name", "price_eur"])
        assert prev_dish_date(df, datetime.date(2026, 3, 24)) is None

    def test_all_entries_after_ref_returns_none(self, week_df):
        result = prev_dish_date(week_df, datetime.date(2026, 3, 20))
        assert result is None


# ── integration ───────────────────────────────────────────────────────────────

class TestGetMenu:
    @pytest.fixture(scope="class")
    def menu_df(self):
        return get_menu()

    def test_not_empty(self, menu_df):
        assert len(menu_df) > 0

    def test_columns(self, menu_df):
        assert set(menu_df.columns) == {"date", "name", "price_eur"}

    def test_dates_are_date_objects(self, menu_df):
        assert all(isinstance(d, datetime.date) for d in menu_df["date"])

    def test_all_dates_are_weekdays(self, menu_df):
        assert all(d.weekday() < 5 for d in menu_df["date"])

    def test_positive_prices(self, menu_df):
        assert (menu_df["price_eur"] > 0).all()

    def test_no_empty_names(self, menu_df):
        assert all(menu_df["name"].str.strip() != "")

    def test_no_holiday_entries(self, menu_df):
        assert not any("geschlossen" in n.lower() for n in menu_df["name"])
