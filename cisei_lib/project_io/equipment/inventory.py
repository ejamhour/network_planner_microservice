"""Read an equipment CSV and validate each row against concrete device IDs.

read_equipment_rows(path, devices) yields normalized dictionaries and preserves
extra descriptive columns. It validates the header before yielding, then checks
required values, device references, and duplicate equipment identities. Errors
include the CSV row where applicable. Callers add project-relative filenames
to their diagnostics. No project data or files are modified.
"""

import csv


class EquipmentRowError(ValueError):
    """Row diagnostic available to SDK and service callers."""

    def __init__(self, message, row, *, code="invalid_equipment", field=None):
        self.row, self.code, self.field = row, code, field
        self.detail = message
        super().__init__(f"Attached equipment row {row}: {message}")


REQUIRED_COLUMNS = frozenset({"equipment_id", "equipment_type", "device_id"})


def equipment_value_issues(row, line):
    """Validate required scalar values without resolving device references."""
    return [EquipmentRowError("required value is empty", line, code="missing_value", field=field)
            for field in sorted(REQUIRED_COLUMNS) if field in row and not row[field].strip()]


def read_equipment_rows(path, devices):
    """Yield stripped CSV rows, retaining only seen identities between rows."""
    required = REQUIRED_COLUMNS
    seen = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        headers = reader.fieldnames or []
        if len(set(headers)) != len(headers):
            raise ValueError("Duplicate attached equipment column names")
        if not required.issubset(headers):
            raise ValueError("Attached equipment requires equipment_id, equipment_type and device_id columns")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise EquipmentRowError("malformed CSV", reader.line_num)
            row = {key: value.strip() for key, value in row.items()}
            if issues := equipment_value_issues(row, reader.line_num):
                raise issues[0]
            if row["device_id"] not in devices:
                raise EquipmentRowError(
                    f"Unknown attachment device: {row['device_id']}", reader.line_num,
                    code="unknown_device", field="device_id",
                )
            key = (row["equipment_type"], row["equipment_id"])
            if key in seen:
                raise EquipmentRowError(f"Duplicate attached equipment: {key}", reader.line_num)
            seen.add(key)
            yield row
