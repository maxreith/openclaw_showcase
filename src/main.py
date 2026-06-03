"""Run all scrapers in parallel groups, using the shared cache to avoid re-fetching."""

import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "src")

from cache import age_minutes, cached, info, save
from config import Location, load_location

MAX_AGE = 180
ACTIVE_CACHE_KEYS = (
    "wolt_restaurants",
    "wolt_deals",
    "ubereats_restaurants",
    "ubereats_deals",
    "brh_menu",
    "hofgarten_menu",
    "campo_menu",
    "kirchenpavillon_menu",
)


def main():
    loc = load_location()
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_wolt = pool.submit(_run_wolt_group, loc)
        fut_ubereats = pool.submit(_run_ubereats_group)
        fut_fast = pool.submit(_run_fast_group)

    all_messages = []
    all_failed = []
    for fut in [fut_wolt, fut_ubereats, fut_fast]:
        messages, failed = fut.result()
        all_messages.extend(messages)
        all_failed.extend(failed)

    for msg in all_messages:
        print(msg)

    print()
    info(ACTIVE_CACHE_KEYS)

    elapsed = time.time() - t0
    print(f"\nTotal: {elapsed:.0f}s")

    if all_failed:
        print(f"Failed: {', '.join(all_failed)}")
        sys.exit(1)


def _run_group(scrapers):
    """Run a list of scrapers sequentially.

    Args:
        scrapers: List of (cache_key, module_path, func_name, kwargs) tuples.

    Returns:
        Tuple of (status_messages, failed_keys).
    """
    from functools import partial
    from importlib import import_module

    messages = []
    failed = []
    for key, module_path, func_name, kwargs in scrapers:
        mod = import_module(module_path)
        fn = partial(getattr(mod, func_name), **kwargs) if kwargs else getattr(mod, func_name)
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


def _run_wolt_group(loc: Location) -> tuple[list[str], list[str]]:
    """Run Wolt scrapers sequentially."""
    coords = {"lat": loc.lat, "lon": loc.lon}
    return _run_group([
        ("wolt_restaurants", "wolt.client", "get_restaurants", coords),
        ("wolt_deals", "wolt.client", "get_deals", coords),
    ])


def _run_ubereats_group() -> tuple[list[str], list[str]]:
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
        return [f"  {key}: FAILED ({elapsed:.0f}s) - {e}" for key in keys], list(keys)


def _run_fast_group() -> tuple[list[str], list[str]]:
    """Run fast scrapers (BRH, Hofgarten, Campo, Kirchenpavillon) sequentially."""
    return _run_group([
        ("brh_menu", "bundesrechnungshof.client", "get_menu", {}),
        ("hofgarten_menu", "hofgarten.client", "get_menu", {}),
        ("campo_menu", "campo.client", "get_menu", {}),
        ("kirchenpavillon_menu", "kirchenpavillon.client", "get_menu", {}),
    ])


if __name__ == "__main__":
    main()
