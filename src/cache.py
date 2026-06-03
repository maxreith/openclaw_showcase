"""Shared DataFrame cache for scraper results.

Stores DataFrames as .feather files in bld/cache/ with timestamps in a JSON
metadata file. Avoids re-scraping when data is still fresh. Thread-safe for
concurrent scraper execution.
"""

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pandas as pd

CACHE_DIR = Path("bld/cache")
META_FILE = CACHE_DIR / "_meta.json"
_meta_lock = threading.Lock()


def cached(
    key: str,
    scrape_fn: Callable[[], pd.DataFrame],
    max_age_minutes: int = 30,
) -> pd.DataFrame:
    """Return cached DataFrame if fresh, otherwise call scrape_fn and cache result.

    Args:
        key: Cache key name.
        scrape_fn: Zero-argument callable that returns a DataFrame.
        max_age_minutes: Maximum age in minutes before re-scraping. Use 0 to
            force a fresh scrape.

    Returns:
        The (possibly cached) DataFrame.
    """
    with _meta_lock:
        age = age_minutes(key)
        if age is not None and age < max_age_minutes:
            return load(key)

    df = scrape_fn()
    save(key, df)
    return df


def load(key: str) -> pd.DataFrame | None:
    """Load a cached DataFrame by key.

    Args:
        key: Cache key name.

    Returns:
        The cached DataFrame, or None if not found.
    """
    path = CACHE_DIR / f"{key}.feather"
    if not path.exists():
        return None
    return pd.read_feather(path)


def save(key: str, df: pd.DataFrame) -> None:
    """Save DataFrame to cache with current timestamp.

    Args:
        key: Cache key name.
        df: DataFrame to cache.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_feather(CACHE_DIR / f"{key}.feather")
    with _meta_lock:
        meta = _read_meta()
        meta[key] = time.time()
        _write_meta(meta)


def age_minutes(key: str) -> float | None:
    """Return age of cached key in minutes.

    Args:
        key: Cache key name.

    Returns:
        Age in minutes, or None if the key is not cached.
    """
    meta = _read_meta()
    ts = meta.get(key)
    if ts is None:
        return None
    return (time.time() - ts) / 60


def info(keys: list[str] | tuple[str, ...] | None = None) -> None:
    """Print summary of cached keys: rows, age, file size.

    Args:
        keys: Optional cache keys to print. If omitted, prints all metadata.
    """
    meta = _read_meta()
    if keys is not None:
        meta = {key: meta[key] for key in keys if key in meta}
    if not meta:
        print("Cache is empty.")
        return

    for key, ts in sorted(meta.items()):
        path = CACHE_DIR / f"{key}.feather"
        age = (time.time() - ts) / 60
        if path.exists():
            size_kb = path.stat().st_size / 1024
            df = pd.read_feather(path)
            print(f"{key}: {len(df)} rows, {age:.0f}m ago, {size_kb:.1f} KB")
        else:
            print(f"{key}: metadata exists but file missing")


def clear(key: str | None = None) -> None:
    """Clear one key or all cached data.

    Args:
        key: Cache key to remove. If None, removes all cached data.
    """
    if key is not None:
        path = CACHE_DIR / f"{key}.feather"
        path.unlink(missing_ok=True)
        meta = _read_meta()
        meta.pop(key, None)
        _write_meta(meta)
    else:
        for f in CACHE_DIR.glob("*.feather"):
            f.unlink()
        _write_meta({})


def _read_meta() -> dict:
    if META_FILE.exists():
        return json.loads(META_FILE.read_text())
    return {}


def _write_meta(meta: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    META_FILE.write_text(json.dumps(meta))
