"""Set up config.toml for a new location based on a German postal code.

Usage:
    pixi run setup-location 53113
"""

import sys
from pathlib import Path

import httpx

_REPO_ROOT = Path(__file__).parent.parent

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "delivery-api-location-setup/1.0"}



def main(postal_code: str) -> None:
    """Look up a German postal code and write location settings to config.toml.

    Args:
        postal_code: German postal code (e.g. "53113").
    """
    print(f"Looking up {postal_code}...")

    place = geocode(postal_code)
    city = extract_city(place["address"])
    state = place["address"].get("state", "")
    lat = round(float(place["lat"]), 4)
    lon = round(float(place["lon"]), 4)

    write_config(lat, lon)

    print(f"  City:      {city}, {state}")
    print(f"  Coords:    {lat}, {lon}")
    print()
    print("Written to config.toml. Delete bld/cache/ before running scrapers.")


def geocode(postal_code: str) -> dict:
    """Query Nominatim for a German postal code.

    Args:
        postal_code: German postal code.

    Returns:
        First Nominatim result dict with lat, lon, and address keys.

    Raises:
        ValueError: If no results are found for the postal code.
    """
    resp = httpx.get(
        NOMINATIM_URL,
        params={
            "postalcode": postal_code,
            "country": "de",
            "format": "json",
            "addressdetails": "1",
            "limit": "1",
        },
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise ValueError(f"No results found for postal code {postal_code!r}")
    return results[0]


def extract_city(address: dict) -> str:
    """Extract city name from a Nominatim address dict.

    Args:
        address: Nominatim address sub-object.

    Returns:
        City name string.

    Raises:
        ValueError: If no recognizable city field is present.
    """
    for key in ("city", "town", "village", "municipality"):
        if key in address:
            return address[key]
    raise ValueError(f"Could not determine city from address: {address}")


def write_config(
    lat: float,
    lon: float,
) -> None:
    """Write location settings to config.toml at the repo root.

    Args:
        lat: Latitude.
        lon: Longitude.
    """
    content = f"""\
[location]
# Wolt: coordinates of the city center
lat = {lat}
lon = {lon}
"""
    (_REPO_ROOT / "config.toml").write_text(content)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <postal_code>")
        sys.exit(1)
    main(sys.argv[1])
