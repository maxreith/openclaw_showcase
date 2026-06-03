"""Tests for src/setup_location.py."""

import sys
import tomllib
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "src")

from setup_location import (
    extract_city,
    geocode,
    write_config,
)

_NOMINATIM_BONN = [
    {
        "lat": "50.7358",
        "lon": "7.0979",
        "address": {
            "city": "Bonn",
            "state": "Nordrhein-Westfalen",
            "country": "Germany",
        },
    }
]

_NOMINATIM_MUNICH = [
    {
        "lat": "48.1351",
        "lon": "11.5820",
        "address": {
            "city": "München",
            "state": "Bayern",
        },
    }
]


def _mock_response(json_data, status_code=200, url=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.url = url or "https://example.com"
    resp.raise_for_status = MagicMock()
    return resp


class TestGeocode:
    def test_returns_first_result(self):
        with patch("httpx.get", return_value=_mock_response(_NOMINATIM_BONN)):
            result = geocode("53113")
        assert result["address"]["city"] == "Bonn"
        assert float(result["lat"]) == pytest.approx(50.7358)

    def test_raises_on_empty_results(self):
        with patch("httpx.get", return_value=_mock_response([])):
            with pytest.raises(ValueError, match="No results"):
                geocode("99999")

    def test_passes_correct_params(self):
        with patch("httpx.get", return_value=_mock_response(_NOMINATIM_BONN)) as mock_get:
            geocode("53113")
        params = mock_get.call_args.kwargs["params"]
        assert params["postalcode"] == "53113"
        assert params["country"] == "de"
        assert params["addressdetails"] == "1"


class TestExtractCity:
    def test_returns_city(self):
        assert extract_city({"city": "Bonn", "state": "NW"}) == "Bonn"

    def test_falls_back_to_town(self):
        assert extract_city({"town": "Königswinter"}) == "Königswinter"

    def test_falls_back_to_village(self):
        assert extract_city({"village": "Birgel"}) == "Birgel"

    def test_falls_back_to_municipality(self):
        assert extract_city({"municipality": "Dahlem"}) == "Dahlem"

    def test_raises_when_no_city_field(self):
        with pytest.raises(ValueError, match="Could not determine city"):
            extract_city({"state": "NW", "country": "Germany"})

    def test_city_takes_priority_over_town(self):
        assert extract_city({"city": "Bonn", "town": "Other"}) == "Bonn"


class TestWriteConfig:
    def test_writes_valid_toml(self, tmp_path, monkeypatch):
        monkeypatch.setattr("setup_location._REPO_ROOT", tmp_path)
        write_config(50.7358, 7.0979)
        data = tomllib.loads((tmp_path / "config.toml").read_text())
        loc = data["location"]
        assert loc["lat"] == pytest.approx(50.7358)
        assert loc["lon"] == pytest.approx(7.0979)
        assert set(loc) == {"lat", "lon"}

    def test_overwrites_existing_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr("setup_location._REPO_ROOT", tmp_path)
        (tmp_path / "config.toml").write_text("[location]\nlat = 0.0\nlon = 0.0\n")
        write_config(48.1351, 11.582)
        data = tomllib.loads((tmp_path / "config.toml").read_text())
        assert data["location"]["lat"] == pytest.approx(48.1351)
