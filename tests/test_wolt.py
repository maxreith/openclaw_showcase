"""Tests for the Wolt client.

Unit tests use fixtures; integration tests hit the real Wolt API.
Run with: pixi run pytest tests/test_wolt.py -v
"""

import asyncio

import pandas as pd
import pytest

from src.wolt.client import (
    Deal,
    Restaurant,
    _build_menu_lookups,
    _classify_deal,
    _enrich_with_deals_async,
    _extract_deal_items,
    _fetch_listing,
    _fetch_menu,
    _fetch_menu_async,
    _fetch_venue_dynamic,
    _fetch_venue_dynamic_async,
    _get_deals_async,
    _has_relevant_discounts,
    _is_first_order_discount,
    _parse_listing_restaurants,
    _parse_venue_deals,
    deals_to_dataframe,
    get_deals,
    get_restaurants,
    restaurants_to_dataframe,
)

KFC_SLUG = "kfc-bonn"


class TestParseListingRestaurants:
    def test_parses_venue_fields(self):
        items = [
            {
                "venue": {
                    "id": "abc123",
                    "name": "Test Restaurant",
                    "slug": "test-restaurant",
                    "address": "Main St 1",
                    "location": [7.1, 50.7],
                    "rating": {"score": 8.5, "volume": 100},
                    "online": True,
                    "estimate_range": "20-30",
                    "price_range": 2,
                    "tags": ["pizza", "italian"],
                    "short_description": "Best pizza",
                }
            }
        ]
        restaurants = _parse_listing_restaurants(items)
        assert len(restaurants) == 1
        r = restaurants[0]
        assert r.id == "abc123"
        assert r.name == "Test Restaurant"
        assert r.slug == "test-restaurant"
        assert r.address == "Main St 1"
        assert r.lng == 7.1
        assert r.lat == 50.7
        assert r.rating_score == 8.5
        assert r.rating_count == 100
        assert r.is_online is True
        assert r.delivery_estimate == "20-30"
        assert r.price_range == 2
        assert r.tags == ["pizza", "italian"]

    def test_handles_missing_rating(self):
        items = [
            {
                "venue": {
                    "id": "abc123",
                    "name": "New Place",
                    "slug": "new-place",
                    "address": "",
                    "location": [0, 0],
                    "rating": None,
                    "online": False,
                    "estimate_range": "",
                    "price_range": 0,
                    "tags": [],
                    "short_description": "",
                }
            }
        ]
        restaurants = _parse_listing_restaurants(items)
        assert len(restaurants) == 1
        assert restaurants[0].rating_score == 0.0
        assert restaurants[0].rating_count == 0

    def test_skips_items_without_venue(self):
        items = [{"link": {"target": "something"}}]
        restaurants = _parse_listing_restaurants(items)
        assert len(restaurants) == 0


class TestParseVenueDeals:
    def test_parses_basket_discount(self):
        dynamic = {
            "venue_raw": {
                "discounts": [
                    {
                        "id": "deal-1",
                        "description": {"title": "5 EUR off", "body": "Min order 15 EUR"},
                        "effects": {
                            "basket_discount": {"amount": 500, "fraction": None},
                            "delivery_discount": None,
                            "free_items": None,
                            "item_discount": None,
                        },
                        "conditions": {
                            "basket_contains": [{"min_amount": 1500}],
                            "delivery_methods": ["homedelivery"],
                        },
                        "end_date": "2026-12-31T23:59:59Z",
                    }
                ]
            }
        }
        deals = _parse_venue_deals(dynamic, "rest-1", "Test Place")
        assert len(deals) == 1
        d = deals[0]
        assert d.restaurant_id == "rest-1"
        assert d.restaurant_name == "Test Place"
        assert d.discount_id == "deal-1"
        assert d.deal_title == "5 EUR off"
        assert d.deal_body == "Min order 15 EUR"
        assert d.deal_type == "basket discount"
        assert d.discount_amount == 500
        assert d.min_basket == 1500
        assert d.delivery_methods == ["homedelivery"]
        assert d.end_date == "2026-12-31T23:59:59Z"

    def test_parses_bogo_deal(self):
        dynamic = {
            "venue_raw": {
                "discounts": [
                    {
                        "id": "deal-2",
                        "description": {"title": "Add 2x, pay for 1"},
                        "effects": {
                            "basket_discount": None,
                            "delivery_discount": None,
                            "free_items": {
                                "buy": 1,
                                "get": 1,
                                "include": {"items": ["item-a", "item-b"]},
                            },
                            "item_discount": None,
                        },
                        "conditions": {
                            "basket_contains": [{}],
                            "delivery_methods": None,
                        },
                        "end_date": None,
                    }
                ]
            }
        }
        item_map = {"item-a": "Zinger Burger", "item-b": "Bucket"}
        price_map = {"item-a": 699, "item-b": 1399}
        deals = _parse_venue_deals(
            dynamic, "rest-2", "KFC",
            item_map=item_map, price_map=price_map,
        )
        assert len(deals) == 1
        assert deals[0].deal_type == "two for one"
        assert deals[0].free_items_buy == 1
        assert deals[0].free_items_get == 1
        assert deals[0].item_names == ["Zinger Burger", "Bucket"]
        assert deals[0].item_prices == [699, 1399]

    def test_skips_first_order_discounts(self):
        dynamic = {
            "venue_raw": {
                "discounts": [
                    {
                        "id": "deal-fo",
                        "description": {"title": "10€ off your first order"},
                        "effects": {
                            "basket_discount": {"amount": 1000},
                            "delivery_discount": None,
                            "free_items": None,
                            "item_discount": None,
                        },
                        "conditions": {"basket_contains": [{"min_amount": 1500}]},
                        "end_date": None,
                    }
                ]
            }
        }
        deals = _parse_venue_deals(dynamic, "rest-3", "Subway")
        assert len(deals) == 0

    def test_empty_discounts(self):
        dynamic = {"venue_raw": {"discounts": []}}
        deals = _parse_venue_deals(dynamic, "rest-4", "Empty")
        assert len(deals) == 0

    def test_no_venue_raw(self):
        dynamic = {"something_else": {}}
        deals = _parse_venue_deals(dynamic, "rest-5", "Missing")
        assert len(deals) == 0


class TestBuildMenuLookups:
    def test_normal_data(self):
        menu_data = {
            "items": [
                {"id": "i1", "name": "Burger", "baseprice": 899},
                {"id": "i2", "name": "Fries", "baseprice": 399},
            ],
            "categories": [
                {"id": "c1", "name": "Mains"},
            ],
        }
        item_map, cat_map, price_map = _build_menu_lookups(menu_data)
        assert item_map == {"i1": "Burger", "i2": "Fries"}
        assert cat_map == {"c1": "Mains"}
        assert price_map == {"i1": 899, "i2": 399}

    def test_none_input(self):
        item_map, cat_map, price_map = _build_menu_lookups(None)
        assert item_map == {}
        assert cat_map == {}
        assert price_map == {}

    def test_empty_lists(self):
        item_map, cat_map, price_map = _build_menu_lookups(
            {"items": [], "categories": []}
        )
        assert item_map == {}
        assert cat_map == {}
        assert price_map == {}

    def test_missing_baseprice(self):
        menu_data = {
            "items": [
                {"id": "i1", "name": "Burger", "baseprice": 899},
                {"id": "i2", "name": "Mystery Item"},
            ],
            "categories": [],
        }
        _, _, price_map = _build_menu_lookups(menu_data)
        assert price_map == {"i1": 899}


class TestExtractDealItems:
    def test_bogo_with_item_ids(self):
        discount = {
            "effects": {
                "free_items": {"include": {"items": ["i1", "i2"]}},
            },
        }
        item_map = {"i1": "Burger", "i2": "Fries"}
        price_map = {"i1": 899, "i2": 399}
        names, cats, prices = _extract_deal_items(
            discount, item_map, {}, price_map
        )
        assert names == ["Burger", "Fries"]
        assert cats == []
        assert prices == [899, 399]

    def test_item_discount_with_categories(self):
        discount = {
            "effects": {
                "item_discount": {"include": {"categories": ["c1"]}},
            },
        }
        cat_map = {"c1": "Sides"}
        names, cats, prices = _extract_deal_items(discount, {}, cat_map)
        assert names == []
        assert cats == ["Sides"]
        assert prices == []

    def test_missing_ids_returns_empty(self):
        discount = {
            "effects": {
                "free_items": {"include": {"items": ["unknown-id"]}},
            },
        }
        names, cats, prices = _extract_deal_items(discount, {}, {})
        assert names == []
        assert cats == []
        assert prices == []

    def test_deduplicates_across_effects_and_conditions(self):
        discount = {
            "effects": {
                "free_items": {"include": {"items": ["i1"]}},
            },
            "conditions": {
                "basket_contains": [{"any_of_items": ["i1", "i2"]}],
            },
        }
        item_map = {"i1": "Burger", "i2": "Fries"}
        price_map = {"i1": 899, "i2": 399}
        names, cats, prices = _extract_deal_items(
            discount, item_map, {}, price_map
        )
        assert names == ["Burger", "Fries"]
        assert prices == [899, 399]

    def test_conditions_paths(self):
        discount = {
            "effects": {},
            "conditions": {
                "basket_contains": [
                    {"any_of_items": ["i1"], "items_from_categories": ["c1"]},
                ],
            },
        }
        item_map = {"i1": "Wrap"}
        cat_map = {"c1": "Wraps"}
        price_map = {"i1": 550}
        names, cats, prices = _extract_deal_items(
            discount, item_map, cat_map, price_map
        )
        assert names == ["Wrap"]
        assert cats == ["Wraps"]
        assert prices == [550]

    def test_empty_discount(self):
        names, cats, prices = _extract_deal_items({}, {}, {})
        assert names == []
        assert cats == []
        assert prices == []

    def test_deals_without_lookups_have_empty_names(self):
        dynamic = {
            "venue_raw": {
                "discounts": [
                    {
                        "id": "deal-1",
                        "description": {"title": "5 EUR off"},
                        "effects": {"basket_discount": {"amount": 500}},
                        "conditions": {"basket_contains": [{"min_amount": 1500}]},
                        "end_date": None,
                    }
                ]
            }
        }
        deals = _parse_venue_deals(dynamic, "r1", "Place")
        assert len(deals) == 1
        assert deals[0].item_names == []
        assert deals[0].category_names == []
        assert deals[0].item_prices == []


class TestClassifyDeal:
    def test_basket_discount(self):
        effects = {
            "basket_discount": {"amount": 500, "fraction": None},
            "free_items": None,
            "item_discount": None,
            "delivery_discount": None,
        }
        assert _classify_deal(effects) == "basket discount"

    def test_percent_off_basket(self):
        effects = {
            "basket_discount": {"amount": None, "fraction": 0.2},
            "free_items": None,
            "item_discount": None,
            "delivery_discount": None,
        }
        assert _classify_deal(effects) == "% off"

    def test_percent_off_item(self):
        effects = {
            "basket_discount": None,
            "free_items": None,
            "item_discount": {"fraction": 0.2},
            "delivery_discount": None,
        }
        assert _classify_deal(effects) == "% off"

    def test_bogo(self):
        effects = {
            "basket_discount": None,
            "free_items": {"buy": 1, "get": 1},
            "item_discount": None,
            "delivery_discount": None,
        }
        assert _classify_deal(effects) == "two for one"

    def test_free_item(self):
        effects = {
            "basket_discount": None,
            "free_items": {"buy": 0, "get": 1},
            "item_discount": None,
            "delivery_discount": None,
        }
        assert _classify_deal(effects) == "free item"

    def test_delivery_discount(self):
        effects = {
            "basket_discount": None,
            "free_items": None,
            "item_discount": None,
            "delivery_discount": {"amount": 300},
        }
        assert _classify_deal(effects) == "delivery discount"

    def test_other(self):
        effects = {
            "basket_discount": None,
            "free_items": None,
            "item_discount": None,
            "delivery_discount": None,
        }
        assert _classify_deal(effects) == "other"


class TestIsFirstOrderDiscount:
    def test_english_first_order(self):
        assert _is_first_order_discount("10€ off your first order", "") is True

    def test_german_first_order(self):
        assert _is_first_order_discount("10€ Rabatt auf deine erste Bestellung", "") is True

    def test_first_time(self):
        assert _is_first_order_discount("First time? Get 5€ off", "") is True

    def test_regular_deal(self):
        assert _is_first_order_discount("Add 2x, pay for 1", "") is False

    def test_body_contains_keyword(self):
        assert _is_first_order_discount("Special offer", "Only for first order") is True


class TestHasRelevantDiscounts:
    def test_no_discounts(self):
        dynamic = {"venue_raw": {"discounts": []}}
        assert _has_relevant_discounts(dynamic) is False

    def test_only_first_order(self):
        dynamic = {
            "venue_raw": {
                "discounts": [
                    {"description": {"title": "10€ off your first order", "body": ""}},
                ]
            }
        }
        assert _has_relevant_discounts(dynamic) is False

    def test_mixed_discounts(self):
        dynamic = {
            "venue_raw": {
                "discounts": [
                    {"description": {"title": "10€ off your first order", "body": ""}},
                    {"description": {"title": "5 EUR off", "body": "Min 15 EUR"}},
                ]
            }
        }
        assert _has_relevant_discounts(dynamic) is True

    def test_all_relevant(self):
        dynamic = {
            "venue_raw": {
                "discounts": [
                    {"description": {"title": "Buy 2 get 1 free", "body": ""}},
                ]
            }
        }
        assert _has_relevant_discounts(dynamic) is True

    def test_missing_venue_raw(self):
        assert _has_relevant_discounts({}) is False

    def test_missing_description(self):
        dynamic = {"venue_raw": {"discounts": [{}]}}
        assert _has_relevant_discounts(dynamic) is True


class TestGetRestaurants:
    @pytest.fixture(scope="class")
    def listing_items(self):
        return _fetch_listing(50.7346, 7.0997)

    @pytest.fixture(scope="class")
    def restaurants(self, listing_items):
        return _parse_listing_restaurants(listing_items)

    @pytest.fixture(scope="class")
    def restaurants_df(self, restaurants):
        return restaurants_to_dataframe(restaurants)

    def test_returns_many_restaurants(self, listing_items):
        assert len(listing_items) > 100

    def test_parses_all_restaurants(self, listing_items, restaurants):
        assert len(restaurants) == len(listing_items)

    def test_known_restaurants_present(self, restaurants):
        names = {r.name for r in restaurants}
        known = ["KFC Bonn", "Subway Bonn Maximilianstraße"]
        for expected in known:
            if expected in names:
                return
        chains = [n for n in names if "kfc" in n.lower() or "subway" in n.lower()]
        assert len(chains) > 0, f"No known chains found. Sample names: {list(names)[:10]}"

    def test_dataframe_has_expected_columns(self, restaurants_df):
        expected = [
            "id", "name", "slug", "address", "lat", "lng",
            "rating_score", "is_online", "delivery_estimate",
            "tags", "has_deals",
        ]
        for col in expected:
            assert col in restaurants_df.columns, f"Missing column: {col}"


class TestGetDeals:
    @pytest.fixture(scope="class")
    def kfc_dynamic(self):
        return _fetch_venue_dynamic(KFC_SLUG)

    @pytest.fixture(scope="class")
    def kfc_deals(self, kfc_dynamic):
        assert kfc_dynamic is not None, "Could not fetch KFC dynamic data"
        return _parse_venue_deals(kfc_dynamic, "kfc-id", "KFC Bonn")

    def test_kfc_has_deals(self, kfc_deals):
        assert len(kfc_deals) > 0, "KFC should have at least one non-first-order deal"

    def test_deals_have_titles(self, kfc_deals):
        for deal in kfc_deals:
            assert deal.deal_title, "Deal missing title"

    def test_deals_have_types(self, kfc_deals):
        valid_types = {
            "basket discount", "% off", "delivery discount",
            "free item", "two for one", "item discount", "other",
        }
        for deal in kfc_deals:
            assert deal.deal_type in valid_types, f"Unknown type: {deal.deal_type}"

    def test_deals_dataframe(self, kfc_deals):
        df = deals_to_dataframe(kfc_deals)
        assert len(df) == len(kfc_deals)
        assert "deal_title" in df.columns
        assert "deal_type" in df.columns

    def test_first_order_filtered(self, kfc_dynamic):
        all_discounts = kfc_dynamic["venue_raw"]["discounts"]
        deals = _parse_venue_deals(kfc_dynamic, "kfc-id", "KFC Bonn")
        first_order_count = sum(
            1
            for d in all_discounts
            if any(
                kw in d.get("description", {}).get("title", "").lower()
                for kw in ["first order", "erste bestellung"]
            )
        )
        assert len(deals) == len(all_discounts) - first_order_count

    def test_kfc_deals_with_menu_resolution(self, kfc_dynamic):
        venue_id = kfc_dynamic.get("venue_raw", {}).get("id", "")
        assert venue_id, "KFC dynamic data missing venue_raw.id"
        menu_data = _fetch_menu(venue_id)
        item_map, cat_map, price_map = _build_menu_lookups(menu_data)
        deals = _parse_venue_deals(
            kfc_dynamic, "kfc-id", "KFC Bonn", item_map, cat_map, price_map
        )
        item_scoped = [d for d in deals if d.item_names]
        if not item_scoped:
            pytest.skip("KFC currently has no item-scoped deals")
        for deal in item_scoped:
            assert all(isinstance(n, str) and n for n in deal.item_names)
            assert all(isinstance(p, int) and p > 0 for p in deal.item_prices)


class TestAsyncFetchVenueDynamic:
    def test_returns_data_for_known_slug(self):
        import httpx

        async def run():
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                return await _fetch_venue_dynamic_async(KFC_SLUG, client)

        result = asyncio.run(run())
        assert result is not None
        assert "venue_raw" in result

    def test_returns_none_for_bad_slug(self):
        import httpx

        async def run():
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                return await _fetch_venue_dynamic_async("nonexistent-slug-xyz", client)

        result = asyncio.run(run())
        assert result is None


class TestAsyncEnrichWithDeals:
    def test_enriches_restaurants(self):
        items = _fetch_listing(50.7346, 7.0997)
        restaurants = _parse_listing_restaurants(items[:5])
        enriched = asyncio.run(_enrich_with_deals_async(restaurants))
        assert len(enriched) == len(restaurants)

    def test_enriched_restaurants_have_deals(self):
        items = _fetch_listing(50.7346, 7.0997)
        restaurants = _parse_listing_restaurants(items)
        enriched = asyncio.run(_enrich_with_deals_async(restaurants))
        deal_count = sum(1 for r in enriched if r.has_deals)
        assert deal_count > 0


class TestAsyncGetDeals:
    def test_returns_dataframe(self):
        restaurants = [
            Restaurant(
                id="", name="", slug=KFC_SLUG, address="", lat=0, lng=0,
                rating_score=0, rating_count=0, is_online=False,
                delivery_estimate="", price_range=0, tags=[],
                short_description="",
            )
        ]
        df = asyncio.run(_get_deals_async(restaurants))
        assert isinstance(df, pd.DataFrame)
