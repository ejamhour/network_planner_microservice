from __future__ import annotations

import re
from html import escape
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse


router = APIRouter(prefix="/sdk", tags=["sdk"])

_SDK_WHEEL_RE = re.compile(
    r"^cisei_planning_sdk-(?P<version>.+?)-py3-none-any\.whl$"
)


@router.get("/latest", summary="Redirect to the newest planning SDK wheel")
def latest_sdk_wheel():
    """
    Redirect notebook installs to the newest SDK wheel available in ``dist``.

    This keeps notebook setup stable:
    ``pip install http://host/sdk/latest``.
    The actual wheel keeps its normal package/version filename under ``/dist``.
    """
    latest = max(_sdk_wheels(), key=lambda item: item[0])[1]
    return RedirectResponse(url=f"/dist/{latest.name}", status_code=307)


@router.get("/latest.whl", summary="Redirect to the newest planning SDK wheel")
def latest_sdk_wheel_file():
    """
    Wheel-shaped alias for pip.

    ``pip install URL`` classifies downloads partly from the URL filename. The
    ``/sdk/latest`` route is readable for browsers, but pip may treat it as a
    source project. This alias keeps a stable URL while ending in ``.whl``.
    """
    return latest_sdk_wheel()


@router.get(
    "/simple/{package_name}/",
    summary="Python package index for SDK wheels",
    response_class=HTMLResponse,
)
def sdk_simple_index(package_name: str):
    """
    Serve a minimal pip-compatible package index.

    Use this from notebooks:
    ``pip install --index-url http://host/sdk/simple cisei-planning-sdk``.

    Pip requires real wheel filenames, so stable aliases such as
    ``latest.whl`` are not sufficient. The index exposes the actual files
    stored under ``/dist`` while keeping the install command version-free.
    """
    normalized = package_name.replace("_", "-").lower()
    if normalized != "cisei-planning-sdk":
        raise HTTPException(status_code=404, detail="Unknown SDK package")

    wheels = _sdk_wheels()
    links = "\n".join(
        f'<a href="/dist/{escape(wheel.name)}">{escape(wheel.name)}</a><br/>'
        for _, wheel in sorted(wheels, reverse=True)
    )
    return HTMLResponse(
        f"<!doctype html><html><body>{links}</body></html>"
    )


@router.get("/simple", summary="Python package index root", response_class=HTMLResponse)
def sdk_simple_root():
    """Serve the minimal package index root."""
    return HTMLResponse(
        '<!doctype html><html><body>'
        '<a href="/sdk/simple/cisei-planning-sdk/">cisei-planning-sdk</a>'
        '</body></html>'
    )


def _version_parts(version: str) -> list[int | str]:
    """
    Parse project-controlled wheel versions for newest-wheel selection.

    The SDK currently uses simple numeric versions such as ``0.1.0``. String
    tokens are kept so future local suffixes still sort deterministically.
    """
    parts: list[int | str] = []
    for token in re.split(r"[._+-]", version):
        if token.isdigit():
            parts.append(int(token))
        else:
            parts.append(token)
    return parts


def _sdk_wheels() -> list[tuple[tuple[int | str, ...], Path]]:
    """Return available planning SDK wheels sorted by parsed version parts."""
    dist_dir = Path("dist")
    wheels = []
    for wheel in dist_dir.glob("cisei_planning_sdk-*-py3-none-any.whl"):
        match = _SDK_WHEEL_RE.match(wheel.name)
        if match:
            wheels.append((tuple(_version_parts(match.group("version"))), wheel))

    if not wheels:
        raise HTTPException(
            status_code=404,
            detail="No cisei_planning_sdk wheel found in dist",
        )
    return wheels
