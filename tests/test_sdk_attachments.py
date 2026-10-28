import csv
import shutil
from pathlib import Path

import pytest

from cisei_planning_sdk import InstalledNetworkProject
from cisei_planning_sdk.attachments import load_attached_equipment
from cisei_planning_sdk.installed_diagnosis import configuration_hash

SOURCE = Path(__file__).resolve().parents[1] / 'examples/projects/e09_diagnostico_bro'


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'bro'; shutil.copytree(SOURCE, root)
    p = InstalledNetworkProject(root); p.import_topology()
    return p


def inventory(project, records):
    with (project.path / 'attached_equipment.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=['equipment_id','equipment_type','device_id'])
        writer.writeheader(); writer.writerows(records)
    with project.scenario_path.open('a',encoding='utf-8') as f:
        f.write('\n[attachments]\nsource = "attached_equipment.csv"\nformat = "csv"\n')


def test_multiple_equipment_on_radio_and_rf_state_unchanged(project):
    before=project.result_path.read_bytes(); fingerprint=configuration_hash(project)
    rows=[{'equipment_id':'001','equipment_type':'DA','device_id':'BRO-S-GE-003:d0'},
          {'equipment_id':'A-001','equipment_type':'AMI','device_id':'BRO-S-GE-003:d0'},
          {'equipment_id':'002','equipment_type':'DA','device_id':'BRO-S-GE-003:d0'}]
    inventory(project,rows)
    assert project.attached_equipment()==rows
    assert configuration_hash(project)==fingerprint
    for layout in ('logical','geographic'):
        view=project.show_topology(layout=layout)
        assert view.result['attached_equipment']==rows
        assert 'equipment-legend' in view.to_html() and '#7c3aed' in view.to_html()
    assert project.result_path.read_bytes()==before
    assert 'attachments' not in project.export_scenario()


def test_unconfigured_inventory_does_not_assert_no_equipment(project):
    assert load_attached_equipment(project.scenario_path) is None
    assert project.attached_equipment()==[]
    assert 'attached_equipment' not in project.show_topology().result


@pytest.mark.parametrize('records, message',[
    ([{'equipment_id':'x','equipment_type':'DA','device_id':'missing'}],'Unknown attachment device'),
    ([{'equipment_id':'','equipment_type':'DA','device_id':'BRO-S-GE-003:d0'}],'required value is empty'),
    ([{'equipment_id':'x','equipment_type':'DA','device_id':'BRO-S-GE-003:d0'}]*2,'Duplicate attached equipment'),
])
def test_invalid_inventory_fails_without_touching_result(project,records,message):
    before=project.result_path.read_bytes(); inventory(project,records)
    with pytest.raises(ValueError,match=message):project.show_topology()
    assert project.result_path.read_bytes()==before


def test_attachment_source_stays_in_project(project):
    with project.scenario_path.open('a',encoding='utf-8') as f:
        f.write('\n[attachments]\nsource = "../outside.csv"\n')
    with pytest.raises(ValueError,match='inside the project'):project.attached_equipment()
