from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cisei_planning_sdk.local_io import load_antenna_library, load_metric_spec


@dataclass
class ScenarioSession:
    """
    Server-side scenario handle returned by ``PlanningClient.define_scenario``.

    The planning worker keeps state between SDK calls. Methods without the
    ``get_`` prefix execute a planning stage and save that stage's output on
    the server-side worker. Methods with the ``get_`` prefix only reload
    already-saved state into this Python object.

    Minimal lifecycle:

    1. ``define_scenario`` stores scenario metadata and instantiated nodes.
    2. ``build_candidate_edges`` stores the candidate graph.
    3. ``compute_metrics`` or ``set_edge_metrics`` stores edge costs.
    4. ``solve`` stores the RPL solution/final planned network.
    5. ``evaluate`` reads the saved solution and returns quality reports.

    ``evaluate`` and ``export_result`` require a solution already saved by
    ``solve``. Calling ``get_solution`` before ``solve`` asks the server for a
    solution that does not exist yet and will fail.
    """

    client: Any
    name: str
    planner: str
    scenario_id: str
    summary: dict[str, Any] = field(default_factory=dict)
    validation_errors: list[str] = field(default_factory=list)
    scenario: dict[str, Any] | None = None
    candidate_edges: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    solution: dict[str, Any] | None = None
    evaluation: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    project: Any = None

    def plan_mesh_link(self, src_site: str, dst_site: str, *, geo_base_url: str,
                       pole_sites=None, poles=None, **options):
        """Plan a link through candidate pole sites and save the standard result.

        Candidate infrastructure comes from [mesh.poles].source (poles.csv),
        separate from network nodes. Profiles describe proposed equipment and
        are filtered by endpoint technology/frequency. Endpoints may be interface IDs or
        unambiguous site IDs. Optional search controls use local [mesh]. All equipment comes from its normal profiles
        and poles.csv. This creates a proposal, not an installed-network edit.
        """
        import tomlkit
        defaults = {}
        if self.project is not None:
            defaults = dict(tomlkit.parse(self.project.scenario_path.read_text(encoding='utf-8')).get('mesh', {}))
        from cisei_planning_sdk.local_io import load_poles_bundle
        if poles is None:
            poles = load_poles_bundle(self.project.scenario_path) if self.project is not None else None
        defaults.pop('poles', None)
        if pole_sites is None:
            pole_sites = defaults.pop('pole_sites', None)
        else:
            defaults.pop('pole_sites', None)
        allowed = {'max_repeaters', 'max_candidates', 'segment_threshold', 'search_heuristic', 'search_midpoint'}
        controls = {**defaults, **options}
        if set(controls) - allowed:
            raise ValueError(f'Unknown mesh controls: {sorted(set(controls) - allowed)}')
        result = self.client._request('post', f'/planning/scenarios/{self.scenario_id}/mesh/plan_link',
                    json={'src_site': src_site, 'dst_site': dst_site, 'pole_sites': list(pole_sites) if pole_sites is not None else None,
                          'poles': poles, 'geo_base_url': geo_base_url, **controls})['data']
        self.result = result
        self.solution = self.metrics = self.candidate_edges = self.evaluation = None
        if self.project is not None:
            self.project.save_result(result)
            self.save_feature_cache()
        return result

    def show_mesh_link(self, *, kind="final", save_path=None):
        """Display the last mesh proposal with the standard interactive map."""
        from cisei_planning_sdk.visualization import plot_network
        if kind not in {"final", "candidate"}:
            raise ValueError("kind must be final or candidate")
        if self.result is None or 'mesh_search' not in self.result:
            raise RuntimeError('Run plan_mesh_link first')
        if save_path is None and self.project is not None:
            search = self.result['mesh_search']
            # Site identifiers are not filesystem paths.
            from hashlib import sha256
            key = sha256((search['src_site'] + ':' + search['dst_site']).encode()).hexdigest()[:12]
            save_path = self.project.path / f'mesh_{key}{"_candidates" if kind == "candidate" else ""}.html'
        from IPython.display import HTML, display
        from html import escape
        search = self.result['mesh_search']
        if kind == 'candidate':
            candidates = self.result.get('mesh_candidates')
            if candidates is None:
                raise RuntimeError('Re-run planning with a service that exports the evaluated graph')
            display(HTML('<p><strong>Enlaces avaliados na busca</strong><br>'
                         + f"{search['evaluated_edges']} enlaces avaliados; "
                         + f"{len(search.get('eligible_candidates', []))} candidatos compatíveis; "
                         + f"{len(search.get('excluded_candidates', []))} candidatos excluídos por incompatibilidade ou configuração. "
                         + 'Laranja: caminho selecionado; cinza: outras alternativas avaliadas. '
                         + 'Clique em um enlace para consultar seu custo e se foi selecionado. '
                         + 'Um candidato sem enlaces não foi avaliado nesta busca.</p>'))
            return plot_network(candidates, kind='candidate', save_path=save_path)
        status = ('Atende ao limite por segmento' if search['meets_segment_threshold']
                  else 'Proposta com segmentos acima do limite; revisar')
        display(HTML('<p><strong>' + escape(status) + '</strong><br>'
                     + escape(' → '.join(search['node_path']))
                     + f"<br>Repetidores: {len(search['repeaters'])}; custo acumulado: {search['path_cost']:.2f}; "
                     + f"limite por segmento: {search['segment_threshold']:.2f}.</p>"))
        return plot_network(self.result, save_path=save_path)

    def show_mesh_candidates(self, *, save_path=None):
        """Inspect all evaluated mesh links, including unselected alternatives."""
        return self.show_mesh_link(kind='candidate', save_path=save_path)

    def show_network(self, *, kind="final", rank_threshold=None, primary_tech="lte", save_path=None):
        """Show a candidate graph or saved solution through the SDK visual view."""
        from cisei_planning_sdk.visualization import plot_network
        if kind not in {"candidate", "final"}:
            raise ValueError("kind must be 'candidate' or 'final'")
        # Candidate inspection is supported before solve; export_result is the
        # existing server export used by the other candidate examples.
        result = self.export_result(rank_threshold=rank_threshold, primary_tech=primary_tech)
        if save_path is None and self.project is not None:
            name = "candidate_network.html" if kind == "candidate" else "solution_network.html"
            save_path = self.project.path / name
        return plot_network(result, kind=kind, save_path=save_path)

    def link(self, geo: Any, src_interface: str, dst_interface: str) -> Any:
        """Prepare two compatible interfaces in an explicitly supplied features client.

        Reads the current server scenario. Uses device mount heights and interface
        frequency exactly as planning extraction does. Does not alter planning
        metrics or register/close the caller's features session.
        """
        from cisei_planning_sdk.link_browser import prepare_link
        return prepare_link(self, geo, src_interface, dst_interface)

    def browse_links(self, geo: Any, edges: list[dict[str, Any]]) -> Any:
        """Browse candidate_edges or planned_edges using the same interface resolver."""
        from cisei_planning_sdk.link_browser import LinkBrowser
        return LinkBrowser(self, geo, edges)

    def show_link_browser(self, geo: Any, edges: list[dict[str, Any]]) -> Any:
        """Open a notebook panel for selecting and inspecting candidate or solution links.

        Provides a link list, Previous/Next buttons and inspection choices.
        Prepares the first link and shows its profile. Does not change planning
        metrics or own/register the supplied features session. Requires the
        notebook extra. The returned panel exposes its selected link as current.
        """
        from cisei_planning_sdk.link_panel import LinkInspectionPanel
        panel = LinkInspectionPanel(self, geo, edges)
        panel.show()
        return panel

    def get_candidate_links(self) -> list[dict[str, Any]]:
        """Retrieve existing geographic candidates, excluding internal connections.

        Does not build candidates, compute metrics, or contact features.
        """
        from copy import deepcopy
        return deepcopy([edge for edge in self.get_candidate_edges()["candidate_edges"]
                         if edge.get("kind") != "internal"])

    def get_solution_links(self) -> list[dict[str, Any]]:
        """Retrieve existing geographic solution links, excluding internal connections.

        Requires a saved solution; does not solve or contact features.
        """
        from copy import deepcopy
        solution = self.get_solution()["planned_edges"]
        internal = {frozenset((edge["src"], edge["dst"]))
                    for edge in self.get_candidate_edges()["candidate_edges"]
                    if edge.get("kind") == "internal"}
        return deepcopy([edge for edge in solution if edge.get("kind") != "internal"
                         and frozenset((edge["src"], edge["dst"])) not in internal])

    def import_feature_cache(self, document: dict[str, Any]) -> dict[str, Any]:
        """Upload raw feature records, without modifying any metric or solution."""
        return self.client._request("post", f"/planning/scenarios/{self.scenario_id}/features/cache",
                                    json=document)["data"]

    def export_feature_cache(self) -> dict[str, Any]:
        """Retrieve portable raw features independently of the planning result."""
        return self.client._request("get", f"/planning/scenarios/{self.scenario_id}/features/cache")["data"]

    def save_feature_cache(self) -> Path:
        if self.project is None:
            raise ValueError("Open a project before saving its feature cache")
        return self.project.save_features(self.export_feature_cache())

    @property
    def ok(self) -> bool:
        """Return true when the server accepted the scenario without errors."""
        return not self.validation_errors

    def upload_configuration(
        self,
        *,
        antenna_library: str | Path | dict[str, Any] | None = None,
        antennas: dict[str, dict[str, Any]] | None = None,
        metrics: dict[str, str | Path | dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Upload scenario-local antenna models/profiles and metric specifications.

        ``antenna_library`` accepts a YAML/JSON file or parsed library document.
        ``antennas`` maps scenario profile ids to profile settings (including
        ``model_id``). ``metrics`` maps technologies to local TOML file paths
        or inline ``{"spec": ...}`` / ``{"formula": ...}`` dictionaries.
        Files are read locally; only their contents reach the API. On success,
        rebuild candidates, recompute metrics and solve again.
        """
        library = (load_antenna_library(self.client.antenna_path(antenna_library))
                   if isinstance(antenna_library, (str, Path)) else antenna_library)
        specs = {tech: load_metric_spec(self.client.metric_path(value))
                 if isinstance(value, (str, Path)) else value
                 for tech, value in (metrics or {}).items()}
        payload = self.client._request(
            "post", f"/planning/scenarios/{self.scenario_id}/configuration",
            json={"antenna_library": library, "antennas": antennas or {}, "metrics": specs},
        )
        data = payload["data"]
        self.scenario = data["scenario"]
        self.summary = data["summary"]
        self.validation_errors = data["validation_errors"]
        self.candidate_edges = self.metrics = self.solution = self.evaluation = self.result = None
        return data

    def refresh(self) -> "ScenarioSession":
        """
        Reload the Stage 1 scenario definition from the server.

        This only refreshes scenario metadata and instantiated nodes. It does
        not rebuild candidate edges, recompute metrics, solve, or evaluate.
        """
        payload = self.client._request(
            "get",
            f"/planning/scenarios/{self.scenario_id}",
        )
        data = payload["data"]
        self.summary = data.get("summary", {})
        self.validation_errors = data.get("validation_errors", [])
        self.scenario = data.get("scenario")
        return self

    def build_candidate_edges(
        self,
        *,
        planner: str | None = None,
        preserve_existing: bool = True,
        primary_tech: str = "lte",
        sector_aware: bool = True,
        limit_m: float | None = None,
        degree: int | None = None,
    ) -> dict[str, Any]:
        """
        Build and save candidate edges for this scenario.

        This is Stage 2. The scenario already contains instantiated sites,
        devices and interfaces. This method creates the graph offered to the
        metric and solver stages: manual candidate edges from the scenario are
        preserved by default, and automatic edges are generated by the selected
        planner.

        Server-side effect:
            Replaces the worker's current candidate graph for this scenario and
            clears any downstream metrics/solution that no longer match the
            active candidate graph.
        """
        payload = self.client._request(
            "post",
            f"/planning/scenarios/{self.scenario_id}/candidate_edges/build",
            json={
                "planner": planner or self.planner,
                "preserve_existing": preserve_existing,
                "primary_tech": primary_tech,
                "sector_aware": sector_aware,
                "limit_m": limit_m,
                "degree": degree,
            },
        )
        self.candidate_edges = payload["data"]
        return self.candidate_edges

    def get_candidate_edges(self) -> dict[str, Any]:
        """
        Reload already-built candidate edges from the server.

        This is a read-only inspection helper. It does not build edges. Use it
        after ``build_candidate_edges`` or after another client/MCP action has
        already built the candidate graph.
        """
        payload = self.client._request(
            "get",
            f"/planning/scenarios/{self.scenario_id}/candidate_edges",
        )
        self.candidate_edges = payload["data"]
        return self.candidate_edges

    def compute_metrics(
        self,
        *,
        geo_base_url: str = "http://planning-service:8080",
        geo_user_prefix: str = "planning-sdk",
        geo_pool_size: int = 1,
        geo_timeout: float = 120.0,
        include_features: bool = False,
        batch_size: int | None = None,
        on_batch=None,
    ) -> dict[str, Any]:
        """
        Compute and save real edge metrics through the external features service.

        With batch_size, each request processes a bounded subset and saves
        the project cache before the next request. on_batch receives progress
        after a successful save. Failures stop further batch submission.

        This is the normal production path after candidate edges have been
        built. The planning service delegates geo feature extraction to the
        features microservice, then collapses those features into the metric
        configured for each interface technology.

        Server-side effect:
            Stores edge metrics and feature-derived metric records on the
            worker. The reusable geo-service pool/cache owned by the worker is
            reused across calls with the same geo connection parameters.
        """
        if batch_size is not None:
            if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
                raise ValueError("batch_size must be a positive integer")
            if self.project is None:
                raise RuntimeError("Batch computation requires a project to save features")
        offset = 0
        while True:
            body = {
                "edge_metrics": None,
                "include_features": include_features,
                "geo_base_url": geo_base_url,
                "geo_user_prefix": geo_user_prefix,
                "geo_pool_size": geo_pool_size,
                "geo_timeout": geo_timeout,
            }
            if batch_size is not None:
                body.update(batch_size=batch_size, batch_offset=offset)
            payload = self.client._request(
                "post", f"/planning/scenarios/{self.scenario_id}/metrics/compute", json=body,
            )
            self.metrics = payload["data"]
            if self.project is not None:
                self.save_feature_cache()
            if batch_size is None:
                return self.metrics
            progress = self.metrics.get("batch")
            if progress is None:
                raise RuntimeError("Server does not support batches; update the network service")
            if on_batch is not None:
                on_batch(dict(progress))
            if progress["complete"]:
                return self.metrics
            if progress["next_offset"] <= offset:
                raise RuntimeError("Batch computation made no progress")
            offset = progress["next_offset"]

    def get_metrics(
        self,
        *,
        include_features: bool = False,
    ) -> dict[str, Any]:
        """
        Reload already-computed metrics from the server.

        ``include_features=True`` also returns the stored feature/metric input
        records, which can be large. This method does not call the feature
        service and does not compute missing metrics.
        """
        payload = self.client._request(
            "get",
            f"/planning/scenarios/{self.scenario_id}/metrics",
            params={"include_features": include_features},
        )
        self.metrics = payload["data"]
        return self.metrics

    def show_metrics(self, *, save_path: str | Path | None = None) -> None:
        """Browse saved link metrics in HTML and save metrics.html in the project.

        Reloads metrics without computing them. The data remains available in
        self.metrics. Requires the notebook extra.
        """
        from cisei_planning_sdk.visualization import show_metrics
        report = self.get_metrics()
        destination = save_path if save_path is not None else self.project.path / "metrics.html" if self.project else None
        options = {"save_path": destination} if destination is not None else {}
        self.metrics_view = show_metrics(report, **options)

    def solve(self) -> dict[str, Any]:
        """
        Solve and save the final planned network.

        This is Stage 6. Candidate edges and metrics must already exist. The
        server runs RPL using the saved edge metrics, stores the solution in the
        planning worker, and returns the selected planned nodes and edges.

        This is the step that saves the solution. Evaluation and result export
        read this saved solution.
        """
        payload = self.client._request(
            "post",
            f"/planning/scenarios/{self.scenario_id}/solution/solve",
        )
        self.solution = payload["data"]
        return self.solution

    def get_solution(self) -> dict[str, Any]:
        """
        Reload the saved solution from the server.

        This is read-only. It does not run RPL and it does not create a
        solution. Call ``solve`` first in the same server-side scenario.
        """
        payload = self.client._request(
            "get",
            f"/planning/scenarios/{self.scenario_id}/solution",
        )
        self.solution = payload["data"]
        return self.solution

    def set_edge_metrics(
        self,
        edge_metrics: list[dict[str, Any]],
        *,
        include_features: bool = False,
    ) -> dict[str, Any]:
        """
        Assign and save explicit edge metrics without calling the features service.

        This path is for tests, demonstrations, or external applications that
        already computed link costs. Each item must provide ``src``, ``dst`` and
        ``metric`` fields.

        Server-side effect:
            Stores edge metrics on the worker exactly like ``compute_metrics``,
            but without geo feature extraction.
        """
        payload = self.client._request(
            "post",
            f"/planning/scenarios/{self.scenario_id}/metrics/compute",
            json={
                "edge_metrics": edge_metrics,
                "include_features": include_features,
            },
        )
        self.metrics = payload["data"]
        return self.metrics

    def show_evaluation(
        self,
        *,
        rank_threshold: float,
        primary_tech: str = "lte",
        solution_kind: str | None = None,
        save_path: str | Path | None = None,
    ) -> None:
        """Evaluate the saved solution and display one HTML browsing report.

        The data remains available in self.evaluation. HTML is saved beside the
        project result by default (evaluation.html). Does not solve or save JSON;
        use save_result(rank_threshold=...) to persist the evaluated result.
        Requires the notebook extra.
        """
        from cisei_planning_sdk.visualization import show_evaluation
        report = self.evaluate(rank_threshold=rank_threshold,
                               primary_tech=primary_tech, solution_kind=solution_kind)
        destination = save_path if save_path is not None else self.project.path / "evaluation.html" if self.project else None
        options = {"save_path": destination} if destination is not None else {}
        self.evaluation_view = show_evaluation(report, **options)

    def evaluate(
        self,
        *,
        rank_threshold: float,
        primary_tech: str = "lte",
        solution_kind: str | None = None,
    ) -> dict[str, Any]:
        """
        Evaluate the already-solved network quality.

        Returns quality summary, per-node quality rows, service assignment rows
        and result edges. ``rank_threshold`` defines the maximum acceptable
        rank/cost for a planned node to be considered good. ``solution_kind``
        selects the server-side result interpreter: ``"graph"`` for generic
        reports or ``"cell"`` for cell-specific reports. When omitted, the
        session's planner type is used.

        This method does not run ``solve``. It reads the solution saved by the
        last successful ``solve`` call and stores the returned report in
        ``self.evaluation`` for notebook inspection.
        """
        payload = self.client._request(
            "get",
            f"/planning/scenarios/{self.scenario_id}/evaluation",
            params={
                "rank_threshold": rank_threshold,
                "primary_tech": primary_tech,
                "solution_kind": solution_kind or self.planner,
            },
        )
        self.evaluation = payload["data"]
        return self.evaluation

    def export_result(
        self,
        *,
        include_features: bool = False,
        rank_threshold: float | None = None,
        primary_tech: str = "lte",
        solution_kind: str | None = None,
    ) -> dict[str, Any]:
        """
        Export the serializable planning result currently saved on the server.

        Use ``include_features=True`` only when the notebook needs the expanded
        feature records. Passing ``rank_threshold`` embeds the evaluation block
        in the exported result. ``solution_kind`` selects the server-side result
        interpreter when embedding that evaluation; omitted means use the
        session's planner type.

        This is read-only: it does not compute metrics, run RPL, or create a
        solution. It exports the scenario's current server-side state.
        """
        params: dict[str, Any] = {
            "include_features": include_features,
            "primary_tech": primary_tech,
            "solution_kind": solution_kind or self.planner,
        }
        if rank_threshold is not None:
            params["rank_threshold"] = rank_threshold

        payload = self.client._request(
            "get",
            f"/planning/scenarios/{self.scenario_id}/export/result",
            params=params,
        )
        self.result = payload["data"]
        return self.result

    def save_result(
        self,
        *,
        include_features: bool = False,
        rank_threshold: float | None = None,
        primary_tech: str = "lte",
        solution_kind: str | None = None,
    ) -> Path:
        """Export the live result into the active project's ``result.json``."""
        if self.project is None:
            raise ValueError("Open a project before saving its result")
        result = self.export_result(
            include_features=include_features,
            rank_threshold=rank_threshold,
            primary_tech=primary_tech,
            solution_kind=solution_kind,
        )
        return self.project.save_result(result)

    def load_result(self) -> dict[str, Any]:
        """Load the active project's saved ``result.json``."""
        if self.project is None:
            raise ValueError("Open a project before loading its result")
        self.result = self.project.load_result()
        return self.result

    def export_scenario(self) -> dict[str, Any]:
        """
        Export the complete Stage 1 scenario definition stored by the server.

        This returns configuration and instantiated sites. It does not include
        candidate edges, metrics, solution, or evaluation reports.
        """
        payload = self.client._request(
            "get",
            f"/planning/scenarios/{self.scenario_id}/export/scenario",
        )
        self.scenario = payload["data"]
        return self.scenario
