"""Preparation of installed links from an existing solved project."""
from .dependencies import network_dependencies


def installed_edges(result, target, source):
    """Keep source parent links, removing only disabled endpoints.

    Existing interface identities must retain their radio configuration. Added
    interfaces have no invented RF links; an external cellular interface can
    therefore be represented without planning an operator's infrastructure.
    """
    network_dependencies(result)  # Validate source parent assignments.
    original = {i["interface_id"]: i for s in source["sites"] for i in s["interfaces"]}
    interfaces = {i["interface_id"]: i for s in target["sites"] for i in s["interfaces"]}
    for iid, before in original.items():
        if iid not in interfaces:
            raise ValueError(f"Installed interface removed by replacement: {iid}")
        for key in ("site_id", "device_id", "tech", "freq_mhz", "tx_power_dbm", "antenna_id"):
            if interfaces[iid].get(key) != before.get(key):
                raise ValueError(f"Installed interface {iid}: replacement changes {key}")
    if "planned_edges" not in result:
        raise ValueError("Installed topology requires planned_edges in source result")
    parent_pairs = {frozenset((r["node_id"], r["parent"]))
                    for r in result["planned_nodes"] if r.get("parent") is not None}
    exported_pairs = {frozenset((e["src"], e["dst"])) for e in result["planned_edges"]}
    if parent_pairs != exported_pairs:
        raise ValueError("Source planned_edges do not match selected parent links")
    metric_records = {frozenset((e["src"], e["dst"])): e for e in result.get("metric_edges", [])}
    edges, excluded = [], []
    for edge in result["planned_edges"]:
        src, dst = edge["src"], edge["dst"]
        if src not in original or dst not in original:
            raise ValueError("Source planned edge references an unknown interface")
        if not interfaces[src].get("enabled", True) or not interfaces[dst].get("enabled", True):
            excluded.append({"src": src, "dst": dst, "reason": "disabled_interface"})
            continue
        attrs = metric_records.get(frozenset((src, dst)), {})
        internal = original[src]["device_id"] == original[dst]["device_id"]
        record = {"src": src, "dst": dst, "kind": attrs.get("kind", "internal" if internal else "rf"),
                  "origin": "installed_solution"}
        if record["kind"] == "internal":
            record["metric"] = edge["metric"]
        edges.append(record)
    return edges, excluded
