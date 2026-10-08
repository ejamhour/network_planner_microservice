# Distributed example review

## Scope

The public collection is `examples/`, served through `/examples/manifest.json`.
Four distinct scenario bundles were retained. The old cell notebook's repeated
inspection cells and commented customization block were replaced with a
complete cell lifecycle and a separate executable customization notebook.
Historical development notebooks under `tests/jupyter` were not removed.

| Capability | Distributed notebook |
| --- | --- |
| Generic graph; single/multiple interfaces; internal routing edges | graph_planning |
| Explicit edge costs; RPL solve; quality reports | graph_planning, planning_sdk |
| Sector-aware cell candidates; client assignment; cell load | planning_sdk |
| Geo features and built-in per-technology metrics | graph_planning and planning_sdk with geo enabled |
| Custom antenna library; profile assignment; metric upload | custom_configuration |
| Gain-sensitive cost comparison; multiple models and technology metrics | custom_configuration |
| Result save/load; evaluation without restoring a live scenario | All four |
| Scenario export/reload retaining uploaded configuration | custom_configuration |
| Validation; status; listing; deletion | advanced_api |
| Sector rotation preview/application; empty-sector pruning | advanced_api |
| One-shot workflow | advanced_api |

This covers the public workflow stages, not every combination of optional
parameters. Cell tuning and one-shot execution currently use public HTTP calls
because the SDK has no wrappers for them.

## Verification

The combined workspace/configuration/notebook test suite passed: **18 tests**.
Tests execute the actual notebook code through FastAPI and SDK HTTP transport,
with registration adapted in-process and deterministic geo responses. They
exercise real planner, antenna, metric compiler, solver and reporting code.
The gain comparison changed the sample cost from about 3.5385 to 3.0526 when
both antenna gains increased by 3 dB, using the same controlled distance.

Live checks against the development hub:

- Graph notebook: passed both bundles using explicit costs.
- Cell notebook: passed using explicit costs.
- Advanced API notebook: passed, including tuning, one-shot execution and deletion.
- Custom notebook: registration, scenario definition and configuration upload
  succeeded, but geo computation failed in the external feature service:
  `prepare_profiles failed` / `Cannot allocate memory` while reading
  `OpenBuildings_S26_W50.parquet`.

Live results were written to `/tmp/planning-example-live-check/results` inside
the network-service development container. Notebook source outputs stay cleared.

## Remaining deployment check

Resolve the external geo service's Parquet allocation failure, then run:

```sh
python examples/run_examples.py --notebook all --use-geo --workspace ./acceptance_workspace
```

The custom live workflow and graph/cell geo modes are not certified by the
successful synthetic checks. No microservice deployment was performed.
