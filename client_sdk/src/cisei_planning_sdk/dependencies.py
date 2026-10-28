"""Read-only dependency analysis of saved parent assignments (no planning)."""
from collections import defaultdict


def network_dependencies(result):
    """Return interface/site dependency sets from the selected topology.

    Counts describe selected paths, not physical failure probabilities. Site
    aggregation deduplicates interfaces and does not count the selected site.
    """
    if "planned_nodes" not in result:
        raise ValueError("Result is missing planned_nodes; solve before analyzing dependencies")
    nodes = {r["node_id"]: dict(r) for r in result.get("rpl_nodes", [])}
    for row in result["planned_nodes"]:
        nodes[row["node_id"]] = {**nodes.get(row["node_id"], {}), **row}
    children = defaultdict(set)
    for iid, row in nodes.items():
        parent = row.get("parent")
        if parent is not None:
            if parent not in nodes:
                raise ValueError(f"Unknown parent {parent!r} for interface {iid!r}")
            children[parent].add(iid)
    # Detect cycles once, in linear time, before traversing descendants.
    indegree = {iid: 0 for iid in nodes}
    for successors in children.values():
        for iid in successors:
            indegree[iid] += 1
    pending = [iid for iid in nodes if not indegree[iid]]
    visited = 0
    while pending:
        iid = pending.pop()
        visited += 1
        for child in children[iid]:
            indegree[child] -= 1
            if not indegree[child]:
                pending.append(child)
    if visited != len(nodes):
        raise ValueError("Parent assignments contain a cycle")
    interfaces = {}
    by_site = defaultdict(set)
    for iid, row in nodes.items():
        descendants = set()
        pending = list(children[iid])
        while pending:
            child = pending.pop()
            if child not in descendants:
                descendants.add(child)
                pending.extend(children[child])
        site_id = row.get("site_id", iid)
        by_site[site_id].add(iid)
        interfaces[iid] = {
            "site_id": site_id, "children": sorted(children[iid]),
            "descendants": sorted(descendants),
        }
    sites = {}
    for site_id, own in by_site.items():
        direct = set().union(*(children[iid] for iid in own)) - own
        descendants = set().union(*(set(interfaces[iid]["descendants"]) for iid in own)) - own
        descendant_sites = {nodes[iid].get("site_id", iid) for iid in descendants} - {site_id}
        descendant_devices = {nodes[iid].get("device_id", iid) for iid in descendants}
        sites[site_id] = {
            "interface_ids": sorted(own), "children": sorted(direct),
            "descendants": sorted(descendants),
            "descendant_sites": sorted(descendant_sites),
            "child_count": len(direct), "descendant_count": len(descendants),
            "descendant_site_count": len(descendant_sites),
            "descendant_device_count": len(descendant_devices),
        }
    return {"interfaces": interfaces, "sites": sites}
