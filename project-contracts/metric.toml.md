# Metric definition files

A metric evaluates named expressions and returns the value of one of them.
The planning operation determines the available inputs and how the returned
value is used.

| Field | Requirement |
| --- | --- |
| `version` | Optional. Defaults to `1`. |
| `result` | Required. Names one entry in `[expressions]`. |
| `[parameters]` | Optional named finite numbers, accessed as `params.<name>`. |
| `[expressions]` | Required nonempty table. Each value is an expression string. Expressions may refer to parameters, operation inputs, and other named expressions. References must be acyclic. The compiler accepts a restricted set of operators and functions. |

```toml
version = 1
result = "cost"

[parameters]
distance_scale_m = 1000.0

[expressions]
cost = "1.0 + features.dist_m / params.distance_scale_m"
```

This is an illustrative edge metric. It returns a cost that grows with
distance. A calibrated metric can also use propagation features and radio
settings.

## Choosing the metric

| Use | Selection | Inputs |
| --- | --- | --- |
| Edge | `[metrics.<tech>]` in `scenario.toml` selects a metric for RF links of that technology. Use a service catalog name or inline definition in `spec`. To upload a workspace file, call `session.upload_configuration(metrics={"lte": "custom_lte.toml"})`. | Link features such as `features.dist_m`, `features.fspl`, and `features.delta_diffra`, plus radio inputs such as `tx.pw`, `tx.ant_gain`, and `rx.ant_gain`. Effective antenna gain is in dBi and may vary by link direction. |
| Path | `session.evaluate_paths(metric="default_path")` scores paths in the current solution using a service catalog metric. | Path segments and their metrics. The default sums the segment metrics. |
| Network | `session.network_metric(metric="default_network")` scores a solution using a service catalog metric. | Path scores and network data exposed to the expression. The default takes the mean of path scores. |

See [gain_aware_metric.toml](../examples/metrics/gain_aware_metric.toml)
for an edge metric that uses endpoint gains. The Graph solver uses edge
costs to select links and calculate accumulated ranks. Path and network
evaluation scores describe the solution under the chosen metrics.
