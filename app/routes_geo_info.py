from fastapi import APIRouter, Query, Request, Body
from fastapi.responses import JSONResponse
from cisei_lib.core.profiles.geo_info import P2PLink, DSFlags
from cisei_lib.core.plot.geo_plot import join_img_str, tile_direction
import cisei_lib.dem.dem_utils as du
from datetime import timedelta
from pydantic import BaseModel, ConfigDict
from pathlib import Path

class CoordPair(BaseModel):
    lat: float
    lon: float

class CoordinateList(BaseModel):
    coords_list: list[CoordPair]

class PlotOptions(BaseModel):    
    model_config = ConfigDict(extra="allow")
    title: str = "Planning Plot"
    xlabel: str = "x values"
    ylabel: str = "y values" 
    figsize: tuple[int, int] = (10, 5)
    dpi: float = 100   
    grid_alpha: float = 0.8

router = APIRouter()

@router.post(
    "/set_link",
    summary="Define the link that is being evaluated",
    description=""
)
def set_link(
    request: Request,
    tx_lat: float = Query(..., description="Transmission latitude"),
    tx_lon: float = Query(..., description="Transmission longitude"),
    rx_lat: float = Query(..., description="Reception latitude"),
    rx_lon: float = Query(..., description="Reception longitude"),
    tx_ha: float = Query(7, description="Transmission antenna height (meters)"),
    rx_ha: float = Query(7, description="Reception antenna height (meters)"),
    freq_mhz: float = Query(900, description="Frequency (MHz)"),
    tx_ha_abs: float | None = Query(None, description="Transmission antenna height including DTM (meters)"),
    rx_ha_abs: float | None = Query(None, description="Reception antenna height including DTM (meters)"),
    on_rooftop: bool = Query(False, description="Add building height to antenna"),
):
    tx = (tx_lat, tx_lon)
    rx = (rx_lat, rx_lon)
    link = P2PLink(tx, rx, tx_ha, rx_ha, freq_mhz, tx_ha_abs, rx_ha_abs, on_rooftop)

    runtime = request.app.state.runtime
    runtime.link_change(link)

    return {"status": "OK", "kind" : "text", "data" : 'OK' }

@router.post(
    "/prepare_profiles",
    summary="Extract rasterio data for profile analysis",
    description=""
)
def prepare_profiles(
    request: Request,   
):

    runtime = request.app.state.runtime    
    link = runtime.link
    
    if link is None:
        return {"status": "error", "kind" : "text", "data": "link not initialized", }
    
    try:
        # runtime.rfo.gio.initialize_2D_dictionaries(link)    
        runtime.rfo.load_profile(link)    
        runtime.step = 2     
        runtime.history.append('prepare_profiles')  
        return {"status": "OK", "kind" : "text", "data": 'link DEM e BLDG is initialized'}
    except Exception as e:
        return {"status": "error", "kind" : "text", "data": str(e) }

@router.get(
    "/link_area",
    summary="Shows the visual representation of the DEM source for the link area",
    description="Returns a base64-encoded PNG showing the selected geographic window."
)
def link_area(
    request: Request,
    ds_string: str = Query(..., description="DTM, DSM or COVER"),
):
    runtime = request.app.state.runtime 
    link = runtime.rfo.link
    
    if link is None:
        return {"status": "error", "data": "link is not set" }
    
    ds_flag = DSFlags[ds_string]    
    img = runtime.rfo.gio.create_b64_map(link.tx, link.rx, ds_flag = ds_flag)
    return JSONResponse(content={"status": "OK", "kind" : "image", "data": img})

@router.post(
    "/link_profile",
    summary="Compute RF link profile",
    description="Returns a base64-encoded PNG showing elevation, tree/building heights, and line-of-sight."
)
def link_profile(
    request: Request,
    v_h: float = Query(0, description="Horizontal Fresnel (positive is right)"),
    options: PlotOptions | None = Body(None, embed=True),
):
    runtime = request.app.state.runtime
    link = runtime.rfo.link
    
    if link is None:
        return {"status": "error", "data": "link is not set", }
    
    kwargs = {} if options is None else options.model_dump()

    img = runtime.rfo.gio.create_b64_link(
        link,
        v_h=v_h,
        **kwargs
    )

    return JSONResponse(content={"status": "OK", "kind": 'image', "data": img})

@router.get(
    '/show_tiff_band',
    summary='Show tiff tile with downscale',
    description="Returns a base64-encoded PNG showing a tiff tile."
)
def show_tiff_band(
    request: Request,
    ds_string: str = Query(..., description="DTM, DSM or COVER"),
    downscale: int | None = Query(10, description="pixel downscale factor"),
    minio_time: int | None = Query(10, description="Minio signed-url time"),
    direction: str | None = Query('horizontal', description="'horizontal' or 'vertical' for 2 tiles"),
    reverse: bool = Query(False, description="reverse tile order before merge"),
):
    runtime = request.app.state.runtime
    link = runtime.link
    ds_flag = DSFlags[ds_string]

    ds_key, tiles = runtime.rfo.gio.select_dataset(ds_flag, [link.tx, link.rx])

    if not tiles:
        return JSONResponse(content={"status": "error", "error": "no tile found"})

    if len(tiles) == 1:
        file_path = str(Path(ds_key) / tiles[0])
        url = runtime.rfo.gio.repo_access.get_object_url(
            file_path, timedelta(seconds=minio_time)
        )
        img_str = du.show_tiff_band(
            url,
            return_base64=True,
            downscale_factor=downscale,
        )
        return JSONResponse(content={"status": "ok", "kind": "image", "data": img_str, "tiles": tiles})

    if len(tiles) == 2:
        if direction not in {"horizontal", "vertical"}:
            return JSONResponse(
                content={
                    "status": "error",
                    "kind" : "json",
                    "data" : { 
                        "tiles": tiles,
                        "message": "two tiles found; provide direction='horizontal' or 'vertical', and optional reverse=true",
                    }
                }
            )

        ordered_tiles = list(reversed(tiles)) if reverse else list(tiles)

        file_path1 = str(Path(ds_key) / ordered_tiles[0])
        file_path2 = str(Path(ds_key) / ordered_tiles[1])

        url1 = runtime.rfo.gio.repo_access.get_object_url(
            file_path1, timedelta(seconds=minio_time)
        )
        url2 = runtime.rfo.gio.repo_access.get_object_url(
            file_path2, timedelta(seconds=minio_time)
        )

        img_str1 = du.show_tiff_band(
            url1,
            return_base64=True,
            downscale_factor=downscale,
        )
        img_str2 = du.show_tiff_band(
            url2,
            return_base64=True,
            downscale_factor=downscale,
        )

        img_str = join_img_str(img_str1, img_str2, direction=direction)

        return JSONResponse(
            content={
                "status": "ok",
                "kind": "image",
                "data": img_str                
            }
        )

    return JSONResponse(
        content={
            "status": "error",
            "data": f"unsupported number of tiles: {len(tiles)}"
        }
    )

@router.post(
    "/lulc_fresnel",
    summary="Show lulc coverage across link",
    description="Returns a base64-encoded PNG showing the LULC coverage and the horizontal fresnel profile."
)
def show_lulc_fresnel(
    request: Request,    
    v_h: float = Query(0, description="Horizontal Fresnel (positive is right)"),
    options: PlotOptions | None = Body(None, embed=True),
):

    runtime = request.app.state.runtime
    link = runtime.rfo.link
    
    if link is None:
        return {"status": "error", "kind":"text", "data": "link is not set", }
    
    kwargs = {} if options is None else options.model_dump()
    kwargs['base64'] = True

    img = runtime.rfo.gio.show_lulc_fresnel_zone(
        link,
        v_h=v_h,    
        **kwargs
    )

    return JSONResponse(content={"status" : "OK", "kind" : "image", "data": img})

@router.post(
    "/show_profiles",
    summary="Show DTM, DSM e LULC profiles",
    description="Returns a base64-encoded PNG showing the LULC coverage and the horizontal fresnel profile."
)
def show_profiles(
    request: Request,    
    v_h: float = Query(0, description="Horizontal Fresnel (positive is right)"),
    v_v: float = Query(0, description="Vertical Fresnel (positive above LOS)"),
    options: dict | None = Body(None, embed=True),
):
    runtime = request.app.state.runtime
    link = runtime.rfo.link
    
    if link is None:
        return {"status": "error", "kind":"text", "data": "link is not set"}   
    
    kwargs = {} if options is None else options # no pydantic json dump()
    
    img = runtime.rfo.gio.show_fresnel_profiles(
        link,
        v_h=v_h,
        v_v=v_v,
        base64=True,
        **kwargs
    )

    return JSONResponse(content={"status":"OK", "kind":"image", "data": img})

@router.post(
    "/plot_surface_dict",
    summary="Show DTM, DSM e LULC surface dicts",
    description="Show the projected DEM info along the link."
)
def plot_surface_dict(
    request: Request,    
    ds_string: str = Query(..., description="DTM, DSM, CLUTTER, COVER"),
    options: PlotOptions | None = Body(None, embed=True),
):
    runtime = request.app.state.runtime   
        
    kwargs = {} if options is None else options.model_dump()
    if kwargs.get('tilte', 'Planning Plot') == 'Planning Plot':
        kwargs['title'] = f' Projected Surface Plot {ds_string}'

    try:
        if ds_string == "COVER":        
            if runtime.rfo.gio.lulc_dict is None:
                raise Exception('Profile was not prepared')
            img = du.plot_surface_dict(runtime.rfo.gio.lulc_dict, runtime.rfo.gio.cover_legend, base64 = True, **kwargs)    
        elif ds_string == "DTM":
            if runtime.rfo.gio.dtm_dict is None:
                raise Exception('Profile was not prepared')
            img = du.plot_surface_dict(runtime.rfo.gio.dtm_dict, base64 = True, **kwargs)    
        elif ds_string == "DSM":
            if runtime.rfo.gio.dsm_dict is None:
                raise Exception('Profile was not prepared')
            img = du.plot_surface_dict(runtime.rfo.gio.dsm_dict, base64 = True, **kwargs)    
        elif ds_string == "CLUTTER":
            if runtime.rfo.gio.clutter_dict is None:
                raise Exception('Profile was not prepared')
            img = du.plot_surface_dict(runtime.rfo.gio.clutter_dict, base64 = True, **kwargs)    
        
        else:
            return {"status": "error", "kind":"text", "data": "invalid DEM type"}      
    
        return JSONResponse(content={"status":"OK", "kind":"image", "data": img})
    except Exception as e:
        return {"status": "error", "kind":"text", "data" : str(e) }

@router.get(
    '/prepare_bldg',
    summary='Prepare building information for the link',
    description="This operation may be slow if it is the first link in the region"
)
def bldg_prepare(
    request: Request,
):
        runtime = request.app.state.runtime
        link = runtime.rfo.link
        
        if link is None:
            return {"status": "error", "error": "link is not set", }                                  
        try:        
            if runtime.rfo.gio.building_df is None:
                runtime.rfo.gio.create_link_dataframe(link)
            runtime.bldg_prepare()      
            return {"status": "OK", "kind":"text", "data" : "link BLDG is loaded" }
        except Exception as e:
            return {"status": "error", "kind":"text", "data" : str(e) }

@router.post(
    '/bldg_fresnel',
    summary='Show buildings foot print accross the link',
    description=""
)
def bldg_fresnel(
    request: Request,
    options: PlotOptions | None = Body(None, embed=True),
):
    runtime = request.app.state.runtime
    kwargs = {} if options is None else options.model_dump()
    
    try:   
        df = runtime.rfo.filtered_df

        if df is None:
            if runtime.rfo.gio.building_df is None:
                raise Exception("buildings are not prepared")    
            else:
                df = runtime.rfo.gio.building_df        
        
        img = runtime.rfo.gio.plot_link_horizontal_profile(runtime.rfo.gio.building_df, show_fresnel=True, **kwargs)  
        return JSONResponse(content={"status":"OK", "kind":"image", "data": img})
    except Exception as e:
        return {"status": "error", "kind":"text", "data" : str(e) }

@router.post(
    '/bldg_profile',
    summary='Show buildings foot print accross the link',
    description=""
)
def bldg_profile(
    request: Request,
    filtered: bool = Query(False, description="use filtered buildings by fresnel"),
    options: PlotOptions | None = Body(None, embed=True),
):
    runtime = request.app.state.runtime
        
    if runtime.rfo.gio.building_df is None:
        return {"status": "error", "kind":"text", "data" : "buildings are not prepared" }    
    
    if filtered:
        if runtime.filtered_df is None:
            return {"status": "error", "kind":"text", "data" : "buildings are not filtered" } 
        df = runtime.filtered_df
    else:
        df = runtime.rfo.gio.building_df 

    kwargs = {} if options is None else options.model_dump()
    
    try:
        # runtime.rfo.gio.initialize_2D_dictionaries(link, DSFlags.DTM)  
        img = runtime.rfo.gio.plot_link_vertical_profile(df, show_fresnel=True, **kwargs)           
        return JSONResponse(content={"status":"OK", "kind":"image", "data": img})
    except Exception as e:
        return {"status": "error", "kind":"text", "data" : str(e) }

@router.get(
    '/bldg_filter',
    summary='fiter buildings based on fresnel invasions',
    description='returns a JSON dataset of the buildings that affects the RF signal'
)
def bldg_filter(
    request: Request,
    v_v: float | None = Query(-1, description="minimal vertical invasion"),
    v_h: float | None = Query(-1, description="minimum horizontal invasion")
):
    
    runtime = request.app.state.runtime
    
    try:        
        if runtime.rfo.gio.building_df is None:
            raise Exception("buildings are not prepared")
    
        if runtime.rfo.filtered_df is None or v_v != -1 and v_h != -1:
            filtered_df = runtime.rfo.gio.filter_buildings_by_fresnel(v_v, v_h)
        else:
            filtered_df = runtime.rfo.filtered_df
        
        runtime.bldg_filter(filtered_df)
        data = filtered_df.to_dict(orient='records')
        return {"status": "OK", "kind": "json", "data": data}
        
    except Exception as e:
        return {"status": "error", "kind":"text", "data" : str(e) }     

@router.get(
    '/bldg_invasions',
    summary='calculate building radial invasions',
    description='returns a JSON dataset with radial invasions'
)
def bldg_invasions(
    request: Request,
):

    runtime = request.app.state.runtime

            
    try:
        if runtime.rfo.gio.building_df is None:
            if runtime.link is None: 
                return {"status": "error", "kind":"text", "data": "link is not set", }
            runtime.rfo.gio.create_link_dataframe(runtime.link)
            runtime.bldg_prepare() 

        if runtime.filtered_df is None:
            filtered_df = runtime.rfo.gio.filter_buildings_by_fresnel(-1,-1)
            runtime.bldg_filter(filtered_df)
    
        zones, invasions = runtime.rfo.gio.buildings_radial_invasions(runtime.filtered_df)

        runtime.bldg_invasions(zones, invasions)
        
        return {"status": "OK", "kind": "json", "data": zones}
        
    except Exception as e:
        return {"status": "error", "kind":"text", "data" : str(e) }     

@router.post(
    '/bldg_browser',
    summary='create internal browser for building visualization',
    description='use to restart browsing'
)
def bldg_browser(
    request: Request,
    order_by_invasion: bool | None = Query(False, description="default is order by distance, set True to show blocking buildings"),     
    options: PlotOptions | None = Body(None, embed=True),
):
    

    runtime = request.app.state.runtime
    if runtime.invasions is None: 
        return {"status": "error", "kind":"text", "data": "buildings invasions is missing", }  

    if order_by_invasion:
        sorted_data = sorted(runtime.invasions, key= lambda x: x['v_v_top'], reverse=True)         
    else:
        sorted_data = sorted(runtime.invasions, key= lambda x: x['t'])        
                            
    try:
        kwargs = {} if options is None else options.model_dump()
        browser = runtime.rfo.gio.building_browser(sorted_data, **kwargs)  
        runtime.bldg_set_browser(browser)              
        return {"status": "OK", "kind": "text", "data": 'building browsing prepared'}
        
    except Exception as e:
        return {"status": "error", "kind":"text", "data" : str(e) }     

@router.get(
    '/bldg_next_invasion',
    summary='return building next invasion',
    description='returns a visual representation of the invasions'
)
def bldg_next_invasion(
    request: Request,
):
    runtime = request.app.state.runtime
   
    if runtime.bldg_browser is None: 
        return {"status": "error", "kind":"text", "data": "building browser is not prepared", }
                
    try:        
        data = runtime.rfo.gio.get_next_invasion(runtime.bldg_browser)            
        if data is None:
            return {"status": "OK", "kind":"text", "data" : 'No more invasions match the criteria' }             
        return JSONResponse(content={"status": "OK", "kind": "image", "data": data['img']})
        
    except Exception as e:
        return {"status": "error", "kind":"text", "data" : str(e) }     

@router.get(
   '/link_features',
   summary='extract link features',
   description='returns a JSON with all link features evaluated'
)
def link_features(
    request: Request,
):
    runtime = request.app.state.runtime
    
    try:
        data = runtime.rfo.evaluate_link()
        return {"status": "OK", "kind": "json", "data": data}
    except Exception as e:
        return {"status": "error", "kind":"text", "data" : str(e) } 



