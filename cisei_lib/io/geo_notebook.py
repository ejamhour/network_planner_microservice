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
    def __init__(self, base_url="http://localhost:8080", timeout=20):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.user_id = None
        self.token = None

#------------------------------------
    def call_api(
        self,
        route,
        params=None,
        body=None,
        method="get",
        timeout=None,
        display_result=True,        
    ):
        if self.user_id:
            url = f"{self.base_url}/{self.user_id}/{route.lstrip('/')}"
            headers = {"X-Token": self.token }
        else:
            url = f"{self.base_url}/{route.lstrip('/')}"
            headers = {}            

        timeout = timeout or self.timeout
        method = method.lower()

        try:
            if method == "post":
                response = requests.post(url, params=params, headers=headers, json=body, timeout=timeout)
            else:
                response = requests.get(url, params=params, headers=headers, timeout=timeout)

            response.raise_for_status()
            result = self._parse_response(response)

        except requests.HTTPError as e:
            response = getattr(e, "response", None)
            result = APIResult(
                "http_error",
                {"status": "error", "kind": "http_error", "data": str(e)},
                response,
            )
        except requests.RequestException as e:
            result = APIResult(
                "request_error",
                {"status": "error", "kind": "request_error", "data": str(e)},
                None,
            )
        except Exception as e:
            result = APIResult(
                "error",
                {"status": "error", "kind": "client_error", "data": str(e)},
                None,
            )

        if display_result:
            self._display_result(result)
            return None
        return result

    def _parse_response(self, response):
        ctype = (response.headers.get("content-type") or "").lower()

        if "application/json" in ctype:
            return self._parse_json_payload(response.json(), response)

        if ctype.startswith("image/"):
            img = Image.open(BytesIO(response.content))
            return APIResult("image", img, response)

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
            img = Image.open(BytesIO(base64.b64decode(payload)))
            return APIResult("image", img, response)

        if kind == "text":
            return APIResult("text", {"status": status, "data": payload}, response)

        return APIResult("json", data, response)

    def _display_result(self, result):
        if result.kind == "image":
            display(result.data)
            return

        if result.kind == "text":
            status = result.data.get("status", "")
            txt = result.data.get("data")

            if txt:
                if status == "ok":
                    display(HTML(f"✅ {txt}"))
                else:
                    display(HTML(f"❌ {txt}"))
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
                if kind == "http_error":
                    display(f"❌ HTTP/backend error: {payload}")
                elif kind == "request_error":
                    display(f"❌ Request error: {payload}")
                elif kind == "client_error":
                    display(f"❌ Client error: {payload}")
                else:
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

        if result.kind == "http_error":
            display(f"❌ HTTP/backend error: {result.data.get('data', 'Unknown error')}")
            return

        if result.kind == "request_error":
            display(f"❌ Request error: {result.data.get('data', 'Unknown error')}")
            return

        if result.kind == "error":
            display(f"❌ Client error: {result.data.get('data', 'Unknown error')}")
            return
     
    def doc(self, name):
        method = getattr(self, name)
        print(f'\n*** {name} ***')
        print(inspect.cleandoc(method.__doc__))

#---------------------------------------
    def register(self, user_id):
        res = self.call_api("register", params={"user_id": user_id}, method="post", display_result=False)
        self.user_id = res.data['user_id']
        self.token = res.data['token']
        display(HTML(f"✅ {res.data['status']}"))


    def link(self, tx, rx, prepare_link=True, **kwargs):
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
        
        if prepare_link:
            self.call_api("set_link", params=params, method="post", display_result=False)    
            return self.call_api("prepare_profiles", method="post")
        else:
            self.call_api("set_link", params=params, method="post", display_result=True)
          
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

    def dem_tiles(self, ds_string : str, **kwargs):
        params = {"ds_string": ds_string}
        options = ['downscale', 'minio_time', 'direction', 'reverse']
        for o in options:
            if o in kwargs:
                params[o] = kwargs[o]

        return self.call_api("show_tiff_band", params=params, method="get")
   
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
       
    def bldg_prepare(self):
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
    
    def bldg_profile(self, filtered=False, **kwargs):   
        kwargs['base64'] = True     
        return self.call_api("bldg_profile", 
            params = {"filtered": filtered}, 
            body={"options": kwargs} if kwargs is not None else None,
            method="post")
           
    def bldg_filter(self, v_v=-1, v_h=-1):
        params = {"v_v": v_v, "v_h" : v_h}        
        return self.call_api("bldg_filter", params=params, method="get")
    
    def bldg_invasions(self):        
        return self.call_api("bldg_invasions", params={}, method="get")

    def bldg_browser(self, order_by_invasion = False, **kwargs):   
        kwargs['base64'] = True        
        return self.call_api("bldg_browser", 
            body={"options": kwargs} if kwargs is not None else None,
            params={'order_by_invasion' : order_by_invasion}, 
            method="post")

    def bldg_next_invasion(self):        
        return self.call_api("bldg_next_invasion",         
            params={}, 
            method="get")
    
    def link_features(self):        
        return self.call_api("link_features",         
            params={}, 
            method="get")