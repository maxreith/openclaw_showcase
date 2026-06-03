"""Client for fetching Wolt restaurant and deal data in Bonn.

Uses Wolt's consumer API for restaurant listings and the venue dynamic
API for deal/discount data. Both endpoints are publicly accessible
via httpx — no browser automation needed. Per-restaurant requests run
concurrently via asyncio with a semaphore to limit parallelism.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from random import uniform

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

BONN_LAT = 50.7346
BONN_LON = 7.0997
LISTING_URL = "https://consumer-api.wolt.com/v1/pages/restaurants"
VENUE_DYNAMIC_URL = "https://consumer-api.wolt.com/order-xp/web/v1/venue/slug/{slug}/dynamic/"
MENU_URL = "https://restaurant-api.wolt.com/v4/venues/{venue_id}/menu"

FIRST_ORDER_KEYWORDS = ["first order", "erste bestellung", "first time"]


@dataclass
class Restaurant:
    """A restaurant from the Wolt listing."""

    id: str
    name: str
    slug: str
    address: str
    lat: float
    lng: float
    rating_score: float
    rating_count: int
    is_online: bool
    delivery_estimate: str
    price_range: int
    tags: list[str]
    short_description: str
    deal_titles: list[str] = field(default_factory=list)
    has_deals: bool = False


@dataclass
class Deal:
    """A venue-level deal/discount from Wolt."""

    restaurant_id: str
    restaurant_name: str
    discount_id: str
    deal_title: str
    deal_body: str
    deal_type: str
    discount_amount: int | None
    discount_fraction: float | None
    free_items_buy: int | None
    free_items_get: int | None
    min_basket: int | None
    delivery_methods: list[str]
    end_date: str | None
    item_names: list[str] = field(default_factory=list)
    category_names: list[str] = field(default_factory=list)
    item_prices: list[int] = field(default_factory=list)


def get_restaurants(lat: float = BONN_LAT, lon: float = BONN_LON) -> pd.DataFrame:
    """Fetch all restaurants for a location, enriched with deal info.

    Args:
        lat: Latitude of the delivery location.
        lon: Longitude of the delivery location.

    Returns:
        DataFrame with one row per restaurant.
    """
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        venue_items = _fetch_listing(lat, lon, client=client)
        restaurants = _parse_listing_restaurants(venue_items)
        restaurants = asyncio.run(_enrich_with_deals_async(restaurants))
    return restaurants_to_dataframe(restaurants)


def get_deals(
    lat: float = BONN_LAT,
    lon: float = BONN_LON,
    venue_slugs: list[str] | None = None,
) -> pd.DataFrame:
    """Fetch venue-level deals for restaurants.

    Args:
        lat: Latitude of the delivery location.
        lon: Longitude of the delivery location.
        venue_slugs: Optional list of venue slugs to fetch. If None,
            fetches all restaurants from the listing.

    Returns:
        DataFrame with one row per deal.
    """
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        if venue_slugs is None:
            venue_items = _fetch_listing(lat, lon, client=client)
            restaurants = _parse_listing_restaurants(venue_items)
        else:
            restaurants = [
                Restaurant(
                    id="", name="", slug=slug, address="", lat=0, lng=0,
                    rating_score=0, rating_count=0, is_online=False,
                    delivery_estimate="", price_range=0, tags=[],
                    short_description="",
                )
                for slug in venue_slugs
            ]

    return asyncio.run(_get_deals_async(restaurants))


def _fetch_listing(
    lat: float, lon: float, client: httpx.Client | None = None,
) -> list[dict]:
    """Fetch restaurant listing from Wolt consumer API.

    Args:
        lat: Latitude.
        lon: Longitude.
        client: Optional httpx.Client for connection reuse.

    Returns:
        List of venue item dicts from the restaurants section.
    """
    http = client or httpx
    resp = http.get(
        LISTING_URL,
        params={"lat": lat, "lon": lon},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    for section in data.get("sections", []):
        if section.get("name") == "restaurants-delivering-venues":
            return section.get("items", [])

    return []


def _parse_listing_restaurants(venue_items: list[dict]) -> list[Restaurant]:
    """Parse listing API items into Restaurant objects.

    Args:
        venue_items: Items from the restaurants-delivering-venues section.

    Returns:
        List of Restaurant objects.
    """
    restaurants = []
    for item in venue_items:
        venue = item.get("venue")
        if not venue or not venue.get("id"):
            continue

        location = venue.get("location", [0, 0])
        rating = venue.get("rating") or {}

        restaurants.append(
            Restaurant(
                id=venue["id"],
                name=venue.get("name", ""),
                slug=venue.get("slug", ""),
                address=venue.get("address", ""),
                lat=location[1] if len(location) > 1 else 0.0,
                lng=location[0] if len(location) > 0 else 0.0,
                rating_score=rating.get("score", 0.0) if isinstance(rating, dict) else 0.0,
                rating_count=rating.get("volume", 0) if isinstance(rating, dict) else 0,
                is_online=venue.get("online", False),
                delivery_estimate=venue.get("estimate_range", ""),
                price_range=venue.get("price_range", 0),
                tags=venue.get("tags", []),
                short_description=venue.get("short_description", ""),
            )
        )

    return restaurants


def _fetch_venue_dynamic(
    slug: str, client: httpx.Client | None = None,
) -> dict | None:
    """Fetch dynamic venue data including discounts.

    Args:
        slug: Venue slug (e.g. "kfc-bonn").
        client: Optional httpx.Client for connection reuse.

    Returns:
        Parsed response dict, or None on failure.
    """
    http = client or httpx
    url = VENUE_DYNAMIC_URL.format(slug=slug)
    try:
        resp = http.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError:
        return None


def _fetch_menu(
    venue_id: str, client: httpx.Client | None = None,
) -> dict | None:
    """Fetch menu data for a venue.

    Args:
        venue_id: Venue hex ID.
        client: Optional httpx.Client for connection reuse.

    Returns:
        Parsed menu JSON, or None on failure.
    """
    http = client or httpx
    url = MENU_URL.format(venue_id=venue_id)
    try:
        resp = http.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError:
        logger.warning("Failed to fetch menu for venue %s", venue_id)
        return None


def _build_menu_lookups(
    menu_data: dict | None,
) -> tuple[dict[str, str], dict[str, str], dict[str, int]]:
    """Build item and category ID-to-name lookup dicts from menu data.

    Args:
        menu_data: Parsed menu JSON, or None.

    Returns:
        Tuple of (item_map, category_map, price_map).
    """
    if menu_data is None:
        return {}, {}, {}
    item_map = {item["id"]: item["name"] for item in menu_data.get("items", [])}
    category_map = {cat["id"]: cat["name"] for cat in menu_data.get("categories", [])}
    price_map = {
        item["id"]: item["baseprice"]
        for item in menu_data.get("items", [])
        if item.get("baseprice") is not None
    }
    return item_map, category_map, price_map


def _extract_deal_items(
    discount: dict,
    item_map: dict[str, str],
    category_map: dict[str, str],
    price_map: dict[str, int] | None = None,
) -> tuple[list[str], list[str], list[int]]:
    """Resolve item/category IDs from a discount to human-readable names.

    Args:
        discount: A single discount dict from the venue dynamic API.
        item_map: Mapping of item ID to item name.
        category_map: Mapping of category ID to category name.
        price_map: Optional mapping of item ID to base price in cents.

    Returns:
        Tuple of (item_names, category_names, item_prices). item_prices is
        parallel to item_names (same order, same dedup).
    """
    if price_map is None:
        price_map = {}

    item_ids: list[str] = []
    category_ids: list[str] = []

    effects = discount.get("effects") or {}
    for effect_key in ("free_items", "item_discount", "basket_discount"):
        effect = effects.get(effect_key) or {}
        include = effect.get("include") or {}
        item_ids.extend(include.get("items") or [])
        category_ids.extend(include.get("categories") or [])

    conditions = discount.get("conditions") or {}
    for basket in conditions.get("basket_contains") or []:
        item_ids.extend(basket.get("any_of_items") or [])
        category_ids.extend(basket.get("items_from_categories") or [])

    seen_names: dict[str, int | None] = {}
    for iid in item_ids:
        if iid in item_map:
            name = item_map[iid]
            if name not in seen_names:
                seen_names[name] = price_map.get(iid)

    item_names = list(seen_names.keys())
    item_prices = [p for p in seen_names.values() if p is not None]

    category_names = list(
        dict.fromkeys(category_map[cid] for cid in category_ids if cid in category_map)
    )
    return item_names, category_names, item_prices


def _parse_venue_deals(
    dynamic_data: dict,
    restaurant_id: str,
    restaurant_name: str,
    item_map: dict[str, str] | None = None,
    category_map: dict[str, str] | None = None,
    price_map: dict[str, int] | None = None,
) -> list[Deal]:
    """Parse deals from venue dynamic API response.

    Filters out first-order discounts since they're not recurring deals.

    Args:
        dynamic_data: Response from the venue dynamic endpoint.
        restaurant_id: Venue hex ID.
        restaurant_name: Venue name.
        item_map: Optional item ID-to-name lookup.
        category_map: Optional category ID-to-name lookup.
        price_map: Optional item ID-to-price lookup (cents).

    Returns:
        List of Deal objects (excluding first-order discounts).
    """
    if item_map is None:
        item_map = {}
    if category_map is None:
        category_map = {}
    if price_map is None:
        price_map = {}

    deals = []
    venue_raw = dynamic_data.get("venue_raw", {})
    discounts = venue_raw.get("discounts", [])

    for discount in discounts:
        description = discount.get("description", {})
        title = description.get("title", "")
        body = description.get("body") or ""

        if _is_first_order_discount(title, body):
            continue

        effects = discount.get("effects") or {}
        conditions = discount.get("conditions") or {}

        basket_discount = effects.get("basket_discount") or {}
        free_items = effects.get("free_items") or {}

        basket_contains = conditions.get("basket_contains") or [{}]
        min_basket = basket_contains[0].get("min_amount") if basket_contains else None

        delivery_methods = conditions.get("delivery_methods") or []

        deal_type = _classify_deal(effects)
        deal_item_names, deal_category_names, deal_item_prices = _extract_deal_items(
            discount, item_map, category_map, price_map
        )

        deals.append(
            Deal(
                restaurant_id=restaurant_id,
                restaurant_name=restaurant_name,
                discount_id=discount.get("id", ""),
                deal_title=title,
                deal_body=body,
                deal_type=deal_type,
                discount_amount=basket_discount.get("amount"),
                discount_fraction=basket_discount.get("fraction"),
                free_items_buy=free_items.get("buy"),
                free_items_get=free_items.get("get"),
                min_basket=min_basket,
                delivery_methods=delivery_methods,
                end_date=discount.get("end_date"),
                item_names=deal_item_names,
                category_names=deal_category_names,
                item_prices=deal_item_prices,
            )
        )

    return deals


def _is_first_order_discount(title: str, body: str) -> bool:
    """Check if a discount is a first-order-only promotion.

    Args:
        title: Discount title text.
        body: Discount body text.

    Returns:
        True if this is a first-order discount.
    """
    text = f"{title} {body}".lower()
    return any(kw in text for kw in FIRST_ORDER_KEYWORDS)


def _classify_deal(effects: dict) -> str:
    """Classify a deal based on its effects structure.

    Args:
        effects: The effects dict from a discount.

    Returns:
        Deal type string: "basket_discount", "percent_off",
        "delivery_discount", "free_item", "bogo", or "other".
    """
    free_items = effects.get("free_items")
    if free_items:
        buy = free_items.get("buy", 0)
        get = free_items.get("get", 0)
        if buy and get:
            return "two for one"
        return "free item"

    item_discount = effects.get("item_discount")
    if item_discount:
        if item_discount.get("fraction"):
            return "% off"
        return "item discount"

    basket_discount = effects.get("basket_discount")
    if basket_discount:
        if basket_discount.get("fraction"):
            return "% off"
        if basket_discount.get("amount"):
            return "basket discount"

    delivery_discount = effects.get("delivery_discount")
    if delivery_discount:
        return "delivery discount"

    return "other"


def _has_relevant_discounts(dynamic_data: dict) -> bool:
    """Check if venue dynamic data contains non-first-order discounts.

    Args:
        dynamic_data: Response from the venue dynamic endpoint.

    Returns:
        True if at least one discount is not a first-order promotion.
    """
    discounts = dynamic_data.get("venue_raw", {}).get("discounts", [])
    for d in discounts:
        desc = d.get("description", {})
        if not _is_first_order_discount(
            desc.get("title", ""), desc.get("body", "") or "",
        ):
            return True
    return False


def _enrich_with_deals(
    restaurants: list[Restaurant],
    client: httpx.Client | None = None,
) -> list[Restaurant]:
    """Fetch deals for each restaurant and populate deal fields.

    Args:
        restaurants: List of Restaurant objects.
        client: Optional httpx.Client for connection reuse.

    Returns:
        Same list with deal_titles and has_deals populated.
    """
    for restaurant in restaurants:
        dynamic = _fetch_venue_dynamic(restaurant.slug, client=client)
        if not dynamic:
            continue

        deals = _parse_venue_deals(dynamic, restaurant.id, restaurant.name)
        if deals:
            restaurant.deal_titles = [d.deal_title for d in deals]
            restaurant.has_deals = True

        time.sleep(uniform(1, 3))

    return restaurants


async def _fetch_venue_dynamic_async(
    slug: str, client: httpx.AsyncClient,
) -> dict | None:
    """Async version of _fetch_venue_dynamic.

    Args:
        slug: Venue slug (e.g. "kfc-bonn").
        client: httpx.AsyncClient for connection reuse.

    Returns:
        Parsed response dict, or None on failure.
    """
    url = VENUE_DYNAMIC_URL.format(slug=slug)
    try:
        resp = await client.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError:
        return None


async def _fetch_menu_async(
    venue_id: str, client: httpx.AsyncClient,
) -> dict | None:
    """Async version of _fetch_menu.

    Args:
        venue_id: Venue hex ID.
        client: httpx.AsyncClient for connection reuse.

    Returns:
        Parsed menu JSON, or None on failure.
    """
    url = MENU_URL.format(venue_id=venue_id)
    try:
        resp = await client.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError:
        logger.warning("Failed to fetch menu for venue %s", venue_id)
        return None


async def _enrich_one_restaurant(
    restaurant: Restaurant, client: httpx.AsyncClient, sem: asyncio.Semaphore,
) -> None:
    """Fetch deals for a single restaurant under a semaphore.

    Args:
        restaurant: Restaurant to enrich in-place.
        client: httpx.AsyncClient for connection reuse.
        sem: Semaphore limiting concurrent requests.
    """
    async with sem:
        dynamic = await _fetch_venue_dynamic_async(restaurant.slug, client)
        await asyncio.sleep(uniform(1, 3))
    if not dynamic:
        return
    deals = _parse_venue_deals(dynamic, restaurant.id, restaurant.name)
    if deals:
        restaurant.deal_titles = [d.deal_title for d in deals]
        restaurant.has_deals = True


async def _enrich_with_deals_async(
    restaurants: list[Restaurant], concurrency: int = 3,
) -> list[Restaurant]:
    """Fetch deals for all restaurants concurrently with bounded parallelism.

    Args:
        restaurants: List of Restaurant objects.
        concurrency: Maximum number of concurrent requests.

    Returns:
        Same list with deal_titles and has_deals populated.
    """
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        tasks = [
            _enrich_one_restaurant(r, client, sem) for r in restaurants
        ]
        await asyncio.gather(*tasks)
    return restaurants


async def _fetch_deals_for_restaurant(
    restaurant: Restaurant,
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
) -> list[Deal]:
    """Fetch all deals for a single restaurant under a semaphore.

    Args:
        restaurant: Restaurant to fetch deals for.
        client: httpx.AsyncClient for connection reuse.
        sem: Semaphore limiting concurrent requests.

    Returns:
        List of Deal objects for this restaurant.
    """
    async with sem:
        dynamic = await _fetch_venue_dynamic_async(restaurant.slug, client)
        await asyncio.sleep(uniform(1, 3))

    if not dynamic:
        return []

    venue_id = dynamic.get("venue_raw", {}).get("id", "")
    item_map, category_map, price_map = {}, {}, {}

    if venue_id and _has_relevant_discounts(dynamic):
        async with sem:
            menu_data = await _fetch_menu_async(venue_id, client)
            await asyncio.sleep(uniform(1, 3))
        item_map, category_map, price_map = _build_menu_lookups(menu_data)

    return _parse_venue_deals(
        dynamic, restaurant.id, restaurant.name,
        item_map, category_map, price_map,
    )


async def _get_deals_async(
    restaurants: list[Restaurant], concurrency: int = 3,
) -> pd.DataFrame:
    """Fetch deals for all restaurants concurrently.

    Args:
        restaurants: List of Restaurant objects.
        concurrency: Maximum number of concurrent requests.

    Returns:
        DataFrame with one row per deal.
    """
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        tasks = [
            _fetch_deals_for_restaurant(r, client, sem) for r in restaurants
        ]
        results = await asyncio.gather(*tasks)

    all_deals = [deal for deals in results for deal in deals]
    return deals_to_dataframe(all_deals)


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
                "id": r.id,
                "name": r.name,
                "slug": r.slug,
                "address": r.address,
                "lat": r.lat,
                "lng": r.lng,
                "rating_score": r.rating_score,
                "rating_count": r.rating_count,
                "is_online": r.is_online,
                "delivery_estimate": r.delivery_estimate,
                "price_range": r.price_range,
                "tags": ", ".join(r.tags),
                "short_description": r.short_description,
                "deal_titles": ", ".join(r.deal_titles),
                "has_deals": r.has_deals,
            }
        )

    return pd.DataFrame(records)


def deals_to_dataframe(deals: list[Deal]) -> pd.DataFrame:
    """Convert Deal list to a pandas DataFrame.

    Args:
        deals: List of Deal objects.

    Returns:
        DataFrame with one row per deal.
    """
    pd.options.future.infer_string = True

    records = []
    for d in deals:
        records.append(
            {
                "restaurant_id": d.restaurant_id,
                "restaurant_name": d.restaurant_name,
                "discount_id": d.discount_id,
                "deal_title": d.deal_title,
                "deal_body": d.deal_body,
                "deal_type": d.deal_type,
                "discount_amount": d.discount_amount,
                "discount_fraction": d.discount_fraction,
                "free_items_buy": d.free_items_buy,
                "free_items_get": d.free_items_get,
                "min_basket": d.min_basket,
                "delivery_methods": ", ".join(d.delivery_methods),
                "end_date": d.end_date,
                "item_names": ", ".join(d.item_names),
                "category_names": ", ".join(d.category_names),
                "item_prices": ", ".join(str(p) for p in d.item_prices),
            }
        )

    return pd.DataFrame(records)


if __name__ == "__main__":
    print("Fetching Wolt restaurants in Bonn...")
    df = get_restaurants()
    print(f"Total restaurants: {len(df)}")
    print(f"Restaurants with deals: {df['has_deals'].sum()}")
    print()
    print("Restaurants with deals:")
    deals_df = df[df["has_deals"]].sort_values("name")
    for _, row in deals_df.iterrows():
        print(f"  {row['name']}: {row['deal_titles']}")
