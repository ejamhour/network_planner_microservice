from dataclasses import dataclass, field
from typing import Any
from shapely.geometry import Point, LineString
from math import inf

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
class PlanningNode:
    node_id: str
    pos: tuple[float, float]
    rank: float = inf
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def fixed(self) -> bool:
        raise NotImplementedError

    @property
    def relay(self) -> bool:
        raise NotImplementedError

@dataclass(slots=True)
class FieldNode(PlanningNode):
    can_relay: bool = True
    connected: bool = False

    @property
    def fixed(self) -> bool:
        return self.connected

    @property
    def relay(self) -> bool:
        return self.can_relay

@dataclass(slots=True)
class TowerNode(PlanningNode):
    azimuth_deg: float = 0.0
    beamwidth_deg: float = 360.0

    @property
    def fixed(self) -> bool:
        return True

    @property
    def relay(self) -> bool:
        return True    