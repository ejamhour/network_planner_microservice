import csv
import hashlib
from pathlib import Path

from cisei_planning_sdk.attachments import load_attached_equipment
from cisei_planning_sdk.local_io import load_scenario_bundle, load_poles_bundle
from cisei_lib.planners import PlanningScenario


def test_agu_redesign_preserves_demands_and_separates_optional_infrastructure():
    project = Path(__file__).resolve().parents[1] / 'examples/projects/e06_agu_redesign'
    config = load_scenario_bundle(project/'scenario.toml')
    scenario = PlanningScenario.from_config_dict(config)
    assert not scenario.validate()
    records = list(csv.DictReader((project/'nodes.csv').open(encoding='utf-8')))
    equipment = load_attached_equipment(project/'scenario.toml')
    assert len(records) == 33 and len(equipment) == 39
    source = list(csv.DictReader((project.parent/'e10_diagnostico/attached_equipment.csv').open(encoding='utf-8')))
    assert [{k:r[k] for k in source[0]} for r in equipment] == source
    da = {r['device_id'].removesuffix(':d0') for r in equipment if r['equipment_type']=='DA'}
    for row in records:
        fixed = row['site_id'] == 'AGU-S-P7-001'
        assert (row['connected']=='true') == fixed
        assert (row['can_route']=='true') == (row['site_id'] not in da)
        if not fixed:
            assert row['rank'] == ''
            assert any(e['device_id']==row['site_id']+':d0' for e in equipment)
        assert 'next_hop' not in row
    for e in equipment:
        expected = 1 if e['equipment_type']=='DA' else 5 + int.from_bytes(hashlib.sha256(e['equipment_id'].encode()).digest()[:4], 'big') % 16
        assert float(e['traffic_units']) == expected
    assert not config['net'].get('candidate_edges')
    poles = load_poles_bundle(project/'scenario.toml')
    assert len(poles) == 22078
    assert all(d['mount_height_m'] == 7.0 for s in poles for d in s['devices'])
    assert {s['site_id'] for s in config['sites']}.isdisjoint({s['site_id'] for s in poles})
    assert 'AGU-S-GE-058' not in {r['site_id'] for r in records}
