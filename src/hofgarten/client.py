"""Client for fetching the weekly Mensa am Hofgarten menu.

Queries the Studierendenwerk Bonn AJAX endpoint that serves structured HTML
for each day. No browser automation needed — plain GET requests via httpx.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from html.parser import HTMLParser
from random import uniform
from time import sleep

import httpx
import pandas as pd

pd.options.future.infer_string = True

BASE_URL = "https://www.studierendenwerk-bonn.de/"
AJAX_TYPE = "1732731666"

DAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:137.0) Gecko/20100101 Firefox/137.0",
}


@dataclass
class MensaItem:
    """A single dish from the daily mensa menu."""

    day: str
    category: str
    name: str
    is_vegan: bool
    price_student: float
    price_staff: float
    price_guest: float
    allergens: list[str] = field(default_factory=list)
    additives: list[str] = field(default_factory=list)


def get_menu(canteen_id: int = 3) -> pd.DataFrame:
    """Fetch this week's menu for the given canteen.

    Args:
        canteen_id: Studierendenwerk canteen identifier (3 = Hofgarten).

    Returns:
        DataFrame with columns matching MensaItem fields plus week_label.
    """
    items: list[MensaItem] = []
    dates = _week_dates()

    for day_name, date_str in dates:
        day_html = _fetch_day(date_str, canteen_id)
        day_items = _parse_meals(day_html)
        for item in day_items:
            item.day = day_name
        items.extend(day_items)
        if date_str != dates[-1][1]:
            sleep(uniform(0.5, 1.5))

    df = pd.DataFrame([vars(item) for item in items])
    if not df.empty:
        df["week_label"] = f"{dates[0][1]} – {dates[-1][1]}"
    return df


def _fetch_day(date_str: str, canteen_id: int) -> str:
    """GET the AJAX endpoint for a single day and return HTML.

    Args:
        date_str: Date in YYYY-MM-DD format.
        canteen_id: Studierendenwerk canteen identifier.

    Returns:
        Raw HTML string from the AJAX response.
    """
    resp = httpx.get(
        BASE_URL,
        params={
            "type": AJAX_TYPE,
            "tx_festwb_mealsajax[date]": date_str,
            "tx_festwb_mealsajax[canteen]": str(canteen_id),
            "tx_festwb_mealsajax[language]": "0",
        },
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.text


def _parse_meals(html_text: str) -> list[MensaItem]:
    """Parse AJAX HTML response into MensaItem objects.

    Args:
        html_text: Raw HTML from the AJAX endpoint.

    Returns:
        List of MensaItem with day field left empty (caller sets it).
    """
    parser = _MealParser()
    parser.feed(html_text)
    return parser.meals


def _week_dates() -> list[tuple[str, str]]:
    """Return (day_name, 'YYYY-MM-DD') pairs for current week Mon–Fri.

    Returns:
        List of 5 tuples from Monday to Friday.
    """
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return [
        (DAYS_DE[i], (monday + timedelta(days=i)).isoformat())
        for i in range(5)
    ]


def _clean_meal_name(name: str) -> tuple[str, bool]:
    """Strip '(VEGAN)' suffix and extra whitespace from meal name.

    Args:
        name: Raw meal name from HTML.

    Returns:
        Tuple of (cleaned_name, is_vegan).
    """
    name = " ".join(name.split())
    is_vegan = "(VEGAN)" in name
    if is_vegan:
        name = " ".join(name.replace("(VEGAN)", "").split())
    return name, is_vegan


def _parse_price(text: str) -> float:
    """Convert price string like '2,90 €' to float 2.90.

    Args:
        text: Price string with comma decimal separator.

    Returns:
        Price as float.
    """
    return float(text.replace("€", "").replace(",", ".").strip())


class _MealParser(HTMLParser):
    """Stateful HTML parser that extracts MensaItem data from AJAX response."""

    def __init__(self):
        super().__init__()
        self.meals: list[MensaItem] = []
        self._category = ""
        self._in_h2 = False
        self._h2_text = ""
        self._in_h5 = False
        self._name = ""
        self._in_strong = False
        self._strong_text = ""
        self._section = ""
        self._in_p = False
        self._p_text = ""
        self._in_price_table = False
        self._in_th = False
        self._th_text = ""
        self._in_td = False
        self._td_text = ""
        self._current_th = ""
        self._allergens: list[str] = []
        self._additives: list[str] = []
        self._prices: dict[str, float] = {}

    def handle_starttag(self, tag, attrs):
        """Track entry into relevant HTML elements."""
        attr_dict = dict(attrs)
        cls = attr_dict.get("class", "")

        if tag == "h2":
            self._in_h2 = True
            self._h2_text = ""
        elif tag == "h5":
            self._in_h5 = True
            self._name = ""
            self._allergens = []
            self._additives = []
            self._prices = {}
            self._section = ""
        elif tag == "strong":
            self._in_strong = True
            self._strong_text = ""
        elif tag == "p" and self._section:
            self._in_p = True
            self._p_text = ""
        elif tag == "table" and "menus__result__prices" in cls:
            self._in_price_table = True
        elif tag == "th" and self._in_price_table:
            self._in_th = True
            self._th_text = ""
        elif tag == "td" and self._in_price_table and self._current_th:
            self._in_td = True
            self._td_text = ""

    def handle_endtag(self, tag):
        """Process data collected within closed elements."""
        if tag == "h2" and self._in_h2:
            self._in_h2 = False
            text = self._h2_text.strip()
            if text and "Erstelle" not in text:
                self._category = text
        elif tag == "h5" and self._in_h5:
            self._in_h5 = False
        elif tag == "strong" and self._in_strong:
            self._in_strong = False
            text = self._strong_text.strip()
            if text in ("Allergene", "Zusatzstoffe"):
                self._section = text
            else:
                self._section = ""
        elif tag == "p" and self._in_p:
            self._in_p = False
            text = self._p_text.strip()
            if text:
                if self._section == "Allergene":
                    self._allergens.append(text)
                elif self._section == "Zusatzstoffe":
                    self._additives.append(text)
        elif tag == "th" and self._in_th:
            self._in_th = False
            self._current_th = self._th_text.strip()
        elif tag == "td" and self._in_td:
            self._in_td = False
            text = self._td_text.strip()
            if self._current_th and text:
                self._prices[self._current_th] = _parse_price(text)
            self._current_th = ""
        elif tag == "table" and self._in_price_table:
            self._in_price_table = False
            self._emit_meal()

    def handle_data(self, data):
        """Collect text content from tracked elements."""
        if self._in_h2:
            self._h2_text += data
        elif self._in_h5:
            self._name += data
        elif self._in_strong:
            self._strong_text += data
        elif self._in_p:
            self._p_text += data
        elif self._in_th:
            self._th_text += data
        elif self._in_td:
            self._td_text += data

    def _emit_meal(self):
        """Create a MensaItem from accumulated state and append to results."""
        if not self._name.strip() or not self._prices:
            return
        name, is_vegan = _clean_meal_name(self._name)
        self.meals.append(MensaItem(
            day="",
            category=self._category,
            name=name,
            is_vegan=is_vegan,
            price_student=self._prices.get("Stud.", 0.0),
            price_staff=self._prices.get("Bed.", 0.0),
            price_guest=self._prices.get("Gast", 0.0),
            allergens=list(self._allergens),
            additives=list(self._additives),
        ))


if __name__ == "__main__":
    df = get_menu()
    pd.set_option("display.max_rows", None)
    pd.set_option("display.max_colwidth", 80)
    print(df.to_string(index=False))
