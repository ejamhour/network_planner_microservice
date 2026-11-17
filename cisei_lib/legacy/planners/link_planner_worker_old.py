from cisei_lib.cli.bhnplanner.link_planner import LinkPlanner
from queue import Empty


class LinkPlannerWorker(LinkPlanner):
    def __init__(self, *args, shared_edges, shared_nodes, queue, lock, **kwargs):
        super().__init__(*args, **kwargs)
        self.shared_edges = shared_edges
        self.shared_nodes = shared_nodes
        self.queue = queue
        self.lock = lock

    # main method of a worker - runs until a link is planned.   
    def consume_links(self, utm_nodes):
        while True:
            try:
                parent, child = self.queue.get(timeout=1)
            except Empty:
                break

            self.set_link_gdf(utm_nodes, child, parent)
            self.plan_link()

    # Override the method of PoleGraph (grandparent) to prevent recalculation of links. 
    def edge_metric(self, src_pos, dst_pos, src_ha, dst_ha):
        if self.context['metric'] == 'improve_clearance':
            key = (tuple(src_pos), tuple(dst_pos), src_ha, dst_ha)

            with self.lock:
                if key in self.shared_edges:
                    return self.shared_edges[key]

            # Compute without lock
            result = super().edge_metric(src_pos, dst_pos, src_ha, dst_ha)

            with self.lock:
                if key in self.shared_edges:
                    return self.shared_edges[key]  # another worker got there first
                self.shared_edges[key] = result
                return result

        raise Exception('evaluate_repeater: unknown metric')

    # Override the method of PoleGraph (grandparent) to update nodes in the graph. 
    def add_edge(self, src_node: dict, dst_node: dict, edge_attrs: dict):
        super().add_edge(src_node, dst_node, edge_attrs)

        edge_key = (src_node["name"], dst_node["name"])
        with self.lock:
            if edge_key not in self.shared_edges:
                self.shared_edges[edge_key] = edge_attrs.copy()

        for node in (src_node, dst_node):
            if node.get("type") == "repeater":
                self.shared_nodes[node["name"]] = node.copy()
