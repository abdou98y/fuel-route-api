"""Match one explicit station snapshot to sampled points on a road."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from scipy.spatial import cKDTree

from .geo import chord_to_miles, miles_to_chord, resample_polyline, to_unit_xyz
from .station_index import StationIndex

if TYPE_CHECKING:
    from .routing import Route


@dataclass(frozen=True)
class StationMatches:
    station_positions: np.ndarray
    mile_markers: np.ndarray
    distances_from_route: np.ndarray

    def __post_init__(self):
        arrays = (self.station_positions, self.mile_markers, self.distances_from_route)
        if any(
            array.ndim != 1 or len(array) != len(self.station_positions)
            for array in arrays
        ):
            raise ValueError(
                "Station-match arrays must be one-dimensional and aligned."
            )
        for array in arrays:
            array.setflags(write=False)

    def __len__(self) -> int:
        return len(self.station_positions)

    @classmethod
    def empty(cls) -> StationMatches:
        return cls(np.array([], dtype=int), np.array([]), np.array([]))


def stations_along_route(
    route: Route,
    stations: StationIndex,
    max_distance_miles: float,
    spacing_miles: float,
) -> StationMatches:
    """Build a road KD-tree; return named station positions and road distances."""
    if not len(stations):
        return StationMatches.empty()

    latitudes, longitudes, markers = resample_polyline(
        route.latitudes, route.longitudes, spacing_miles
    )
    # Broad geographic prefilter avoids querying stations far from this road.
    pad = max_distance_miles / 50 + 0.1
    in_box = np.flatnonzero(
        (stations.latitudes >= latitudes.min() - pad)
        & (stations.latitudes <= latitudes.max() + pad)
        & (stations.longitudes >= longitudes.min() - pad * 1.5)
        & (stations.longitudes <= longitudes.max() + pad * 1.5)
    )
    if not len(in_box):
        return StationMatches.empty()

    tree = cKDTree(to_unit_xyz(latitudes, longitudes))
    chord, nearest = tree.query(
        stations.xyz[in_box], distance_upper_bound=miles_to_chord(max_distance_miles)
    )
    hit = np.isfinite(chord)
    return StationMatches(
        station_positions=in_box[hit],
        mile_markers=markers[nearest[hit]],
        distances_from_route=chord_to_miles(chord[hit]),
    )
