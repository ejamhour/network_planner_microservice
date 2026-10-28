"""Local inventory of equipment served by communication devices."""
import csv
from pathlib import Path

import tomlkit

from .local_io import load_scenario_bundle


def load_attached_equipment(scenario_path):
    """Return records, or None when no attachment inventory is declared.

    The inventory is descriptive. It never changes RF topology, costs or
    diagnostic validity, and an absent row does not establish removability.
    """
    scenario_path = Path(scenario_path).resolve()
    document = tomlkit.parse(scenario_path.read_text(encoding='utf-8')).unwrap()
    settings = document.get('attachments')
    if settings is None:
        return None
    if not isinstance(settings, dict) or not settings.get('source'):
        raise ValueError('[attachments].source is required')
    if settings.get('format', 'csv') != 'csv':
        raise ValueError('Attached equipment must use CSV format')
    source = (scenario_path.parent / settings['source']).resolve()
    if not source.is_relative_to(scenario_path.parent):
        raise ValueError('Attached equipment file must remain inside the project')
    scenario = load_scenario_bundle(scenario_path)
    devices = {d['device_id'] for site in scenario.get('sites', []) for d in site.get('devices', [])}
    with source.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        fields = {'equipment_id', 'equipment_type', 'device_id'}
        if not fields.issubset(reader.fieldnames or []):
            raise ValueError('Attached equipment requires equipment_id, equipment_type and device_id columns')
        result, seen = [], set()
        for index, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f'Attached equipment row {index}: malformed CSV')
            row = {k: (v or '').strip() for k,v in row.items()}
            if any(not row[key] for key in fields):
                raise ValueError(f'Attached equipment row {index}: required value is empty')
            if row['device_id'] not in devices:
                raise ValueError(f'Unknown attachment device: {row["device_id"]}')
            key = (row['equipment_type'], row['equipment_id'])
            if key in seen:
                raise ValueError(f'Duplicate attached equipment: {key}')
            seen.add(key)
            result.append(row)
    return result
