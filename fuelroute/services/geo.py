"""Small, dependency-light geometry helpers (all distances in miles)."""

import numpy as np

EARTH_RADIUS_MILES = 3958.7613


def haversine_miles(lat1, lon1, lat2, lon2):
    """Vectorised great-circle distance. Accepts scalars or numpy arrays."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))


def to_unit_xyz(lats, lons):
    """Project lat/lon onto the unit sphere so a KD-tree can do nearest-neighbour."""
    lat = np.radians(np.asarray(lats, dtype=float))
    lon = np.radians(np.asarray(lons, dtype=float))
    return np.column_stack(
        (np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat))
    )


def chord_to_miles(chord):
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.clip(chord / 2, 0, 1))


def miles_to_chord(miles):
    return 2 * np.sin(miles / (2 * EARTH_RADIUS_MILES))


def cumulative_miles(lats, lons):
    """Distance along a polyline at each vertex, starting from 0."""
    seg = haversine_miles(lats[:-1], lons[:-1], lats[1:], lons[1:])
    return np.concatenate(([0.0], np.cumsum(seg)))


def resample_polyline(lats, lons, spacing_miles):
    """Return points every `spacing_miles` along the polyline plus their mile markers.

    OSRM geometries are dense in cities and sparse on long straight highways;
    resampling gives an even resolution for snapping stations to the route.
    """
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    cum = cumulative_miles(lats, lons)
    total = cum[-1]
    if total == 0:
        return lats[:1], lons[:1], np.array([0.0])
    markers = np.append(np.arange(0.0, total, spacing_miles), total)
    # Linear interpolation in lat/lon is plenty accurate at sub-mile spacing.
    return np.interp(markers, cum, lats), np.interp(markers, cum, lons), markers
