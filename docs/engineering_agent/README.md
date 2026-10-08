# CISEI planning engineering-agent knowledge package

Snapshot date: 2026-09-24  
Repository base commit: `2a63fd5d7dc826915a19d3d5ac9e90b9fa66d464`  
Source state: working tree contains later, uncommitted planning SDK and service changes.

This directory is the knowledge package for a shared engineering assistant that
helps the team design the assistant SDK and MCP integration. It documents the
software that exists today and keeps proposed assistant behavior visibly
separate from implemented behavior.

Upload these Markdown files to the custom agent in this order:

1. `00_AGENT_INSTRUCTIONS.md` — behavior and decision rules.
2. `01_CURRENT_ARCHITECTURE.md` — implemented services and ownership.
3. `02_WORKSPACE_AND_SDK.md` — local workspace and public SDK lifecycle.
4. `03_SCENARIO_FORMAT.md` — scenario dictionary, TOML, and CSV contract.
5. `04_SERVICE_API_AND_RESULTS.md` — FastAPI stages and result structure.
6. `05_ASSISTANT_WORKFLOW.md` — proposed iterative planning workflow.
7. `06_DESIGN_GAPS_AND_DECISIONS.md` — confirmed decisions and unresolved work.
8. `07_CANONICAL_CASES.md` — current LTE, Wi-SUN, custom antenna, and custom
   metric patterns.

The authoritative implementation sources used for this snapshot are:

- `client_sdk/src/cisei_planning_sdk/`
- `app/routes_planning.py`, `app/main.py`, `app/main_hub.py`, and `app/session.py`
- `app/feature_cache.py`
- `cisei_lib/planners/planning_scenario.py`
- `cisei_lib/planners/graph_planner.py`
- `cisei_lib/planners/planning_solution.py`
- `examples/`

Generated build folders, deleted files, `.Trash-0`, notebook checkpoints, and
legacy planner files are not authoritative.

## Status vocabulary

- **Implemented** means the behavior is present in the source listed above.
- **Decision** means the team has chosen the direction, although implementation
  may still be incomplete.
- **Proposal** means a recommended design that remains open to revision.
- **Gap** means current code does not yet meet the intended workflow.

## Version warning

`client_sdk/pyproject.toml` declares version `0.1.18`, while
`client_sdk/src/cisei_planning_sdk/__init__.py` currently reports `0.1.16`.
The built/distributed package may therefore differ from a direct source import.
Resolve this before using the version as a compatibility guarantee.
