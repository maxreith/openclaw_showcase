"""Discovery script for Uber Eats — Phase 3.

Extract getStoreV1 data from React Query cache to understand
item-level deal structure.
"""

import json
import re
from pathlib import Path

from camoufox.sync_api import Camoufox

from src.ubereats.client import (
    _JS_WAIT_FOR_SEO_FEED,
    _JS_WAIT_FOR_STORE_V1,
    _wait_for_page_data,
)

BLD_DIR = Path("bld")
BONN_URL = "https://www.ubereats.com/de-en/city/bonn-nw"


def decode_script_json(script_text: str) -> dict | None:
    """Decode JSON from a script tag with unicode escapes."""
    text = (
        script_text.strip()
        .replace("\\u0022", '"')
        .replace("\\u002F", "/")
        .replace("\\u0027", "'")
        .replace("\\u003C", "<")
        .replace("\\u003E", ">")
        .replace("\\u0026", "&")
        .replace("%5C", "\\")
    )
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def discover_store_data():
    """Load a store page with deals and extract getStoreV1 data."""
    BLD_DIR.mkdir(exist_ok=True)

    with Camoufox(headless=True) as browser:
        page = browser.new_page()

        # Load city page first for context
        page.goto(BONN_URL, wait_until="domcontentloaded")
        _wait_for_page_data(page, _JS_WAIT_FOR_SEO_FEED)

        # Find the store link format (slug/base64uuid) from the feed page
        links = page.query_selector_all("a[href*='/store/']")
        store_links = {}
        for link in links:
            href = link.get_attribute("href")
            if href and "/store/" in href and "play.google" not in href:
                parts = href.split("/store/")[1].split("/")
                if len(parts) == 2:
                    slug, b64uuid = parts
                    store_links[slug] = b64uuid

        print(f"Found {len(store_links)} store links")

        # Navigate to page 3 which has Pizza Company Tannenbusch
        page.goto(f"{BONN_URL}?page=3", wait_until="domcontentloaded")
        _wait_for_page_data(page, _JS_WAIT_FOR_SEO_FEED)

        links = page.query_selector_all("a[href*='/store/']")
        for link in links:
            href = link.get_attribute("href")
            if href and "/store/" in href and "play.google" not in href:
                parts = href.split("/store/")[1].split("/")
                if len(parts) == 2:
                    slug, b64uuid = parts
                    store_links[slug] = b64uuid

        print(f"Total store links: {len(store_links)}")

        # Find Pizza Company
        pizza_b64 = store_links.get("pizza-company-tannenbusch")
        print(f"Pizza Company b64uuid: {pizza_b64}")

        if pizza_b64:
            store_url = f"https://www.ubereats.com/de-en/store/pizza-company-tannenbusch/{pizza_b64}"
        else:
            print("Pizza Company not found in links, using first deal store")
            # Fallback to first available store
            store_url = f"https://www.ubereats.com/de-en/store/{list(store_links.keys())[0]}/{list(store_links.values())[0]}"

        print(f"\nNavigating to: {store_url}")
        page.goto(store_url, wait_until="domcontentloaded")
        _wait_for_page_data(page, _JS_WAIT_FOR_STORE_V1)

        title = page.title()
        print(f"Title: {title}")

        if "nothing to eat" in title.lower():
            print("ERROR: Store not available")
            return

        # Extract React Query cache
        html = page.content()
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)

        for script in scripts:
            script = script.strip()
            if '"queries"' not in script and "\\u0022queries\\u0022" not in script:
                continue
            data = decode_script_json(script)
            if not data or "queries" not in data:
                continue

            for q in data["queries"]:
                qkey = q.get("queryKey", [])
                name = qkey[0] if isinstance(qkey, list) and qkey else ""
                if name != "getStoreV1":
                    continue

                store_data = q["state"]["data"]
                (BLD_DIR / "ubereats_getStoreV1.json").write_text(
                    json.dumps(store_data, indent=2, ensure_ascii=False)
                )
                print(f"\nSaved getStoreV1 ({len(json.dumps(store_data))} bytes)")
                explore_store_v1(store_data)
                return


def explore_store_v1(data: dict):
    """Print structure of getStoreV1 response."""
    print(f"\nTop-level keys: {list(data.keys())[:20]}")

    # Check for catalog sections
    catalog = data.get("catalogSectionsMap", {})
    if catalog:
        print(f"\ncatalogSectionsMap: {len(catalog)} entries")
        for section_id, section in list(catalog.items())[:3]:
            print(f"  Section '{section_id}': keys={list(section.keys())[:10]}")
            if isinstance(section, list):
                for item in section[:2]:
                    if isinstance(item, dict):
                        print(f"    Item keys: {list(item.keys())[:10]}")

    # Check for menu/items
    for key in ["sectionEntitiesMap", "menuList", "menu", "items", "subsectionsMap"]:
        val = data.get(key)
        if val:
            if isinstance(val, dict):
                print(f"\n{key}: {len(val)} entries")
                for k, v in list(val.items())[:3]:
                    if isinstance(v, dict):
                        print(f"  '{k[:60]}': keys={list(v.keys())[:10]}")
                    elif isinstance(v, list):
                        print(f"  '{k[:60]}': list[{len(v)}]")
            elif isinstance(val, list):
                print(f"\n{key}: list[{len(val)}]")

    # Search for promotion/deal data
    text = json.dumps(data)
    for pattern in ["promotion", "buyOneGetOne", "BOGO", "discount",
                     "promoTag", "itemPromotion", "2for1", "Buy 1"]:
        count = text.count(pattern)
        if count > 0:
            print(f"\nPattern '{pattern}' found {count} times")
            # Show first occurrence context
            idx = text.find(pattern)
            print(f"  Context: ...{text[max(0,idx-100):idx+200]}...")


if __name__ == "__main__":
    discover_store_data()
