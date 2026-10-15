"""Run distributed notebook code cells against a deployed service, without Jupyter."""
import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notebook", choices=["all", "graph_planning", "example1_LTE_planning_basic", "example_1_LTE_planning_and_link_inspection", "example_2_LTE_simulation", "custom_configuration", "advanced_api"], default="all")
    parser.add_argument("--base-url", default="http://network-service:8080")
    parser.add_argument("--geo-url", default="http://planning-service:8080")
    parser.add_argument("--features-url", default="http://features:8080")
    parser.add_argument("--workspace", default="./cisei_workspace")
    args = parser.parse_args()
    workspace = Path(args.workspace).expanduser().resolve()
    directory = workspace / "notebooks"
    if not directory.is_dir():
        raise FileNotFoundError(f"Workspace notebooks directory not found: {directory}")
    names = ["graph_planning", "example1_LTE_planning_basic", "example_2_LTE_simulation", "custom_configuration", "advanced_api"] if args.notebook == "all" else [args.notebook]
    previous_directory = Path.cwd()
    previous_features_url = os.environ.get("CISEI_FEATURES_URL")
    previous_planning_url = os.environ.get("PLANNING_BASE_URL")
    previous_geo_url = os.environ.get("PLANNING_GEO_URL")
    try:
        os.environ["CISEI_FEATURES_URL"] = args.features_url
        os.environ["PLANNING_BASE_URL"] = args.base_url
        os.environ["PLANNING_GEO_URL"] = args.geo_url
        os.chdir(directory)
        for name in names:
            notebook = json.loads((directory / f"{name}.ipynb").read_text(encoding="utf-8"))
            namespace = {"__name__": "__main__"}
            print(f"Running {name}", flush=True)
            for index, cell in enumerate(notebook["cells"]):
                if cell["cell_type"] == "code":
                    source = "".join(cell["source"])
                    source = source.replace('"http://network-service:8080"', repr(args.base_url))
                    source = source.replace(
                        "scenario.compute_metrics()",
                        f"scenario.compute_metrics(geo_base_url={args.geo_url!r})",
                    )
                    exec(compile(source, f"{name}:cell-{index}", "exec"), namespace)
            print(f"PASS: {name}", flush=True)
    finally:
        os.chdir(previous_directory)
        for key, value in (("CISEI_FEATURES_URL", previous_features_url),
                           ("PLANNING_BASE_URL", previous_planning_url),
                           ("PLANNING_GEO_URL", previous_geo_url)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    main()
