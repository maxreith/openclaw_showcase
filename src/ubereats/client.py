"""Client for fetching Uber Eats restaurant and deal data in Bonn.

Extracts data from SSR state embedded in Uber Eats HTML pages using
Camoufox for anti-bot bypass. Feed-level restaurant data comes from
the Redux-like seoFeed state. Item-level deal data comes from the
React Query cache (getStoreV1) on store pages.
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from random import uniform

import pandas as pd
from camoufox.sync_api import Camoufox
from playwright.sync_api import TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

_WAIT_TIMEOUT = 30_000

_JS_WAIT_FOR_SEO_FEED = r"""
() => Array.from(document.querySelectorAll('script')).some(
    s => s.textContent.includes('seoFeed') || s.textContent.includes('\u0022seoFeed\u0022')
)
"""

_JS_WAIT_FOR_STORE_V1 = r"""
() => Array.from(document.querySelectorAll('script')).some(
    s => s.textContent.includes('getStoreV1') || s.textContent.includes('\u0022getStoreV1\u0022')
)
"""

BONN_URL = "https://www.ubereats.com/de-en/city/bonn-nw"
TOTAL_PAGES = 5


@dataclass
class Restaurant:
    """A restaurant from the Uber Eats feed."""

    uuid: str
    name: str
    slug: str
    b64uuid: str
    address: str
    lat: float
    lng: float
    rating: float
    rating_count: int
    is_open: bool
    delivery_eta: str
    delivery_fee: str
    price_bucket: str
    cuisines: list[str]
    deal_descriptions: list[str] = field(default_factory=list)
    has_deals: bool = False


@dataclass
class Deal:
    """An item-level deal from a store page."""

    restaurant_uuid: str
    restaurant_name: str
    item_title: str
    deal_description: str
    original_price: int | None
    buy_quantity: int | None
    get_quantity: int | None
    promo_type: str
    item_uuid: str = ""


def get_restaurants(city_url: str = BONN_URL) -> pd.DataFrame:
    """Fetch all restaurants for a city, including feed-level deal info.

    Args:
        city_url: Uber Eats city page URL.

    Returns:
        DataFrame with one row per restaurant.
    """
    feed_responses = _intercept_feed(city_url)
    restaurants = _parse_feed_restaurants(feed_responses)
    return restaurants_to_dataframe(restaurants)


def get_deals(
    city_url: str = BONN_URL,
    store_uuids: list[str] | None = None,
) -> pd.DataFrame:
    """Fetch item-level deal details for restaurants with deals.

    Args:
        city_url: Uber Eats city page URL.
        store_uuids: Optional list of store UUIDs to fetch. If None,
            fetches all stores with deals from the feed.

    Returns:
        DataFrame with one row per deal item.
    """
    feed_responses = _intercept_feed(city_url)
    restaurants = _parse_feed_restaurants(feed_responses)

    if store_uuids is not None:
        targets = [r for r in restaurants if r.uuid in store_uuids]
    else:
        targets = [r for r in restaurants if r.has_deals]

    if not targets:
        return deals_to_dataframe([])

    all_deals = []
    with Camoufox(headless=True) as browser:
        page = browser.new_page()
        page.goto(city_url, wait_until="domcontentloaded", timeout=60000)
        _wait_for_page_data(page, _JS_WAIT_FOR_SEO_FEED)

        _check_cloudflare(page)

        for restaurant in targets:
            store_data = _intercept_store(page, restaurant)
            if store_data:
                deals = _parse_store_deals(
                    store_data, restaurant.uuid, restaurant.name
                )
                all_deals.extend(deals)
            time.sleep(uniform(1, 3))

    return deals_to_dataframe(all_deals)


def get_restaurants_and_deals(
    city_url: str = BONN_URL,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch restaurants and deals in a single browser session.

    Args:
        city_url: Uber Eats city page URL.

    Returns:
        Tuple of (restaurants_df, deals_df).
    """
    with Camoufox(headless=True) as browser:
        page = browser.new_page()
        feed_responses = _intercept_feed_with_page(page, city_url)
        restaurants = _parse_feed_restaurants(feed_responses)
        restaurants_df = restaurants_to_dataframe(restaurants)

        targets = [r for r in restaurants if r.has_deals]
        if not targets:
            return restaurants_df, deals_to_dataframe([])

        all_deals = []
        for restaurant in targets:
            try:
                store_data = _intercept_store(page, restaurant)
            except Exception as exc:
                logger.warning("Store page failed for %s: %s", restaurant.name, exc)
                try:
                    page = browser.new_page()
                    store_data = _intercept_store(page, restaurant)
                except Exception:
                    logger.warning("Retry also failed for %s, skipping", restaurant.name)
                    continue
            if store_data:
                deals = _parse_store_deals(
                    store_data, restaurant.uuid, restaurant.name
                )
                all_deals.extend(deals)
            time.sleep(uniform(1, 3))

    return restaurants_df, deals_to_dataframe(all_deals)


def _wait_for_page_data(page, js_expression: str, timeout: int = _WAIT_TIMEOUT) -> None:
    """Wait for SSR data to appear in script tags.

    Args:
        page: Playwright page object.
        js_expression: JS function that returns True when data is ready.
        timeout: Max wait time in milliseconds.
    """
    try:
        page.wait_for_function(js_expression, timeout=timeout)
    except PlaywrightTimeout:
        logger.warning("Timed out waiting for page data on %s", page.url)


def _intercept_feed(city_url: str = BONN_URL) -> list[dict]:
    """Load all paginated feed pages and extract SSR state.

    Args:
        city_url: Uber Eats city page URL.

    Returns:
        List of stores dicts (one per page), each mapping uuid -> store data.
    """
    with Camoufox(headless=True) as browser:
        page = browser.new_page()
        return _intercept_feed_with_page(page, city_url)


def _intercept_feed_with_page(page, city_url: str = BONN_URL) -> list[dict]:
    """Load all paginated feed pages using an existing page object.

    Args:
        page: Playwright page object.
        city_url: Uber Eats city page URL.

    Returns:
        List of stores dicts (one per page), each mapping uuid -> store data.
    """
    all_page_stores = []

    for page_num in range(1, TOTAL_PAGES + 1):
        url = f"{city_url}?page={page_num}" if page_num > 1 else city_url
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        _wait_for_page_data(page, _JS_WAIT_FOR_SEO_FEED)

        _check_cloudflare(page)

        html = page.content()
        stores, store_links = _extract_feed_stores(html)
        if stores:
            all_page_stores.append(
                {"stores": stores, "store_links": store_links}
            )

    return all_page_stores


def _intercept_store(page, restaurant: Restaurant) -> dict | None:
    """Navigate to a store page and extract getStoreV1 data.

    Args:
        page: Playwright page with cookies from a prior city page visit.
        restaurant: Restaurant to visit.

    Returns:
        Parsed getStoreV1 response dict, or None if unavailable.
    """
    store_url = (
        f"https://www.ubereats.com/de-en/store/"
        f"{restaurant.slug}/{restaurant.b64uuid}"
    )
    page.goto(store_url, wait_until="domcontentloaded", timeout=60000)
    _wait_for_page_data(page, _JS_WAIT_FOR_STORE_V1)

    if "nothing to eat" in page.title().lower():
        return None

    _check_cloudflare(page)

    html = page.content()
    return _extract_store_data(html)


def _parse_feed_restaurants(feed_responses: list[dict]) -> list[Restaurant]:
    """Parse feed response pages into Restaurant objects.

    Args:
        feed_responses: List of page dicts with 'stores' and 'store_links'.

    Returns:
        Deduplicated list of Restaurant objects.
    """
    seen = set()
    restaurants = []

    for page_data in feed_responses:
        stores = page_data["stores"]
        store_links = page_data["store_links"]

        for uuid, store in stores.items():
            if uuid in seen:
                continue
            seen.add(uuid)

            location = store.get("location", {})
            meta = store.get("meta", {})
            promotion = store.get("promotion")

            deal_descriptions = []
            has_deals = False
            if promotion:
                has_deals = True
                text = promotion.get("text", "")
                desc = promotion.get("description", "")
                if text:
                    deal_descriptions.append(text.strip())
                if desc and desc != text:
                    deal_descriptions.append(desc.strip())

            eta_range = store.get("etaRange", {})
            eta_text = ""
            if isinstance(eta_range, dict):
                eta_text = eta_range.get("text", "")

            restaurants.append(
                Restaurant(
                    uuid=uuid,
                    name=store.get("title", ""),
                    slug=store.get("slug", ""),
                    b64uuid=store_links.get(store.get("slug", ""), ""),
                    address=location.get("formattedAddress", ""),
                    lat=location.get("latitude", 0.0),
                    lng=location.get("longitude", 0.0),
                    rating=store.get("rating", {}).get("ratingValue", 0.0)
                    if isinstance(store.get("rating"), dict)
                    else 0.0,
                    rating_count=0,
                    is_open=store.get("isOpen", False),
                    delivery_eta=eta_text,
                    delivery_fee=meta.get("deliveryFee") or "",
                    price_bucket=meta.get("priceBucket", ""),
                    cuisines=meta.get("categories", []),
                    deal_descriptions=deal_descriptions,
                    has_deals=has_deals,
                )
            )

    return restaurants


def _parse_store_deals(
    store_data: dict, restaurant_uuid: str, restaurant_name: str
) -> list[Deal]:
    """Parse item-level deals from a getStoreV1 response.

    Args:
        store_data: Parsed getStoreV1 response.
        restaurant_uuid: UUID of the restaurant.
        restaurant_name: Name of the restaurant.

    Returns:
        List of Deal objects.
    """
    deals = []
    seen_uuids: set[str] = set()
    catalog_map = store_data.get("catalogSectionsMap", {})

    for _section_key, sections in catalog_map.items():
        if not isinstance(sections, list):
            continue
        for section in sections:
            payload = section.get("payload", {}).get("standardItemsPayload", {})
            if not payload:
                continue

            promo_uuid = payload.get("promoUUID")
            section_title = payload.get("title", {})
            if isinstance(section_title, dict):
                section_title = section_title.get("text", "")

            items = payload.get("catalogItems", [])
            for item in items:
                item_uuid = item.get("uuid", "")
                if item_uuid and item_uuid in seen_uuids:
                    continue
                if item_uuid:
                    seen_uuids.add(item_uuid)

                item_promo = item.get("itemPromotion")
                if not item_promo and not promo_uuid:
                    continue

                promo_type = ""
                buy_qty = None
                get_qty = None

                if item_promo:
                    promo_type = item_promo.get("type", "")
                    bogo = item_promo.get("buyXGetYItemPromotion", {})
                    if bogo:
                        buy_qty = bogo.get("buyQuantity")
                        get_qty = bogo.get("getQuantity")

                deal_desc = section_title if promo_uuid else promo_type

                deals.append(
                    Deal(
                        restaurant_uuid=restaurant_uuid,
                        restaurant_name=restaurant_name,
                        item_title=item.get("title", ""),
                        deal_description=deal_desc,
                        original_price=item.get("price"),
                        buy_quantity=buy_qty,
                        get_quantity=get_qty,
                        promo_type=promo_type,
                        item_uuid=item_uuid,
                    )
                )

    return deals


def restaurants_to_dataframe(restaurants: list[Restaurant]) -> pd.DataFrame:
    """Convert Restaurant list to a pandas DataFrame.

    Args:
        restaurants: List of Restaurant objects.

    Returns:
        DataFrame with one row per restaurant.
    """
    pd.options.future.infer_string = True

    records = []
    for r in restaurants:
        records.append(
            {
                "uuid": r.uuid,
                "name": r.name,
                "slug": r.slug,
                "address": r.address,
                "lat": r.lat,
                "lng": r.lng,
                "rating": r.rating,
                "rating_count": r.rating_count,
                "is_open": r.is_open,
                "delivery_eta": r.delivery_eta,
                "delivery_fee": r.delivery_fee,
                "price_bucket": r.price_bucket,
                "cuisines": ", ".join(r.cuisines),
                "deal_descriptions": ", ".join(r.deal_descriptions),
                "has_deals": r.has_deals,
            }
        )

    return pd.DataFrame(records)


def deals_to_dataframe(deals: list[Deal]) -> pd.DataFrame:
    """Convert Deal list to a pandas DataFrame.

    Args:
        deals: List of Deal objects.

    Returns:
        DataFrame with one row per deal item.
    """
    pd.options.future.infer_string = True

    records = []
    for d in deals:
        records.append(
            {
                "restaurant_uuid": d.restaurant_uuid,
                "restaurant_name": d.restaurant_name,
                "item_uuid": d.item_uuid,
                "item_title": d.item_title,
                "deal_description": d.deal_description,
                "original_price": d.original_price,
                "buy_quantity": d.buy_quantity,
                "get_quantity": d.get_quantity,
                "promo_type": d.promo_type,
            }
        )

    return pd.DataFrame(records)


def _extract_feed_stores(html: str) -> tuple[dict, dict]:
    """Extract store data and link mappings from feed page HTML.

    Args:
        html: Full page HTML content.

    Returns:
        Tuple of (stores_map, store_links) where stores_map maps
        uuid -> store dict and store_links maps slug -> b64uuid.
    """
    store_links = _extract_store_links(html)

    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
    for script in scripts:
        script = script.strip()
        if '"seoFeed"' not in script and "\\u0022seoFeed\\u0022" not in script:
            continue

        data = _decode_script_json(script)
        if not data:
            continue

        seo_feed = data.get("seoFeed", {})
        for _path_key, feed in seo_feed.items():
            feed_data = feed.get("data", {})
            for element in feed_data.get("elements", []):
                if element.get("type") == "stores" and "storesMap" in element:
                    return element["storesMap"], store_links

    return {}, store_links


def _extract_store_links(html: str) -> dict:
    """Extract slug -> b64uuid mapping from store links in HTML.

    Args:
        html: Full page HTML content.

    Returns:
        Dict mapping store slug to base64-encoded UUID.
    """
    links = {}
    for match in re.finditer(r'href="[^"]*?/store/([^/"]+)/([^/"]+)"', html):
        slug, b64uuid = match.group(1), match.group(2)
        links[slug] = b64uuid
    return links


def _extract_store_data(html: str) -> dict | None:
    """Extract getStoreV1 data from a store page's React Query cache.

    Args:
        html: Full store page HTML content.

    Returns:
        Parsed getStoreV1 response dict, or None if not found.
    """
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
    for script in scripts:
        script = script.strip()
        if '"queries"' not in script and "\\u0022queries\\u0022" not in script:
            continue

        data = _decode_script_json(script)
        if not data or "queries" not in data:
            continue

        for query in data["queries"]:
            qkey = query.get("queryKey", [])
            if isinstance(qkey, list) and qkey and qkey[0] == "getStoreV1":
                return query.get("state", {}).get("data")

    return None


def _decode_script_json(script_text: str) -> dict | None:
    """Decode JSON from a script tag with unicode escapes.

    Args:
        script_text: Raw script tag content.

    Returns:
        Parsed JSON dict, or None on failure.
    """
    text = (
        script_text.replace("\\u0022", '"')
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


def _check_cloudflare(page) -> None:
    """Raise an error if the page is showing a Cloudflare challenge.

    Args:
        page: Playwright page object.

    Raises:
        RuntimeError: If a Cloudflare challenge is detected.
    """
    title = page.title().lower()
    if "just a moment" in title or "challenge" in title:
        raise RuntimeError(
            f"Cloudflare challenge detected on {page.url}. "
            "Try again or use a different network."
        )


if __name__ == "__main__":
    print("Fetching restaurants...")
    df = get_restaurants()
    print(f"Total restaurants: {len(df)}")
    print(f"Restaurants with deals: {df['has_deals'].sum()}")
    print()
    print("Restaurants with deals:")
    deals_df = df[df["has_deals"]].sort_values("name")
    for _, row in deals_df.iterrows():
        print(f"  {row['name']}: {row['deal_descriptions']}")
