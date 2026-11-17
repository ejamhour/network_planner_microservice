"""Compare coordinate representations without selecting or changing either."""
from functools import lru_cache
from math import isfinite

from pyproj import Geod, Transformer

_GEOD = Geod(ellps="WGS84")


@lru_cache(maxsize=32)
def _geographic_transformer(working_crs):
    """Reuse transformations across sites and streamed pole rows."""
    return Transformer.from_crs(working_crs, "EPSG:4326", always_xy=True)


def coordinate_discrepancy_m(site, working_crs):
    """Return geodesic separation in metres for a site with both pairs.

    Reproject x/y to WGS84 before measuring, so the CRS need not use metres.
    Source coordinates are never modified. Invalid transforms are input errors.
    """
    lon, lat = _geographic_transformer(working_crs).transform(site.x, site.y, errcheck=True)
    _, _, distance = _GEOD.inv(site.lon, site.lat, lon, lat)
    if not isfinite(distance):
        raise ValueError("Coordinate comparison did not produce a finite distance")
    return distance
