"""Install network and features SDKs and download planning workspace examples."""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from urllib.request import urlopen


def refresh_notebooks(workspace: Path, base_url: str) -> None:
    """Replace distributed notebooks, backing up existing copies first."""
    with urlopen(base_url + "/examples/manifest.json", timeout=120) as response:
        names = json.load(response)["files"]
    backup = workspace / "notebook_backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    for name in names:
        # Only direct notebook files; never refresh projects, results, or config.
        if not isinstance(name, str) or "\\" in name or ":" in name:
            continue
        parts = name.split("/")
        if len(parts) != 2 or parts[0] != "notebooks" or not parts[1].endswith(".ipynb") or parts[1].startswith("."):
            continue
        target = workspace / name
        if not target.resolve().is_relative_to(workspace.resolve()):
            raise ValueError("Notebook path escapes workspace")
        from urllib.parse import quote
        with urlopen(base_url + "/examples/" + quote(name, safe="/"), timeout=120) as response:
            content = response.read()
        json.loads(content)  # Validate before replacing a local notebook.
        if target.exists():
            backup.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup / parts[1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    if backup.exists():
        print("Previous notebook copies saved in:", backup)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://network-service:8080")
    parser.add_argument("--features-base-url", default="http://features:8080")
    parser.add_argument("--workspace", default="./cisei_workspace")
    parser.add_argument("--refresh-notebooks", action="store_true",
                        help="Replace example notebooks, backing up local copies; preserve projects/results")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    address = urlsplit(base_url)
    if address.scheme not in {"http", "https"} or not address.hostname:
        parser.error("--base-url must be an HTTP or HTTPS service URL")
    features_url = args.features_base_url.rstrip("/")
    features_address = urlsplit(features_url)
    if features_address.scheme not in {"http", "https"} or not features_address.hostname:
        parser.error("--features-base-url must be an HTTP or HTTPS service URL")
    workspace = Path(args.workspace).expanduser().resolve()
    print(f"Python: {sys.executable}\nWorkspace: {workspace}", flush=True)
    pip = [sys.executable, "-m", "pip"]

    # Select the SDK only from the service; install its dependencies using the
    # user's normal pip index (PyPI by default). The service hosts no dependencies.
    with TemporaryDirectory(prefix="cisei-sdk-install-") as temporary:
        for service_url, service_address, requirement in (
            (base_url, address, "cisei-planning-sdk>=0.1.60"),
            (features_url, features_address, "cisei-features-sdk>=0.1.1,<0.2.0"),
        ):
            command = pip + [
                "download", "--no-cache-dir", "--no-deps", "--only-binary=:all:",
                "--index-url", f"{service_url}/sdk/simple", "--dest", temporary,
            ]
            if service_address.scheme == "http":
                command += ["--trusted-host", service_address.hostname]
            subprocess.run(command + [requirement], check=True)
        wheels = list(Path(temporary).glob("cisei_planning_sdk-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("Expected exactly one planning SDK wheel")
        features_wheels = list(Path(temporary).glob("cisei_features_sdk-*.whl"))
        if len(features_wheels) != 1:
            raise RuntimeError("Expected exactly one features SDK wheel")
        subprocess.run(pip + [
            "install", "--upgrade", "--no-cache-dir", "--root-user-action=ignore",
            str(wheels[0]) + "[notebook]",
            str(features_wheels[0]) + "[notebook]",
        ], check=True)

    if args.refresh_notebooks:
        refresh_notebooks(workspace, base_url)

    # A fresh process imports the newly installed SDK, even after an upgrade.
    subprocess.run([
        sys.executable, "-c",
        "import sys; from cisei_planning_sdk import init_workspace, __version__; "
        "root = init_workspace(sys.argv[1], base_url=sys.argv[2]); "
        "print('SDK:', __version__); print('Workspace ready:', root); "
        "print('Notebooks:', root / 'notebooks')",
        str(workspace), base_url,
    ], check=True)


if __name__ == "__main__":
    main()
