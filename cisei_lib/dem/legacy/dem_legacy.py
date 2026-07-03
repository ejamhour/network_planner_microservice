import os, shutil
import glob
import json
import rasterio
import pandas as pd
from rasterio.windows import Window
import tempfile
import cisei_lib.core.profiles.geo_tools as geoTools
from cisei_lib.io.minio_access import MinioAccess
from pathlib import Path

# Default region for Paraná (lat/long boundaries)
PR = [
    ('S', 22, 30, 58),
    ('S', 26, 43, 0),
    ('W', 48, 5, 37),
    ('W', 54, 37, 8)
]

# Paraná Bounds in UTM Zone 22S (Meters)
PR_UTM = {
	'utm_n' : 7505850,  # Max Northing
	'utm_s' : 7041150,  # Min Northing
	'utm_w' : 127450,   # Min Easting
	'utm_e' : 789350   # Max Easting
}


URGS = 'https://e4ftl01.cr.usgs.gov/ASTT/ASTGTM.003/2000.03.01/'
DTM  = 'https://hge-iph.github.io/anadem/'

# Base dataset directory, configurable for container or host
DATASETS_DIR = os.getenv("GEO_DATA_PATH", "/workspaces/planning_service/data/GeoDatasets")

class GeoStorage:
    """Abstract interface for GeoTIFF storage backends."""
    def list_tiffs(self, prefix: str) -> list[str]: ...
    def download_temp(self, key: str, tmpdir: str) -> str: ...
    def upload_json(self, local_path: str, remote_key: str) -> None: ...


class MinioGeoStorage(GeoStorage):
    def __init__(self, client: MinioAccess):
        self.client = client

    def list_tiffs(self, prefix):
        return [obj for obj in self.client.remote_list(prefix) if obj.endswith(".tif")]

    def download_temp(self, key, tmpdir):
        local_path = Path(tmpdir) / Path(key).name
        self.client.client.fget_object(
            self.client.bucket, self.client._map_remote(key), str(local_path)
        )
        return str(local_path)

    def upload_json(self, local_path, remote_key):
        self.client.client.fput_object(
            self.client.bucket, self.client._map_remote(remote_key), str(local_path)
        )


class geoDataset:
    """
    Base class for GeoTIFF-based datasets.
    Handles local repository structure, coordinate conversions,
    tile metadata indexing, and info.json creation/loading.
    """

    def __init__(self, repo: str, region: list, storage: GeoStorage | None = None):
        self.repo = os.path.join(DATASETS_DIR, repo)
        self.region = region
        self.storage = storage or MinioGeoStorage(MinioAccess())

        # Ensure local repository exists
        os.makedirs(self.repo, exist_ok=True)

        # Ensure info.json exists locally
        info_path = os.path.join(self.repo, "info.json")
        if not os.path.exists(info_path):
            print(f"[geoDataset] info.json missing for {repo}, downloading from MinIO...")
            try:
                self.repo_access.download_object(f"{repo}/info.json", info_path)
            except Exception as e:
                print(f"[geoDataset] Warning: could not fetch info.json: {e}")

        # Load info.json if available
        self.info = None
        if os.path.exists(info_path):
            self.info = pd.read_json(info_path)

        # Convert region limits to decimal degrees
        n, s, e, w = (int(geoTools.dms_to_dd(*c)) for c in region)
        s -= 1
        w -= 1
        self.re = {"lon_w": w, "lat_n": n, "lon_e": e, "lat_s": s}
   
    def clear_cache(self, refresh_info: bool = True):
        """
        Clear all locally cached data for this dataset.

        Removes the entire local repository folder (including all .tif files and info.json),
        recreates it empty, and optionally re-downloads the info.json from MinIO.

        Parameters
        ----------
        refresh_info : bool, optional
            If True (default), immediately re-downloads info.json from MinIO after clearing.
            If False, leaves the folder empty for lazy reinitialization.
        """
        if os.path.isdir(self.repo):
            print(f"[geoDataset] Clearing local cache: {self.repo}")
            shutil.rmtree(self.repo)

        os.makedirs(self.repo, exist_ok=True)

        if refresh_info:
            info_path = os.path.join(self.repo, "info.json")
            try:
                print(f"[geoDataset] Downloading fresh info.json from MinIO...")
                self.repo_access.download_object(f"{os.path.basename(self.repo)}/info.json", info_path)
            except Exception as e:
                print(f"[geoDataset] Warning: failed to refresh info.json: {e}")

    def build_json(self):
        """
        Scan repository for .tif files and generate info.json with geographic bounds.
        This function is deprecated by the use of MinIO and replaced by build_json_remote
        """
        info = []
        for f in glob.glob("*.tif", root_dir=self.repo):
            try:
                with rasterio.open(os.path.join(self.repo, f)) as src:
                    b = src.bounds
                    info.append({
                        "file": f,
                        "lon_w": b.left, "lon_e": b.right,
                        "lat_n": b.top, "lat_s": b.bottom
                    })
            except Exception as e:
                print(f"[geoDataset] Skipped {f}: {e}")

        info_path = os.path.join(self.repo, "info.json")
        with open(info_path, "w") as f:
            f.write(json.dumps(info, indent=4))
        print(f"[geoDataset] Wrote {len(info)} entries to {info_path}")

    def build_json_remote(self):
        """
        Build info.json directly from the storage backend.
        Works for any GeoTIFF-capable backend implementing GeoStorage.
        """
        prefix = os.path.basename(self.repo)
        info = []
        print(f"[geoDataset] Building remote info.json for '{prefix}'...")

        with tempfile.TemporaryDirectory() as tmpdir:
            for obj_name in self.storage.list_tiffs(prefix):
                local_path = self.storage.download_temp(obj_name, tmpdir)
                try:
                    with rasterio.open(local_path) as src:
                        b = src.bounds
                        info.append({
                            "file": Path(obj_name).name,
                            "lon_w": b.left, "lon_e": b.right,
                            "lat_n": b.top, "lat_s": b.bottom
                        })
                except Exception as e:
                    print(f"[geoDataset] Skipped {obj_name}: {e}")

            info_path = os.path.join(tmpdir, "info.json")
            json.dump(info, open(info_path, "w"), indent=4)
            self.storage.upload_json(info_path, f"{prefix}/info.json")
            print(f"[geoDataset] Uploaded info.json ({len(info)}) to {prefix}")

    def dataset_info(self, src):

        print(f'indexes: {src.indexes}')
        #print(f'profile: {src.profile}')
        #print(f'tags: {src.tags()}')
    
        lon_e, lat_n = src.xy(0, src.width) # y,x (row, column)
        lon_w, lat_s = src.xy(src.height, 0) 
        print( src.bounds)
        print(f'W:{round(lon_w,4)} E:{round(lon_e,4)} N:{round(lat_n,4)} S:{round(lat_s,4)}')

        # y,x = row,col
        res= f'y,x = index(lon,lat):\n' \
            + f'EN: {src.index(lon_e, lat_n)}\n' \
            + f'WS: {src.index(lon_w, lat_s)}\n' 

        print(res)

        f = lambda x : round(x,3)

        # y,x = row,col
        res = f'lon,lat = src.xy(y,x):\n' \
            + f'0,width: {[*map(f,src.xy(0,src.width))]}\n' \
            + f'height, 0: {[*map(f,src.xy(src.height,0))]}\n'
        
        print(res)

        res = f'lon,lat = src.transform * (x,y):\n' \
            + f'0,heigth: {[*map(f,src.transform * (0, src.height))]}\n' \
            + f'width, 0: {[*map(f,src.transform * (src.width, 0))]}\n' 
        
        print(res)

    def geo_to_pixel(self, src, coord):

        '''
        x,y = ~src.transform * (lon, lat)
        print(f'x={int(x)},y={int(y)}')
        '''

        lat, lon = coord
        y,x = src.index(lon, lat)
        return x,y # col,row

    def geo_to_window(self, src, coord_nw, coord_se):

        col_i, row_i = self.geo_to_pixel(src, coord_nw)
        col_f, row_f = self.geo_to_pixel(src, coord_se)

        cols = col_f - col_i
        rows = row_f - row_i   

        return {'col_off':col_i, 'row_off':row_i, 'width':cols, 'height':rows }   
 
    def is_coordinate(self, bounds, coord_nw, coord_se=None):

        lat, lon = coord_nw
        
        if coord_se is None:
            pf = (bounds.left <= lon <=  bounds.right) and (bounds.bottom <= lat <= bounds.top)
        else:
            lat_s, lon_e = coord_se
            pf = (bounds.left <= lon <=  bounds.right) and (bounds.bottom <= lat <= bounds.top) \
                 and (bounds.left <= lon_e <=  bounds.right) and (bounds.bottom <= lat_s <= bounds.top)

        return pf
    
    def split_tiff(self, src, out_path, coord_nw, coord_se):

        win = self.geo_to_window(src, coord_nw, coord_se)      
        print(win)           
        window_pix = Window(**win)
        
        data = src.read(window=window_pix)
       
        profile = src.profile
        profile['width'], profile['height'] = win['width'], win['height']
        profile['transform'] = src.window_transform(window_pix)

        with rasterio.open(out_path, 'w', **profile) as dst:
            dst.write(data)

    def read_dataset(self, src, **kwargs):
        if 'window' in kwargs:
            print(kwargs['window'])
            lon_w, lat_n, lon_e, lat_s = (kwargs['window'][i] for i in ['lon_w', 'lat_n', 'lon_e', 'lat_s'])
            win = self.geo_to_window(src, (lat_n, lon_w), (lat_s, lon_e))                 
            window_pix = Window(**win)
            data = src.read(1,window=window_pix)
            
        else:
            data = src.read(1)
            lon_w, lat_n = src.bounds.left, src.bounds.top
            lon_e, lat_s = src.bounds.right, src.bounds.bottom 

        bounds = {'coord_nw' : (lat_n, lon_w), 'coord_se' : (lat_s, lon_e) }
        return data, bounds

    
class geoURGS(geoDataset):
    
    '''
    Must authenticate to the USGS site: ('ejamhour@gmail.com', 'M0nk&yIsInMyHead')
    https://lpdaac.usgs.gov/products/ast14othv003/
    - download is automated with webbrowser (can't use requests because authentication is a redirect to a form)
    - DEM is only ground. LiDAR is surface (include buildings and trees) - MDR (Modelo Digital de Elevação)
    '''
        
    def __init__(self, **kwargs):

        repo = kwargs.get('repo','URGSData')
        region = kwargs.get('region', PR)

        super().__init__(repo, region)

        self.url = kwargs.get('url', URGS)     

    def update_repo(self):

        for lat in range(self.re['lat_n'], self.re['lat_s'], -1):
            for lon in range(self.re['lon_w'], self.re['lon_e'], 1): 
                f = f'ASTGTMV003_S{abs(lat-1)}W0{abs(lon)}'      
                zip_path = os.path.join(self.repo, f + '.zip')
                tif_path = zip_path.replace('.zip', '_dem.tif')

                if not (os.path.isfile( zip_path ) or os.path.isfile( tif_path )):
                    url = f'{self.url}{f}.zip'
                    # webbrowser.open(url) # this does not work
                    input('Download next?')

                if not os.path.isfile( tif_path ) and os.path.isfile(zip_path):
                    geoTools.unzip(zip_path, tif_path)
                    os.remove(zip_path)


    def check_repo(self, delete_extra=False):

        files = glob.glob('*.tif', root_dir=self.repo) 

        for lat in range(self.re['lat_n'], self.re['lat_s'], -1):
            for lon in range(self.re['lon_w'], self.re['lon_e'], 1): 
                f = f'ASTGTMV003_S{abs(lat-1)}W0{abs(lon)}_dem.tif'  
                if f not in files:
                    print(f'missing {f}')
                else:
                    files.remove(f)

        if len(files) > 0:
            print(f'There are {len(files)} extra files')
            print(files)
            if delete_extra:
                print('This files will be removed')
                for f in files:
                    os.remove(os.path.join(self.repo, f))


class geoCover(geoDataset):
    '''
    Must authenticate to WorldCover site: ('ejamhour', 'M0nk&yIsInMyHead')
    URL: https://esa-worldcover.org - Explore and Download
    - Select the rectangles corresponding to the regions to download (show administrative bounds)
    - Select map tiff (what is the purpose of input quality?)
    '''

    def __init__(self, **kwargs):
       
        repo = kwargs.get('repo','CoverData')  
        region = kwargs.get('region', PR)

        super().__init__(repo, region)
      

    def update_repo(self, srcdir):
    
        files = glob.glob('*.tif', root_dir=srcdir) 

        if len(files) == 0:
            raise Exception(f'No Worldcover tif files found in {self.repo}!')

        for f in files:                  
            i_file = os.path.join(srcdir, f)
            with rasterio.open(i_file) as src:                  
                print(f, src.bounds)
                for lat in range(self.re['lat_n'], self.re['lat_s'], -1):
                    for lon in range(self.re['lon_w'], self.re['lon_e'], 1):                            
                        if self.is_coordinate(src.bounds, lon, lat, lon+1, lat-1):
                            print('extracting ...', lat, lon, lat-1, lon+1)    
                                                      
                            o_file = os.path.join(self.repo, f'ESA_WorldCover_S{abs(lat-1)}W0{abs(lon)}.tif')
                            if not os.path.isfile(o_file):
                                self.split_tiff(src, o_file, lon, lat, lon+1, lat-1 )                                
                            else:
                                print('OK') 
              
                 
                 
if __name__ == '__main__':

    obj = geoURGS()
    obj.check_repo(delete_extra=True)
    obj.build_json()

    exit()
    obj = geoCover()
    src_repo = '/home/self/Documents/CISEI/Telecom/arquivos/WorldCover/'
    # obj.update_repo(src_repo) 
    files = glob.glob('*.tif', root_dir=src_repo) 
    with rasterio.open(os.path.join(src_repo,files[0])) as src: 
        print(src.tags()['legend'])
        print(src.shape)
    exit()    

    #obj = geoURGS()     
    #obj.update_repo() 
    #obj.build_json()


    files = glob.glob('*.tif', root_dir=obj.repo) 
    with rasterio.open(os.path.join(obj.repo,files[0])) as src: 
        print(src.profile)
        # obj.show_tiff(src)
            



