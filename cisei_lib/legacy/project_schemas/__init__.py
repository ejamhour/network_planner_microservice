"""Versioned project bundle contracts."""

import json
from importlib.resources import files


def load_document(name):
    return json.loads(files(__package__).joinpath("v1", name).read_text(encoding="utf-8"))
