import matplotlib.pyplot as plt
import numpy as np
import msgpack
import networkx as nx
from typing import List, Dict, Any, Optional, Tuple
import math




class BaseMetrics():
    nx_dg: nx.DiGraph | None

    def __init__(self):
        pass

    def load_graph(self):
        pass

        
    def base(data, p, r =2):
        min_val = min([x for x in data if x > 0]) # remove errors
        filtered = [x for x in data if x < r * min_val]
        return np.percentile(filtered, p)  

    def pavg(data, p):
        per = np.percentile(data, p)
        filtered_data = [x for x in data if x <= per]
        return np.mean(filtered_data)
    
    
    def replace_zeros(data, base):
        return [d if d > 0 else 7* base for d in data]   


class RTTMetrics(BaseMetrics):

    def __init__(self, **kwargs):

        DEFAULT_CONTEXT = {
            'mac_retries' : 7,
            'symetry' : 1            
        }
        
        self.context = {**DEFAULT_CONTEXT, **kwargs}  


    def load_data(self, data: list):
        self.data =  np.array(data)


    def _hist_mode(values: List[float], bin_width: float = 1.0, min_val: float = 2.0
                ) -> Tuple[Optional[float], float]:
        """
        Returns (mode_center, support_fraction) for the first nonzero mode of 'values'
        using a simple histogram. Ignores values < min_val (noise floor).
        """
        xs = [v for v in values if v >= min_val and math.isfinite(v)]
        if not xs:
            return None, 0.0
        lo, hi = min(xs), max(xs)
        if hi <= lo:
            return None, 0.0
        nbins = max(1, int(math.ceil((hi - lo) / bin_width)))
        bins = [0]*nbins
        for v in xs:
            i = min(nbins-1, int((v - lo) // bin_width))
            bins[i] += 1
        peak_i = max(range(nbins), key=lambda i: bins[i])
        mode_center = lo + (peak_i + 0.5) * bin_width
        support = bins[peak_i] / max(1, len(xs))
        return mode_center, support

    def estimate_delta_from_spread(min_rtt: List[float], max_rtt: List[float],
                                bin_ms: float = 1.0, noise_floor_ms: float = 2.0,
                                min_support: float = 0.10) -> Tuple[Optional[float], float]:
        """
        Δ estimation from burst spreads S = max - min, across bursts of a single link.
        Returns (delta_ms, support_fraction). delta_ms is None if no stable nonzero mode found.
        """
        spreads = [(mx - mn) for mn, mx in zip(min_rtt, max_rtt)
                if math.isfinite(mn) and math.isfinite(mx)]
        mode, support = _hist_mode(spreads, bin_width=bin_ms, min_val=noise_floor_ms)
        if mode is None or support < min_support:
            return None, support
        return mode, support

    def classify_bursts(min_rtt: List[float], avg_rtt: List[float], max_rtt: List[float],
                        delta_ms: Optional[float], tau: float = 0.20, eps: float = 0.5
                    ) -> Dict[str, Any]:
        """
        Classifies each burst using only min/avg/max and a Δ (if available).
        S = max - min       (spread)
        A = avg - min       (mean inflation)
        R = A / (S + eps)   (fill ratio)
        Alignment test (if Δ): |S - kΔ|/Δ <= tau for some integer k>=1.
        Labels:
        - 'RF'        if aligned and R <= 0.25
        - 'Capacity'  if not aligned and R >= 0.60
        - 'Mixed'     otherwise
        Returns per-burst metrics and link-level rollup.
        """
        n = min(len(min_rtt), len(avg_rtt), len(max_rtt))
        rows = []
        aligned_deltas = []  # for Δ stability via implied Δ_i = S/k
        for i in range(n):
            mn, av, mx = min_rtt[i], avg_rtt[i], max_rtt[i]
            if not (math.isfinite(mn) and math.isfinite(av) and math.isfinite(mx)):
                rows.append({"S": float("nan"), "A": float("nan"), "R": float("nan"),
                            "k": None, "align": None, "label": "Invalid"})
                continue
            S = mx - mn
            A = av - mn
            R = A / (S + eps)
            k = None
            align = None
            if delta_ms is not None and delta_ms > 0 and S >= 2.0:  # ignore tiny spreads
                k_est = max(1, int(round(S / delta_ms)))
                align = abs(S - k_est * delta_ms) / delta_ms
                if align <= tau:
                    k = k_est
                    aligned_deltas.append(S / k_est)
            # decision
            if k is not None and R <= 0.25:
                label = "RF"
            elif (k is None) and (R >= 0.60):
                label = "Capacity"
            else:
                label = "Mixed"
            rows.append({"S": S, "A": A, "R": R, "k": k, "align": align, "label": label})

        # roll-up
        valid_rows = [r for r in rows if math.isfinite(r["S"])]
        rf = sum(1 for r in valid_rows if r["label"] == "RF")
        cap = sum(1 for r in valid_rows if r["label"] == "Capacity")
        mix = sum(1 for r in valid_rows if r["label"] == "Mixed")
        frac = lambda x: (x / len(valid_rows)) if valid_rows else 0.0
        med_R = statistics.median([r["R"] for r in valid_rows]) if valid_rows else float("nan")
        # Δ stability: coefficient of variation over implied Δ_i
        delta_cv = (statistics.pstdev(aligned_deltas) / statistics.mean(aligned_deltas)
                    if len(aligned_deltas) >= 2 and statistics.mean(aligned_deltas) > 0 else None)

        return {
            "per_burst": rows,
            "rollup": {
                "n_bursts": len(valid_rows),
                "frac_RF": frac(rf),
                "frac_Capacity": frac(cap),
                "frac_Mixed": frac(mix),
                "median_R": med_R,
                "delta_locked": (delta_ms is not None),
                "delta_cv": delta_cv
            }
        }

    def fit_delta_and_classify(min_rtt: List[float], avg_rtt: List[float], max_rtt: List[float],
                            bin_ms: float = 1.0, noise_floor_ms: float = 2.0,
                            min_support: float = 0.10, tau: float = 0.20, eps: float = 0.5
                            ) -> Dict[str, Any]:
        """
        Convenience wrapper: estimate Δ from spreads, then classify bursts.
        """
        delta_ms, support = estimate_delta_from_spread(
            min_rtt, max_rtt, bin_ms=bin_ms, noise_floor_ms=noise_floor_ms, min_support=min_support
        )
        result = classify_bursts(min_rtt, avg_rtt, max_rtt, delta_ms, tau=tau, eps=eps)
        result["delta"] = {"value_ms": delta_ms, "support": support}
        return result




class MACRetryMetrics(BaseMetrics):

    def __init__(self):
        pass