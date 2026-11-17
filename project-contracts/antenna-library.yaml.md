# Antenna model library

Use a YAML or JSON library to define reusable antenna models. The planning
service ships a global catalog. To add a workspace model, put the library
under `antennas/` and upload it to the open scenario. The interface's
`antenna_id` selects a scenario antenna profile. That profile's `model_id`
selects the model.

```python
session.upload_configuration(
    antenna_library="custom_omni.yaml",
    antennas={"lte_terminal": {"model_id": "custom_omni"}},
)
```

Here `lte_terminal` is an existing scenario antenna profile. Apply the
configuration before building candidates and computing metrics.

## Library fields

| Field | Meaning |
| --- | --- |
| `antennas` | Required list of model definitions. |
| `id` | Required model ID, unique in the library. |
| `vendor`, `model`, `type`, `use` | Optional model metadata. `type` defaults to `unknown`. |
| Model `frequency_mhz` | Optional two-value frequency range in MHz. |
| `patterns` | Supply at least one pattern for a model used in gain calculation. |
| Pattern `frequency_mhz`, `max_gain_dbi` | Required reference frequency and peak gain for each pattern. |
| `band_mhz` | Optional two-value frequency band. The evaluator prefers a pattern whose band contains the link frequency, then selects the closest reference frequency among eligible patterns. If no band matches, it uses the closest reference frequency overall. |
| `attenuation` | Optional table in dB, keyed first by azimuth offset and then by elevation offset, both in degrees relative to boresight. The evaluator interpolates and subtracts attenuation from peak gain. With no attenuation data it uses peak gain in all directions. |
| `provenance`, `note` | Optional pattern metadata. |

See the [workspace library example](../examples/antennas/custom_omni.yaml)
for a model with one frequency and a constant gain. For a simple scenario
you can also set `gain_dbi` directly on a scenario antenna profile.

Mount height, installed azimuth, downtilt, and pole shadowing describe the
installation. See the [per-interface placement reference](antenna-placements.toml.md).
