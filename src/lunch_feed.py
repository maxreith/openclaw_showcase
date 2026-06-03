"""Lunch data feed for the OpenClaw WhatsApp workflow.

Refreshes all scraper caches (if stale) using parallel groups, then prints
mensa menus and deals to stdout for OpenClaw consumption. Status messages go
to stderr.
"""

import datetime
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "src")

import pandas as pd

from cache import age_minutes, cached, load, save

GERMAN_DAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

MAX_AGE = 180

SCRAPERS = [
    ("wolt_restaurants", "wolt.client", "get_restaurants"),
    ("wolt_deals", "wolt.client", "get_deals"),
    ("ubereats_restaurants", "ubereats.client", "get_restaurants"),
    ("ubereats_deals", "ubereats.client", "get_deals"),
    ("brh_menu", "bundesrechnungshof.client", "get_menu"),
    ("hofgarten_menu", "hofgarten.client", "get_menu"),
    ("campo_menu", "campo.client", "get_menu"),
    ("kirchenpavillon_menu", "kirchenpavillon.client", "get_menu"),
]

FEED_KEYS = [
    "brh_menu",
    "hofgarten_menu",
    "campo_menu",
    "kirchenpavillon_menu",
    "ubereats_deals",
    "wolt_deals",
]

SECTION_TITLES = {
    "brh_menu": "BRH Menu",
    "hofgarten_menu": "Hofgarten Menu",
    "campo_menu": "Mensa Campo Menu",
    "kirchenpavillon_menu": "Kirchenpavillon",
    "ubereats_deals": "Uber Eats Deals",
    "wolt_deals": "Wolt Deals",
}


def main():
    """Refresh caches and print the feed to stdout."""
    compact = "--compact" in sys.argv
    cache_only = "--cache-only" in sys.argv
    failed = [] if cache_only else refresh_caches()
    if compact:
        print_feed_compact(failed)
    else:
        print_feed(failed)
    sys.exit(1 if failed else 0)


def refresh_caches():
    """Refresh all scraper caches in parallel groups, returning list of failed keys."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_wolt = pool.submit(_refresh_wolt_group)
        fut_ubereats = pool.submit(_refresh_ubereats_group)
        fut_fast = pool.submit(_refresh_fast_group)

    all_failed = []
    for fut in [fut_wolt, fut_ubereats, fut_fast]:
        messages, failed = fut.result()
        for msg in messages:
            print(msg, file=sys.stderr)
        all_failed.extend(failed)
    return all_failed


def _refresh_group(scrapers: list[tuple[str, str, str]]) -> tuple[list[str], list[str]]:
    """Run a list of scrapers sequentially.

    Args:
        scrapers: List of (cache_key, module_path, func_name) tuples.

    Returns:
        Tuple of (status_messages, failed_keys).
    """
    from importlib import import_module

    messages = []
    failed = []
    for key, module_path, func_name in scrapers:
        mod = import_module(module_path)
        fn = getattr(mod, func_name)
        t0 = time.time()
        try:
            cached(key, fn, max_age_minutes=MAX_AGE)
            elapsed = time.time() - t0
            messages.append(f"  {key}: ok ({elapsed:.0f}s)")
        except Exception as e:
            elapsed = time.time() - t0
            messages.append(f"  {key}: FAILED ({elapsed:.0f}s) — {e}")
            failed.append(key)
    return messages, failed


def _refresh_wolt_group() -> tuple[list[str], list[str]]:
    """Run Wolt scrapers sequentially."""
    return _refresh_group([
        ("wolt_restaurants", "wolt.client", "get_restaurants"),
        ("wolt_deals", "wolt.client", "get_deals"),
    ])


def _refresh_ubereats_group() -> tuple[list[str], list[str]]:
    """Run Uber Eats scrapers in one browser session."""
    from ubereats.client import get_restaurants_and_deals

    keys = ("ubereats_restaurants", "ubereats_deals")
    if all((age := age_minutes(key)) is not None and age < MAX_AGE for key in keys):
        return [f"  {key}: ok (cached)" for key in keys], []

    t0 = time.time()
    try:
        restaurants_df, deals_df = get_restaurants_and_deals()
        save("ubereats_restaurants", restaurants_df)
        save("ubereats_deals", deals_df)
        elapsed = time.time() - t0
        return [f"  {key}: ok ({elapsed:.0f}s)" for key in keys], []
    except Exception as e:
        elapsed = time.time() - t0
        stale_restaurants = load("ubereats_restaurants")
        stale_deals = load("ubereats_deals")
        if stale_restaurants is not None and stale_deals is not None:
            age = age_minutes("ubereats_deals")
            return [f"  {key}: STALE ({elapsed:.0f}s, {age:.0f}m old) - {e}" for key in keys], []
        return [f"  {key}: FAILED ({elapsed:.0f}s) - {e}" for key in keys], list(keys)


def _refresh_fast_group() -> tuple[list[str], list[str]]:
    """Run fast scrapers (BRH, Hofgarten, Campo, Kirchenpavillon) sequentially."""
    return _refresh_group([
        ("brh_menu", "bundesrechnungshof.client", "get_menu"),
        ("hofgarten_menu", "hofgarten.client", "get_menu"),
        ("campo_menu", "campo.client", "get_menu"),
        ("kirchenpavillon_menu", "kirchenpavillon.client", "get_menu"),
    ])


def print_feed(failed):
    """Load cached DataFrames and print feed sections to stdout."""
    pd.set_option("display.max_colwidth", None)
    pd.set_option("display.max_rows", None)
    pd.set_option("display.width", None)

    for key in FEED_KEYS:
        title = SECTION_TITLES[key]
        print(f"=== {title} ===")
        if key in failed:
            print("(unavailable)")
        else:
            df = load(key)
            if df is None or df.empty:
                print("(no data)")
            else:
                print(df.to_string(index=False))
        print()


def print_feed_compact(failed):
    """Print a compact feed filtered to today's items with fewer columns."""
    today = GERMAN_DAYS[datetime.date.today().weekday()]

    sections = [
        ("BRH Menu", "brh_menu", _compact_brh, today),
        ("Hofgarten Menu", "hofgarten_menu", _compact_hofgarten, today),
        ("Mensa Campo", "campo_menu", _compact_campo, today),
        ("Kirchenpavillon", "kirchenpavillon_menu", _compact_kirchenpavillon, None),
        ("Uber Eats Deals", "ubereats_deals", _compact_ubereats, None),
        ("Wolt Deals", "wolt_deals", _compact_wolt, None),
    ]

    for title, key, transform, arg in sections:
        print(f"=== {title} ===")
        if key in failed:
            print("(unavailable)")
        else:
            df = load(key)
            if df is None or df.empty:
                print("(no data)")
            else:
                result = transform(df, arg) if arg else transform(df)
                if result.empty:
                    print("(no data)")
                else:
                    print(result.to_markdown(index=False))
        print()


def _compact_brh(df, today):
    """Filter BRH menu to today's Stammessen, Essen 2, and Nachhaltig."""
    keep = ("Stammessen", "Essen 2", "Nachhaltig")
    mask = (df["day"] == today) & df["category"].str.startswith(keep)
    return df.loc[mask, ["category", "name", "price_eur"]]


def _compact_hofgarten(df, today):
    """Filter Hofgarten menu to today's main dishes and soups."""
    mask = (df["day"] == today) & df["category"].isin(("Hauptgericht", "Suppe & Eintopf"))
    return df.loc[mask, ["name", "is_vegan", "price_student"]]


def _compact_campo(df, today):
    """Filter Mensa Campo menu to today's Spezial, Hauptgerichte, and Suppe."""
    keep = ("Unser Spezial", "Hauptgericht", "Suppe & Eintopf")
    mask = (df["day"] == today) & df["category"].isin(keep)
    return df.loc[mask, ["category", "name", "is_vegan", "price_student"]]


def _compact_kirchenpavillon(df):
    """Show today's dish and most recent prior day's dish (sold at discount)."""
    if "date" not in df.columns:
        return pd.DataFrame(columns=["slot", "name"])
    from kirchenpavillon.client import prev_dish_date

    today = datetime.date.today()
    rows = []
    today_rows = df[df["date"] == today]
    if not today_rows.empty:
        rows.append({"slot": "heute", "name": today_rows.iloc[0]["name"]})
    yesterday_date = prev_dish_date(df, today)
    if yesterday_date is not None:
        yest_rows = df[df["date"] == yesterday_date]
        if not yest_rows.empty:
            rows.append({"slot": "gestern (reduziert)", "name": yest_rows.iloc[0]["name"]})
    return pd.DataFrame(rows)


def _compact_wolt(df):
    """Select key columns from Wolt deals with computed new price in EUR."""
    result = df[["restaurant_name", "deal_title", "deal_type", "item_names"]].copy()
    empty = result["item_names"].isna() | (result["item_names"].str.strip() == "")
    result.loc[empty, "item_names"] = df.loc[empty, "category_names"]
    result["new_price"] = _compute_wolt_new_price(df)
    return result


def _compute_wolt_new_price(df):
    """Compute effective price in EUR based on deal type and discount fields.

    Args:
        df: Wolt deals DataFrame with item_prices, deal_type, discount_fraction,
            discount_amount columns.

    Returns:
        Series of new prices in EUR, rounded to 2 decimals.
    """
    first_price = df["item_prices"].apply(_first_price_cents)
    price = pd.Series(pd.NA, index=df.index, dtype="Float64")

    mask_tfo = df["deal_type"] == "two for one"
    price = price.mask(mask_tfo, first_price[mask_tfo])

    mask_pct = df["deal_type"] == "% off"
    fraction = df["discount_fraction"].astype("Float64")
    price = price.mask(mask_pct, first_price[mask_pct] * (1 - fraction[mask_pct]))

    mask_item = df["deal_type"] == "item discount"
    amount = df["discount_amount"].astype("Float64")
    price = price.mask(mask_item, first_price[mask_item] - amount[mask_item])

    return (price / 100).round(2)


def _compact_ubereats(df):
    """Select key columns from Uber Eats deals with derived deal type and price."""
    result = df[["restaurant_name", "item_title"]].copy()
    result["deal_type"] = df["promo_type"].apply(_ubereats_deal_type)
    result["deal_type"] = result["deal_type"].fillna(df["deal_description"])
    result["effective_price"] = _compute_ubereats_effective_price(df)
    return result


def _ubereats_deal_type(promo_type):
    """Map Uber Eats promo_type to a human-friendly label."""
    if pd.isna(promo_type) or str(promo_type).strip() == "":
        return pd.NA
    if promo_type == "buyXGetYItemPromotion":
        return "two for one"
    return pd.NA


def _compute_ubereats_effective_price(df):
    """Compute effective price in EUR for Uber Eats deals."""
    price_cents = df["original_price"].astype("Float64")
    buy = df["buy_quantity"].astype("Float64")
    get = df["get_quantity"].astype("Float64")

    is_bogo = df["promo_type"] == "buyXGetYItemPromotion"
    effective = price_cents.copy()
    effective = effective.mask(is_bogo, price_cents / (buy + get) * buy)
    return (effective / 100).round(2)


def _first_price_cents(val):
    """Extract the first price (in cents) from an item_prices string or list.

    Args:
        val: Comma-separated string of prices, a list, or a single value.

    Returns:
        First price as float, or pd.NA if unavailable.
    """
    if isinstance(val, (list, tuple)):
        return float(val[0]) if val else pd.NA
    if pd.isna(val) or str(val).strip() == "":
        return pd.NA
    return float(str(val).split(",")[0].strip())


if __name__ == "__main__":
    main()
