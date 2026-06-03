"""Client for fetching the daily menu from Kirchenpavillon (ekir.de).

Scrapes the WordPress menu page, extracting day-specific dish entries keyed by
actual date. Returns a DataFrame with one row per available business day;
holiday closures are excluded. No browser automation needed — plain GET via httpx.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from html.parser import HTMLParser

import httpx
import pandas as pd

pd.options.future.infer_string = True

MENU_URL = "https://kirchenpavillon.ekir.de/inhalt/speisekarte/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

GERMAN_DAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]

GERMAN_MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8,
    "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}

_MONTH_RE = "|".join(GERMAN_MONTHS)


@dataclass
class DishEntry:
    """A single daily dish from the Kirchenpavillon menu."""

    date: date
    name: str
    price_eur: float


def get_menu() -> pd.DataFrame:
    """Fetch and parse all available daily dishes from the Kirchenpavillon menu page.

    Returns:
        DataFrame with columns: date, name, price_eur.
        One row per available business day (holidays excluded).
    """
    html = _fetch_page()
    entries = _parse_menu(html)
    if not entries:
        return pd.DataFrame(columns=["date", "name", "price_eur"])
    return pd.DataFrame([vars(e) for e in entries])


def prev_dish_date(menu_df: pd.DataFrame, ref_date: date) -> date | None:
    """Return the most recent dish date strictly before ref_date.

    Because holiday and weekend entries are never added to the DataFrame,
    this naturally resolves Monday → prior Friday, and skips closed holidays.

    Args:
        menu_df: DataFrame returned by get_menu().
        ref_date: Reference date (typically today).

    Returns:
        Most recent date before ref_date, or None if no earlier entry exists.
    """
    earlier = menu_df[menu_df["date"] < ref_date]
    if earlier.empty:
        return None
    return earlier["date"].max()


def _fetch_page() -> str:
    """GET the Kirchenpavillon menu page and return raw HTML."""
    resp = httpx.get(MENU_URL, headers=HEADERS, follow_redirects=True, timeout=15)
    resp.raise_for_status()
    return resp.text


def _parse_menu(html: str) -> list[DishEntry]:
    """Parse the raw HTML into a list of DishEntry objects."""
    parser = _MenuParser()
    parser.feed(html)
    return parser.entries


def _parse_week_start(h2_text: str) -> date | None:
    """Parse the Monday start date from a 'Tagesgerichte ...' H2 heading.

    Handles three formats:
      Format 1 (same month):   "23. bis 27. März"    → March 23
      Format 2 (attached):     "30.März bis 2. April" → March 30
      Format 3 (cross-month):  "28. bis 2. Mai"       → April 28

    Args:
        h2_text: The full H2 text including 'Tagesgerichte ' prefix.

    Returns:
        The date of Monday (start of week), or None on parse failure.
    """
    text = h2_text.strip()
    text_lower = text.lower()

    nums = [int(x) for x in re.findall(r'\d+', text)]
    all_months = re.findall(rf'({_MONTH_RE})', text_lower)

    if not nums or not all_months:
        return None

    start_day = nums[0]
    end_day = nums[1] if len(nums) > 1 else start_day

    if len(all_months) >= 2:
        # Two months present: first belongs to start, second to end
        start_month = GERMAN_MONTHS[all_months[0]]
    else:
        # One month: it belongs to the end date
        end_month = GERMAN_MONTHS[all_months[0]]
        # If start_day > end_day the week crosses a month boundary
        start_month = end_month if start_day <= end_day else (end_month - 1 or 12)

    today = date.today()
    year = today.year
    # Handle year boundary: December start crossing into January end
    if start_month == 12 and today.month == 1:
        year -= 1
    elif start_month == 1 and today.month == 12:
        year += 1

    try:
        parsed = date(year, start_month, start_day)
        # Normalize to Monday of the same week (handles headings that start mid-week)
        return parsed - timedelta(days=parsed.weekday())
    except ValueError:
        return None


_HEADING_TAGS = {'h2', 'h3', 'h4', 'h5', 'h6'}


class _MenuParser(HTMLParser):
    """Stateful HTML parser for the Kirchenpavillon WordPress menu page."""

    def __init__(self):
        super().__init__()
        self.entries: list[DishEntry] = []
        self._in_tagesgerichte = False
        self._current_week_start: date | None = None
        self._week_price = 10.0

        self._in_h2 = False
        self._h2_text = ""

        self._in_strong = False
        self._strong_text = ""

        self._current_day_date: date | None = None
        self._dish_text = ""
        self._collecting_dish = False

    def handle_starttag(self, tag, attrs):
        if tag in _HEADING_TAGS:
            self._in_h2 = True
            self._h2_text = ""
            self._collecting_dish = False
            self._dish_text = ""
        elif tag in ("strong", "b"):
            self._in_strong = True
            self._strong_text = ""

    def handle_endtag(self, tag):
        if tag in _HEADING_TAGS and self._in_h2:
            self._in_h2 = False
            h2 = self._h2_text.strip()
            if h2.startswith("Tagesgerichte"):
                week_start = _parse_week_start(h2)
                if week_start is not None:
                    self._current_week_start = week_start
                    self._week_price = 10.0
                    self._in_tagesgerichte = True
            else:
                self._in_tagesgerichte = False

        elif tag in ("strong", "b") and self._in_strong:
            self._in_strong = False
            day = self._strong_text.strip()
            if day in GERMAN_DAYS and self._in_tagesgerichte and self._current_week_start:
                offset = GERMAN_DAYS.index(day)
                self._current_day_date = self._current_week_start + timedelta(days=offset)
                self._collecting_dish = True
                self._dish_text = ""

        elif tag == "p" and self._collecting_dish:
            dish = self._dish_text.strip()
            if dish and "geschlossen" not in dish.lower():
                self.entries.append(DishEntry(
                    date=self._current_day_date,
                    name=dish,
                    price_eur=self._week_price,
                ))
            self._collecting_dish = False
            self._dish_text = ""
            self._current_day_date = None

    def handle_data(self, data):
        if self._in_h2:
            self._h2_text += data
        elif self._in_strong:
            self._strong_text += data
        elif self._collecting_dish:
            self._dish_text += data
        elif self._in_tagesgerichte:
            # Capture the per-week price from "je 10,00 €" lines
            m = re.search(r'je\s+(\d+),(\d{2})\s*€', data)
            if m:
                self._week_price = float(f"{m.group(1)}.{m.group(2)}")


if __name__ == "__main__":
    df = get_menu()
    pd.set_option("display.max_rows", None)
    pd.set_option("display.max_colwidth", 80)
    print(df.to_string(index=False))
