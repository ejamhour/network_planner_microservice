# Archived network-service code

This package contains historical code outside the active planning runtime.
Some older scripts still depend on the original CLI or data layout and are
retained as source references.

- `core/`, `dem/`, `io/minio_access.py`, and the archived resources hold
  the local RF, terrain and storage stack. The sibling features service has
  its own RF and terrain implementation, and the network service calls it
  through `cisei_lib.io`.
- `planners/` holds earlier backhaul planners and workers.
- `importer/`, `monitoring/`, `usermng/`, `tools/`, and the other
  archived I/O modules hold earlier standalone workflows and utilities.

The active mesh link engine still uses the current LinkPlanner and PoleGraph.
LinkPlanner also retains its optional persistence path, including its
serializer, home-folder helper, and serialization utilities.
