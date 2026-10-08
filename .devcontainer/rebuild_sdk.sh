#!/bin/bash
# client_sdk/pyproject.toml
# client_sdk/src/cisei_planning_sdk/__init__.py

echo "Remember to update version"

python -m pip wheel \
  --no-deps \
  --wheel-dir dist \
  ./client_sdk

