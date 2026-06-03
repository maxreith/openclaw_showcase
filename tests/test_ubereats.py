"""Tests for the Uber Eats client.

These are integration tests that hit the real Uber Eats website.
Run with: pixi run pytest tests/test_ubereats.py -v
"""

from unittest.mock import MagicMock, patch

import pytest

from src.ubereats.client import (
    Deal,
    Restaurant,
    _JS_WAIT_FOR_SEO_FEED,
    _decode_script_json,
    _extract_feed_stores,
    _extract_store_data,
    _extract_store_links,
    _intercept_feed,
    _intercept_feed_with_page,
    _parse_feed_restaurants,
    _parse_store_deals,
    _wait_for_page_data,
    deals_to_dataframe,
    get_restaurants,
    get_restaurants_and_deals,
    restaurants_to_dataframe,
)


PIZZA_COMPANY_UUID = "c05f5c91-8b20-52b0-9452-e783f4a12cd6"


@pytest.fixture(scope="module")
def feed_responses():
    return _intercept_feed()


@pytest.fixture(scope="module")
def restaurants(feed_responses):
    return _parse_feed_restaurants(feed_responses)


@pytest.fixture(scope="module")
def restaurants_df(restaurants):
    return restaurants_to_dataframe(restaurants)


class TestDecodeScriptJson:
    def test_decodes_unicode_escapes(self):
        script = '\\u0022key\\u0022:\\u0022value\\u0022'
        result = _decode_script_json("{" + script + "}")
        assert result == {"key": "value"}

    def test_returns_none_on_invalid_json(self):
        assert _decode_script_json("not json") is None


class TestExtractStoreLinks:
    def test_extracts_links(self):
        html = '<a href="/de-en/store/my-store/abc123">Store</a>'
        links = _extract_store_links(html)
        assert links == {"my-store": "abc123"}

    def test_ignores_non_store_links(self):
        html = '<a href="/de-en/city/bonn">City</a>'
        links = _extract_store_links(html)
        assert links == {}


class TestParseStoreDeals:
    def test_parses_bogo_deal(self):
        store_data = {
            "catalogSectionsMap": {
                "section-1": [
                    {
                        "type": "SECTION",
                        "catalogSectionUUID": "s1",
                        "payload": {
                            "standardItemsPayload": {
                                "title": {"text": "Buy 1, get 1 free"},
                                "promoUUID": "promo-1",
                                "catalogItems": [
                                    {
                                        "uuid": "item-1",
                                        "title": "Test Pizza",
                                        "price": 1790,
                                        "itemPromotion": {
                                            "type": "buyXGetYItemPromotion",
                                            "buyXGetYItemPromotion": {
                                                "buyQuantity": 1,
                                                "getQuantity": 1,
                                                "maxRedemptionCount": 3,
                                            },
                                        },
                                    }
                                ],
                                "sectionUUID": "s1",
                                "catalogSectionAnalyticsData": {},
                                "paginationEnabled": False,
                                "scores": [],
                            }
                        },
                    }
                ]
            }
        }

        deals = _parse_store_deals(store_data, "rest-uuid", "Test Restaurant")
        assert len(deals) == 1
        assert deals[0].item_uuid == "item-1"
        assert deals[0].item_title == "Test Pizza"
        assert deals[0].deal_description == "Buy 1, get 1 free"
        assert deals[0].original_price == 1790
        assert deals[0].buy_quantity == 1
        assert deals[0].get_quantity == 1
        assert deals[0].promo_type == "buyXGetYItemPromotion"

    def test_deduplicates_across_sections(self):
        store_data = {
            "catalogSectionsMap": {
                "section-1": [
                    {
                        "type": "SECTION",
                        "payload": {
                            "standardItemsPayload": {
                                "title": {"text": "VIP Deals"},
                                "promoUUID": "promo-1",
                                "catalogItems": [
                                    {
                                        "uuid": "item-dup",
                                        "title": "Dup Pizza",
                                        "price": 1200,
                                        "itemPromotion": {
                                            "type": "buyXGetYItemPromotion",
                                            "buyXGetYItemPromotion": {
                                                "buyQuantity": 1,
                                                "getQuantity": 1,
                                            },
                                        },
                                    }
                                ],
                            }
                        },
                    }
                ],
                "section-2": [
                    {
                        "type": "SECTION",
                        "payload": {
                            "standardItemsPayload": {
                                "title": {"text": "B Deals"},
                                "promoUUID": "promo-2",
                                "catalogItems": [
                                    {
                                        "uuid": "item-dup",
                                        "title": "Dup Pizza",
                                        "price": 1200,
                                        "itemPromotion": {
                                            "type": "buyXGetYItemPromotion",
                                            "buyXGetYItemPromotion": {
                                                "buyQuantity": 1,
                                                "getQuantity": 1,
                                            },
                                        },
                                    }
                                ],
                            }
                        },
                    }
                ],
            }
        }
        deals = _parse_store_deals(store_data, "rest-uuid", "Test Restaurant")
        assert len(deals) == 1
        assert deals[0].item_uuid == "item-dup"

    def test_keeps_items_without_uuid(self):
        store_data = {
            "catalogSectionsMap": {
                "section-1": [
                    {
                        "type": "SECTION",
                        "payload": {
                            "standardItemsPayload": {
                                "title": {"text": "Deals"},
                                "promoUUID": "promo-1",
                                "catalogItems": [
                                    {"title": "No UUID 1", "price": 500},
                                    {"title": "No UUID 2", "price": 600},
                                ],
                            }
                        },
                    }
                ],
            }
        }
        deals = _parse_store_deals(store_data, "rest-uuid", "Test Restaurant")
        assert len(deals) == 2
        assert all(d.item_uuid == "" for d in deals)

    def test_skips_non_promo_items(self):
        store_data = {
            "catalogSectionsMap": {
                "section-1": [
                    {
                        "type": "SECTION",
                        "catalogSectionUUID": "s1",
                        "payload": {
                            "standardItemsPayload": {
                                "title": {"text": "Regular Menu"},
                                "catalogItems": [
                                    {
                                        "uuid": "item-1",
                                        "title": "Regular Item",
                                        "price": 1000,
                                    }
                                ],
                                "sectionUUID": "s1",
                                "catalogSectionAnalyticsData": {},
                                "paginationEnabled": False,
                                "scores": [],
                            }
                        },
                    }
                ]
            }
        }
        deals = _parse_store_deals(store_data, "rest-uuid", "Test Restaurant")
        assert len(deals) == 0


class TestGetRestaurants:
    def test_returns_dataframe_with_restaurants(self, restaurants_df):
        assert len(restaurants_df) > 50

    def test_known_restaurants_present(self, restaurants_df):
        names = set(restaurants_df["name"])
        for expected in ["Burger King Bornheimer Str.", "McDonald's Bonn"]:
            if expected in names:
                return
        known_chains = [n for n in names if "burger" in n.lower() or "mcdonald" in n.lower()]
        assert len(known_chains) > 0, "No known chains found in restaurants"

    def test_some_restaurants_have_deals(self, restaurants_df):
        assert restaurants_df["has_deals"].sum() > 5

    def test_deal_descriptions_populated(self, restaurants_df):
        deals = restaurants_df[restaurants_df["has_deals"]]
        assert all(deals["deal_descriptions"] != "")

    def test_dataframe_has_expected_columns(self, restaurants_df):
        expected = [
            "uuid", "name", "slug", "address", "lat", "lng",
            "rating", "is_open", "delivery_eta", "cuisines",
            "deal_descriptions", "has_deals",
        ]
        for col in expected:
            assert col in restaurants_df.columns, f"Missing column: {col}"

    def test_pizza_company_present(self, restaurants_df):
        pizza = restaurants_df[
            restaurants_df["name"].str.contains("Pizza Company", case=False)
        ]
        assert len(pizza) > 0, "Pizza Company Tannenbusch not found"

    def test_pizza_company_has_deals(self, restaurants_df):
        pizza = restaurants_df[
            restaurants_df["name"].str.contains("Pizza Company Tannenbusch", case=False)
        ]
        assert len(pizza) > 0, "Pizza Company Tannenbusch not found"
        assert pizza.iloc[0]["has_deals"], "Pizza Company should have deals"


class TestGetDeals:
    @pytest.fixture(scope="class")
    def pizza_deals(self, feed_responses, restaurants):
        from camoufox.sync_api import Camoufox

        pizza = [r for r in restaurants if "Pizza Company Tannenbusch" in r.name]
        assert len(pizza) > 0, "Pizza Company Tannenbusch not found in feed"
        pizza_rest = pizza[0]

        with Camoufox(headless=True) as browser:
            page = browser.new_page()
            from src.ubereats.client import BONN_URL, _intercept_store

            page.goto(BONN_URL, wait_until="domcontentloaded", timeout=60000)
            _wait_for_page_data(page, _JS_WAIT_FOR_SEO_FEED)

            store_data = _intercept_store(page, pizza_rest)

        assert store_data is not None, "Could not load Pizza Company store page"

        return _parse_store_deals(
            store_data, pizza_rest.uuid, pizza_rest.name
        )

    def test_pizza_company_has_deals(self, pizza_deals):
        assert len(pizza_deals) > 0

    def test_pizza_company_2for1_pizza(self, pizza_deals):
        pizza_items = [
            d for d in pizza_deals
            if "individual pizza" in d.item_title.lower()
            or "giant" in d.item_title.lower()
        ]
        assert len(pizza_items) > 0, (
            f"No Individual Pizza deal found. "
            f"Deals: {[d.item_title for d in pizza_deals]}"
        )
        deal = pizza_items[0]
        assert deal.buy_quantity == 1
        assert deal.get_quantity == 1

    def test_deals_have_item_titles(self, pizza_deals):
        for deal in pizza_deals:
            assert deal.item_title, "Deal missing item title"

    def test_deals_have_descriptions(self, pizza_deals):
        for deal in pizza_deals:
            assert deal.deal_description, "Deal missing description"


class TestGetRestaurantsAndDeals:
    @pytest.fixture(scope="class")
    def combined_result(self):
        return get_restaurants_and_deals()

    def test_returns_two_dataframes(self, combined_result):
        restaurants_df, deals_df = combined_result
        assert isinstance(restaurants_df, pd.DataFrame)
        assert isinstance(deals_df, pd.DataFrame)

    def test_restaurants_non_empty(self, combined_result):
        restaurants_df, _ = combined_result
        assert len(restaurants_df) > 50

    def test_deals_non_empty(self, combined_result):
        _, deals_df = combined_result
        assert len(deals_df) > 0

    def test_restaurants_have_expected_columns(self, combined_result):
        restaurants_df, _ = combined_result
        for col in ["uuid", "name", "slug", "has_deals"]:
            assert col in restaurants_df.columns


def _make_restaurant(name, uuid="uuid-1", has_deals=True):
    """Create a minimal Restaurant for testing."""
    return Restaurant(
        uuid=uuid, name=name, slug=name.lower().replace(" ", "-"),
        b64uuid="b64", address="addr", lat=0.0, lng=0.0,
        rating=0.0, rating_count=0, is_open=True,
        delivery_eta="", delivery_fee="", price_bucket="",
        cuisines=[], deal_descriptions=[], has_deals=has_deals,
    )


_STORE_DATA_WITH_DEAL = {
    "catalogSectionsMap": {
        "s1": [{
            "type": "SECTION",
            "payload": {"standardItemsPayload": {
                "title": {"text": "2-for-1"},
                "promoUUID": "p1",
                "catalogItems": [{
                    "uuid": "item-1", "title": "Burger", "price": 1000,
                    "itemPromotion": {
                        "type": "buyXGetYItemPromotion",
                        "buyXGetYItemPromotion": {"buyQuantity": 1, "getQuantity": 1},
                    },
                }],
            }},
        }],
    },
}


class TestCrashRecovery:
    """Tests that get_restaurants_and_deals survives per-store crashes."""

    @patch("src.ubereats.client.time.sleep")
    @patch("src.ubereats.client._intercept_feed_with_page")
    @patch("src.ubereats.client.Camoufox")
    def test_skips_crashed_store_returns_remaining_deals(
        self, mock_camo, mock_feed, mock_sleep,
    ):
        r1 = _make_restaurant("Crash Store", uuid="crash-1")
        r2 = _make_restaurant("Good Store", uuid="good-1")
        mock_feed.return_value = [
            {"stores": {
                r.uuid: {"title": r.name, "slug": r.slug, "promotion": {"text": "deal"},
                          "location": {}, "meta": {}, "etaRange": {}, "rating": {}}
                for r in [r1, r2]
            }, "store_links": {r.slug: r.b64uuid for r in [r1, r2]}},
        ]

        mock_browser = MagicMock()
        mock_page = MagicMock()
        mock_browser.new_page.return_value = mock_page
        mock_camo.return_value.__enter__ = MagicMock(return_value=mock_browser)
        mock_camo.return_value.__exit__ = MagicMock(return_value=False)

        call_count = 0

        def fake_intercept(page, restaurant):
            nonlocal call_count
            call_count += 1
            if restaurant.uuid == "crash-1":
                raise RuntimeError("Page crashed")
            return _STORE_DATA_WITH_DEAL

        with patch("src.ubereats.client._intercept_store", side_effect=fake_intercept):
            with patch("src.ubereats.client._parse_feed_restaurants", return_value=[r1, r2]):
                restaurants_df, deals_df = get_restaurants_and_deals()

        assert len(deals_df) > 0
        assert all(deals_df["restaurant_name"] == "Good Store")
        assert call_count >= 3  # crash-1 first try + retry + good-1

    @patch("src.ubereats.client.time.sleep")
    @patch("src.ubereats.client._intercept_feed_with_page")
    @patch("src.ubereats.client.Camoufox")
    def test_retry_succeeds_on_fresh_page(
        self, mock_camo, mock_feed, mock_sleep,
    ):
        r1 = _make_restaurant("Flaky Store", uuid="flaky-1")
        mock_feed.return_value = [
            {"stores": {
                r1.uuid: {"title": r1.name, "slug": r1.slug, "promotion": {"text": "deal"},
                           "location": {}, "meta": {}, "etaRange": {}, "rating": {}}
            }, "store_links": {r1.slug: r1.b64uuid}},
        ]

        mock_browser = MagicMock()
        mock_page = MagicMock()
        mock_browser.new_page.return_value = mock_page
        mock_camo.return_value.__enter__ = MagicMock(return_value=mock_browser)
        mock_camo.return_value.__exit__ = MagicMock(return_value=False)

        attempts = []

        def fake_intercept(page, restaurant):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("Page crashed")
            return _STORE_DATA_WITH_DEAL

        with patch("src.ubereats.client._intercept_store", side_effect=fake_intercept):
            with patch("src.ubereats.client._parse_feed_restaurants", return_value=[r1]):
                _, deals_df = get_restaurants_and_deals()

        assert len(deals_df) == 1
        assert deals_df.iloc[0]["item_title"] == "Burger"
        assert mock_browser.new_page.call_count >= 2  # initial + retry
