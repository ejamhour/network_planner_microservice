from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib.resources import files
from math import atan2, cos, degrees, hypot, isfinite, radians, sin
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class AntennaPattern:
    """One frequency-specific antenna pattern from the antenna library."""

    frequency_mhz: float
    max_gain_dbi: float
    attenuation: dict[float, dict[float, float]] | None
    band_mhz: tuple[float, float] | None = None
    provenance: str | None = None
    note: str | None = None

    def gain_dbi(
        self,
        *,
        azimuth_offset_deg: float = 0.0,
        elevation_offset_deg: float = 0.0,
    ) -> float:
        """
        Return gain at the given angular offset from boresight.

        Library attenuation values are relative to ``max_gain_dbi``. Missing
        attenuation data means "use max gain", which is appropriate for models
        where only manufacturer gain is available.
        """
        if not self.attenuation:
            return float(self.max_gain_dbi)

        attenuation_db = interpolate_attenuation(
            self.attenuation,
            azimuth_offset_deg=azimuth_offset_deg,
            elevation_offset_deg=elevation_offset_deg,
        )
        return float(self.max_gain_dbi) - attenuation_db


@dataclass(frozen=True, slots=True)
class AntennaModel:
    """Antenna model declared in ``resources/antennas/antenna_library.yaml``."""

    antenna_id: str
    vendor: str | None
    model: str | None
    antenna_type: str
    use: str | None
    frequency_mhz: tuple[float, float] | None
    patterns: tuple[AntennaPattern, ...]

    def pattern_for_frequency(self, frequency_mhz: float) -> AntennaPattern:
        """Select the best available pattern for the requested frequency."""
        frequency_mhz = _finite_float(frequency_mhz, "frequency_mhz")
        if not self.patterns:
            raise ValueError(f"Antenna {self.antenna_id!r} has no patterns")

        containing_band = [
            pattern
            for pattern in self.patterns
            if (
                pattern.band_mhz is not None
                and pattern.band_mhz[0] <= frequency_mhz <= pattern.band_mhz[1]
            )
        ]
        if containing_band:
            return min(
                containing_band,
                key=lambda pattern: abs(pattern.frequency_mhz - frequency_mhz),
            )

        return min(
            self.patterns,
            key=lambda pattern: abs(pattern.frequency_mhz - frequency_mhz),
        )


class AntennaPlanner:
    """
    Antenna library loader and gain evaluator.

    This class replaces the old antenna assignment planner. It does not modify
    network graphs or create back-to-back nodes. Its job is only to calculate
    effective antenna gain from a library model, antenna orientation, link
    direction and optional side-pole shadowing.

    Angle convention
    ----------------
    ``bearing_deg`` is an absolute world bearing from the transmitting
    interface toward the receiving interface, with 0 degrees pointing north and
    positive angles rotating clockwise. ``azimuth_deg`` is the installed
    antenna boresight in the same convention. The library stores attenuation as
    offsets from boresight, so the evaluator converts
    ``bearing_deg - azimuth_deg`` to an offset in ``[-180, 180)``.

    ``elevation_deg`` is the vertical link angle from source to destination.
    ``downtilt_deg`` is added to that angle to obtain the elevation offset used
    by the pattern. For the first version, mechanical/electrical downtilt are
    represented by the single ``downtilt_deg`` argument.

    Parameters
    ----------
    library_path:
        Optional path to an antenna library YAML file. If omitted, the bundled
        ``cisei_lib/resources/antennas/antenna_library.yaml`` is loaded.

    Typical usage
    -------------
    ``gain_dbi`` is useful when the caller already knows angular offsets.
    ``effective_gain_dbi`` is useful when the caller knows bearing/elevation.
    ``link_gain_dbi`` is useful when the caller has projected coordinates and
    antenna installation heights. ``suggest_orientation`` and
    ``suggest_side_pole_shadow`` provide first-pass azimuth, tilt and
    side-mounted omni shadow settings from destination coordinates.
    """

    def __init__(self, library_path: str | Path | None = None) -> None:
        if library_path is None:
            library_path = (
                files("cisei_lib")
                .joinpath("resources", "antennas", "antenna_library.yaml")
            )
        self.library_path = library_path
        self.models = load_antenna_library(library_path)

    def model(self, antenna_id: str) -> AntennaModel:
        """
        Return one antenna model by library id.

        Parameters
        ----------
        antenna_id:
            Identifier declared in the antenna library, for example
            ``"pctel_boa9028"`` or ``"commscope_rv_65s_fvb"``.

        Returns
        -------
        AntennaModel
            Parsed antenna metadata and frequency-specific patterns.
        """
        try:
            return self.models[str(antenna_id)]
        except KeyError as error:
            raise KeyError(f"Unknown antenna model: {antenna_id}") from error

    def antenna_ids(self) -> list[str]:
        """
        Return available antenna ids in library order.

        This is mainly a discovery helper for notebooks, reports, and API
        validation.
        """
        return list(self.models)

    def gain_dbi(
        self,
        antenna_id: str,
        *,
        frequency_mhz: float,
        azimuth_offset_deg: float = 0.0,
        elevation_offset_deg: float = 0.0,
    ) -> float:
        """
        Return library gain for angular offsets from boresight.

        Parameters
        ----------
        antenna_id:
            Antenna model id from the library.
        frequency_mhz:
            Operating frequency in MHz. The closest matching pattern is used;
            if a pattern declares ``band_mhz`` and the frequency is inside that
            band, that pattern is preferred.
        azimuth_offset_deg:
            Horizontal angular offset from boresight in degrees. ``0`` means
            directly in front of the antenna. Positive/negative signs are
            equivalent after circular interpolation.
        elevation_offset_deg:
            Vertical angular offset from boresight in degrees. ``0`` means on
            the horizon/boresight plane for the stored pattern.

        Returns
        -------
        float
            Effective antenna gain in dBi after subtracting interpolated
            attenuation from the selected pattern's ``max_gain_dbi``.
        """
        model = self.model(antenna_id)
        pattern = model.pattern_for_frequency(frequency_mhz)
        return pattern.gain_dbi(
            azimuth_offset_deg=azimuth_offset_deg,
            elevation_offset_deg=elevation_offset_deg,
        )

    def effective_gain_dbi(
        self,
        antenna_id: str,
        *,
        frequency_mhz: float,
        bearing_deg: float,
        elevation_deg: float = 0.0,
        azimuth_deg: float = 0.0,
        downtilt_deg: float = 0.0,
        modifier: Mapping[str, Any] | None = None,
    ) -> float:
        """
        Return gain toward a destination direction.

        Parameters
        ----------
        antenna_id:
            Antenna model id from the library.
        frequency_mhz:
            Operating frequency in MHz.
        bearing_deg:
            Absolute bearing from this antenna to the other endpoint. Uses the
            planner convention: 0 degrees north, clockwise positive.
        elevation_deg:
            Vertical angle from this antenna to the other endpoint. Positive is
            upward from source to destination.
        azimuth_deg:
            Installed antenna boresight. For an omni antenna this usually has
            no azimuth effect unless a shadow modifier is used. For a
            sector/yagi/directional antenna it defines the main direction.
        downtilt_deg:
            Tilt offset added to ``elevation_deg`` before evaluating the
            pattern. Positive values move the evaluated direction upward in the
            current convention; use negative values for downward mechanical
            tilt if that is how the scenario chooses to represent downtilt.
        modifier:
            Optional modifier for side-pole shadowing:

        ``shadow_azimuth_deg``
            Absolute direction of maximum shadow loss in degrees. If omitted,
            no shadow loss is applied.
        ``shadow_width_deg``
            Angular width affected by the pole shadow. Defaults to 60.
        ``shadow_loss_db``
            Constant loss applied inside the shadow width. Defaults to 8.

        Returns
        -------
        float
            Effective gain in dBi, including library pattern attenuation and
            side-pole shadow loss.
        """
        azimuth_offset = signed_angle_delta(bearing_deg, azimuth_deg)
        elevation_offset = float(elevation_deg) + float(downtilt_deg)

        gain = self.gain_dbi(
            antenna_id,
            frequency_mhz=frequency_mhz,
            azimuth_offset_deg=azimuth_offset,
            elevation_offset_deg=elevation_offset,
        )
        gain -= pole_shadow_loss_db(
            bearing_deg=bearing_deg,
            modifier=modifier or {},
        )
        return gain

    def link_gain_dbi(
        self,
        antenna_id: str,
        *,
        frequency_mhz: float,
        src_pos: tuple[float, float],
        dst_pos: tuple[float, float],
        src_height_m: float = 0.0,
        dst_height_m: float = 0.0,
        azimuth_deg: float = 0.0,
        downtilt_deg: float = 0.0,
        modifier: Mapping[str, Any] | None = None,
    ) -> float:
        """
        Return effective gain using projected positions and heights.

        Parameters
        ----------
        antenna_id:
            Antenna model id from the library.
        frequency_mhz:
            Operating frequency in MHz.
        src_pos:
            Source projected coordinates as ``(x, y)`` in meters. This should
            be the same working CRS used by the planner.
        dst_pos:
            Destination projected coordinates as ``(x, y)`` in meters.
        src_height_m:
            Source antenna installation height in meters.
        dst_height_m:
            Destination antenna installation height in meters.
        azimuth_deg:
            Installed source antenna boresight in absolute world bearing.
        downtilt_deg:
            Source antenna tilt offset passed to ``effective_gain_dbi``.
        modifier:
            Optional shadow modifier.

        Returns
        -------
        float
            Effective source antenna gain toward the destination in dBi.
        """
        bearing = bearing_deg(src_pos, dst_pos)
        elevation = elevation_angle_deg(src_pos, dst_pos, src_height_m, dst_height_m)
        return self.effective_gain_dbi(
            antenna_id,
            frequency_mhz=frequency_mhz,
            bearing_deg=bearing,
            elevation_deg=elevation,
            azimuth_deg=azimuth_deg,
            downtilt_deg=downtilt_deg,
            modifier=modifier,
        )

    def suggest_orientation(
        self,
        src_pos: tuple[float, float],
        dst_positions: Iterable[tuple[float, float]] | tuple[float, float],
        *,
        src_height_m: float = 0.0,
        dst_heights_m: Iterable[float] | float | None = None,
    ) -> dict[str, Any]:
        """
        Suggest boresight azimuth and downtilt for one installation.

        Parameters
        ----------
        src_pos:
            Source projected coordinates as ``(x, y)`` in meters.
        dst_positions:
            One destination ``(x, y)`` tuple or an iterable of destination
            tuples. A single destination makes the azimuth point exactly at the
            destination. Multiple destinations use the circular mean of all
            bearings, which is a deterministic first-pass sector orientation.
        src_height_m:
            Source antenna installation height in meters.
        dst_heights_m:
            Optional destination height or iterable of destination heights in
            meters. If omitted, all destination heights are treated as ``0``.

        Returns
        -------
        dict
            ``azimuth_deg`` is the suggested absolute boresight direction.
            ``downtilt_deg`` is chosen so the mean destination elevation is
            close to boresight under ``effective_gain_dbi``'s convention.
            ``target_bearings_deg`` and ``target_elevations_deg`` are included
            for reporting and manual review.
        """
        positions = _position_list(dst_positions)
        heights = _height_list(dst_heights_m, len(positions))
        bearings = [bearing_deg(src_pos, dst_pos) for dst_pos in positions]
        elevations = [
            elevation_angle_deg(src_pos, dst_pos, src_height_m, dst_height_m)
            for dst_pos, dst_height_m in zip(positions, heights)
        ]
        mean_elevation = sum(elevations) / len(elevations)

        return {
            "azimuth_deg": circular_mean_deg(bearings),
            "downtilt_deg": -mean_elevation,
            "target_bearings_deg": bearings,
            "target_elevations_deg": elevations,
        }

    def suggest_side_pole_shadow(
        self,
        src_pos: tuple[float, float],
        dst_positions: Iterable[tuple[float, float]] | tuple[float, float],
        *,
        shadow_width_deg: float = 60.0,
        shadow_loss_db: float = 8.0,
    ) -> dict[str, Any]:
        """
        Suggest side-pole shadow settings for an omni installation.

        Parameters
        ----------
        src_pos:
            Source projected coordinates as ``(x, y)`` in meters.
        dst_positions:
            One destination ``(x, y)`` tuple or an iterable of destination
            tuples that should remain in the preferred coverage direction.
        shadow_width_deg:
            Width of the angular sector affected by pole shadowing.
        shadow_loss_db:
            Constant loss applied inside the shadow width.

        Returns
        -------
        dict
            Modifier fields accepted by ``effective_gain_dbi`` and suitable for
            saving on ``AntennaSpec``: ``shadow_azimuth_deg``,
            ``shadow_width_deg`` and ``shadow_loss_db``. The shadow center is
            placed opposite the circular mean bearing to the destinations.
        """
        positions = _position_list(dst_positions)
        target_azimuth = circular_mean_deg(
            [bearing_deg(src_pos, dst_pos) for dst_pos in positions]
        )
        return {
            "shadow_azimuth_deg": wrap_angle_deg(target_azimuth + 180.0),
            "shadow_width_deg": _finite_float(shadow_width_deg, "shadow_width_deg"),
            "shadow_loss_db": _finite_float(shadow_loss_db, "shadow_loss_db"),
            "target_azimuth_deg": target_azimuth,
        }


def load_antenna_library(path: str | Path) -> dict[str, AntennaModel]:
    """Load antenna models from the YAML antenna library."""
    if hasattr(path, "read_text"):
        text = path.read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")

    document = yaml.safe_load(text) or {}
    models = {}
    for item in document.get("antennas", []):
        model = _model_from_config(item)
        if model.antenna_id in models:
            raise ValueError(f"Duplicated antenna id: {model.antenna_id}")
        models[model.antenna_id] = model
    return models


def interpolate_attenuation(
    attenuation: Mapping[Any, Mapping[Any, Any]],
    *,
    azimuth_offset_deg: float,
    elevation_offset_deg: float,
) -> float:
    """Bilinearly interpolate attenuation in dB."""
    azimuth_table = _normalize_attenuation(attenuation)
    az0, az1, az_weight = _circular_bracket(
        sorted(azimuth_table),
        wrap_angle_deg(azimuth_offset_deg),
    )
    el0, el1, el_weight = _linear_bracket(
        sorted(azimuth_table[az0]),
        elevation_offset_deg,
    )

    def at(az: float, el: float) -> float:
        return float(azimuth_table[az][el])

    v00 = at(az0, el0)
    v01 = at(az0, el1)
    v10 = at(az1, el0)
    v11 = at(az1, el1)

    v0 = v00 + (v01 - v00) * el_weight
    v1 = v10 + (v11 - v10) * el_weight
    return float(v0 + (v1 - v0) * az_weight)


def pole_shadow_loss_db(
    *,
    bearing_deg: float,
    modifier: Mapping[str, Any],
) -> float:
    """Return extra attenuation from explicit side-pole shadow parameters."""
    shadow_azimuth = modifier.get("shadow_azimuth_deg")
    if shadow_azimuth is None:
        return 0.0

    shadow_width = float(modifier.get("shadow_width_deg", 60.0))
    shadow_loss = float(modifier.get("shadow_loss_db", 8.0))
    if shadow_width <= 0 or shadow_loss <= 0:
        return 0.0

    delta = abs(signed_angle_delta(bearing_deg, float(shadow_azimuth)))
    if delta <= shadow_width / 2.0:
        return shadow_loss
    return 0.0


def bearing_deg(
    src_pos: tuple[float, float],
    dst_pos: tuple[float, float],
) -> float:
    """Return bearing from source to destination, 0 deg north, clockwise."""
    dx = float(dst_pos[0]) - float(src_pos[0])
    dy = float(dst_pos[1]) - float(src_pos[1])
    if dx == 0 and dy == 0:
        return 0.0
    return wrap_angle_deg(degrees(atan2(dx, dy)))


def elevation_angle_deg(
    src_pos: tuple[float, float],
    dst_pos: tuple[float, float],
    src_height_m: float,
    dst_height_m: float,
) -> float:
    """Return link elevation angle from source to destination."""
    dx = float(dst_pos[0]) - float(src_pos[0])
    dy = float(dst_pos[1]) - float(src_pos[1])
    distance_m = hypot(dx, dy)
    if distance_m == 0:
        return 0.0
    return degrees(atan2(float(dst_height_m) - float(src_height_m), distance_m))


def circular_mean_deg(angles_deg: Iterable[float]) -> float:
    """
    Return the circular mean of bearings in degrees.

    The result uses the planner bearing convention: 0 degrees north and
    clockwise positive. Empty input raises ``ValueError``.
    """
    angles = list(angles_deg)
    if not angles:
        raise ValueError("At least one angle is required")

    x = sum(sin(radians(float(angle))) for angle in angles)
    y = sum(cos(radians(float(angle))) for angle in angles)
    if x == 0 and y == 0:
        return 0.0
    return wrap_angle_deg(degrees(atan2(x, y)))


def wrap_angle_deg(angle_deg: float) -> float:
    """Wrap an angle to [0, 360)."""
    return float(angle_deg) % 360.0


def signed_angle_delta(angle_deg: float, reference_deg: float) -> float:
    """Return signed angular difference in [-180, 180)."""
    return (float(angle_deg) - float(reference_deg) + 180.0) % 360.0 - 180.0


def _model_from_config(config: Mapping[str, Any]) -> AntennaModel:
    patterns = tuple(_pattern_from_config(item) for item in config.get("patterns", []))
    return AntennaModel(
        antenna_id=str(config["id"]),
        vendor=config.get("vendor"),
        model=config.get("model"),
        antenna_type=str(config.get("type", "unknown")),
        use=config.get("use"),
        frequency_mhz=_range_or_none(config.get("frequency_mhz")),
        patterns=patterns,
    )


def _pattern_from_config(config: Mapping[str, Any]) -> AntennaPattern:
    return AntennaPattern(
        frequency_mhz=_finite_float(config["frequency_mhz"], "frequency_mhz"),
        max_gain_dbi=_finite_float(config["max_gain_dbi"], "max_gain_dbi"),
        band_mhz=_range_or_none(config.get("band_mhz")),
        provenance=config.get("provenance"),
        note=config.get("note"),
        attenuation=(
            _normalize_attenuation(config["attenuation"])
            if config.get("attenuation") is not None
            else None
        ),
    )


def _normalize_attenuation(
    attenuation: Mapping[Any, Mapping[Any, Any]],
) -> dict[float, dict[float, float]]:
    normalized = {}
    for azimuth, elevation_table in attenuation.items():
        normalized[wrap_angle_deg(float(azimuth))] = {
            float(elevation): _finite_float(value, "attenuation")
            for elevation, value in dict(elevation_table).items()
        }
    return normalized


def _range_or_none(value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    left, right = value
    return (
        _finite_float(left, "range minimum"),
        _finite_float(right, "range maximum"),
    )


def _circular_bracket(values: list[float], angle_deg: float) -> tuple[float, float, float]:
    if not values:
        raise ValueError("Cannot interpolate an empty azimuth table")
    if len(values) == 1:
        return values[0], values[0], 0.0

    angle = wrap_angle_deg(angle_deg)
    if angle in values:
        return angle, angle, 0.0

    extended = values + [values[0] + 360.0]
    for left, right in zip(extended, extended[1:]):
        candidate = angle
        if candidate < left:
            candidate += 360.0
        if left <= candidate <= right:
            return (
                wrap_angle_deg(left),
                wrap_angle_deg(right),
                (candidate - left) / (right - left),
            )

    raise ValueError(f"Could not bracket azimuth angle: {angle_deg}")


def _linear_bracket(values: list[float], value: float) -> tuple[float, float, float]:
    if not values:
        raise ValueError("Cannot interpolate an empty elevation table")
    if len(values) == 1:
        return values[0], values[0], 0.0

    value = float(value)
    if value <= values[0]:
        return values[0], values[0], 0.0
    if value >= values[-1]:
        return values[-1], values[-1], 0.0

    for left, right in zip(values, values[1:]):
        if left <= value <= right:
            return left, right, (value - left) / (right - left)

    raise ValueError(f"Could not bracket elevation angle: {value}")


def _finite_float(value: Any, name: str) -> float:
    value = float(value)
    if not isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _position_list(
    positions: Iterable[tuple[float, float]] | tuple[float, float],
) -> list[tuple[float, float]]:
    if (
        isinstance(positions, tuple)
        and len(positions) == 2
        and not isinstance(positions[0], (tuple, list))
    ):
        positions = [positions]

    result = [
        (_finite_float(position[0], "x"), _finite_float(position[1], "y"))
        for position in positions
    ]
    if not result:
        raise ValueError("At least one destination position is required")
    return result


def _height_list(
    heights: Iterable[float] | float | None,
    size: int,
) -> list[float]:
    if heights is None:
        return [0.0] * size
    if isinstance(heights, (int, float)):
        return [_finite_float(heights, "dst_height_m")] * size

    result = [_finite_float(height, "dst_height_m") for height in heights]
    if len(result) != size:
        raise ValueError("dst_heights_m must match destination positions")
    return result
