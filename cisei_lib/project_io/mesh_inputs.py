"""Prepare existing Mesh request inputs from a saved server project.

Read controls from the compiled artifact and expand its referenced pole CSV
using the shared pole reader. Poles remain separate from scenario nodes and
from the compiled JSON; this operation makes no features requests.
"""
from .compiled_scenario import read_compiled_scenario
from .diagnostics import ProjectIssue, ProjectValidationError
from .input_validation import _companion_path
from .pole_inventory import iter_pole_instances
from .project_operations import _locked_inputs
from cisei_lib.planners.mesh.options import MeshNetworkOptions


def prepare_mesh_inputs(store, identity, scenario, input_identity, overrides):
    with _locked_inputs(store, identity) as (folder, _, inputs):
        if inputs != input_identity:
            raise ProjectValidationError([ProjectIssue("scenario.toml", "active_project_outdated",
                "Close the project, rebuild and activate again.")], stage="mesh_preparation")
        config = read_compiled_scenario(folder / "compiled_scenario.json")
        if config.get("build_report", {}).get("inputs") != inputs:
            raise ProjectValidationError([ProjectIssue("compiled_scenario.json", "compiled_scenario_outdated",
                "Rebuild and activate the project before Mesh preparation.")], stage="mesh_preparation")
        mesh = dict(config.get("mesh", {}))
        poles = mesh.pop("poles", None)
        mesh.pop("pole_sites", None)
        network = mesh.pop("network", {})
        controls = {**mesh, **network, **overrides}
        if unknown := set(controls) - MeshNetworkOptions.model_fields.keys():
            raise ProjectValidationError([ProjectIssue("scenario.toml", "unknown_mesh_option",
                f"Unknown mesh network controls: {sorted(unknown)}", field="mesh")], stage="mesh_preparation")
        options = MeshNetworkOptions.model_validate(controls).model_dump()
        records = []
        if poles:
            source = _companion_path(folder, poles["source"])
            for _, node, _ in iter_pole_instances(source, poles, config, scenario):
                record = scenario._site_node_to_config(node)
                record["pole_id"] = node.site_id
                records.append(record)
        return {**options, "poles": records}
