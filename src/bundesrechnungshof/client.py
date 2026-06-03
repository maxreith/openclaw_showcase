"""Client for fetching the weekly menu from Catering Günther (Bundesrechnungshof casino).

Downloads the current week's PDF menu from catering-guenther.de and extracts
the structured table using pymupdf. No OCR or AI API needed — the PDF has
a proper text layer.
"""

import re
from dataclasses import dataclass

import httpx
import pandas as pd
import pymupdf

pd.options.future.infer_string = True

MENU_PAGE_URL = "https://www.catering-guenther.de/unsere-speisekarte/"
BASE_URL = "https://www.catering-guenther.de"

DAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass
class MenuItem:
    """A single dish from the weekly menu."""

    day: str
    category: str
    name: str
    price_eur: float | None


def get_menu() -> pd.DataFrame:
    """Download and parse the current weekly menu into a DataFrame.

    Returns:
        DataFrame with columns: day, category, name, price_eur, week_label.
    """
    pdf_url = _find_pdf_url()
    pdf_bytes = _download_pdf(pdf_url)
    rows = _parse_pdf(pdf_bytes)
    week_label = _extract_week_label(pdf_bytes)

    df = pd.DataFrame([vars(item) for item in rows])
    df["week_label"] = week_label
    return df


def _find_pdf_url() -> str:
    """Scrape the menu page to find the current week's PDF link."""
    resp = httpx.get(MENU_PAGE_URL, headers=HEADERS, follow_redirects=True, timeout=15)
    resp.raise_for_status()
    match = re.search(r'href\s*=\s*"([^"]+/BRH\d{4}-KW\d+\.pdf)"', resp.text)
    if not match:
        raise ValueError("Could not find menu PDF link on the page")
    url = match.group(1)
    if url.startswith("/"):
        url = BASE_URL + url
    return url


def _download_pdf(url: str) -> bytes:
    """Download the PDF and return raw bytes."""
    resp = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=15)
    resp.raise_for_status()
    return resp.content


def _extract_week_label(pdf_bytes: bytes) -> str:
    """Extract the 'Speiseplan vom ... bis ...' label from the PDF."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    text = page.get_text()
    match = re.search(r"Speiseplan vom [\d.]+ bis [\d.]+\d{4}", text)
    return match.group(0) if match else ""


def _parse_pdf(pdf_bytes: bytes) -> list[MenuItem]:
    """Extract menu items from the PDF table structure."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    tables = page.find_tables()

    menu_table = _find_menu_table(tables)
    if menu_table is None:
        raise ValueError("Could not find the menu table in the PDF")

    data = menu_table.extract()
    return _parse_table_rows(data)


def _find_menu_table(tables: pymupdf.table.TableFinder) -> pymupdf.table.Table | None:
    """Find the main menu table (the one with day-of-week headers)."""
    for table in tables.tables:
        header = table.extract()[0]
        cell_texts = [c for c in header if c]
        if any(day in cell_texts for day in DAYS):
            return table
    return None


def _parse_table_rows(data: list[list[str | None]]) -> list[MenuItem]:
    """Parse extracted table data into MenuItem objects."""
    header = data[0]
    day_cols = {}
    for col_idx, cell in enumerate(header):
        if cell and cell.strip() in DAYS:
            day_cols[cell.strip()] = col_idx

    items: list[MenuItem] = []
    for row in data[1:]:
        category = _clean_text(row[0]) if row[0] else ""
        if not category or category == "Tagestipp":
            continue

        if _is_taglich_row(row, day_cols):
            items.extend(_expand_taglich_row(row, day_cols, category))
        else:
            for day, col_idx in day_cols.items():
                cell = row[col_idx] if col_idx < len(row) else None
                if not cell or not cell.strip():
                    continue
                items.extend(_parse_cell(cell, day, category))

    return items


def _is_taglich_row(row: list[str | None], day_cols: dict[str, int]) -> bool:
    """Check if the first day column says 'Täglich'."""
    first_col = min(day_cols.values())
    cell = row[first_col] if first_col < len(row) else None
    return bool(cell and cell.strip() == "Täglich")


def _expand_taglich_row(
    row: list[str | None],
    day_cols: dict[str, int],
    category: str,
) -> list[MenuItem]:
    """Collect items from a 'Täglich' row and replicate for every weekday."""
    templates: list[MenuItem] = []
    for col_idx in day_cols.values():
        cell = row[col_idx] if col_idx < len(row) else None
        if not cell or cell.strip() == "Täglich" or _is_info_only(cell):
            continue
        templates.extend(_parse_cell(cell, "__skip__", category))

    items: list[MenuItem] = []
    for day in DAYS:
        for t in templates:
            items.append(MenuItem(day=day, category=t.category, name=t.name, price_eur=t.price_eur))
    return items


def _is_info_only(cell: str) -> bool:
    """Check if a cell is just informational text, not a menu item."""
    info_phrases = [
        "Alle Essen",
        "Alle Komponenten",
        "kombinierbar",
        "auch to Go",
    ]
    return any(phrase in cell for phrase in info_phrases)


def _parse_cell(raw_cell: str, day: str, category: str) -> list[MenuItem]:
    """Parse a single table cell into one or more MenuItems."""
    if not raw_cell or "-Siehe Aushang-" in raw_cell:
        return []

    text = raw_cell.strip()
    text = re.sub(r"^Tagestipp\s*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^Aus dem Pizzaofen\s*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^Vegan\n", "", text, flags=re.MULTILINE)

    lines = _merge_continuation_lines(text.split("\n"))

    items = []
    for line in lines:
        line = re.sub(r"\s+", " ", line).strip()
        if not line or _is_info_only(line) or _is_subtitle(line):
            continue
        for chunk in _split_into_dishes(line):
            name, price = _extract_price(chunk)
            name = name.strip().strip(",")
            if name and not _is_info_only(name):
                items.append(MenuItem(day=day, category=category, name=name, price_eur=price))
    return items


def _merge_continuation_lines(lines: list[str]) -> list[str]:
    """Merge lines that are continuations of the previous dish.

    Rules:
    - Price-only lines (e.g. '3,80 €') always merge up.
    - Size indicators (Kl./Klein/Groß) always merge up.
    - Preposition lines (mit/und/dazu/oder/in) merge up only if the
      previous line does NOT already contain a price (meaning the dish
      description is still ongoing).
    """
    price_only = re.compile(r"^\s*\d+,\d{2}\s*€.*$")
    size_indicator = re.compile(r"^\s*(Kl\.|Klein\b|Groß\b|Gr\.)")
    preposition = re.compile(r"^\s*(mit\b|und\b|dazu\b|oder\b|in\b)")
    has_price = re.compile(r"\d+,\d{2}\s*€")
    size_no_euro = re.compile(r"Kl\.\s*\d+,\d{2}\s*/\s*Gr\.\s*\d+,\d{2}")

    merged: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if not merged:
            merged.append(stripped)
            continue

        if price_only.match(stripped) or size_indicator.match(stripped):
            merged[-1] = merged[-1] + " " + stripped
        elif preposition.match(stripped) and not has_price.search(merged[-1]):
            merged[-1] = merged[-1] + " " + stripped
        else:
            merged.append(stripped)

    result: list[str] = []
    for line in merged:
        if result and not has_price.search(result[-1]) and not size_no_euro.search(result[-1]):
            result[-1] = result[-1] + " " + line
        else:
            result.append(line)
    return result


def _split_into_dishes(text: str) -> list[str]:
    """Split a cell with multiple dishes into individual dish strings.

    Uses price patterns as dish boundaries.  Size-variant prices like
    'Kl. 2,50 € / Gr. 3,00 €' or 'Klein 1,90 € Groß 3,80 €' count
    as a single boundary.
    """
    size_variant = re.compile(
        r"Kl\.?\s*\d+,\d{2}\s*€?\s*/\s*Gr\.?\s*\d+,\d{2}\s*€?"
        r"|Klein\s*\d+,\d{2}\s*€\s*Groß\s*\d+,\d{2}\s*€"
    )
    simple_price = re.compile(r"\d+,\d{2}\s*€(?:\s*MSC)?")

    boundaries: list[tuple[int, int]] = []
    for m in size_variant.finditer(text):
        boundaries.append((m.start(), m.end()))

    for m in simple_price.finditer(text):
        if not any(s <= m.start() and m.end() <= e for s, e in boundaries):
            boundaries.append((m.start(), m.end()))

    boundaries.sort()

    if not boundaries:
        return [text] if text.strip() else []

    chunks = []
    start = 0
    for _, end in boundaries:
        chunks.append(text[start:end].strip())
        start = end

    remainder = text[start:].strip()
    if remainder and not _is_info_only(remainder) and not _is_subtitle(remainder):
        chunks.append(remainder)

    return [c for c in chunks if c]


def _is_subtitle(text: str) -> bool:
    """Check if remaining text is a dish subtitle (e.g. quoted style name)."""
    return bool(re.match(r'^"[^"]*"$', text.strip()))


def _extract_price(text: str) -> tuple[str, float | None]:
    """Extract price from text, returning (name, price).

    For size variants (Kl./Gr. or Klein/Groß), returns the small price.
    """
    size_kl = re.search(
        r"Kl\.?\s*(\d+,\d{2})\s*€?\s*/\s*Gr\.?\s*\d+,\d{2}\s*€?", text
    )
    if size_kl:
        price = float(size_kl.group(1).replace(",", "."))
        name = text[: size_kl.start()].strip()
        return name, price

    size_klein = re.search(
        r"Klein\s*(\d+,\d{2})\s*€\s*Groß\s*\d+,\d{2}\s*€", text
    )
    if size_klein:
        price = float(size_klein.group(1).replace(",", "."))
        name = text[: size_klein.start()].strip()
        return name, price

    match = re.search(r"(\d+,\d{2})\s*€(?:\s*MSC)?", text)
    if not match:
        return text, None
    price = float(match.group(1).replace(",", "."))
    name = text[: match.start()].strip()
    return name, price


def _clean_text(text: str | None) -> str:
    """Normalize whitespace in extracted text."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return text


if __name__ == "__main__":
    df = get_menu()
    pd.set_option("display.max_rows", None)
    pd.set_option("display.max_colwidth", 60)
    print(df.to_string(index=False))
