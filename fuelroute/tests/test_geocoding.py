from unittest import mock

import pytest

from fuelroute.services.geocoding import (
    GeocodingError,
    geocode,
    lookup_city,
    normalize_city,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Mc Donald", "mcdonald"),
        ("Ft. Worth", "fortworth"),
        ("Saint Louis", "stlouis"),
        ("S Coffeyville", "southcoffeyville"),
    ],
)
def test_normalize_city(raw, expected):
    assert normalize_city(raw) == expected


@pytest.mark.parametrize(
    "city,state",
    [
        ("Big Cabin", "OK"),
        ("Dodge City", "KS"),
        ("Nashville", "TN"),
        ("Saint Louis", "MO"),
        ("De Forest", "WI"),
    ],
)
def test_lookup_city_uses_offline_gazetteer(city, state):
    assert lookup_city(city, state) is not None


def test_geocode_city_state_offline():
    with mock.patch("fuelroute.services.geocoding.requests.get") as http:
        location = geocode("Chicago, Illinois")
    http.assert_not_called()
    assert location.source == "gazetteer"
    assert location.latitude == pytest.approx(41.84, abs=0.2)


def test_geocode_coordinates():
    location = geocode("39.7392, -104.9903")
    assert (location.latitude, location.longitude, location.source) == (
        39.7392,
        -104.9903,
        "coordinates",
    )


def test_rejects_locations_outside_usa():
    with pytest.raises(GeocodingError, match="outside the USA"):
        geocode("48.8566, 2.3522")  # Paris


def test_falls_back_to_nominatim_for_addresses(settings):
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    response = mock.Mock(status_code=200)
    response.json.return_value = [
        {"lat": "40.7484", "lon": "-73.9857", "display_name": "Empire State Building"}
    ]
    with mock.patch(
        "fuelroute.services.geocoding.requests.get", return_value=response
    ) as http:
        location = geocode("350 5th Ave, New York")
    http.assert_called_once()
    assert location.source == "nominatim"
