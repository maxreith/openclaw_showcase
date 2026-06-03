import threading
import time
from unittest.mock import patch

import pandas as pd
import pytest

from src.cache import CACHE_DIR, META_FILE, age_minutes, cached, clear, info, load, save


@pytest.fixture(autouse=True)
def _clean_cache(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr("src.cache.CACHE_DIR", cache_dir)
    monkeypatch.setattr("src.cache.META_FILE", cache_dir / "_meta.json")


def _sample_df():
    return pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})


def test_save_and_load_roundtrip():
    df = _sample_df()
    save("test_key", df)
    loaded = load("test_key")
    pd.testing.assert_frame_equal(loaded, df)


def test_cached_uses_fresh_cache():
    call_count = 0

    def scrape():
        nonlocal call_count
        call_count += 1
        return _sample_df()

    cached("k", scrape, max_age_minutes=60)
    cached("k", scrape, max_age_minutes=60)
    assert call_count == 1


def test_cached_refreshes_stale_data():
    call_count = 0

    def scrape():
        nonlocal call_count
        call_count += 1
        return _sample_df()

    cached("k", scrape, max_age_minutes=60)
    assert call_count == 1

    with patch("src.cache.time.time", return_value=time.time() + 3601):
        cached("k", scrape, max_age_minutes=60)
    assert call_count == 2


def test_cached_force_refresh():
    call_count = 0

    def scrape():
        nonlocal call_count
        call_count += 1
        return _sample_df()

    cached("k", scrape, max_age_minutes=0)
    cached("k", scrape, max_age_minutes=0)
    assert call_count == 2


def test_clear_single_key():
    save("a", _sample_df())
    save("b", _sample_df())
    clear("a")
    assert load("a") is None
    assert load("b") is not None


def test_clear_all():
    save("a", _sample_df())
    save("b", _sample_df())
    clear()
    assert load("a") is None
    assert load("b") is None


def test_info_shows_entries(capsys):
    save("demo", _sample_df())
    info()
    out = capsys.readouterr().out
    assert "demo" in out
    assert "3 rows" in out


def test_load_nonexistent_returns_none():
    assert load("nonexistent") is None


def test_concurrent_saves_preserve_all_keys():
    barrier = threading.Barrier(2)

    def save_key(key):
        barrier.wait()
        save(key, _sample_df())

    t1 = threading.Thread(target=save_key, args=("key_a",))
    t2 = threading.Thread(target=save_key, args=("key_b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert load("key_a") is not None
    assert load("key_b") is not None
    assert age_minutes("key_a") is not None
    assert age_minutes("key_b") is not None
