"""Local inventory of equipment served by communication devices."""
from pathlib import Path

import tomlkit

from .local_io import load_scenario_bundle

try:
    from ._equipment import read_equipment_rows
except ModuleNotFoundError as exc:
    if exc.name != f"{__package__}._equipment":
        raise
    from cisei_lib.project_io.equipment import read_equipment_rows


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
    return list(read_equipment_rows(source, devices))
