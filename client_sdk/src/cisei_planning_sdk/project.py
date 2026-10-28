"""Local project conventions; no manifest or server-side filesystem needed."""
from dataclasses import dataclass
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from cisei_planning_sdk.local_io import load_result, save_result, save_scenario


@dataclass
class PlanningProject:
    path: str | Path
    scenario_file: str = "scenario.toml"
    features_file: str = "features.json"
    result_file: str = "result.json"

    def __post_init__(self):
        self.path = Path(self.path).expanduser().resolve()
        for name in (self.scenario_file, self.features_file, self.result_file):
            if Path(name).is_absolute() or not (self.path / name).resolve().is_relative_to(self.path):
                raise ValueError("Project filenames must remain inside the project directory")
        if len({self.scenario_path, self.features_path, self.result_path}) != 3:
            raise ValueError("Scenario, feature cache and result must use different files")
        self.path.mkdir(parents=True, exist_ok=True)

    @property
    def scenario_path(self):
        return self.path / self.scenario_file

    @property
    def name(self):
        return self.path.name

    @property
    def features_path(self):
        return self.path / self.features_file

    @property
    def result_path(self):
        return self.path / self.result_file

    def load_features(self):
        return json.loads(self.features_path.read_text(encoding="utf-8"))

    def save_features(self, document):
        """Atomically replace the cache so an interrupted write preserves the old file."""
        text = json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False)
        self.features_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.features_path.parent,
                                    prefix=".features-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(text + "\n")
            temporary.replace(self.features_path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return self.features_path

    def save_result(self, result):
        return save_result(result, self.result_path)

    def load_result(self):
        return load_result(self.result_path)

    def save_scenario(self, config):
        return save_scenario(config, self.scenario_path)

    def show_network(self, *, save_path=None):
        """Display the saved solution offline, including its saved quality."""
        from cisei_planning_sdk.visualization import plot_network
        return plot_network(self.load_result(), save_path=save_path or self.path / "solution_network.html")

    def show_dependencies(self, *, save_path=None):
        """Display an interactive descendant explorer for the saved solution."""
        from IPython.display import display
        from cisei_planning_sdk.network_view import NetworkView
        view = NetworkView(self.load_result(), dependency_mode=True)
        view.save_html(save_path or self.path / "dependencies.html")
        display(view)
        return view

    def save_redundancy_selection(self, site_id):
        """Persist the engineer's manual choice, with source-result identity."""
        import hashlib
        from cisei_planning_sdk.dependencies import network_dependencies
        result_bytes = self.result_path.read_bytes()
        result = json.loads(result_bytes)
        report = network_dependencies(result)
        if site_id not in report["sites"]:
            raise ValueError(f"Unknown site: {site_id}")
        own = report["sites"][site_id]["interface_ids"]
        if len(own) != 1:
            raise ValueError("Selection currently requires one interface per site")
        planned = {row["node_id"]: row for row in result["planned_nodes"]}
        row = planned.get(own[0], {})
        if row.get("parent") is None or row.get("connected"):
            raise ValueError("Choose a served site without initial external connectivity")
        selection = {
            "selected_site": site_id, "selected_interface": own[0],
            "active_sites": sorted({site_id, *report["sites"][site_id]["descendant_sites"]}),
            "source_result_sha256": hashlib.sha256(result_bytes).hexdigest(),
        }
        path = self.path / "redundancy_selection.json"
        path.write_text(json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
