"""Local views of exported planning results; no service connection required."""
from math import isfinite
from typing import Any


def show_metrics(metrics: dict[str, Any], *, save_path=None, display=True):
    """Browse saved link costs locally in HTML; no metric computation occurs."""
    from cisei_planning_sdk.metrics_view import MetricsView
    view = MetricsView(metrics)
    if save_path is not None:
        view.save_html(save_path)
    if display:
        from IPython.display import display as show
        show(view)
    return view


def show_evaluation(evaluation: dict[str, Any], *, save_path=None, display=True):
    """Show one offline HTML evaluation browser, with optional standalone export."""
    from cisei_planning_sdk.evaluation_view import EvaluationView
    view = EvaluationView(evaluation)
    if save_path is not None:
        view.save_html(save_path)
    if display:
        from IPython.display import display as show
        show(view)
    return view


def _plot_network_matplotlib(result: dict[str, Any], *, kind: str = "final", ax=None):
    """Draw a site-level candidate or final network from a loaded result.

    Interfaces at the same site are collapsed into one marker. Candidate links
    are undirected lines. Final links use each planned node's parent and point
    toward its routing root. Internal edges are omitted and parallel site links
    are collapsed. All original sites remain visible, including unserved sites.
    Coordinates are projected x/y values, not a street-map background.
    Returns a Matplotlib Axes. Install ``cisei-planning-sdk[notebook]`` to plot.
    """
    if kind not in {"candidate", "final"}:
        raise ValueError("kind must be 'candidate' or 'final'")
    edge_key = "candidate_edges" if kind == "candidate" else "planned_edges"
    for key in ("rpl_nodes", edge_key):
        if key not in result:
            raise ValueError(f"Result is missing {key!r}; export it before plotting")
    if kind == "final" and "planned_nodes" not in result:
        raise ValueError("Result is missing 'planned_nodes'")
    import matplotlib.pyplot as plt

    nodes = {row["node_id"]: row for row in result["rpl_nodes"]}
    served = {row["node_id"] for row in result.get("planned_nodes", [])}
    sites = {}
    for node_id, row in nodes.items():
        x, y = row.get("x"), row.get("y")
        if x is None or y is None or not isfinite(float(x)) or not isfinite(float(y)):
            raise ValueError(f"Node {node_id!r} has no finite projected coordinates")
        site_id = row.get("site_id") or node_id
        site = sites.setdefault(site_id, {"pos": (float(x), float(y)), "connected": False, "served": False})
        site["connected"] |= bool(row.get("connected"))
        site["served"] |= node_id in served
    if ax is None:
        _, ax = plt.subplots(figsize=(10, 7))
    drawn = set()
    if kind == "candidate":
        for edge in result[edge_key]:
            src, dst = nodes[edge["src"]], nodes[edge["dst"]]
            a = src.get("site_id") or edge["src"]
            b = dst.get("site_id") or edge["dst"]
            site_pair = frozenset((a, b))
            if a == b or site_pair in drawn:
                continue
            drawn.add(site_pair)
            ax.plot(
                (sites[a]["pos"][0], sites[b]["pos"][0]),
                (sites[a]["pos"][1], sites[b]["pos"][1]),
                color="#78909c", alpha=0.45, lw=1.0, zorder=1,
            )
    else:
        for row in result["planned_nodes"]:
            node_id = row["node_id"]
            parent_id = row.get("parent")
            if parent_id is None or node_id not in nodes or parent_id not in nodes:
                continue
            node = nodes[node_id]
            parent = nodes[parent_id]
            child_site = node.get("site_id") or node_id
            parent_site = parent.get("site_id") or parent_id
            route = (child_site, parent_site)
            if child_site == parent_site or route in drawn:
                continue
            drawn.add(route)
            ax.annotate(
                "",
                xy=sites[parent_site]["pos"],
                xytext=sites[child_site]["pos"],
                arrowprops={
                    "arrowstyle": "->",
                    "color": "#78909c",
                    "alpha": 0.65,
                    "lw": 1.2,
                },
                zorder=1,
            )
    groups = [("Connected site / tower", "#2457a7", "^")]
    groups += [("Client / relay", "#299d8f", "o")] if kind == "candidate" else [
        ("Served client / relay", "#299d8f", "o"), ("Unserved client / relay", "#d65f5f", "o")]
    for label, color, marker in groups:
        selected = []
        for site in sites.values():
            category = ("Connected site / tower" if site["connected"] else
                        "Client / relay" if kind == "candidate" else
                        "Served client / relay" if site["served"] else "Unserved client / relay")
            if category == label:
                selected.append(site["pos"])
        if selected:
            ax.scatter(*zip(*selected), color=color, marker=marker, s=75, label=label, zorder=3)
    for site_id, site in sites.items():
        ax.annotate(str(site_id), site["pos"], xytext=(5, 5), textcoords="offset points", fontsize=8)
    ax.set(title="Candidate network" if kind == "candidate" else "Planned network",
           xlabel="Projected easting (m)", ylabel="Projected northing (m)")
    ax.set_aspect("equal", adjustable="datalim")
    ax.margins(0.15)
    if sites:
        ax.legend()
    ax.grid(alpha=0.15)
    return ax


def plot_network(result: dict[str, Any], *, kind: str = "final", ax=None, display=True, save_path=None):
    """Display an offline interactive network inside a notebook.

    Returns NetworkView, with save_html(path) for a standalone browser file.
    Endpoint selection filters links; click nodes or links for saved details.
    Candidate sectors require antenna metadata from an updated service export.
    Pass save_path to write the same view beside a project result.
    Pass ax for the legacy Matplotlib rendering, or display=False for file use.
    """
    if ax is not None:
        return _plot_network_matplotlib(result, kind=kind, ax=ax)
    from cisei_planning_sdk.network_view import NetworkView
    view = NetworkView(result, kind=kind)
    if save_path is not None:
        view.save_html(save_path)
    if display:
        from IPython.display import display as show
        show(view)
    return view
