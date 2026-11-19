"""Local output-file IO, independent of project compilation and planner logic."""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def write_json_artifact(path, data):
    """Stream JSON to an atomic local output file."""
    return _write_artifact(path, lambda stream: json.dump(data, stream, allow_nan=False))


def write_text_artifact(path, text):
    """Save a server-produced text file without parsing or transforming it."""
    return _write_artifact(path, lambda stream: stream.write(text))


def _write_artifact(path, writer):
    path = Path(path).expanduser().absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
