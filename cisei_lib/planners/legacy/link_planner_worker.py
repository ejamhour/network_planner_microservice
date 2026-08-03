from cisei_lib.planners.link_planner import LinkPlanner
from queue import Empty



class LinkPlannerWorker(LinkPlanner):
    def __init__(
        self,
        project: str,
        home,
        *,
        shared_edges,
        shared_nodes,
        queue,
        lock,
        **planner_kwargs,
    ):
        super().__init__(
            project=project,
            home=home,
            **planner_kwargs,
        )

        self.shared_edges = shared_edges
        self.shared_nodes = shared_nodes
        self.queue = queue
        self.lock = lock

    # Runs links from the shared queue until it is empty.
    def consume_links(self):
        while True:
            try:
                src_id, dst_id = self.queue.get(timeout=1)
            except Empty:
                break

            self.set_link_by_id(src_id, dst_id)
            self.plan_link()

    # Reuses metric evaluations produced by any worker.
    def edge_metric(self, src_pos, dst_pos, src_ha, dst_ha):
        key = (
            tuple(src_pos),
            tuple(dst_pos),
            src_ha,
            dst_ha,
            self.context.get("metric_spec", "default"),
        )

        with self.lock:
            if key in self.shared_edges:
                return self.shared_edges[key]

        # Expensive evaluation is intentionally performed outside the lock.
        result = super().edge_metric(
            src_pos,
            dst_pos,
            src_ha,
            dst_ha,
        )

        with self.lock:
            if key in self.shared_edges:
                return self.shared_edges[key]

            self.shared_edges[key] = result
            return result

    # Adds every evaluated candidate edge to the global graph data.
    def add_edge(self, src_node: dict, dst_node: dict, edge_attrs: dict):
        super().add_edge(src_node, dst_node, edge_attrs)

        # PoleGraph uses an undirected nx.Graph.
        edge_key = tuple(sorted((
            src_node["name"],
            dst_node["name"],
        )))

        with self.lock:
            if edge_key not in self.shared_edges:
                self.shared_edges[edge_key] = edge_attrs.copy()

            for node in (src_node, dst_node):
                if node.get("hop_type") == "rep":
                    self.shared_nodes[node["name"]] = node.copy()

    def get_node(self, name):
        if name in self.shared_nodes:
            return self.shared_nodes[name]

        return self.get_catalog_node(name)