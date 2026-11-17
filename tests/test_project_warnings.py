"""Construction feedback distinguishes warnings from blocking failures."""
import csv
from pathlib import Path

import pytest
import tomlkit
from pyproj import Transformer

from cisei_lib.project_io import (ProjectIssue, ProjectReport, ProjectValidationError,
                                 build_scenario, load_compiled_scenario,
                                 read_compiled_scenario, validate_inputs)


@pytest.fixture
def coordinate_project(tmp_path):
    config = {
        "scenario": {"working_crs": "EPSG:31982"},
        "antennas": {"omni": {"gain_dbi": 3}},
        "interfaces": {"radio": {"tech": "generic", "freq_mhz": 915, "tx_power_dbm": 20, "antenna_id": "omni"}},
        "devices": {"device": {"interfaces": ["radio"]}},
        "instances": {"source": "nodes.csv"},
    }
    (tmp_path / "scenario.toml").write_text(tomlkit.dumps(config), encoding="utf-8")
    return tmp_path


def write_nodes(folder, *, offset=0, projected_only=False, geographic_only=False):
    lat, lon = -25.43, -49.34
    x, y = Transformer.from_crs("EPSG:4326", "EPSG:31982", always_xy=True).transform(lon, lat)
    row = {"site_id": "a", "device_profile": "device", "lat": lat, "lon": lon, "x": x + offset, "y": y}
    if projected_only:
        row.pop("lat")
        row.pop("lon")
    if geographic_only:
        row.pop("x")
        row.pop("y")
    with (folder / "nodes.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    return row


def test_warning_allows_complete_build_and_preserves_coordinate_pairs(coordinate_project):
    original = write_nodes(coordinate_project, offset=10)
    assert validate_inputs(coordinate_project)["valid"]
    report = build_scenario(coordinate_project, return_report=True)
    assert report["valid"] and report["stage"] == "scenario_build"
    issue, = report["issues"]
    assert issue["severity"] == "warning" and issue["code"] == "coordinate_mismatch"
    assert issue["details"]["site_id"] == "a"
    assert issue["details"]["distance_m"] == pytest.approx(10, abs=.1)
    assert issue["details"]["tolerance_m"] == 1
    compiled = coordinate_project / report["compiled_scenario"]
    document = read_compiled_scenario(compiled)
    assert document["build_report"] == report
    assert len(document["sites"]) == 1
    assert len(document["sites"][0]["interfaces"]) == 1
    for key in ("lat", "lon", "x", "y"):
        assert document["sites"][0][key] == original[key]
    # Loading uses saved state and never reapplies corrected/changed sources.
    write_nodes(coordinate_project)
    restored = load_compiled_scenario(compiled).site_nodes["a"].site
    assert restored.x == original["x"]
    corrected = build_scenario(coordinate_project, return_report=True)
    assert corrected["issues"] == []


@pytest.mark.parametrize("offset", [0, .5])
def test_consistent_pairs_have_no_warning(coordinate_project, offset):
    write_nodes(coordinate_project, offset=offset)
    assert build_scenario(coordinate_project, return_report=True)["issues"] == []


@pytest.mark.parametrize("mode", ["projected_only", "geographic_only"])
def test_missing_pair_is_calculated_without_mismatch_warning(coordinate_project, mode):
    original = write_nodes(coordinate_project, **{mode: True})
    path = build_scenario(coordinate_project)
    assert isinstance(path, Path)
    document = read_compiled_scenario(path)
    assert document["build_report"]["issues"] == []
    site = document["sites"][0]
    assert site["lat"] == pytest.approx(-25.43, abs=1e-7)
    assert site["lon"] == pytest.approx(-49.34, abs=1e-7)
    assert all(site[key] == original[key] for key in ("x", "y") if key in original)
    assert all(key in site for key in ("lat", "lon", "x", "y"))


def test_configurable_warning_threshold(coordinate_project):
    write_nodes(coordinate_project, offset=10)
    source = coordinate_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document["validation"] = {"coordinate_warning_tolerance_m": 20}
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    report = build_scenario(coordinate_project, return_report=True)
    assert report["valid"] and report["issues"] == []
    assert report["options"]["coordinate_warning_tolerance_m"] == 20


def test_errors_still_block_and_include_warnings_preserving_previous_output(coordinate_project):
    write_nodes(coordinate_project)
    compiled = build_scenario(coordinate_project)
    previous = compiled.read_bytes()
    write_nodes(coordinate_project, offset=10)
    source = coordinate_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document["interfaces"]["radio"]["antenna_id"] = "missing"
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    with pytest.raises(ProjectValidationError) as caught:
        build_scenario(coordinate_project, return_report=True)
    report = caught.value.to_dict()
    assert not report["valid"]
    assert {i["severity"] for i in report["issues"]} == {"error", "warning"}
    assert compiled.read_bytes() == previous


def test_pole_coordinate_warning_uses_physical_csv_row(coordinate_project):
    row = write_nodes(coordinate_project)
    source = coordinate_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document["mesh"] = {"poles": {"source": "poles.csv"}}
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    (coordinate_project / "poles.csv").write_text(
        "pole_id,device_profile,lat,lon,x,y\n"
        f"p1,device,{row['lat']},{row['lon']},{row['x'] + 10},{row['y']}\n", encoding="utf-8"
    )
    report = build_scenario(coordinate_project, return_report=True)
    issue, = report["issues"]
    assert (issue["file"], issue["row"], issue["severity"]) == ("poles.csv", 2, "warning")
    assert issue["details"]["site_id"] == "p1"
    assert "poles" not in read_compiled_scenario(coordinate_project / "compiled_scenario.json")


@pytest.mark.parametrize("value", [-1, True, float("nan"), float("inf"), "1"])
def test_invalid_warning_threshold_is_a_blocking_syntax_error(coordinate_project, value):
    write_nodes(coordinate_project)
    source = coordinate_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document["validation"] = {"coordinate_warning_tolerance_m": value}
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    with pytest.raises(ProjectValidationError) as caught:
        validate_inputs(coordinate_project)
    assert caught.value.stage == "input_validation"
    assert caught.value.issues[0].severity == "error"
    assert caught.value.issues[0].field == "validation.coordinate_warning_tolerance_m"


def test_report_validity_depends_on_errors_not_warnings():
    warning = ProjectIssue("scenario.toml", "example", "review", severity="warning")
    error = ProjectIssue("scenario.toml", "example", "fix")
    assert ProjectReport("scenario_build", (warning,)).to_dict()["valid"]
    assert not ProjectReport("scenario_build", (warning, error)).to_dict()["valid"]


def test_embedded_sites_follow_the_same_resolution_and_warning_path(coordinate_project):
    write_nodes(coordinate_project, offset=10)
    compiled = build_scenario(coordinate_project)
    config = load_compiled_scenario(compiled).to_config_dict()
    expected = dict(config["sites"][0])
    (coordinate_project / "scenario.toml").write_text(tomlkit.dumps(config), encoding="utf-8")
    report = build_scenario(coordinate_project, return_report=True)
    assert report["valid"]
    assert report["issues"][0]["code"] == "coordinate_mismatch"
    assert report["issues"][0]["file"] == "scenario.toml"
    saved = read_compiled_scenario(compiled)["sites"][0]
    assert all(saved[key] == expected[key] for key in ("lat", "lon", "x", "y"))


@pytest.mark.parametrize("mode", ["csv", "embedded"])
def test_incomplete_coordinate_pair_is_a_syntax_error(coordinate_project, mode):
    write_nodes(coordinate_project)
    source = coordinate_project / "scenario.toml"
    if mode == "csv":
        (coordinate_project / "nodes.csv").write_text(
            "site_id,device_profile,lat\na,device,-25.43\n", encoding="utf-8"
        )
    else:
        config = load_compiled_scenario(build_scenario(coordinate_project)).to_config_dict()
        for key in ("lon", "x", "y"):
            config["sites"][0].pop(key)
        source.write_text(tomlkit.dumps(config), encoding="utf-8")
    for operation in (validate_inputs, build_scenario):
        with pytest.raises(ProjectValidationError) as caught:
            operation(coordinate_project)
        assert caught.value.stage == "input_validation"
        assert caught.value.issues[0].code == "missing_coordinate_pair"


def test_projected_only_coordinates_require_declared_crs(coordinate_project):
    write_nodes(coordinate_project, projected_only=True)
    source = coordinate_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document.pop("scenario")
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    with pytest.raises(ProjectValidationError) as caught:
        validate_inputs(coordinate_project)
    assert caught.value.issues[0].code == "missing_crs"


def test_warning_only_feedback_cannot_be_raised_as_validation_error():
    warning = ProjectIssue("scenario.toml", "review", "review", severity="warning")
    with pytest.raises(ValueError, match="use ProjectReport"):
        ProjectValidationError([warning])


def test_coordinate_comparison_reports_metres_for_geographic_crs():
    from types import SimpleNamespace
    from cisei_lib.planners.shared.position_validation import coordinate_discrepancy_m
    site = SimpleNamespace(lat=-25.43, lon=-49.34, x=-49.339, y=-25.43)
    distance = coordinate_discrepancy_m(site, "EPSG:4326")
    assert 90 < distance < 120  # 0.001 longitude degrees at this latitude, in metres.
    assert site.x == -49.339 and site.lon == -49.34


def test_pole_error_row_is_not_overwritten_by_scratch_instance_ordinal(coordinate_project):
    row = write_nodes(coordinate_project)
    source = coordinate_project / "scenario.toml"
    document = tomlkit.parse(source.read_text(encoding="utf-8"))
    document["mesh"] = {"poles": {"source": "poles.csv"}}
    source.write_text(tomlkit.dumps(document), encoding="utf-8")
    (coordinate_project / "poles.csv").write_text(
        "pole_id,device_profile,lat,lon,x,y\n"
        f"p1,device,{row['lat']},{row['lon']},{row['x'] + 10},{row['y']}\n"
        f"p2,unknown,{row['lat']},{row['lon']},{row['x']},{row['y']}\n", encoding="utf-8"
    )
    with pytest.raises(ProjectValidationError) as caught:
        build_scenario(coordinate_project)
    assert not caught.value.to_dict()["valid"]
    error = next(i for i in caught.value.issues if i.severity == "error")
    warning = next(i for i in caught.value.issues if i.severity == "warning")
    assert (error.code, error.row) == ("unknown_profile", 3)
    assert (warning.code, warning.row) == ("coordinate_mismatch", 2)
