"""In-memory, numpy-backed view of all fuel stations.

~6.5k rows fit comfortably in memory; loading them once per process means a
route request does zero database queries and only vectorised math.

Snapshots are read-only and process-local. Clearing this cache affects only
the current process; restart all API workers after a station-price import.
"""

import threading
from dataclasses import dataclass

import numpy as np

from .geo import to_unit_xyz


@dataclass(frozen=True)
class StationIndex:
    ids: np.ndarray
    names: tuple[str, ...]
    addresses: tuple[str, ...]
    cities: tuple[str, ...]
    states: tuple[str, ...]
    prices: np.ndarray
    latitudes: np.ndarray
    longitudes: np.ndarray
    xyz: np.ndarray

    def __post_init__(self):
        for array in (self.ids, self.prices, self.latitudes, self.longitudes, self.xyz):
            array.setflags(write=False)

    def __len__(self):
        return len(self.ids)


_lock = threading.Lock()
_index: StationIndex | None = None


def get_station_index() -> StationIndex:
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                _index = _build()
    return _index


def clear_station_index():
    """Drop this process's cached snapshot; existing request snapshots stay valid."""
    global _index
    with _lock:
        _index = None


def _build() -> StationIndex:
    from fuelroute.models import FuelStation

    rows = list(
        FuelStation.objects.values_list(
            "opis_id",
            "name",
            "address",
            "city",
            "state",
            "retail_price",
            "latitude",
            "longitude",
        )
    )
    ids, names, addresses, cities, states, prices, lats, lons = (
        zip(*rows) if rows else ([],) * 8
    )
    lats, lons = np.array(lats, dtype=float), np.array(lons, dtype=float)
    return StationIndex(
        ids=np.array(ids, dtype=int),
        names=tuple(names),
        addresses=tuple(addresses),
        cities=tuple(cities),
        states=tuple(states),
        prices=np.array(prices, dtype=float),
        latitudes=lats,
        longitudes=lons,
        xyz=to_unit_xyz(lats, lons),
    )
