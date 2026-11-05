"""Resolve optional installation overrides into portable scenario instances."""
from copy import deepcopy
from hashlib import sha256
from math import isfinite
from pathlib import Path

import tomlkit


def placement_source(scenario_path, config):
    """Explicit reference wins; otherwise use the conventional adjacent file."""
    base = Path(scenario_path).resolve().parent
    settings = config.get('antenna_placements')
    if settings is None:
        path = base / 'antenna_placements.toml'
        return path if path.is_file() else None
    if (not isinstance(settings, dict) or set(settings) - {'source'}
            or not isinstance(settings.get('source'), str) or not settings['source'].strip()):
        raise ValueError('[antenna_placements] requires only a nonempty source filename')
    path = (base / settings['source']).resolve()
    if not path.is_relative_to(base):
        raise ValueError('Antenna placement file must remain inside the project')
    if not path.is_file():
        raise FileNotFoundError(f'Antenna placement file not found: {path}')
    return path


def apply_antenna_placements(config, scenario_path):
    """Apply interface-specific installations after normal instance expansion.

    Isolated effective antenna profiles let the existing RF engine use its
    normal antenna catalog, without changing a shared profile or API schema.
    """
    path = placement_source(scenario_path, config)
    config.pop('antenna_placements', None)
    if path is None:
        return
    if not path.resolve().is_relative_to(Path(scenario_path).resolve().parent):
        raise ValueError('Antenna placement file must remain inside the project')
    document = tomlkit.parse(path.read_text(encoding='utf-8')).unwrap()
    if set(document) - {'placements'} or not isinstance(document.get('placements', {}), dict):
        raise ValueError('Antenna placement file accepts only a placements table')
    interfaces = {}
    for site in config.get('sites', []):
        for interface in site.get('interfaces', []):
            iid = interface['interface_id']
            if iid in interfaces:
                raise ValueError(f'Duplicate interface id: {iid}')
            interfaces[iid] = interface
    antennas = config.get('antennas', {})
    antenna_fields = {'azimuth_deg', 'downtilt_deg', 'beamwidth_deg',
                      'shadow_azimuth_deg', 'shadow_width_deg', 'shadow_loss_db'}
    updates = []
    for iid, fields in document.get('placements', {}).items():
        if iid not in interfaces:
            raise ValueError(f'Antenna placement references unknown interface: {iid}')
        if not isinstance(fields, dict) or set(fields) - (antenna_fields | {'antenna_id', 'mount_height_m', 'tilt_deg'}):
            raise ValueError(f'Antenna placement {iid}: unsupported placement fields')
        fields = deepcopy(fields)
        if 'tilt_deg' in fields:
            if 'downtilt_deg' in fields:
                raise ValueError(f'Antenna placement {iid}: use downtilt_deg or tilt_deg, not both')
            fields['downtilt_deg'] = fields.pop('tilt_deg')
        interface = interfaces[iid]
        antenna_id = fields.get('antenna_id', interface['antenna_id'])
        # Canonical exports can be reopened beside the same extension file.
        # Resolve through our effective entry rather than layering it again.
        inherited = antennas.get(antenna_id, {}) if isinstance(antenna_id, str) else {}
        if ('antenna_id' not in fields and inherited.get('placement_interface_id') == iid
                and inherited.get('placement_base_antenna_id') in antennas):
            antenna_id = inherited['placement_base_antenna_id']
        if not isinstance(antenna_id, str) or antenna_id not in antennas:
            raise ValueError(f'Antenna placement {iid}: unknown antenna_id {antenna_id!r}')
        for key, value in fields.items():
            if key == 'antenna_id':
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                raise ValueError(f'Antenna placement {iid}: {key} must be finite numeric data')
            if key in {'mount_height_m', 'shadow_loss_db'} and value < 0:
                raise ValueError(f'Antenna placement {iid}: {key} must be nonnegative')
            if key in {'beamwidth_deg', 'shadow_width_deg'} and not 0 < value <= 360:
                raise ValueError(f'Antenna placement {iid}: {key} must be in (0, 360]')
        changes = {key: fields[key] for key in antenna_fields if key in fields}
        effective = None
        if changes:
            effective = deepcopy(antennas[antenna_id])
            effective.update(changes)
            effective['placement_interface_id'] = iid
            effective['placement_base_antenna_id'] = antenna_id
            derived_id = '__placement_' + sha256(iid.encode()).hexdigest()
            if (derived_id in antennas and antennas[derived_id] != effective
                    and antennas[derived_id].get('placement_interface_id') != iid):
                raise ValueError(f'Antenna placement {iid}: effective antenna ID collision')
            antenna_id = derived_id
        updates.append((interface, antenna_id, effective, fields))
    for interface, antenna_id, effective, fields in updates:
        if effective is not None:
            config['antennas'][antenna_id] = effective
        interface['antenna_id'] = antenna_id
        if 'mount_height_m' in fields:
            interface['mount_height_m'] = fields['mount_height_m']
