import base64
from io import BytesIO
from dataclasses import dataclass

import requests
from PIL import Image
from IPython.display import display
from IPython.display import display, JSON, HTML

import inspect


@dataclass
class APIResult:
    kind: str
    data: object
    response: requests.Response | None = None

    def _repr_pretty_(self, p, cycle):
        if cycle:
            p.text("APIResult(...)")
        else:
            p.text(f"APIResult(kind={self.kind!r})")

    def __repr__(self):
        return f"APIResult(kind={self.kind!r})"


class GeoNotebook:
    def __init__(self, base_url="http://localhost:8080", timeout=20, auto_display=True):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.auto_display = auto_display

    def call_api(self, route, params=None, body=None, method="get", timeout=None,
                display_result=True, json_mode="auto"):
        url = f"{self.base_url}/{route.lstrip('/')}"
        timeout = timeout or self.timeout
        method = method.lower()

        try:
            if method == "post":
                response = requests.post(url, params=params, json=body, timeout=timeout)
            else:
                response = requests.get(url, params=params, timeout=timeout)

            response.raise_for_status()
            result = self._parse_response(response)

            if self.auto_display and display_result:
                self._display_result(result, response, json_mode=json_mode)
                return None

            return result

        except requests.HTTPError as e:
            response = getattr(e, "response", None)
            error_result = APIResult("http_error", {"error": str(e)}, response)
        except requests.RequestException as e:
            error_result = APIResult("request_error", {"error": str(e)}, None)
        except Exception as e:
            error_result = APIResult("error", {"error": str(e)}, None)

        if self.auto_display and display_result:
            self._display_result(error_result, error_result.response, json_mode=json_mode)
            return None

        return error_result

    def _parse_response(self, response):
        ctype = (response.headers.get("content-type") or "").lower()

        if "application/json" in ctype:
            data = response.json()
            return self._parse_json_payload(data, response)

        if ctype.startswith("image/"):
            img = Image.open(BytesIO(response.content))
            return APIResult("image", img, response)

        if "text/" in ctype:
            return APIResult("text", response.text, response)

        return APIResult("bytes", response.content, response)

    def _parse_json_payload(self, data, response):
        if (
            isinstance(data, dict)
            and data.get("status") == "OK"
            and isinstance(data.get("data"), dict)
            and "plot" in data["data"]
        ):
            img = Image.open(BytesIO(base64.b64decode(data["data"]["plot"])))
            return APIResult("image", img, response)

        return APIResult("json", data, response)

    def _display_result(self, result, json_mode="auto"):
        if result.kind == "image":
            display(result.data)
            return

        if result.kind == "json":
            data = result.data

            if json_mode == "json":
                display(JSON(data))
                return

            if isinstance(data, dict):
                status = data.get("status")
                payload = data.get("data")

                if status == "OK" and payload is None:
                    display(HTML("✅ OK"))
                    return

                if status == "error":
                    display(f"❌ Error: {payload}")
                    return

            if json_mode == "text":
                display("OK")
                return

            display(JSON(data))
            return

        if result.kind == "text":
            display(result.data.strip() or "OK")
            return

        if result.kind == "http_error":
            msg = result.data.get("error", "HTTP error")
            display(f"❌ HTTP/backend error: {msg}")
            return

        if result.kind in ("request_error", "error"):
            msg = result.data.get("error", "Unknown error")
            display(f"❌ Client/request error: {msg}")
            return

        display("OK")
    
    def doc(self, name):
        method = getattr(self, name)
        print(f'\n*** {name} ***')
        print(inspect.cleandoc(method.__doc__))

    def link(self, tx, rx, **kwargs):
        """
        Updates the active link stored in the backend runtime..

        Mandatory params
        ----------
        tx : (lat, lon) 
        rx : (lat, lon)

        Optional (must include param name)
        ----------
        tx_ha: tx antenna height in meters (default = 7)
        rx_ha: rx antenna height in meters (default = 7)
        freq_mhz: frequency of the RF signal in mHz (default = 900)
        tx_ha_abs: tx absolute antenna height (including DTM) in meters
        rx_ha_abs: tx absolute antenna height (including DTM) in meters
        on_rooftop: add building height automatically (True or False)    

        Return:
        ---------
        JSON response from the backend. Access payload with `result.data`.
        """
        tx_lat, tx_lon = tx
        rx_lat, rx_lon = rx

        params = {
            "tx_lat": tx_lat,
            "tx_lon": tx_lon,
            "rx_lat": rx_lat,
            "rx_lon": rx_lon,
            **kwargs,
        }

        self.call_api("set_link", params=params, method="post", display_result=False)
        return self.call_api("prepare_profiles", method="post", json_mode = "auto")
      
    def link_area(self, ds_string : str):
        """
        Shows the visual representation of the DEM source for the link area

        Mandatory params
        ----------
        ds_string: COVER, DTM or DSM

        APIResult
        -------
        Visual representation of the link area
        """

        return self.call_api("link_area", params={"ds_string": ds_string}, method="get") 

    def link_profile(self, v_h=0, **kwargs):
        """
        Shows the elevation profile of a slice of the horizontal fresnel plane.

        Mandatory params
        ----------
        v_h : horizontal fresnel offset where left > 0 (defaut = 0)

        Optional (must include param name)
        ----------
        figsize: figure size (w, h) in inches
        dpi: figure dpi (pixels per inch)
        
        Return:
        ---------
        Visual representation of the link profile.
        """
                
        return self.call_api(
            "link_profile", 
            params={"v_h": v_h},
            body={"options": kwargs} if kwargs is not None else None,
            method="post")

    def lulc_fresnel(self, **kwargs):        
        return self.call_api("lulc_fresnel", 
            body={"options": kwargs} if kwargs is not None else None,
            method="post")

    def dem_profiles(self, v_h=0, v_v=0, **kwargs):
        return self.call_api(
            "show_profiles",
            params={"v_h": v_h, "v_v": v_v},
            body={"options": kwargs} if kwargs is not None else None,
            method="post",
        )

    def dem_surface(self, ds, **kwargs):
        return self.call_api(
            "plot_surface_dict",
            params={"ds_string": ds},
            body={"options": kwargs} if kwargs is not None else None,
            method="post",
        )
    
    def prepare_bldg(self):
        """
        Load building information for the link - required for building clutering evaluation

        Return:
        ---------
        JSON response from the backend. Access payload with `result.data`.        
        """        
         
        return self.call_api('prepare_bldg', params={}, method="get", timeout=120 )
    
    def bldg_fresnel(self, **kwargs):   
        kwargs['base64'] = True     
        return self.call_api("bldg_fresnel", 
            body={"options": kwargs} if kwargs is not None else None,
            method="post")
    
    def bldg_profile(self, **kwargs):   
        kwargs['base64'] = True     
        return self.call_api("bldg_profile", 
            body={"options": kwargs} if kwargs is not None else None,
            method="post")