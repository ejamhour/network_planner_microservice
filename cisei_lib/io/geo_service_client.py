import base64
import secrets
from io import BytesIO
from dataclasses import dataclass

import requests
from PIL import Image


@dataclass
class APIResult:
    kind: str
    data: object
    response: requests.Response | None = None

    def __repr__(self):
        return f"APIResult(kind={self.kind!r})"


class GeoServiceClient:
    def __init__(self, base_url=None, timeout=20):
        if base_url is None:
            base_url = "http://planning-service:8080"
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.user_id = None
        self.token = None



    def request(
        self,
        route,
        params=None,
        body=None,
        method="get",
        timeout=None,
    ):
        if self.user_id:
            url = f"{self.base_url}/{self.user_id}/{route.lstrip('/')}"
            headers = {"X-Token": self.token}
        else:
            url = f"{self.base_url}/{route.lstrip('/')}"
            headers = {}
        
        # print(url)

        timeout = timeout or self.timeout
        method = method.lower()

        try:
            if method == "post":
                response = requests.post(
                    url,
                    params=params,
                    headers=headers,
                    json=body,
                    timeout=timeout,
                )
            else:
                response = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=timeout,
                )

            response.raise_for_status()
            return self._parse_response(response)

        except requests.HTTPError as e:
            response = getattr(e, "response", None)
            return APIResult(
                "http_error",
                {"status": "error", "kind": "http_error", "data": str(e)},
                response,
            )

        except requests.RequestException as e:
            return APIResult(
                "request_error",
                {"status": "error", "kind": "request_error", "data": str(e)},
                None,
            )

        except Exception as e:
            return APIResult(
                "error",
                {"status": "error", "kind": "client_error", "data": str(e)},
                None,
            )

    def _parse_response(self, response):
        ctype = (response.headers.get("content-type") or "").lower()

        if "application/json" in ctype:
            return self._parse_json_payload(response.json(), response)

        if ctype.startswith("image/"):
            image = Image.open(BytesIO(response.content))
            return APIResult("image", image, response)

        if "text/" in ctype:
            return APIResult("text", response.text, response)

        return APIResult("bytes", response.content, response)

    def _parse_json_payload(self, data, response):
        if not isinstance(data, dict):
            return APIResult("json", data, response)

        status = str(data.get("status", "")).lower()
        kind = data.get("kind")
        payload = data.get("data")

        if kind == "image" and status == "ok":
            image = Image.open(BytesIO(base64.b64decode(payload)))
            return APIResult("image", image, response)

        if kind == "text":
            return APIResult(
                "text",
                {"status": status, "data": payload},
                response,
            )

        return APIResult("json", data, response)

    def register(self, user_id=None):
        if user_id is None:
            user_id = secrets.token_urlsafe(16)

        result = self.request(
            "register",
            params={"user_id": user_id},
            method="post",
        )

        print(result)

        self.user_id = result.data["user_id"]
        self.token = result.data["token"]

        return result

    def workers_status(self) -> APIResult:
        """Return hub worker and memory status."""
        try:
            response = requests.get(
                f"{self.base_url}/status",
                timeout=self.timeout,
            )

            response.raise_for_status()
            return self._parse_response(response)

        except requests.HTTPError as exc:
            response = getattr(exc, "response", None)
            return APIResult(
                "http_error",
                {
                    "status": "error",
                    "kind": "http_error",
                    "data": str(exc),
                },
                response,
            )

        except requests.RequestException as exc:
            return APIResult(
                "request_error",
                {
                    "status": "error",
                    "kind": "request_error",
                    "data": str(exc),
                },
                None,
            )

    def close_worker(self) -> APIResult:
        """Stop this client's worker while keeping its registration."""
        if not self.user_id or not self.token:
            return APIResult(
                "error",
                {
                    "status": "error",
                    "kind": "client_error",
                    "data": "Client is not registered",
                },
                None,
            )

        try:
            response = requests.delete(
                f"{self.base_url}/workers/{self.user_id}",
                headers={"X-Token": self.token},
                timeout=self.timeout,
            )

            response.raise_for_status()
            return self._parse_response(response)

        except requests.HTTPError as exc:
            response = getattr(exc, "response", None)
            return APIResult(
                "http_error",
                {
                    "status": "error",
                    "kind": "http_error",
                    "data": str(exc),
                },
                response,
            )

        except requests.RequestException as exc:
            return APIResult(
                "request_error",
                {
                    "status": "error",
                    "kind": "request_error",
                    "data": str(exc),
                },
                None,
            )
    
    # --- service encapsulation methods

    def link(self, tx, rx, prepare_link=True, **kwargs):
        tx_lat, tx_lon = tx
        rx_lat, rx_lon = rx

        params = {
            "tx_lat": tx_lat,
            "tx_lon": tx_lon,
            "rx_lat": rx_lat,
            "rx_lon": rx_lon,
            **kwargs,
        }

        if prepare_link:
            self.request(
                "set_link",
                params=params,
                method="post",
            )
            return self.request(
                "prepare_profiles",
                method="post",
            )

        return self.request(
            "set_link",
            params=params,
            method="post",
        )

    def link_area(self, ds_string):
        return self.request(
            "link_area",
            params={"ds_string": ds_string},
            method="get",
        )

    def link_profile(self, v_h=0, **kwargs):
        return self.request(
            "link_profile",
            params={"v_h": v_h},
            body={"options": kwargs},
            method="post",
        )

    def lulc_fresnel(self, **kwargs):
        return self.request(
            "lulc_fresnel",
            body={"options": kwargs},
            method="post",
        )

    def dem_tiles(self, ds_string, **kwargs):
        params = {"ds_string": ds_string}

        for option in ["downscale", "minio_time", "direction", "reverse"]:
            if option in kwargs:
                params[option] = kwargs[option]

        return self.request(
            "show_tiff_band",
            params=params,
            method="get",
        )

    def dem_profiles(self, v_h=0, v_v=0, **kwargs):
        return self.request(
            "show_profiles",
            params={"v_h": v_h, "v_v": v_v},
            body={"options": kwargs},
            method="post",
        )

    def dem_surface(self, ds, **kwargs):
        return self.request(
            "plot_surface_dict",
            params={"ds_string": ds},
            body={"options": kwargs},
            method="post",
        )

    def bldg_prepare(self):
        return self.request(
            "prepare_bldg",
            params={},
            method="get",
            timeout=120,
        )

    def bldg_fresnel(self, **kwargs):
        kwargs["base64"] = True

        return self.request(
            "bldg_fresnel",
            body={"options": kwargs},
            method="post",
        )

    def bldg_profile(self, filtered=False, **kwargs):
        kwargs["base64"] = True

        return self.request(
            "bldg_profile",
            params={"filtered": filtered},
            body={"options": kwargs},
            method="post",
        )

    def bldg_filter(self, v_v=-1, v_h=-1):
        return self.request(
            "bldg_filter",
            params={"v_v": v_v, "v_h": v_h},
            method="get",
        )

    def bldg_invasions(self):
        return self.request(
            "bldg_invasions",
            params={},
            method="get",
        )

    def bldg_browser(self, order_by_invasion=False, **kwargs):
        kwargs["base64"] = True

        return self.request(
            "bldg_browser",
            params={"order_by_invasion": order_by_invasion},
            body={"options": kwargs},
            method="post",
        )

    def bldg_next_invasion(self):
        return self.request(
            "bldg_next_invasion",
            params={},
            method="get",
        )

    def link_features(self):
        return self.request(
            "link_features",
            params={},
            method="get",
        )

    def point_samples(
        self,
        points: list[tuple[float, float]],
        ds_string: str = "DTM",
        nodata_value: float | None = 0,
    ) -> APIResult:
        body = {
            "coords_list": [
                {"lat": lat, "lon": lon}
                for lat, lon in points
            ]
        }

        return self.request(
            "point_samples",
            params={
                "ds_string": ds_string,
                "nodata_value": nodata_value,
            },
            body=body,
            method="post",
        )