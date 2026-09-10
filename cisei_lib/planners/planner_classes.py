from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from shapely.geometry import Point, LineString
from math import floor, inf, isfinite

from pyproj import Transformer

@dataclass(slots=True)
class Pole:
    """
    Candidate repeater location.

    Coordinate convention:
    - lat and lon are geographic coordinates in EPSG:4326.
    - pos is represented as [lat, lon].
    - Shapely geometry uses (lon, lat).
    - Projection to UTM is performed internally by PoleGraph.
    """
        
    pole_id: str
    lat: float
    lon: float

    name: str | None = None
    ant_h: float | None = None
    elevation: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        record = {
            "pole_id": self.pole_id,
            "name": self.name or self.pole_id,
            "pos": [self.lat, self.lon],
            "geometry": Point(self.lon, self.lat),
        }

        if self.ant_h is not None:
            record["ant_h"] = self.ant_h

        if self.elevation is not None:
            record["elevation"] = self.elevation

        record.update(self.extra)
        return record

@dataclass(slots=True)
class LinkNode:
    name: str
    lat: float
    lon: float

    ant_h: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def pos(self) -> tuple[float, float]:
        return self.lat, self.lon

@dataclass(slots=True)
class PlannerNode:
    node_id: str
    lat: float
    lon: float

    name: str | None = None
    ant_h: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        record = {
            "node_id": self.node_id,
            "name": self.name or self.node_id,
            "pos": [self.lat, self.lon],
            "geometry": Point(self.lon, self.lat),
        }

        if self.ant_h is not None:
            record["ant_h"] = self.ant_h

        record.update(self.extra)
        return record

@dataclass(slots=True)
class RPLNode:
    node_id: str
    connected: bool = False
    rpl_relay: bool = True
    rank: float = inf
    pos_utm: tuple[float, float] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def get_utm_epsg(lon: float, lat: float) -> str:
    """
    Return the WGS84 UTM EPSG code for a geographic point.

    Curitiba (~ -49, -25) maps to EPSG:32722.
    """
    zone = floor((lon + 180) / 6) + 1

    if 56.0 <= lat < 64.0 and 3.0 <= lon < 12.0:
        zone = 32

    if 72.0 <= lat < 84.0:
        if 0.0 <= lon < 9.0:
            zone = 31
        elif 9.0 <= lon < 21.0:
            zone = 33
        elif 21.0 <= lon < 33.0:
            zone = 35
        elif 33.0 <= lon < 42.0:
            zone = 37

    epsg_base = 32600 if lat >= 0 else 32700
    return f"EPSG:{epsg_base + zone}"


def _id_part(prefix: str, value: int | str) -> str:
    if isinstance(value, int):
        return f"{prefix}{value}"

    value = str(value).strip()
    if not value:
        raise ValueError("ID parts cannot be empty")

    return value


def make_device_id(site_id: str, device_key: int | str = 0) -> str:
    return f"{site_id}:{_id_part('d', device_key)}"


def make_interface_id(
    site_id: str,
    device_index: int | str = 0,
    interface_index: int | str = 0,
) -> str:
    return (
        f"{site_id}:"
        f"{_id_part('d', device_index)}:"
        f"{_id_part('i', interface_index)}"
    )


@dataclass(slots=True)
class Site:
    """
    Physical planning place.

    The planner owns one working CRS per run. If x/y are provided, they must
    already be normalized to that working CRS.
    """
    site_id: str
    lat: float | None = None
    lon: float | None = None
    x: float | None = None
    y: float | None = None
    kind: str = "field"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def position_id(self) -> str:
        return self.site_id

    def resolve(
        self,
        working_crs: str | None = None,
        *,
        validate: bool = False,
        tolerance_m: float = 1.0,
    ) -> str:
        has_geo = self.lat is not None and self.lon is not None
        has_xy = self.x is not None and self.y is not None

        if not has_geo and not has_xy:
            raise ValueError(
                f"Site {self.site_id} requires lat/lon or x/y"
            )

        if has_geo:
            self.lat = self._finite_float(self.lat, "lat")
            self.lon = self._finite_float(self.lon, "lon")
            working_crs = working_crs or get_utm_epsg(self.lon, self.lat)

        if has_xy:
            self.x = self._finite_float(self.x, "x")
            self.y = self._finite_float(self.y, "y")

        if has_geo and not has_xy:
            transformer = Transformer.from_crs(
                "EPSG:4326",
                working_crs,
                always_xy=True,
            )
            self.x, self.y = transformer.transform(self.lon, self.lat)
            return working_crs

        if has_xy and not has_geo:
            if working_crs is None:
                raise ValueError(
                    f"working_crs is required for projected-only position "
                    f"{self.site_id}"
                )

            transformer = Transformer.from_crs(
                working_crs,
                "EPSG:4326",
                always_xy=True,
            )
            self.lon, self.lat = transformer.transform(self.x, self.y)
            return working_crs

        if working_crs is None:
            raise ValueError(
                f"working_crs is required to use projected coordinates for "
                f"{self.site_id}"
            )

        if validate:
            transformer = Transformer.from_crs(
                "EPSG:4326",
                working_crs,
                always_xy=True,
            )
            expected_x, expected_y = transformer.transform(self.lon, self.lat)
            if (
                abs(expected_x - self.x) > tolerance_m
                or abs(expected_y - self.y) > tolerance_m
            ):
                raise ValueError(
                    f"Projected coordinates for {self.site_id} differ "
                    f"from lat/lon by more than {tolerance_m} m"
                )

        return working_crs

    @property
    def geo_pos(self) -> tuple[float, float]:
        if self.lat is None or self.lon is None:
            raise ValueError(f"Site {self.site_id} is not resolved")
        return self.lat, self.lon

    @property
    def projected_pos(self) -> tuple[float, float]:
        if self.x is None or self.y is None:
            raise ValueError(f"Site {self.site_id} is not resolved")
        return self.x, self.y

    def to_record(self) -> dict[str, Any]:
        record = {
            "site_id": self.site_id,
            "kind": self.kind,
            "lat": self.lat,
            "lon": self.lon,
            "x": self.x,
            "y": self.y,
        }

        if self.lat is not None and self.lon is not None:
            record["pos"] = [self.lat, self.lon]
            record["geometry"] = Point(self.lon, self.lat)

        record.update(self.extra)
        return record

    @staticmethod
    def _finite_float(value: float | None, name: str) -> float:
        if value is None:
            raise ValueError(f"{name} is required")

        value = float(value)
        if not isfinite(value):
            raise ValueError(f"{name} must be finite")

        return value


@dataclass(slots=True)
class AntennaSpec:
    kind: str = "omni"
    model_id: str | None = None
    description: str | None = None
    gain_dbi: float = 0.0
    height_m: float = 7.0
    azimuth_deg: float | None = None
    downtilt_deg: float | None = None
    beamwidth_deg: float | None = None
    shadow_azimuth_deg: float | None = None
    shadow_width_deg: float | None = None
    shadow_loss_db: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "kind": self.kind,
            "model_id": self.model_id,
            "description": self.description,
            "height_m": self.height_m,
            "azimuth_deg": self.azimuth_deg,
            "downtilt_deg": self.downtilt_deg,
            "beamwidth_deg": self.beamwidth_deg,
            "shadow_azimuth_deg": self.shadow_azimuth_deg,
            "shadow_width_deg": self.shadow_width_deg,
            "shadow_loss_db": self.shadow_loss_db,
        }
        if self.model_id is None or self.gain_dbi != 0.0:
            data["gain_dbi"] = self.gain_dbi
        data.update(self.extra)
        return data


@dataclass(slots=True)
class Device:
    device_id: str
    site_id: str
    connected: bool = False
    can_route: bool = False
    rank: float = inf
    mount_height_m: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def radio_id(self) -> str:
        return self.device_id

    @property
    def position_id(self) -> str:
        return self.site_id

    def to_dict(self) -> dict[str, Any]:
        data = {
            "device_id": self.device_id,
            "site_id": self.site_id,
            "connected": self.connected,
            "can_route": self.can_route,
            "rank": self.rank,
            "mount_height_m": self.mount_height_m,
        }
        data.update(self.extra)
        return data


@dataclass(slots=True)
class RadioInterface:
    interface_id: str
    device_id: str
    site_id: str
    tech: str
    freq_mhz: float
    tx_power_dbm: float
    antenna_id: str
    can_relay: bool = True
    medium: str = "rf"
    max_links: float = inf
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def radio_id(self) -> str:
        return self.device_id

    @property
    def position_id(self) -> str:
        return self.site_id

    def to_rpl_node(
        self,
        site: Site,
        device: Device | None = None,
    ) -> RPLNode:
        if site.site_id != self.site_id:
            raise ValueError(
                f"Interface {self.interface_id} belongs to "
                f"{self.site_id}, not {site.site_id}"
            )

        if device is not None and device.device_id != self.device_id:
            raise ValueError(
                f"Interface {self.interface_id} belongs to "
                f"{self.device_id}, not {device.device_id}"
            )

        extra = {
            "site_id": self.site_id,
            "device_id": self.device_id,
            "tech": self.tech,
            "freq_mhz": self.freq_mhz,
            "tx_power_dbm": self.tx_power_dbm,
            "antenna_id": self.antenna_id,
            "interface_can_relay": self.can_relay,
            "medium": self.medium,
            "max_links": self.max_links,
        }

        if device is not None:
            extra["mount_height_m"] = device.mount_height_m
            extra["device_connected"] = device.connected
            extra["device_can_route"] = device.can_route

        extra.update(self.extra)

        return RPLNode(
            node_id=self.interface_id,
            connected=bool(device.connected) if device is not None else False,
            rpl_relay=(
                self.can_relay
                or (bool(device.can_route) if device is not None else False)
            ),
            rank=device.rank if device is not None else inf,
            pos_utm=site.projected_pos,
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        data = {
            "interface_id": self.interface_id,
            "device_id": self.device_id,
            "site_id": self.site_id,
            "tech": self.tech,
            "freq_mhz": self.freq_mhz,
            "tx_power_dbm": self.tx_power_dbm,
            "antenna_id": self.antenna_id,
            "can_relay": self.can_relay,
            "medium": self.medium,
            "max_links": self.max_links if isfinite(self.max_links) else None,
        }
        data.update(self.extra)
        return data


@dataclass(slots=True)
class RadioInterfacePattern:
    interface_key: int | str = 0
    tech: str = "wisun"
    freq_mhz: float = 900.0
    tx_power_dbm: float = 20.0
    antenna_id: str = "default_omni"
    can_relay: bool = True
    medium: str = "rf"
    max_links: float = inf
    extra: dict[str, Any] = field(default_factory=dict)

    def instantiate(
        self,
        site_id: str,
        device_key: int | str,
    ) -> RadioInterface:
        return RadioInterface(
            interface_id=make_interface_id(
                site_id,
                device_key,
                self.interface_key,
            ),
            device_id=make_device_id(site_id, device_key),
            site_id=site_id,
            tech=self.tech,
            freq_mhz=self.freq_mhz,
            tx_power_dbm=self.tx_power_dbm,
            antenna_id=self.antenna_id,
            can_relay=self.can_relay,
            medium=self.medium,
            max_links=self.max_links,
            extra=deepcopy(self.extra),
        )


@dataclass(slots=True)
class DevicePattern:
    device_key: int | str = 0
    connected: bool = False
    can_route: bool = False
    rank: float = inf
    mount_height_m: float | None = None
    interfaces: list[RadioInterfacePattern] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def instantiate(
        self,
        site_id: str,
    ) -> tuple[Device, list[RadioInterface]]:
        device = Device(
            device_id=make_device_id(site_id, self.device_key),
            site_id=site_id,
            connected=self.connected,
            can_route=self.can_route,
            rank=self.rank,
            mount_height_m=self.mount_height_m,
            extra=deepcopy(self.extra),
        )
        interfaces = [
            interface.instantiate(site_id, self.device_key)
            for interface in self.interfaces
        ]
        return device, interfaces


@dataclass(slots=True)
class SitePattern:
    name: str
    kind: str = "field"
    devices: list[DevicePattern] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def instantiate(
        self,
        site_id: str,
        *,
        lat: float | None = None,
        lon: float | None = None,
        x: float | None = None,
        y: float | None = None,
        working_crs: str | None = None,
        resolve: bool = False,
        validate: bool = False,
        tolerance_m: float = 1.0,
        extra: dict[str, Any] | None = None,
    ) -> "SiteNode":
        site_id = self._clean_site_id(site_id)
        site = Site(
            site_id=site_id,
            lat=lat,
            lon=lon,
            x=x,
            y=y,
            kind=self.kind,
            extra=extra or {},
        )

        devices = []
        interfaces = []

        for device_pattern in self.devices:
            device, device_interfaces = device_pattern.instantiate(site_id)
            devices.append(device)
            interfaces.extend(device_interfaces)

        node = SiteNode(
            site=site,
            devices=devices,
            interfaces=interfaces,
            extra=deepcopy(self.extra),
        )

        if resolve:
            node.resolve_position(
                working_crs,
                validate=validate,
                tolerance_m=tolerance_m,
            )

        return node

    def instantiate_many(
        self,
        rows: Iterable[Mapping[str, Any]] | Any,
        *,
        id_prefix: str | None = None,
        working_crs: str | None = None,
        resolve: bool = False,
        validate: bool = False,
        tolerance_m: float = 1.0,
    ) -> list["SiteNode"]:
        records = self._records(rows)
        id_prefix = id_prefix or self.name or "node"

        nodes = []
        used_ids = set()
        generated_count = 0

        for index, row in enumerate(records, start=1):
            site_id = self._row_site_id(row)
            if site_id is None:
                generated_count += 1
                site_id = f"{id_prefix}_{generated_count}"

            site_id = self._clean_site_id(site_id)
            if site_id in used_ids:
                raise ValueError(f"Duplicated site ID: {site_id}")
            used_ids.add(site_id)

            node = self.instantiate(
                site_id=site_id,
                lat=self._row_value(row, "lat"),
                lon=self._row_value(row, "lon"),
                x=self._row_value(row, "x"),
                y=self._row_value(row, "y"),
                working_crs=working_crs,
                resolve=resolve,
                validate=validate,
                tolerance_m=tolerance_m,
                extra={
                    key: value
                    for key, value in row.items()
                    if key not in {
                        "site_id",
                        "position_id",
                        "id",
                        "name",
                        "lat",
                        "lon",
                        "x",
                        "y",
                    }
                },
            )
            nodes.append(node)

        return nodes

    @classmethod
    def default_wisun(
        cls,
        *,
        name: str = "wisun",
        kind: str = "field",
        antenna_id: str = "default_omni",
        tech: str = "wisun",
        freq_mhz: float = 900.0,
        tx_power_dbm: float = 20.0,
        mount_height_m: float | None = None,
        connected: bool = False,
        can_route: bool = False,
        can_relay: bool = True,
        rank: float = inf,
    ) -> "SitePattern":
        return cls(
            name=name,
            kind=kind,
            devices=[
                DevicePattern(
                    device_key=0,
                    connected=connected,
                    can_route=can_route,
                    rank=rank,
                    mount_height_m=mount_height_m,
                    interfaces=[
                        RadioInterfacePattern(
                            interface_key=0,
                            tech=tech,
                            freq_mhz=freq_mhz,
                            tx_power_dbm=tx_power_dbm,
                            antenna_id=antenna_id,
                            can_relay=can_relay,
                        )
                    ],
                )
            ],
        )

    @classmethod
    def tower_sectors(
        cls,
        antenna_ids: list[str],
        *,
        name: str = "tower",
        tech: str = "lte",
        freq_mhz: float = 900.0,
        tx_power_dbm: float = 43.0,
        mount_height_m: float | None = None,
        connected: bool = True,
        can_route: bool = False,
        can_relay: bool = True,
        rank: float = 0.0,
    ) -> "SitePattern":
        return cls(
            name=name,
            kind="tower",
            devices=[
                DevicePattern(
                    device_key=0,
                    connected=connected,
                    can_route=can_route,
                    rank=rank,
                    mount_height_m=mount_height_m,
                    interfaces=[
                        RadioInterfacePattern(
                            interface_key=index,
                            tech=tech,
                            freq_mhz=freq_mhz,
                            tx_power_dbm=tx_power_dbm,
                            antenna_id=antenna_id,
                            can_relay=can_relay,
                        )
                        for index, antenna_id in enumerate(antenna_ids)
                    ],
                )
            ],
        )

    @staticmethod
    def _records(rows: Iterable[Mapping[str, Any]] | Any) -> list[dict[str, Any]]:
        if hasattr(rows, "to_dict"):
            rows = rows.to_dict("records")

        return [
            dict(row)
            if isinstance(row, Mapping)
            else dict(row._asdict())
            if hasattr(row, "_asdict")
            else dict(vars(row))
            for row in rows
        ]

    @staticmethod
    def _row_site_id(row: Mapping[str, Any]) -> str | None:
        for key in ("site_id", "position_id", "id", "name"):
            value = row.get(key)
            if not SitePattern._is_missing(value):
                return str(value).strip()
        return None

    @staticmethod
    def _row_value(row: Mapping[str, Any], key: str) -> Any:
        value = row.get(key)
        if SitePattern._is_missing(value):
            return None
        return value

    @staticmethod
    def _clean_site_id(site_id: str) -> str:
        site_id = str(site_id).strip()
        if not site_id:
            raise ValueError("site_id cannot be empty")
        return site_id

    @staticmethod
    def _is_missing(value: Any) -> bool:
        if value is None:
            return True

        try:
            if value != value:
                return True
        except TypeError:
            pass

        return isinstance(value, str) and not value.strip()


@dataclass(slots=True)
class SiteNode:
    site: Site
    devices: list[Device] = field(default_factory=list)
    interfaces: list[RadioInterface] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def node_id(self) -> str:
        return self.site.site_id

    @property
    def site_id(self) -> str:
        return self.site.site_id

    @property
    def position(self) -> Site:
        return self.site

    @property
    def radios(self) -> list[Device]:
        return self.devices

    def resolve_position(
        self,
        working_crs: str | None = None,
        *,
        validate: bool = False,
        tolerance_m: float = 1.0,
    ) -> str:
        return self.site.resolve(
            working_crs,
            validate=validate,
            tolerance_m=tolerance_m,
        )

    def rpl_nodes(self) -> list[RPLNode]:
        devices = {device.device_id: device for device in self.devices}
        return [
            interface.to_rpl_node(
                self.site,
                devices.get(interface.device_id),
            )
            for interface in self.interfaces
        ]

@dataclass(slots=True)
class FieldSite(SiteNode):
    @classmethod
    def with_default_interface(
        cls,
        site_id: str,
        *,
        lat: float | None = None,
        lon: float | None = None,
        x: float | None = None,
        y: float | None = None,
        tech: str = "wisun",
        freq_mhz: float = 900.0,
        tx_power_dbm: float = 20.0,
        mount_height_m: float | None = None,
        antenna_id: str = "default_omni",
        connected: bool = False,
        can_route: bool = False,
        can_relay: bool = True,
        rank: float = inf,
        working_crs: str | None = None,
        resolve: bool = False,
        validate: bool = False,
        tolerance_m: float = 1.0,
        extra: dict[str, Any] | None = None,
    ) -> "FieldSite":
        pattern = SitePattern.default_wisun(
            name="field",
            kind="field",
            tech=tech,
            freq_mhz=freq_mhz,
            tx_power_dbm=tx_power_dbm,
            mount_height_m=mount_height_m,
            antenna_id=antenna_id,
            connected=connected,
            can_route=can_route,
            can_relay=can_relay,
            rank=rank,
        )
        node = pattern.instantiate(
            site_id,
            lat=lat,
            lon=lon,
            x=x,
            y=y,
            working_crs=working_crs,
            resolve=resolve,
            validate=validate,
            tolerance_m=tolerance_m,
            extra=extra,
        )
        return cls(
            site=node.site,
            devices=node.devices,
            interfaces=node.interfaces,
            extra=node.extra,
        )

@dataclass(slots=True)
class TowerSite(SiteNode):
    @classmethod
    def with_sector_interfaces(
        cls,
        site_id: str,
        *,
        antenna_ids: list[str],
        lat: float | None = None,
        lon: float | None = None,
        x: float | None = None,
        y: float | None = None,
        tech: str = "lte",
        freq_mhz: float = 900.0,
        tx_power_dbm: float = 43.0,
        mount_height_m: float | None = None,
        connected: bool = True,
        can_route: bool = False,
        can_relay: bool = True,
        rank: float = 0.0,
        working_crs: str | None = None,
        resolve: bool = False,
        validate: bool = False,
        tolerance_m: float = 1.0,
        extra: dict[str, Any] | None = None,
    ) -> "TowerSite":
        pattern = SitePattern.tower_sectors(
            antenna_ids,
            tech=tech,
            freq_mhz=freq_mhz,
            tx_power_dbm=tx_power_dbm,
            mount_height_m=mount_height_m,
            connected=connected,
            can_route=can_route,
            can_relay=can_relay,
            rank=rank,
        )
        node = pattern.instantiate(
            site_id,
            lat=lat,
            lon=lon,
            x=x,
            y=y,
            working_crs=working_crs,
            resolve=resolve,
            validate=validate,
            tolerance_m=tolerance_m,
            extra=extra,
        )
        return cls(
            site=node.site,
            devices=node.devices,
            interfaces=node.interfaces,
            extra=node.extra,
        )
