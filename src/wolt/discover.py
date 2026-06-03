"""Discovery script for Wolt API — validate JSON paths before writing parsers.

Fetches consumer API listing and venue dynamic data to confirm
deal structure and identify test anchor restaurants.
"""

import json
from pathlib import Path

import httpx

BLD_DIR = Path("bld")
BONN_LAT = 50.7346
BONN_LON = 7.0997


def discover_listing():
    """Fetch consumer API listing and save to bld/wolt_listing.json."""
    BLD_DIR.mkdir(exist_ok=True)

    url = "https://consumer-api.wolt.com/v1/pages/restaurants"
    params = {"lat": BONN_LAT, "lon": BONN_LON}

    print(f"Fetching {url} ...")
    resp = httpx.get(url, params=params, timeout=30)
    print(f"Status: {resp.status_code}")
    resp.raise_for_status()
    data = resp.json()

    out = BLD_DIR / "wolt_listing.json"
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"Saved listing ({len(json.dumps(data))} bytes) to {out}")

    explore_listing(data)
    return data


def explore_listing(data: dict):
    """Print structure of listing response."""
    print(f"\nTop-level keys: {list(data.keys())}")

    sections = data.get("sections", [])
    print(f"Sections: {len(sections)}")
    for i, section in enumerate(sections):
        name = section.get("name", "?")
        title = section.get("title", "?")
        items = section.get("items", [])
        print(f"  [{i}] name={name!r} title={title!r} items={len(items)}")

        if items and i < 5:
            item = items[0]
            print(f"    First item keys: {list(item.keys())[:15]}")
            venue = item.get("venue", {})
            if venue:
                print(f"    venue keys: {list(venue.keys())[:20]}")
                print(f"    venue.id = {venue.get('id')}")
                print(f"    venue.name = {venue.get('name')}")
                print(f"    venue.slug = {venue.get('slug')}")
                print(f"    venue.address = {venue.get('address')}")
                loc = venue.get("location", [])
                print(f"    venue.location = {loc}")
                rating = venue.get("rating", {})
                print(f"    venue.rating = {rating}")
                print(f"    venue.online = {venue.get('online')}")
                print(f"    venue.estimate_range = {venue.get('estimate_range')}")
                print(f"    venue.price_range = {venue.get('price_range')}")
                print(f"    venue.tags = {venue.get('tags', [])[:5]}")
                print(f"    venue.delivery_specs = {venue.get('delivery_specs')}")
                print(f"    venue.short_description = {venue.get('short_description')}")

            link = item.get("link", {})
            if link:
                print(f"    link = {link}")

    # Count total venues
    all_venues = []
    for section in sections:
        for item in section.get("items", []):
            venue = item.get("venue")
            if venue and venue.get("id"):
                all_venues.append(venue)

    print(f"\nTotal venues with IDs: {len(all_venues)}")
    slugs = [v["slug"] for v in all_venues if v.get("slug")]
    print(f"Sample slugs: {slugs[:10]}")

    return all_venues


def discover_venue_dynamic(slug: str):
    """Fetch dynamic venue data and save to bld/wolt_venue_dynamic.json."""
    BLD_DIR.mkdir(exist_ok=True)

    url = f"https://consumer-api.wolt.com/order-xp/web/v1/venue/slug/{slug}/dynamic/"
    print(f"\nFetching {url} ...")
    resp = httpx.get(url, timeout=30)
    print(f"Status: {resp.status_code}")
    resp.raise_for_status()
    data = resp.json()

    out = BLD_DIR / "wolt_venue_dynamic.json"
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"Saved venue dynamic ({len(json.dumps(data))} bytes) to {out}")

    explore_venue_dynamic(data)
    return data


def explore_venue_dynamic(data: dict):
    """Print discount structure from venue dynamic data."""
    print(f"\nTop-level keys: {list(data.keys())}")

    venue_raw = data.get("venue_raw", {})
    if venue_raw:
        print(f"venue_raw keys: {list(venue_raw.keys())[:20]}")
        discounts = venue_raw.get("discounts", [])
        print(f"discounts: {len(discounts)}")
        for i, d in enumerate(discounts):
            print(f"\n  Discount [{i}]:")
            print(f"    id: {d.get('id')}")
            desc = d.get("description", {})
            print(f"    title: {desc.get('title')}")
            print(f"    body: {desc.get('body')}")
            print(f"    effects: {d.get('effects')}")
            print(f"    conditions: {d.get('conditions')}")
            print(f"    end_date: {d.get('end_date')}")
            print(f"    all keys: {list(d.keys())}")
    else:
        print("No venue_raw found!")
        # Check alternative paths
        text = json.dumps(data)
        for pattern in ["discount", "deal", "promotion", "offer"]:
            count = text.lower().count(pattern)
            if count:
                print(f"  Pattern '{pattern}' found {count} times")


if __name__ == "__main__":
    data = discover_listing()

    # Try to find a venue with deals to test dynamic endpoint
    all_venues = []
    for section in data.get("sections", []):
        for item in section.get("items", []):
            venue = item.get("venue")
            if venue and venue.get("slug"):
                all_venues.append(venue)

    if all_venues:
        # Try first few venues to find one with deals
        for venue in all_venues[:5]:
            slug = venue["slug"]
            print(f"\n{'='*60}")
            print(f"Trying venue: {venue.get('name')} (slug={slug})")
            try:
                discover_venue_dynamic(slug)
            except httpx.HTTPStatusError as e:
                print(f"  Error: {e}")
