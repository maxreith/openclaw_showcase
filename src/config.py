"""Load location configuration from config.toml."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent


@dataclass
class Location:
    """Location parameters for all scrapers.

    Attributes:
        lat: Latitude of the city center (used by Wolt).
        lon: Longitude of the city center (used by Wolt).
    """

    lat: float
    lon: float


def load_location() -> Location:
    """Read location settings from config.toml at the repo root.

    Returns:
        Location dataclass populated from the [location] section.
    """
    with open(_REPO_ROOT / "config.toml", "rb") as f:
        data = tomllib.load(f)
    loc = data["location"]
    return Location(
        lat=loc["lat"],
        lon=loc["lon"],
    )
