# Canonical planning cases

These cases summarize the active distributed examples. They demonstrate valid
combinations but do not replace the scenario contract or runtime validation.

## Basic LTE cell planning

Source project: `examples/projects/example1_LTE_planning_basic`.

The scenario defines three 120-degree sector antenna profiles at azimuths 0,
120, and 240 degrees, plus an omnidirectional client antenna. Four LTE interface
profiles reference those antennas at 915 MHz. Tower device profiles contain two
or three sector interfaces; the client device profile contains one
non-relaying interface.

Connected towers have rank 0. Client sites are unconnected and cannot route.
The LTE metric refers to the built-in `lte_tower_node` specification. Site
instances come from `nodes.csv` through its `device_profile` column. The cell
planner builds primary candidates, so this example does not require a generic
`net.connectivity` rule.

## Single-radio Wi-SUN mesh

Source project: `examples/projects/graph_planner_wisun`.

The scenario defines gateway and node omnidirectional antennas and three
single-interface device profiles:

- gateway: connected, routing-capable, rank 0;
- router: unconnected, routing-capable, relay-capable interface;
- leaf: unconnected, cannot route, non-relaying interface.

All interfaces use technology `wisun` at 915 MHz. The metric refers to the
built-in `default` specification. One RF connectivity rule selects relay
sources and any destination, limits candidates to three neighbors, and limits
distance to 3000 metres. The example assigns `woodland` and `deneka` as leaves.

This case demonstrates mesh planning rather than LTE cell planning. A site is a
gateway because its device profile is connected/root-capable; a plot marker or
site name does not establish that role.

## Custom antenna library

Source: `examples/antennas/custom_omni.yaml`.

The example is a version-1 YAML document with an `antennas` list. Its one model
has:

- ID `custom_omni`;
- type `omni`;
- a human-readable model description;
- supported frequency range 902–928 MHz;
- a 915 MHz pattern with maximum gain 6 dBi.

The SDK reads the complete document and uploads it to an active scenario. A
scenario antenna profile can then select the model through `model_id`. The
upload is scenario-local and in memory.

## Custom metric

Sources: `examples/metrics/custom_metric.toml` and
`examples/metrics/gain_aware_metric.toml`.

Each file declares one version-1 metric, names `metric` as its result, and
defines the expression under `expressions.metric`. The basic demonstration uses
distance in metres. The gain-aware demonstration also reads transmitter and
receiver antenna gain. These formulas are demonstrations, not calibrated RF
models.

The SDK validates that the local file parses as TOML, then sends its text as the
scenario metric specification for one technology. Compilation and execution
occur in the network service.

## Embedded-site scenario editing

The advanced distributed notebook demonstrates a different representation:
the SDK opens an existing project, retrieves the expanded scenario from the
server, modifies the returned dictionary by adding a concrete site, and saves
that dictionary locally as TOML. An exported scenario includes embedded sites;
it does not include candidate edges, metrics, solution, or evaluation.

This pattern is useful for programmatic editing. For assistant generation, the
preferred exchange is likewise a structured scenario dictionary, followed by
validation and deterministic TOML serialization.

## What examples do not establish

Examples do not define supported technologies globally, prove that a metric is
appropriate, define business acceptance, or guarantee that every valid schema
combination can be solved. The engineering assistant must use the current
schema, catalogues, validator, and planning objectives together.

