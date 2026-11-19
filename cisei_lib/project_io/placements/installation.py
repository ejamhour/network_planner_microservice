"""Apply per-interface antenna installation inputs to an expanded scenario.

The placement document is a parsed TOML dictionary of the form:
    {"placements": {"<interface_id>": {"mount_height_m": 12.0, ...}}}

The target config is an expanded scenario dictionary containing concrete
interfaces under config["sites"] and profiles under config["antennas"].
Node CSV expansion must happen before placements are applied.

placement_source() locates a file for the preserved original SDK loading path.
apply_antenna_placements() reads that file and applies its document.
The server scenario builder resolves file references itself and calls
apply_placement_document() with the parsed document.

Application updates config in place and returns None. Mounting height is
written onto the selected interface. Orientation, beamwidth, and shadow
overrides produce a copy of the selected antenna profile, stored under a
generated ID in config["antennas"]. Only the selected interface's antenna_id
is changed to reference that profile. An explicit antenna_id can select a
different existing scenario profile. Omitted height retains the interface/device
value. On reapplication to a generated placement profile, orientation, beamwidth
and shadow fields currently resolve from its original base profile; omitted
values can therefore revert. That is a legacy expanded-TOML reimport case;
loading compiled_scenario.json never calls this application function. Normal
recompilation begins from source inputs.

All entries are checked before installation changes are committed. This code
does not tune antennas or write project files. The caller serializes the
updated config into its scenario or compiled artifact.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import tomlkit

from .syntax import ANTENNA_FIELDS, placement_syntax_issues


def placement_source(scenario_path, config):
    """Return a placement file Path, or None when no file is declared or found.

    Inputs are the scenario TOML path and its parsed configuration dictionary.
    Resolve [antenna_placements].source relative to that path. When the section
    is absent, look for an adjacent antenna_placements.toml. An invalid or
    missing explicit reference raises an error. Neither input is modified.
    The server scenario builder handles the newer [files] declaration separately.
    """
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
    """Read a placement file and update an expanded config in place.

    Inputs are the expanded scenario dictionary and the original TOML path.
    Locate the placement file first. After successful lookup, remove the local
    antenna_placements declaration, then parse and apply the document. Return
    None. Lookup failure leaves config untouched. Parsing/application failure
    occurs after declaration removal, even though installation updates are
    committed only after their validation. With no file, declaration removal
    is the only possible mutation. Errors propagate to the caller.
    """
    path = placement_source(scenario_path, config)
    config.pop('antenna_placements', None)
    if path is None:
        return
    if not path.resolve().is_relative_to(Path(scenario_path).resolve().parent):
        raise ValueError('Antenna placement file must remain inside the project')
    document = tomlkit.parse(path.read_text(encoding='utf-8')).unwrap()
    apply_placement_document(config, document)


def apply_placement_document(config, document):
    """Validate parsed placements and mutate the supplied expanded config.

    document contains a placements table keyed by concrete interface ID.
    config supplies the concrete sites/interfaces and antenna profile catalog.
    Return None. Update interface mount_height_m and antenna_id as requested,
    and add effective antenna profiles for orientation or shadow overrides.
    Existing shared profiles and the placement document are left untouched.
    Validation failure leaves config unchanged.
    """
    syntax_issues = placement_syntax_issues(document)
    if syntax_issues:
        field, message = syntax_issues[0]
        error = ValueError(message)
        error.field, error.code = field, "invalid_placement"
        raise error
    interfaces = {}
    for site in config.get('sites', []):
        for interface in site.get('interfaces', []):
            iid = interface['interface_id']
            if iid in interfaces:
                raise ValueError(f'Duplicate interface id: {iid}')
            interfaces[iid] = interface
    antennas = config.get('antennas', {})
    antenna_fields = ANTENNA_FIELDS
    updates = []
    for iid, fields in document.get('placements', {}).items():
        field = f"placements.{iid}"
        try:
            if iid not in interfaces:
                raise ValueError(f'Antenna placement references unknown interface: {iid}')
            fields = deepcopy(fields)
            if 'tilt_deg' in fields:
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
        except (ValueError, TypeError, KeyError) as exc:
            exc.field = field
            exc.code = "invalid_placement"
            raise

    for interface, antenna_id, effective, fields in updates:
        if effective is not None:
            config['antennas'][antenna_id] = effective
        interface['antenna_id'] = antenna_id
        if 'mount_height_m' in fields:
            interface['mount_height_m'] = fields['mount_height_m']
