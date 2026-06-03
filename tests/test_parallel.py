"""Tests for parallel scraper orchestration in main.py and lunch_feed.py."""

import sys
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, "src")

from config import Location
from main import _run_fast_group, _run_group, _run_wolt_group, main
from lunch_feed import (
    _refresh_fast_group,
    _refresh_group,
    _refresh_wolt_group,
    refresh_caches,
)


def _sample_df():
    return pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})


@pytest.fixture(autouse=True)
def _clean_cache(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr("src.cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("src.cache.META_FILE", cache_dir / "_meta.json")


class TestRunGroup:
    def test_runs_all_scrapers(self):
        mod = MagicMock()
        mod.get_menu.return_value = _sample_df()
        with patch.dict("sys.modules", {"bundesrechnungshof.client": mod, "hofgarten.client": mod}):
            with patch("main.cached", side_effect=lambda k, fn, **kw: fn()):
                messages, failed = _run_group([
                    ("brh_menu", "bundesrechnungshof.client", "get_menu", {}),
                    ("hofgarten_menu", "hofgarten.client", "get_menu", {}),
                ])
        assert len(messages) == 2
        assert failed == []
        assert all("ok" in m for m in messages)

    def test_failure_does_not_stop_group(self):
        call_count = {"n": 0}

        def fake_cached(key, fn, **kw):
            call_count["n"] += 1
            if key == "brh_menu":
                raise RuntimeError("boom")
            return fn()

        mod = MagicMock()
        mod.get_menu.return_value = _sample_df()
        with patch.dict("sys.modules", {"bundesrechnungshof.client": mod, "hofgarten.client": mod}):
            with patch("main.cached", side_effect=fake_cached):
                messages, failed = _run_group([
                    ("brh_menu", "bundesrechnungshof.client", "get_menu", {}),
                    ("hofgarten_menu", "hofgarten.client", "get_menu", {}),
                ])
        assert failed == ["brh_menu"]
        assert call_count["n"] == 2


_DUMMY_LOC = Location(
    lat=50.7346,
    lon=7.0997,
)


class TestMainParallel:
    def test_all_groups_execute(self):
        groups_called = set()

        def track_wolt(loc):
            groups_called.add("wolt")
            return [" wolt: ok"], []

        def track_fast():
            groups_called.add("fast")
            return ["  fast: ok"], []

        with (
            patch("main._run_wolt_group", side_effect=track_wolt),
            patch("main._run_fast_group", side_effect=track_fast),
            patch("main.load_location", return_value=_DUMMY_LOC),
            patch("main.info"),
        ):
            main()

        assert groups_called == {"wolt", "fast"}

    def test_failure_in_one_group_does_not_prevent_others(self):
        def fail_fast():
            raise RuntimeError("scraper exploded")

        def ok_group(loc):
            return ["  ok"], []

        with (
            patch("main._run_wolt_group", side_effect=ok_group),
            patch("main._run_fast_group", side_effect=fail_fast),
            patch("main.load_location", return_value=_DUMMY_LOC),
            patch("main.info"),
        ):
            with pytest.raises(RuntimeError):
                main()


class TestRefreshCachesParallel:
    def test_all_groups_execute(self):
        groups_called = set()

        def track_wolt():
            groups_called.add("wolt")
            return ["  wolt: ok"], []

        def track_fast():
            groups_called.add("fast")
            return ["  fast: ok"], []

        with (
            patch("lunch_feed._refresh_wolt_group", side_effect=track_wolt),
            patch("lunch_feed._refresh_fast_group", side_effect=track_fast),
        ):
            failed = refresh_caches()

        assert groups_called == {"wolt", "fast"}
        assert failed == []

    def test_failure_collected_across_groups(self):
        with (
            patch("lunch_feed._refresh_wolt_group", return_value=([], ["wolt_deals"])),
            patch("lunch_feed._refresh_fast_group", return_value=([], [])),
        ):
            failed = refresh_caches()

        assert failed == ["wolt_deals"]
