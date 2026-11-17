# RSSI baseline recipe

`BaselineProject` reads `baseline.json`. It enriches cached RF features,
fits an RSSI model, evaluates it, and can export a planning metric. File
paths in the recipe are workspace-relative and must remain inside the
workspace. Use distinct paths for inputs and generated artifacts.

| Field | Meaning |
| --- | --- |
| `version` | Required recipe version. Use `1`. |
| `data` | Required path to the raw `features.json` input. |
| `enriched` | Optional path for the enriched cache. Defaults to `enriched_features.json` in the project. |
| `measurements` | Optional path to the measurement document. If omitted, pass measurements to `enrich_features()`. |
| `model` | Required destination path for the fitted model JSON. |
| `metric` | Required destination path for the exported metric TOML. |
| `filters` | Optional filters applied to the enriched training records before fitting. |
| `stages` | Fit stages with a name, optional additional filters, and a `fit` configuration. See the example for fixed terms, learned terms and their constraints, and the response expression. |

The workflow writes the enriched cache, fitted model, `training_result.json`,
and `evaluation.json` as the corresponding stages run. The metric TOML is
written by a separate export call. Installed-network diagnosis can also use
a baseline recipe and trained model.

See the [E08 recipe](../examples/projects/e08_baseline_copel/baseline.json),
the [cache and measurement format](features.json.md), and the
[metric definition reference](metric.toml.md).
