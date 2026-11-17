from pathlib import Path
from shutil import copyfile

import pytest

from cisei_lib.planners import PlanningScenario
from cisei_lib.project_io import build_scenario, load_compiled_scenario, read_compiled_scenario
from cisei_lib.project_io import ProjectValidationError, validate_inputs


EXAMPLE = Path(__file__).resolve().parents[1] / "examples/projects/graph_planner_wisun"


@pytest.fixture
def combined_project(tmp_path):
    reference = EXAMPLE.parents[2] / "project-contracts"
    text = (reference / "scenario.reference.toml").read_text(encoding="utf-8")
    text = text.replace('source = "scenario.reference.nodes.csv"\n', "")
    text = (
        '[files]\nnodes = "nodes.csv"\n'
        'antenna_placements = "installations.toml"\n'
        'poles = "poles.csv"\nattached_equipment = "equipment.csv"\n\n' + text
    )
    (tmp_path / "scenario.toml").write_text(text, encoding="utf-8")
    copyfile(reference / "scenario.reference.nodes.csv", tmp_path / "nodes.csv")
    (tmp_path / "installations.toml").write_text(
        '[placements."dual_radio:d0:i1"]\nmount_height_m = 12.0\nazimuth_deg = 90.0\n',
        encoding="utf-8",
    )
    (tmp_path / "poles.csv").write_text(
        "pole_id,device_profile,lat,lon,mount_height_m\npole1,dual_radio_router,-25.42,-49.32,7\n",
        encoding="utf-8",
    )
    (tmp_path / "equipment.csv").write_text(
        "equipment_id,equipment_type,device_id\nmeter1,meter,dual_radio:d0\n",
        encoding="utf-8",
    )
    return tmp_path


def test_combined_project_builds_and_reloads(combined_project):
    path = build_scenario(combined_project)
    document = read_compiled_scenario(path)
    scenario = load_compiled_scenario(path)
    assert len(scenario.site_nodes) == 4
    assert scenario.site_nodes["dual_radio"].rpl_nodes()[1].extra["mount_height_m"] == 12.0
    assert document["attachments"]["source"] == "equipment.csv"
    assert document["mesh"]["poles"]["source"] == "poles.csv"
    assert "poles" not in document


def test_independent_inventory_errors_have_structured_feedback(combined_project):
    bundle = build_scenario(combined_project)
    previous = bundle.read_bytes()
    (combined_project / "poles.csv").write_text(
        "pole_id,device_profile,lat,lon\npole1,missing,-25,-49\n", encoding="utf-8"
    )
    (combined_project / "equipment.csv").write_text(
        "equipment_id,equipment_type,device_id\nmeter1,meter,missing\n", encoding="utf-8"
    )
    with pytest.raises(ProjectValidationError) as caught:
        build_scenario(combined_project)
    feedback = caught.value.to_dict()
    assert not feedback["valid"]
    issues = {issue["file"]: issue for issue in feedback["issues"]}
    assert issues["poles.csv"]["code"] == "unknown_profile"
    assert issues["equipment.csv"]["code"] == "unknown_device"
    assert issues["equipment.csv"]["row"] == 2
    assert issues["equipment.csv"]["field"] == "device_id"
    assert bundle.read_bytes() == previous


@pytest.mark.parametrize(
    "filename, broken, expected",
    [
        ("scenario.toml", "[broken", "scenario.toml:"),
        ("nodes.csv", None, "nodes.csv:"),
        ("installations.toml", '[placements."missing"]\nazimuth_deg = 90.0\n',
         "installations.toml: Antenna placement references unknown interface: missing"),
        ("poles.csv", "pole_id,device_profile,lat,lon\npole1,missing,-25.42,-49.32\n",
         "poles.csv, row 2: Unknown device profile: missing"),
        ("equipment.csv", "equipment_id,equipment_type,device_id\nmeter1,meter,missing\n",
         "equipment.csv, row 2: Unknown attachment device: missing"),
    ],
)
def test_combined_project_failure_feedback_and_recovery(combined_project, filename, broken, expected):
    bundle = build_scenario(combined_project)
    previous = bundle.read_bytes()
    target = combined_project / filename
    original = target.read_bytes()
    if broken is None:
        target.unlink()
    else:
        target.write_text(broken, encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        build_scenario(combined_project)
    assert expected in str(caught.value)
    assert bundle.read_bytes() == previous
    target.write_bytes(original)
    assert build_scenario(combined_project).read_bytes() == previous


@pytest.mark.parametrize("use_files_table", [False, True])
def test_installed_inventory_references_valid_devices(tmp_path, use_files_table):
    source = EXAMPLE.parent / "e10_diagnostico"
    text = (source / "scenario.toml").read_text(encoding="utf-8")
    if use_files_table:
        text = '[files]\nattached_equipment = "attached_equipment.csv"\n\n' + text.replace(
            'source = "attached_equipment.csv"\n', ""
        )
    (tmp_path / "scenario.toml").write_text(text, encoding="utf-8")
    for name in ("nodes.csv", "attached_equipment.csv"):
        copyfile(source / name, tmp_path / name)
    bundle = build_scenario(tmp_path)
    previous = bundle.read_bytes()
    assert len(load_compiled_scenario(bundle).site_nodes) == 65
    assert read_compiled_scenario(bundle)["attachments"]["source"] == "attached_equipment.csv"
    (tmp_path / "attached_equipment.csv").write_text(
        "equipment_id,equipment_type,device_id\nmeter1,meter,unknown\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match=r"attached_equipment\.csv, row 2: Unknown attachment device"):
        build_scenario(tmp_path)
    assert bundle.read_bytes() == previous


@pytest.mark.parametrize("use_files_table", [False, True])
def test_staged_project_bundle_round_trip(tmp_path, use_files_table):
    source = EXAMPLE / "scenario.toml"
    text = source.read_text(encoding="utf-8")
    if use_files_table:
        text = '[files]\nnodes = "nodes.csv"\n\n' + text.replace('source = "nodes.csv"\n', "")
    (tmp_path / "scenario.toml").write_text(text, encoding="utf-8")
    copyfile(EXAMPLE / "nodes.csv", tmp_path / "nodes.csv")
    bundle = build_scenario(tmp_path)
    (tmp_path / "nodes.csv").unlink()

    restored = load_compiled_scenario(bundle).to_json_dict()
    expected = PlanningScenario.from_toml(source).to_json_dict()
    expected.pop("instances")
    assert len(restored["sites"]) == 13
    assert restored == expected


def test_missing_node_csv_does_not_publish_or_replace_bundle(tmp_path):
    copyfile(EXAMPLE / "scenario.toml", tmp_path / "scenario.toml")
    with pytest.raises(ValueError, match=r"^nodes\.csv:"):
        build_scenario(tmp_path)
    assert not (tmp_path / "compiled_scenario.json").exists()

    copyfile(EXAMPLE / "nodes.csv", tmp_path / "nodes.csv")
    bundle = build_scenario(tmp_path)
    previous = bundle.read_bytes()
    (tmp_path / "nodes.csv").unlink()
    with pytest.raises(ValueError, match=r"^nodes\.csv:"):
        build_scenario(tmp_path)
    assert bundle.read_bytes() == previous


@pytest.mark.parametrize("declaration", ["files", "legacy", "convention"])
def test_bundle_preserves_per_interface_placements(tmp_path, declaration):
    reference = EXAMPLE.parents[2] / "project-contracts"
    source = reference / "scenario.reference.toml"
    text = source.read_text(encoding="utf-8")
    name = "antenna_placements.toml" if declaration == "convention" else "installations.toml"
    if declaration == "files":
        text = f'[files]\nantenna_placements = "{name}"\n\n' + text
    elif declaration == "legacy":
        text += f'\n[antenna_placements]\nsource = "{name}"\n'
    (tmp_path / "scenario.toml").write_text(text, encoding="utf-8")
    copyfile(reference / "scenario.reference.nodes.csv", tmp_path / "scenario.reference.nodes.csv")
    placement = tmp_path / name
    placement.write_text(
        '[placements."dual_radio:d0:i0"]\nmount_height_m = 12.0\nazimuth_deg = 110.0\ndowntilt_deg = 2.0\n'
        '[placements."dual_radio:d0:i1"]\nshadow_azimuth_deg = 45.0\n',
        encoding="utf-8",
    )
    bundle = build_scenario(tmp_path)
    previous = bundle.read_bytes()
    placement.write_text('[placements."unknown"]\nmount_height_m = 9.0\n', encoding="utf-8")
    with pytest.raises(ValueError, match=rf"{name}:.*unknown interface: unknown"):
        build_scenario(tmp_path)
    assert bundle.read_bytes() == previous
    placement.unlink()

    scenario = load_compiled_scenario(bundle)
    lte, wisun = scenario.site_nodes["dual_radio"].rpl_nodes()
    assert lte.extra["mount_height_m"] == 12.0
    assert wisun.extra["mount_height_m"] == 8.0
    lte_profile = scenario.antenna_profiles[lte.extra["antenna_id"]]
    wisun_profile = scenario.antenna_profiles[wisun.extra["antenna_id"]]
    assert (lte_profile.azimuth_deg, lte_profile.downtilt_deg) == (110.0, 2.0)
    assert wisun_profile.shadow_azimuth_deg == 45.0
    assert wisun_profile.azimuth_deg is None


@pytest.mark.parametrize("use_files_table", [False, True])
def test_mesh_bundle_references_pole_inventory_without_embedding_it(tmp_path, use_files_table):
    source = EXAMPLE.parent / "example_5_Mesh_network"
    text = (source / "scenario.toml").read_text(encoding="utf-8")
    if use_files_table:
        text = '[files]\npoles = "poles.csv"\n\n' + text.replace('source = "poles.csv"\n', "")
    (tmp_path / "scenario.toml").write_text(text, encoding="utf-8")
    copyfile(source / "nodes.csv", tmp_path / "nodes.csv")
    pole_file = tmp_path / "poles.csv"
    copyfile(source / "poles.csv", pole_file)
    bundle = build_scenario(tmp_path)
    previous = bundle.read_bytes()
    pole_file.write_text(
        "pole_id,device_profile,lat,lon\ninvalid,missing,-25.4,-49.3\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"poles\.csv, row 2: Unknown device profile: missing"):
        build_scenario(tmp_path)
    assert bundle.read_bytes() == previous
    copyfile(source / "poles.csv", pole_file)

    saved = read_compiled_scenario(bundle)
    scenario = load_compiled_scenario(bundle)
    assert len(scenario.site_nodes) == 5
    assert "poles" not in saved
    assert saved["mesh"]["poles"]["source"] == "poles.csv"
    assert (tmp_path / saved["mesh"]["poles"]["source"]).read_bytes() == (source / "poles.csv").read_bytes()
    assert saved["mesh"]["network"]["final_degree"] == 3


@pytest.mark.parametrize("filename, content, row, field", [
    ("nodes.csv", "site_id,device_profile,lat,lon\nbad,missing,-25,-49\n", 2, "device_profile"),
    ("nodes.csv", "site_id,device_profile,lat,lon\nbad,dual_radio_router,invalid,-49\n", 2, "lat"),
    ("installations.toml", '[placements."dual_radio:d0:i1"]\nmount_height_m = -1\n', None,
     "placements.dual_radio:d0:i1.mount_height_m"),
])
def test_field_diagnostics_preserve_bundle_and_recover(combined_project, filename, content, row, field):
    bundle = build_scenario(combined_project)
    previous = bundle.read_bytes()
    source = combined_project / filename
    original = source.read_bytes()
    source.write_text(content, encoding="utf-8")
    with pytest.raises(ProjectValidationError) as caught:
        build_scenario(combined_project)
    issue = caught.value.to_dict()["issues"][0]
    assert (issue["file"], issue["row"], issue["field"]) == (filename, row, field)
    assert bundle.read_bytes() == previous
    source.write_bytes(original)
    assert build_scenario(combined_project).read_bytes() == previous


def test_independent_parse_failures_are_collected(combined_project):
    (combined_project / "nodes.csv").write_text("site_id,device_profile\nbad,profile,extra\n", encoding="utf-8")
    (combined_project / "installations.toml").write_text("[broken", encoding="utf-8")
    (combined_project / "poles.csv").unlink()
    with pytest.raises(ProjectValidationError) as caught:
        build_scenario(combined_project)
    issues = {issue.file: issue for issue in caught.value.issues}
    assert set(issues) == {"nodes.csv", "installations.toml", "poles.csv"}
    assert issues["nodes.csv"].row == 2
    assert issues["installations.toml"].line is not None
    assert not (combined_project / "compiled_scenario.json").exists()


def test_syntax_validation_does_not_construct_instances(combined_project, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("syntax validation must not instantiate a scenario")
    monkeypatch.setattr(PlanningScenario, "from_config_dict", forbidden)
    assert validate_inputs(combined_project) == {
        "valid": True, "stage": "input_validation", "issues": []
    }
    assert not (combined_project / "compiled_scenario.json").exists()


def test_reference_failure_belongs_to_build_stage(combined_project):
    (combined_project / "nodes.csv").write_text(
        "site_id,device_profile,lat,lon\nbad,missing,-25,-49\n", encoding="utf-8"
    )
    assert validate_inputs(combined_project)["valid"]
    with pytest.raises(ProjectValidationError) as caught:
        build_scenario(combined_project)
    assert caught.value.to_dict()["stage"] == "scenario_build"
    assert caught.value.issues[0].field == "device_profile"


def test_missing_input_stops_build_before_instantiation(combined_project, monkeypatch):
    previous = build_scenario(combined_project).read_bytes()
    (combined_project / "poles.csv").unlink()
    def forbidden(*args, **kwargs):
        raise AssertionError("failed syntax validation must stop construction")
    monkeypatch.setattr(PlanningScenario, "from_config_dict", forbidden)
    for operation in (validate_inputs, build_scenario):
        with pytest.raises(ProjectValidationError) as caught:
            operation(combined_project)
        assert caught.value.to_dict()["stage"] == "input_validation"
        assert caught.value.issues[0].code == "missing_file"
    assert (combined_project / "compiled_scenario.json").read_bytes() == previous


def test_syntax_reports_multiple_node_and_placement_fields(combined_project, monkeypatch):
    compiled = build_scenario(combined_project)
    previous = compiled.read_bytes()
    (combined_project / "nodes.csv").write_text(
        "site_id,device_profile,lat,lon,connected\n"
        "bad,dual_radio_router,invalid,-49,perhaps\n"
        "other,,95,-49,false\n", encoding="utf-8"
    )
    (combined_project / "installations.toml").write_text(
        '[placements."dual_radio:d0:i1"]\nmount_height_m = -1\nbeamwidth_deg = 361\n', encoding="utf-8"
    )
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid syntax must not instantiate a scenario")
    monkeypatch.setattr(PlanningScenario, "from_config_dict", forbidden)
    with pytest.raises(ProjectValidationError) as caught:
        validate_inputs(combined_project)
    assert caught.value.stage == "input_validation"
    found = {(issue.file, issue.row, issue.field) for issue in caught.value.issues}
    assert found == {
        ("nodes.csv", 2, "lat"), ("nodes.csv", 2, "connected"),
        ("nodes.csv", 3, "lat"), ("nodes.csv", 3, "device_profile"),
        ("installations.toml", None, "placements.dual_radio:d0:i1.mount_height_m"),
        ("installations.toml", None, "placements.dual_radio:d0:i1.beamwidth_deg"),
    }
    assert compiled.read_bytes() == previous


def test_custom_profile_column_extra_attributes_and_infinite_rank(combined_project):
    scenario_file = combined_project / "scenario.toml"
    import tomlkit
    document = tomlkit.parse(scenario_file.read_text(encoding="utf-8"))
    document.setdefault("instances", {})["profile_column"] = "profile"
    scenario_file.write_text(tomlkit.dumps(document), encoding="utf-8")
    (combined_project / "nodes.csv").write_text(
        "site_id,profile,lat,lon,rank,custom_tag\n"
        "dual_radio,dual_radio_router,-25,-49,inf,anything\n", encoding="utf-8"
    )
    assert validate_inputs(combined_project)["valid"]


def test_valid_placement_syntax_does_not_resolve_interface(combined_project):
    (combined_project / "installations.toml").write_text(
        '[placements."missing"]\nantenna_id = "unknown"\nazimuth_deg = 90\n', encoding="utf-8"
    )
    assert validate_inputs(combined_project)["valid"]
    with pytest.raises(ProjectValidationError) as caught:
        build_scenario(combined_project)
    assert caught.value.stage == "scenario_build"


def test_inventory_syntax_collects_fields_without_instantiation(combined_project, monkeypatch):
    compiled = build_scenario(combined_project)
    previous = compiled.read_bytes()
    (combined_project / "poles.csv").write_text(
        "pole_id,device_profile,lat,lon,connected\n"
        ",,bad,-49,false\n", encoding="utf-8"
    )
    (combined_project / "equipment.csv").write_text(
        "equipment_id,equipment_type,device_id,notes\n"
        ",meter,,anything\n", encoding="utf-8"
    )
    def forbidden(*args, **kwargs):
        raise AssertionError("inventory syntax does not instantiate a scenario")
    monkeypatch.setattr(PlanningScenario, "from_config_dict", forbidden)
    with pytest.raises(ProjectValidationError) as caught:
        validate_inputs(combined_project)
    found = {(i.file, i.row, i.field) for i in caught.value.issues}
    assert found == {
        ("poles.csv", 1, "connected"), ("poles.csv", 2, "pole_id"),
        ("poles.csv", 2, "device_profile"), ("poles.csv", 2, "lat"),
        ("equipment.csv", 2, "equipment_id"), ("equipment.csv", 2, "device_id"),
    }
    assert caught.value.stage == "input_validation"
    assert compiled.read_bytes() == previous


@pytest.mark.parametrize("filename,content,field", [
    ("poles.csv", "device_profile,lat,lon\nunknown,-25,-49\n", "pole_id"),
    ("equipment.csv", "equipment_id,equipment_type\na,meter\n", "device_id"),
])
def test_inventory_missing_header_is_syntax_error(combined_project, filename, content, field):
    (combined_project / filename).write_text(content, encoding="utf-8")
    with pytest.raises(ProjectValidationError) as caught:
        build_scenario(combined_project)
    assert caught.value.stage == "input_validation"
    assert any(i.code == "missing_column" and i.field == field and i.row == 1
               for i in caught.value.issues)


def test_inventory_references_are_checked_only_during_build(combined_project):
    (combined_project / "equipment.csv").write_text(
        "equipment_id,equipment_type,device_id,notes\na,meter,missing,retained\n", encoding="utf-8"
    )
    (combined_project / "poles.csv").write_text(
        "pole_id,device_profile,lat,lon\npole1,missing,-25,-49\n", encoding="utf-8"
    )
    assert validate_inputs(combined_project)["valid"]
    with pytest.raises(ProjectValidationError) as caught:
        build_scenario(combined_project)
    assert caught.value.stage == "scenario_build"
    assert {i.code for i in caught.value.issues} == {"unknown_profile", "unknown_device"}


def test_toml_field_errors_are_collected_before_construction(combined_project, monkeypatch):
    import tomlkit
    compiled = build_scenario(combined_project)
    previous = compiled.read_bytes()
    source = combined_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document["interfaces"]["lte_access"]["freq_mhz"] = "invalid"
    document["interfaces"]["lte_access"]["enabled"] = "yes"
    del document["interfaces"]["lte_terminal"]["antenna_id"]
    document["devices"]["lte_client"]["interfaces"] = "lte_terminal"
    document["net"]["candidate_edges"] = [{"src": "missing"}]
    document["overrides"] = {"interfaces": {"missing": {"enabled": 1, "gain": 3}}}
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    def forbidden(*args, **kwargs):
        raise AssertionError("TOML syntax checks must not instantiate a scenario")
    monkeypatch.setattr(PlanningScenario, "from_config_dict", forbidden)
    with pytest.raises(ProjectValidationError) as caught:
        validate_inputs(combined_project)
    assert caught.value.stage == "input_validation"
    assert {i.field for i in caught.value.issues} == {
        "interfaces.lte_access.freq_mhz", "interfaces.lte_access.enabled",
        "interfaces.lte_terminal.antenna_id", "devices.lte_client.interfaces",
        "net.candidate_edges[0].dst", "overrides.interfaces.missing.enabled",
        "overrides.interfaces.missing.gain",
    }
    assert compiled.read_bytes() == previous


@pytest.mark.parametrize("section,value", [
    ("antennas", []), ("interfaces", []), ("devices", []),
    ("net", []), ("sites", {}), ("metrics", []),
])
def test_toml_section_shape_is_a_syntax_issue(combined_project, section, value):
    import tomlkit
    source = combined_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document[section] = value
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    with pytest.raises(ProjectValidationError) as caught:
        validate_inputs(combined_project)
    assert caught.value.stage == "input_validation"
    assert any(i.field == section and i.code == "invalid_type" for i in caught.value.issues)


def test_embedded_sites_report_nested_field_paths(combined_project):
    import tomlkit
    source = combined_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document["sites"] = [{"site_id": "embedded", "devices": [{"connected": "perhaps"}],
                          "interfaces": [{"interface_id": "radio", "enabled": "true"}]}]
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    with pytest.raises(ProjectValidationError) as caught:
        validate_inputs(combined_project)
    fields = {i.field for i in caught.value.issues}
    assert "sites[0].devices[0].device_id" in fields
    assert "sites[0].devices[0].connected" in fields
    assert "sites[0].interfaces[0].device_id" in fields
    assert "sites[0].interfaces[0].enabled" in fields


def test_toml_extra_metadata_is_preserved(combined_project):
    import tomlkit
    source = combined_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document["devices"]["lte_client"]["custom_tag"] = "user metadata"
    document["interfaces"]["lte_terminal"]["freq_mhz"] = "915"
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    assert validate_inputs(combined_project)["valid"]
    assert read_compiled_scenario(build_scenario(combined_project))["devices"]["lte_client"]["custom_tag"] == "user metadata"


def test_mesh_options_use_shared_api_constraints(combined_project, monkeypatch):
    import tomlkit
    source = combined_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document.setdefault("mesh", {}).update({"max_repeaters": 21, "search_heuristic": "invalid",
                                           "network": {"parent_candidates": 0, "corridor_m": -1}})
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    def forbidden(*args, **kwargs):
        raise AssertionError("option syntax must not construct a scenario")
    monkeypatch.setattr(PlanningScenario, "from_config_dict", forbidden)
    with pytest.raises(ProjectValidationError) as caught:
        validate_inputs(combined_project)
    assert {i.field for i in caught.value.issues} == {
        "mesh.max_repeaters", "mesh.search_heuristic", "mesh.network.parent_candidates", "mesh.network.corridor_m"
    }
    from app.routes_planning import MeshNetworkBody
    from cisei_lib.planners.mesh.options import MeshNetworkOptions
    assert issubclass(MeshNetworkBody, MeshNetworkOptions)


@pytest.mark.parametrize("spec", [
    'result = "cost"\n[expressions]\ncost = "("',
    'result = "missing"\n[expressions]\ncost = "1"',
    'result = "cost"\n[expressions]\ncost = "1"\n[parameters]\nx = "invalid"',
])
def test_inline_metric_syntax_is_checked_without_compilation(combined_project, spec, monkeypatch):
    import tomlkit
    import cisei_lib.planners.shared.metric_compiler as compiler
    source = combined_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document["metrics"]["lte"]["spec"] = spec
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    def forbidden(*args, **kwargs):
        raise AssertionError("syntax pass must not compile metrics")
    monkeypatch.setattr(compiler, "compile_metric_spec", forbidden)
    with pytest.raises(ProjectValidationError) as caught:
        validate_inputs(combined_project)
    assert caught.value.stage == "input_validation"
    assert any(i.code == "invalid_metric" and i.field == "metrics.lte.spec" for i in caught.value.issues)


def test_metric_syntax_does_not_resolve_catalog_or_expression_dependencies(combined_project):
    import tomlkit
    source = combined_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document["metrics"]["lte"]["spec"] = "unknown_catalog_entry"
    document["metrics"]["wisun"]["spec"] = 'result = "cost"\n[expressions]\ncost = "undefined_name + 1"'
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    assert validate_inputs(combined_project)["valid"]


def test_connectivity_and_capacity_errors_are_input_issues(combined_project, monkeypatch):
    import tomlkit
    source = combined_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document["interfaces"]["lte_access"]["max_links"] = 0
    document["net"]["connectivity"] = [
        {"kind": "rf", "source": "invalid", "destination": "invalid", "degree": -1, "limit": -1},
        {"kind": "internal", "metric": -1},
        {"kind": "unsupported"},
    ]
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    def forbidden(*args, **kwargs):
        raise AssertionError("input checking must not instantiate a scenario")
    monkeypatch.setattr(PlanningScenario, "from_config_dict", forbidden)
    with pytest.raises(ProjectValidationError) as caught:
        validate_inputs(combined_project)
    assert caught.value.stage == "input_validation"
    assert {i.field for i in caught.value.issues} == {
        "interfaces.lte_access.max_links", "net.connectivity[0].tech",
        "net.connectivity[0].source", "net.connectivity[0].destination",
        "net.connectivity[0].degree", "net.connectivity[0].limit",
        "net.connectivity[1].metric", "net.connectivity[2].kind",
    }


@pytest.mark.parametrize("capacity", [1, 2.5, "unlimited", "infinity", float("inf")])
def test_capacity_conventions_are_shared_with_constructor(combined_project, capacity):
    import tomlkit
    from cisei_lib.planners.shared.input_values import parse_max_links
    source = combined_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document["interfaces"]["lte_access"]["max_links"] = capacity
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    assert validate_inputs(combined_project)["valid"]
    scenario = load_compiled_scenario(build_scenario(combined_project))
    assert scenario.interface_profiles["lte_access"]["max_links"] == parse_max_links(capacity)


def test_graph_execution_uses_shared_connectivity_checks():
    from cisei_lib.planners import GraphPlanner
    scenario = PlanningScenario.from_toml(EXAMPLE / "scenario.toml")
    scenario.connectivity_rules.clear()
    scenario.add_connectivity_rule({"kind": "rf", "tech": "wisun", "source": "invalid"})
    planner = GraphPlanner(scenario)
    with pytest.raises(ValueError, match="Selector must be"):
        planner.build_candidate_edges_from_rules()


def test_antenna_library_issues_do_not_construct_models(combined_project, monkeypatch):
    import tomlkit
    import cisei_lib.planners.shared.antenna_planner as antennas
    source = combined_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document["antenna_library"] = {"antennas": [
        {"id": "custom", "frequency_mhz": [915], "patterns": [
            {"frequency_mhz": "bad", "attenuation": []},
            {"frequency_mhz": 915, "max_gain_dbi": float("inf")}
        ]}
    ]}
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    def forbidden(*args, **kwargs):
        raise AssertionError("syntax checks must not construct antenna models")
    monkeypatch.setattr(antennas, "AntennaModel", forbidden)
    monkeypatch.setattr(antennas, "AntennaPattern", forbidden)
    with pytest.raises(ProjectValidationError) as caught:
        validate_inputs(combined_project)
    assert caught.value.stage == "input_validation"
    assert {i.field for i in caught.value.issues} == {
        "antenna_library.antennas[0].frequency_mhz",
        "antenna_library.antennas[0].patterns[0].frequency_mhz",
        "antenna_library.antennas[0].patterns[0].max_gain_dbi",
        "antenna_library.antennas[0].patterns[0].attenuation",
        "antenna_library.antennas[0].patterns[1].max_gain_dbi",
    }


def test_library_shape_rules_are_shared_with_loader():
    from cisei_lib.planners.shared.antenna_planner import antenna_library_issues, antenna_models_from_config
    document = {"antennas": [{"id": "custom", "patterns": "invalid"}]}
    assert antenna_library_issues(document)[0][0] == "antennas[0].patterns"
    with pytest.raises(ValueError) as caught:
        antenna_models_from_config(document)
    assert caught.value.field == "antennas[0].patterns"
    assert not antenna_library_issues({"antennas": [{"id": "metadata_only"}]})


def test_rule_flags_report_syntax_fields(combined_project):
    import tomlkit
    source = combined_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document["net"]["connectivity"][0]["sector_aware"] = "invalid"
    document["net"]["connectivity"][2]["enabled_when_device_can_route"] = "invalid"
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    with pytest.raises(ProjectValidationError) as caught:
        validate_inputs(combined_project)
    assert {i.field for i in caught.value.issues} == {
        "net.connectivity[0].sector_aware", "net.connectivity[2].enabled_when_device_can_route"
    }


def test_false_sector_flag_does_not_enable_cell_filter(monkeypatch):
    from cisei_lib.planners import GraphPlanner
    import cisei_lib.planners.cell.cell_planner as cell
    scenario = PlanningScenario.from_toml(EXAMPLE / "scenario.toml")
    scenario.connectivity_rules.clear()
    scenario.add_connectivity_rule({"kind": "rf", "tech": "wisun", "sector_aware": "false"})
    def forbidden(*args, **kwargs):
        raise AssertionError("false must not enable sector filtering")
    monkeypatch.setattr(cell, "CellPlanner", forbidden)
    assert GraphPlanner(scenario).build_candidate_edges_from_rules()


def test_profiles_only_pass_syntax_but_fail_build_with_no_interfaces(combined_project):
    import tomlkit
    compiled = build_scenario(combined_project)
    previous = compiled.read_bytes()
    source = combined_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document.pop("files")
    document.pop("instances", None)
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    assert validate_inputs(combined_project)["valid"]
    with pytest.raises(ProjectValidationError) as caught:
        build_scenario(combined_project)
    assert caught.value.stage == "scenario_build"
    assert [(i.code, i.field) for i in caught.value.issues] == [("no_interfaces", "sites")]
    assert "No interface instances were created" in caught.value.issues[0].message
    assert compiled.read_bytes() == previous


def test_empty_node_inventory_does_not_publish_compiled_scenario(combined_project):
    (combined_project / "nodes.csv").write_text("site_id,device_profile,lat,lon\n", encoding="utf-8")
    assert validate_inputs(combined_project)["valid"]
    with pytest.raises(ProjectValidationError) as caught:
        build_scenario(combined_project)
    assert caught.value.issues[0].code == "no_interfaces"
    assert not (combined_project / "compiled_scenario.json").exists()


@pytest.mark.parametrize("change,expected", [("device_without_interfaces", "device_without_interfaces"),
                                            ("unknown_device", "unknown_device")])
def test_embedded_device_interface_ownership_is_checked_at_build(combined_project, change, expected):
    import tomlkit
    document = load_compiled_scenario(build_scenario(combined_project)).to_config_dict()
    if change == "device_without_interfaces":
        document["sites"][0]["devices"].append({"device_id": "unattached"})
    else:
        document["sites"][0]["interfaces"][0]["device_id"] = "unknown"
    (combined_project / "scenario.toml").write_text(tomlkit.dumps(document), encoding="utf-8")
    assert validate_inputs(combined_project)["valid"]
    with pytest.raises(ProjectValidationError) as caught:
        build_scenario(combined_project)
    assert caught.value.stage == "scenario_build"
    assert expected in {i.code for i in caught.value.issues}


def test_isolated_device_and_disabled_interface_are_valid_build_inputs(combined_project):
    import tomlkit
    document = read_compiled_scenario(build_scenario(combined_project))
    document["sites"] = document["sites"][:1]
    document.pop("attachments", None)
    document.pop("mesh", None)
    document["net"] = {}
    document["sites"][0]["interfaces"][0]["enabled"] = False
    (combined_project / "scenario.toml").write_text(tomlkit.dumps(document), encoding="utf-8")
    assert validate_inputs(combined_project)["valid"]
    scenario = load_compiled_scenario(build_scenario(combined_project))
    assert len(scenario.site_nodes) == 1
    assert not next(iter(scenario.site_nodes.values())).interfaces[0].enabled


@pytest.mark.parametrize("field,value,code", [
    ("scenario.working_crs", "invalid_crs", "invalid_crs"),
    ("instances.format", "parquet", "unsupported_format"),
])
def test_crs_and_format_fail_at_input_stage_preserving_previous_build(combined_project, monkeypatch, field, value, code):
    import tomlkit
    compiled = build_scenario(combined_project)
    previous = compiled.read_bytes()
    source = combined_project / "scenario.toml"
    original = source.read_bytes()
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    section, name = field.split(".")
    document[section][name] = value
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid CRS/format must stop before scenario construction")
    with monkeypatch.context() as patch:
        patch.setattr(PlanningScenario, "from_config_dict", forbidden)
        for operation in (validate_inputs, build_scenario):
            with pytest.raises(ProjectValidationError) as caught:
                operation(combined_project)
            assert caught.value.stage == "input_validation"
            assert [(i.file, i.field, i.code) for i in caught.value.issues] == [("scenario.toml", field, code)]
            assert compiled.read_bytes() == previous
    source.write_bytes(original)
    assert build_scenario(combined_project).read_bytes() == previous


def test_crs_and_format_issues_are_collected_together(combined_project):
    import tomlkit
    source = combined_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document["scenario"]["working_crs"] = "invalid_crs"
    document["instances"]["format"] = "json"
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    with pytest.raises(ProjectValidationError) as caught:
        validate_inputs(combined_project)
    assert {i.field for i in caught.value.issues} == {"scenario.working_crs", "instances.format"}


@pytest.mark.parametrize("table_format", ["csv", "CSV", " Csv "])
def test_instance_format_normalization_matches_constructor(combined_project, table_format):
    import tomlkit
    source = combined_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document["instances"]["format"] = table_format
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    assert validate_inputs(combined_project)["valid"]
    assert build_scenario(combined_project).is_file()


def test_crs_definition_checks_do_not_require_instances(tmp_path, monkeypatch):
    (tmp_path / "scenario.toml").write_text('[scenario]\nworking_crs = "EPSG:31982"\n', encoding="utf-8")
    def forbidden(*args, **kwargs):
        raise AssertionError("valid CRS syntax must not construct a scenario")
    monkeypatch.setattr(PlanningScenario, "from_config_dict", forbidden)
    assert validate_inputs(tmp_path)["valid"]


@pytest.mark.parametrize("values,expected", [
    ("", 4), ("9", 9), ("0", 0),
])
def test_mounting_height_profile_row_and_placement_precedence(combined_project, values, expected):
    import tomlkit
    import csv
    source = combined_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document["devices"]["dual_radio_router"]["mount_height_m"] = 4
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    nodes = combined_project / "nodes.csv"
    with nodes.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames
        rows = list(reader)
    for row in rows:
        if row["site_id"] == "dual_radio":
            row["mount_height_m"] = values
    with nodes.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    scenario = load_compiled_scenario(build_scenario(combined_project))
    site = scenario.site_nodes["dual_radio"]
    assert site.devices[0].mount_height_m == expected
    # Per-interface placement changes one radio, without changing the device default.
    assert [n.extra["mount_height_m"] for n in site.rpl_nodes()] == [expected, 12]


def test_explicit_interface_height_takes_precedence_over_device_default(combined_project):
    import tomlkit
    config = load_compiled_scenario(build_scenario(combined_project)).to_config_dict()
    site = next(site for site in config["sites"] if site["site_id"] == "dual_radio")
    site["devices"][0]["mount_height_m"] = 4
    site["interfaces"][0]["mount_height_m"] = 9
    (combined_project / "scenario.toml").write_text(tomlkit.dumps(config), encoding="utf-8")
    restored = load_compiled_scenario(build_scenario(combined_project))
    site = restored.site_nodes["dual_radio"]
    assert site.devices[0].mount_height_m == 4
    assert [n.extra["mount_height_m"] for n in site.rpl_nodes()] == [9, 12]
