import csv
from pathlib import Path

import tomlkit
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from app.main import app
from app.main_hub import hub_app


SCHEMA = "/planning/project-schemas/v1/mesh-link"
EXAMPLE = Path(__file__).resolve().parents[1] / "examples/projects/exemple_4_Mesh_basic"


def test_mesh_link_schema_is_discoverable_and_example_validates():
    hub = TestClient(hub_app)
    catalog = hub.get("/planning/project-schemas").json()
    assert catalog["schemas"][0]["operation"] == "mesh-link"
    definition = hub.get(SCHEMA).json()
    files = definition["files"]
    assert files["scenario.toml"]["status"] == "required"
    sections = files["scenario.toml"]["sections"]
    assert sections["scenario"]["fields"]["working_crs"]["status"] == "conditional"
    assert sections["instances"]["fields"]["source"]["status"] == "required"
    assert sections["mesh.poles"]["status"] == "optional"
    assert files["poles.csv"]["status"] == "conditional"
    assert files["poles.csv"]["columns"]["pole_id"]["status"] == "required"
    assert files["nodes.csv"]["columns"]["site_id"]["status"] == "optional"
    assert files["nodes.csv"]["required_column_groups"][0]["any_of"] == [["lat", "lon"], ["x", "y"]]
    assert files["attached_equipment.csv"]["columns"]["device_id"]["status"] == "required"
    Draft202012Validator.check_schema(definition["schema"])
    Draft202012Validator(definition["schema"]).validate(definition["example"])
    client = TestClient(app)
    response = client.post(f"{SCHEMA}/validate", json=definition["example"])
    assert response.status_code == 200
    assert response.json() == {"schema_version": "v1", "operation": "mesh-link", "valid": True, "issues": []}


def test_existing_mesh_link_project_validates_and_reports_csv_row_errors():
    client = TestClient(app)
    bundle = {
        "scenario": tomlkit.parse((EXAMPLE / "scenario.toml").read_text(encoding="utf-8")).unwrap(),
        "operation": {"src_site": "copel:d0:i0", "dst_site": "guaira:d0:i0"},
        "tables": {},
    }
    for name in ("nodes.csv", "poles.csv"):
        with (EXAMPLE / name).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            bundle["tables"][name] = {"headers": reader.fieldnames, "rows": list(reader)}
    assert client.post(f"{SCHEMA}/validate", json=bundle).json()["valid"]

    bundle["tables"]["poles.csv"]["rows"][0]["device_profile"] = "unknown"
    result = client.post(f"{SCHEMA}/validate", json=bundle).json()
    assert not result["valid"]
    assert result["issues"][0]["file"] == "poles.csv"
    assert result["issues"][0]["row"] == 2
    assert result["issues"][0]["column"] == "device_profile"

    del bundle["tables"]["poles.csv"]
    result = client.post(f"{SCHEMA}/validate", json=bundle).json()
    assert not result["valid"]
    assert result["issues"][0]["message"] == "Referenced CSV table is missing."


def test_invalid_projected_crs_returns_a_validation_issue():
    client = TestClient(app)
    bundle = client.get(SCHEMA).json()["example"]
    bundle["scenario"]["scenario"]["working_crs"] = "invalid-crs"
    result = client.post(f"{SCHEMA}/validate", json=bundle)
    assert result.status_code == 200
    assert not result.json()["valid"]


def test_direct_link_and_attachment_validation():
    client = TestClient(app)
    bundle = client.get(SCHEMA).json()["example"]
    del bundle["scenario"]["mesh"]
    del bundle["tables"]["poles.csv"]
    assert client.post(f"{SCHEMA}/validate", json=bundle).json()["valid"]

    bundle["scenario"]["attachments"] = {"source": "attached_equipment.csv"}
    bundle["tables"]["attached_equipment.csv"] = {
        "headers": ["equipment_id", "equipment_type", "device_id"],
        "rows": [{"equipment_id": "meter1", "equipment_type": "meter", "device_id": "copel:d0"}],
    }
    assert client.post(f"{SCHEMA}/validate", json=bundle).json()["valid"]
    bundle["tables"]["attached_equipment.csv"]["rows"][0]["device_id"] = "unknown"
    result = client.post(f"{SCHEMA}/validate", json=bundle).json()
    assert not result["valid"]
    assert result["issues"][0]["column"] == "device_id"

    del bundle["scenario"]["attachments"]
    operation = bundle["operation"]
    operation["src_site"], operation["dst_site"] = operation["dst_site"], operation["src_site"]
    result = client.post(f"{SCHEMA}/validate", json=bundle).json()
    assert not result["valid"]
    assert "destination must be connected" in result["issues"][0]["message"]
