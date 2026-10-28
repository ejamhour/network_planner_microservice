"""Notebook controls for inspecting an explicit list of planning links."""
from html import escape

from .link_browser import LinkBrowser


class LinkInspectionPanel:
    """A caller-owned features session with a link selector and one output area.

    Every inspection prepares the selected link again, because other notebook
    cells or panels may have changed the shared features session.
    """

    VIEWS = (
        ("Link profile", "profile"),
        ("Land cover map", "cover"),
        ("Terrain map", "terrain"),
        ("Surface map", "surface"),
        ("Land cover along the link", "lulc"),
        ("Terrain and surface profiles", "dem_profiles"),
        ("Building footprints", "buildings"),
        ("Building heights", "building_heights"),
        ("Link features", "features"),
    )

    def __init__(self, scenario, geo, edges, *, auto_load=True, initial_endpoint=None):
        import ipywidgets as widgets

        # Use the underlying client to keep intermediate responses out of cells.
        self.browser = LinkBrowser(scenario, getattr(geo, "client", geo), edges)
        self._busy = False
        self._changing = False
        self._endpoints_by_edge = [self._endpoints(edge) for edge in self.browser.edges]
        endpoint_ids = sorted({node for pair in self._endpoints_by_edge for node in pair})
        if initial_endpoint is None:
            initial_endpoint = self._endpoints_by_edge[0][0] if self.browser.edges else ""
        self.endpoint = widgets.Text(
            value=initial_endpoint, description="Endpoint:",
            placeholder="Type a site ID", continuous_update=False,
            layout=widgets.Layout(width="320px", max_width="100%"),
        )
        self.endpoint_list = widgets.Dropdown(
            options=[("All endpoints", "")] + [(node, node) for node in endpoint_ids],
            value=initial_endpoint, description="Select:",
            layout=widgets.Layout(width="320px", max_width="100%"),
        )
        self._visible_indices = [i for i, pair in enumerate(self._endpoints_by_edge)
                                 if not self.endpoint.value or self.endpoint.value in pair]
        self.count = widgets.HTML()
        self.links = widgets.Select(
            options=[(self._label(i, self.browser.edges[i]), i) for i in self._visible_indices],
            rows=min(6, max(3, len(self.browser.edges))),
            description="Links:", layout=widgets.Layout(width="760px", max_width="100%"),
        )
        self.view = widgets.Dropdown(options=self.VIEWS, description="Show:")
        self.previous = widgets.Button(description="Previous", icon="arrow-left")
        self.next = widgets.Button(description="Next", icon="arrow-right")
        self.refresh = widgets.Button(description="Show", icon="refresh")
        self.status = widgets.HTML()
        self.output = widgets.Output()
        self.widget = widgets.VBox([
            widgets.HBox([self.endpoint, self.endpoint_list],
                         layout=widgets.Layout(flex_flow="row wrap")),
            self.count, self.links, widgets.HBox([self.previous, self.next]),
            widgets.HBox([self.view, self.refresh]), self.status, self.output,
        ])
        self.endpoint.observe(self._endpoint_changed, names="value")
        self.endpoint_list.observe(self._endpoint_list_changed, names="value")
        self.links.observe(self._selection_changed, names="value")
        self.view.observe(self._selection_changed, names="value")
        self.previous.on_click(lambda _: self._move(-1))
        self.next.on_click(lambda _: self._move(1))
        self.refresh.on_click(lambda _: self._render())
        self._update_controls()
        if self.browser.edges and auto_load:
            self._render()
        elif self.browser.edges:
            self.status.value = "Select a view and press Show to inspect the selected link."
        else:
            self.status.value = "No links available for inspection."

    @staticmethod
    def _endpoints(edge):
        return tuple(str(edge.get(f"{side}_site") or edge.get(f"{side}_site_id")
                         or edge[side].split(":")[0]) for side in ("src", "dst"))

    def _endpoint_list_changed(self, change):
        if not self._changing and not self._busy:
            self.endpoint.value = change["new"]

    def _endpoint_changed(self, change):
        if self._changing or self._busy:
            return
        endpoint = self.endpoint.value.strip()
        self._changing = True
        try:
            available = {value for _, value in self.endpoint_list.options}
            self.endpoint_list.value = endpoint if endpoint in available else None
        finally:
            self._changing = False
        self._visible_indices = [i for i, pair in enumerate(self._endpoints_by_edge)
                                 if not endpoint or endpoint in pair]
        self._changing = True
        try:
            previous = self.links.value
            self.links.options = [(self._label(i, self.browser.edges[i]), i)
                                  for i in self._visible_indices]
            self.links.value = (previous if previous in self._visible_indices else
                                self._visible_indices[0] if self._visible_indices else None)
        finally:
            self._changing = False
        self.output.clear_output()
        self.status.value = ("Select a link or press Show to inspect it." if self._visible_indices
                             else "No links match this endpoint. Select a listed ID; clear the field for all links.")
        self._update_controls()

    @staticmethod
    def _label(index, edge):
        src = edge.get("src_site") or edge.get("src_site_id") or edge["src"].split(":")[0]
        dst = edge.get("dst_site") or edge.get("dst_site_id") or edge["dst"].split(":")[0]
        return f"{index + 1}. {src} → {dst}  ({edge['src']} → {edge['dst']})"

    @property
    def current(self):
        """The successfully prepared link, or None before a successful selection."""
        return self.browser.current

    def show(self):
        from IPython.display import display
        display(self.widget)

    def _update_controls(self):
        empty = not self._visible_indices
        index = self.links.value
        position = self._visible_indices.index(index) if index in self._visible_indices else -1
        self.endpoint.disabled = self.endpoint_list.disabled = self._busy or not self.browser.edges
        self.count.value = f"{len(self._visible_indices)} of {len(self.browser.edges)} links · incoming and outgoing"
        self.links.disabled = self.view.disabled = self.refresh.disabled = self._busy or empty
        self.previous.disabled = self._busy or empty or position <= 0
        self.next.disabled = self._busy or empty or position < 0 or position >= len(self._visible_indices) - 1

    def _selection_changed(self, change):
        if not self._changing and not self._busy:
            self._render()

    def _move(self, step):
        if self._busy or self.links.value is None:
            return
        position = self._visible_indices.index(self.links.value) + step
        if 0 <= position < len(self._visible_indices):
            self.links.value = self._visible_indices[position]

    def _inspect(self):
        geo = self.browser.geo
        view = self.view.value
        if view in {"cover", "terrain", "surface"}:
            return geo.link_area({"cover": "COVER", "terrain": "DTM", "surface": "DSM"}[view])
        if view in {"buildings", "building_heights"}:
            geo.bldg_prepare()
            return geo.bldg_fresnel() if view == "buildings" else geo.bldg_profile()
        return getattr(geo, {
            "profile": "link_profile", "lulc": "lulc_fresnel",
            "dem_profiles": "dem_profiles", "features": "link_features",
        }[view])()

    def _render(self):
        from IPython.display import JSON, display

        if self._busy or self.links.value is None:
            return
        self._busy = True
        self._update_controls()
        self.status.value = "Loading selected link…"
        try:
            self.output.clear_output(wait=True)
            # Keep service errors outside Output's context: in a running kernel
            # Output can capture and suppress exceptions before our handler.
            self.browser.select(self.links.value)
            result = self._inspect()
            payload = getattr(result, "payload", result)
            with self.output:
                if payload is not None:
                    display(JSON(payload) if isinstance(payload, (dict, list)) else payload)
            self.status.value = escape(self._label(self.browser.index, self.current))
        except Exception as exc:
            # Failed preparation keeps the previous position. A failed view
            # keeps the newly prepared link and can be retried with Show.
            self._changing = True
            self.links.value = self.browser.index if self.browser.index in self._visible_indices else None
            self._changing = False
            self.output.clear_output()
            self.status.value = "Could not show the link: " + escape(str(exc))
        finally:
            self._busy = False
            self._update_controls()
