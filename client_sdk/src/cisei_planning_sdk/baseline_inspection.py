"""Inspect measured links with the existing, caller-owned features viewer."""
from copy import deepcopy
import math

from .link_panel import LinkInspectionPanel


def finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


class MeasuredLinkScenario:
    """Adapter for LinkBrowser; no server scenario or planning interfaces needed."""
    def __init__(self, edges, *, freq_mhz, minimum_height_m):
        self.edges = {(edge["src"], edge["dst"]): deepcopy(edge) for edge in edges}
        self.freq_mhz = freq_mhz
        self.minimum_height_m = minimum_height_m

    def link(self, geo, src, dst):
        record = self.edges[(src, dst)]["record"]
        positions, heights = [], []
        for side in ("tx", "rx"):
            position = record[side].get("pos")
            if not isinstance(position, (list, tuple)) or len(position) != 2:
                raise ValueError(f"{side} requires latitude and longitude")
            lat, lon = (finite(v, f"{side} position") for v in position)
            if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                raise ValueError(f"Invalid {side} coordinates")
            height = finite(record[side].get("ant_height"), f"{side} height")
            if self.minimum_height_m is not None:
                height = max(height, self.minimum_height_m)
            if height < 0:
                raise ValueError(f"Invalid {side} height")
            positions.append((lat, lon))
            heights.append(height)
        return geo.link(*positions, tx_ha=heights[0], rx_ha=heights[1], freq_mhz=self.freq_mhz)


class BaselineInspectionPanel(LinkInspectionPanel):
    """Largest prediction discrepancies, using the established features views.

    No geographic request on construction. Press Show to load the selected
    link in the supplied features session. No registration/close is owned here.
    """

    def __init__(self, geo, edges, *, freq_mhz, minimum_height_m=None):
        freq_mhz = finite(freq_mhz, "Frequency")
        if freq_mhz <= 0:
            raise ValueError("Frequency must be positive")
        if minimum_height_m is not None:
            minimum_height_m = finite(minimum_height_m, "Minimum height")
            if minimum_height_m < 0:
                raise ValueError("Minimum height must be nonnegative")
        scenario = MeasuredLinkScenario(edges, freq_mhz=freq_mhz,
                                        minimum_height_m=minimum_height_m)
        super().__init__(scenario, geo, edges, auto_load=False, initial_endpoint="")
        self.links.description = "Enlaces:"
        self.refresh.description = "Inspecionar"
        self.status.value = ("Selecione uma visualização e pressione Inspecionar."
                             if edges else "Nenhum enlace atende aos filtros de desvio.")

    @staticmethod
    def _label(index, edge):
        return (f"{index+1}. {edge['src_site']} → {edge['dst_site']} · "
                f"previsto {edge['predicted']:.1f} / medido {edge['measured']:.1f} dBm · "
                f"diferença {edge['residual']:+.1f} dB")
