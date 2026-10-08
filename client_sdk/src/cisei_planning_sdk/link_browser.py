"""Prepare planning interfaces in a caller-owned features session."""
from copy import deepcopy
from math import isfinite


def prepare_link(scenario, geo, src_interface, dst_interface):
    """Use current server configuration, without registering or owning geo."""
    if src_interface == dst_interface:
        raise ValueError("Select two distinct interfaces")
    config = scenario.export_scenario()
    interfaces = {}
    for site in config.get("sites", []):
        devices = {device["device_id"]: device for device in site.get("devices", [])}
        for interface in site.get("interfaces", []):
            identifier = interface["interface_id"]
            if identifier in interfaces:
                raise ValueError(f"Duplicate interface ID: {identifier}")
            interfaces[identifier] = (site, interface, devices)
    endpoints = []
    for identifier in (src_interface, dst_interface):
        if identifier not in interfaces:
            raise ValueError(f"Unknown interface: {identifier}")
        site, interface, devices = interfaces[identifier]
        device = devices.get(interface["device_id"])
        if device is None:
            raise ValueError(f"Missing device for interface {identifier}")
        height = device.get("mount_height_m")
        values = (site.get("lat"), site.get("lon"), height, interface.get("freq_mhz"))
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) for v in values):
            raise ValueError(f"Missing or invalid coordinates, mount height, or frequency for {identifier}")
        if not -90 <= values[0] <= 90 or not -180 <= values[1] <= 180 or values[3] <= 0:
            raise ValueError(f"Invalid coordinates or frequency for {identifier}")
        if not interface.get("tech"):
            raise ValueError(f"Missing technology for {identifier}")
        endpoints.append((site, interface, height))
    src_site, src, src_height = endpoints[0]
    dst_site, dst, dst_height = endpoints[1]
    if src["tech"] != dst["tech"]:
        raise ValueError(f"Technology mismatch: {src['tech']} != {dst['tech']}")
    if src["freq_mhz"] != dst["freq_mhz"]:
        raise ValueError(f"Frequency mismatch: {src['freq_mhz']} != {dst['freq_mhz']}")
    if src_site["site_id"] == dst_site["site_id"]:
        raise ValueError("Interfaces on the same site do not form a geographic link")
    return geo.link((src_site["lat"], src_site["lon"]), (dst_site["lat"], dst_site["lon"]),
                    tx_ha=src_height, rx_ha=dst_height, freq_mhz=src["freq_mhz"])


class LinkBrowser:
    """Browse an explicit candidate/planned edge list; first next opens row zero.

    Construction makes no features request. Selection prepares the link and
    returns the features result. Position changes only after successful preparation.
    Internal edges fail explicitly; the caller's list is never silently filtered.
    """
    def __init__(self, scenario, geo, edges):
        self.scenario = scenario
        self.geo = geo
        self.edges = deepcopy(list(edges))
        if any(not isinstance(edge, dict) or not isinstance(edge.get("src"), str)
               or not isinstance(edge.get("dst"), str) for edge in self.edges):
            raise ValueError("Each edge must be a dictionary with src and dst interface IDs")
        self.index = -1

    @property
    def current(self):
        return deepcopy(self.edges[self.index]) if self.index >= 0 else None

    def select(self, index):
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError("Edge index must be an integer")
        if not 0 <= index < len(self.edges):
            raise IndexError("Edge index outside browsing list")
        edge = self.edges[index]
        if edge.get("kind") == "internal":
            raise ValueError("Internal edges do not form geographic links")
        result = self.scenario.link(self.geo, edge["src"], edge["dst"])
        self.index = index
        return result

    def next(self):
        if self.index + 1 >= len(self.edges):
            raise StopIteration("No more links")
        return self.select(self.index + 1)

    def previous(self):
        if self.index <= 0:
            raise StopIteration("No previous link")
        return self.select(self.index - 1)
