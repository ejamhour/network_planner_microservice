"""Local workspace creation and public example downloads."""
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import requests


def init_workspace(
    path: str | Path = "planning_workspace",
    *,
    base_url: str | None = None,
    timeout: float = 120.0,
) -> Path:
    """Create a local planning workspace and return its root.

    Supply the hub's base URL to download its example manifest and files into
    the workspace root. Existing files are preserved, including edited examples.
    Omit the URL for a completely offline, empty workspace.
    """
    root = Path(path).expanduser().resolve()
    for name in ("projects", "notebooks", "antennas", "metrics", "data"):
        (root / name).mkdir(parents=True, exist_ok=True)
    if base_url is None:
        return root

    url = base_url.rstrip("/") + "/examples"
    response = requests.get(url + "/manifest.json", timeout=timeout)
    response.raise_for_status()
    examples = root
    targets = []
    for name in response.json()["files"]:
        # Validate the full manifest before writing any downloaded files.
        if not isinstance(name, str) or not name or "\\" in name or ":" in name:
            raise ValueError(f"Invalid example path: {name!r}")
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or name == ".":
            raise ValueError(f"Invalid example path: {name!r}")
        target = examples.joinpath(*relative.parts)
        if not target.resolve().is_relative_to(examples):
            raise ValueError(f"Example path escapes workspace: {name!r}")
        targets.append((name, target))
    for name, target in targets:
        if target.exists():
            continue
        response = requests.get(url + "/" + quote(name, safe="/"), timeout=timeout)
        response.raise_for_status()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            handle.write(response.content)
    return root


def locate_workspace(path: str | Path | None = None) -> Path:
    """Locate the workspace used by an SDK client.

    An explicit path always wins. Without one, a notebook opened from the
    conventional ``notebooks`` directory uses its parent workspace. A process
    already running at a workspace root keeps that root; otherwise the current
    directory becomes a new workspace.
    """
    if path is not None:
        return Path(path).expanduser().resolve()

    current = Path.cwd().resolve()
    if current.name == "notebooks":
        return current.parent
    if any((current / name).exists() for name in ("projects", "antennas", "metrics")):
        return current
    return current
