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
