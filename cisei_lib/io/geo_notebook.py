"""Jupyter adapter for GeoServiceClient.

This module contains presentation code only.  The REST transport and domain
methods live in geo_service_client.py.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any, Callable
import inspect

from IPython.display import HTML, JSON, display
from PIL import Image

from geo_service_client import (
    APIResult,
    GeoServiceAPIError,
    GeoServiceClient,
    GeoServiceError,
    GeoServiceHTTPError,
    GeoServiceRequestError,
)


class GeoNotebook:
    """Notebook-facing adapter around GeoServiceClient.

    Domain methods are forwarded to `self.client`. By default, results are
    displayed and `None` is returned, matching the previous notebook behavior.

    To get the raw APIResult instead, call any forwarded method with:

        display_result=False
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        timeout: int = 20,
        client: GeoServiceClient | None = None,
        **client_kwargs: Any,
    ):
        self.client = client or GeoServiceClient(
            base_url=base_url,
            timeout=timeout,
            **client_kwargs,
        )

    @property
    def base_url(self) -> str:
        return self.client.base_url

    @property
    def timeout(self) -> int:
        return self.client.timeout

    @property
    def user_id(self) -> str | None:
        return self.client.user_id

    @property
    def token(self) -> str | None:
        return self.client.token

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.client, name)

        if not callable(attr):
            return attr

        def displayed_call(*args: Any, display_result: bool = True, **kwargs: Any) -> APIResult | None:
            try:
                result = attr(*args, **kwargs)
            except GeoServiceError as e:
                result = self._exception_result(e)

            if display_result:
                self._display_result(result)
                return None

            return result

        displayed_call.__name__ = name
        displayed_call.__doc__ = attr.__doc__
        return displayed_call

    def call_api(
        self,
        route: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        method: str = "get",
        timeout: int | None = None,
        display_result: bool = True,
    ) -> APIResult | None:
        """Call an arbitrary backend route from a notebook."""
        try:
            result = self.client.request(
                route,
                params=params,
                body=body,
                method=method,
                timeout=timeout,
            )
        except GeoServiceError as e:
            result = self._exception_result(e)

        if display_result:
            self._display_result(result)
            return None

        return result

    def doc(self, name: str) -> None:
        """Print the docstring for a forwarded client method."""
        method: Callable[..., Any] = getattr(self.client, name)
        print(f"\n*** {name} ***")
        print(inspect.cleandoc(method.__doc__ or ""))

    def _exception_result(self, exc: GeoServiceError) -> APIResult:
        if isinstance(exc, GeoServiceHTTPError):
            return APIResult(
                kind="error",
                data={
                    "status": "error",
                    "kind": "http_error",
                    "data": str(exc),
                },
                status="error",
            )

        if isinstance(exc, GeoServiceRequestError):
            return APIResult(
                kind="error",
                data={
                    "status": "error",
                    "kind": "request_error",
                    "data": str(exc),
                },
                status="error",
            )

        if isinstance(exc, GeoServiceAPIError):
            return APIResult(
                kind="error",
                data={
                    "status": "error",
                    "kind": "api_error",
                    "data": exc.payload,
                },
                status="error",
            )

        return APIResult(
            kind="error",
            data={
                "status": "error",
                "kind": "client_error",
                "data": str(exc),
            },
            status="error",
        )

    def _display_result(self, result: APIResult) -> None:
        if result.kind == "image_bytes":
            img = Image.open(BytesIO(result.data))
            display(img)
            return

        if result.kind == "text":
            if result.status == "error":
                display(HTML(f"❌ {result.data}"))
            elif result.data:
                display(HTML(f"✅ {result.data}"))
            return

        if result.kind == "json":
            data = result.data

            if not isinstance(data, dict):
                display(JSON(data))
                return

            status = str(data.get("status", "")).lower()
            kind = data.get("kind")
            payload = data.get("data")

            if status == "error":
                display(f"❌ Error: {payload}")
                return

            if "data" not in data:
                display(JSON(data))
                return

            if payload is None:
                return

            if isinstance(payload, (dict, list)):
                display(JSON(payload))
            else:
                display(payload)
            return

        if result.kind == "error":
            data = result.data
            if isinstance(data, dict):
                kind = data.get("kind", "error")
                payload = data.get("data", "Unknown error")
                display(f"❌ {kind}: {payload}")
            else:
                display(f"❌ {data}")
            return

        if result.kind == "bytes":
            display(f"{len(result.data)} bytes")
            return

        display(result.data)
