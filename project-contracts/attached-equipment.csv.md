# Attached equipment inventory

Add an inventory to an installed-network project when you need to record
equipment served by its radios. Declare the file in `scenario.toml`.

```toml
[attachments]
source = "attached_equipment.csv"
# format = "csv"
```

The path is relative to the project and must stay within it. All three
columns below are required and their values must be nonempty.

| Column | Meaning |
| --- | --- |
| `equipment_id` | Identifier of the attached item. The combination of type and ID must be unique. |
| `equipment_type` | Category recorded in the inventory. |
| `device_id` | Concrete scenario device ID. For a node CSV row with site ID `radio_a`, the SDK generates `radio_a:d0`. |

Extra descriptive columns are retained. Multiple items can reference one
device. Independent co-located radios have separate device IDs. See the
[node CSV reference](nodes.csv.md).

Installed-network inspection and reconfiguration use this inventory to
display attached equipment and protect its devices during removal review.
The inventory has no effect on RF metrics or topology. An absent row is
insufficient evidence that a device can be removed.

See the [installed network example](../examples/projects/e10_diagnostico/attached_equipment.csv).
