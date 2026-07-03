import geopandas as gpd
from shapely.geometry import Point, LineString
from shapely.geometry import box
import numpy as np
import geopandas as gpd
import networkx as nx
import numpy as np
from shapely.geometry import Point, box


def density_color(d, max_d):
    if d > 0.9 * max_d: return '#800026'
    if d > 0.7 * max_d: return '#BD0026'
    if d > 0.5 * max_d: return '#E31A1C'
    if d > 0.3 * max_d: return '#FC4E2A'
    if d > 0.1 * max_d: return '#FD8D3C'
    if d > 0:          return '#FEB24C'
    return '#FFEDA0'

def hexbin_layer(in_file, out_file, **kwargs):
    # Load pole (or anything else) positions
    poles = gpd.read_file(in_file).to_crs(epsg=3857)

    # Create grid (500m x 500m)
    xmin, ymin, xmax, ymax = poles.total_bounds
    cell_size = kwargs.get('cell_size', 500)
    cols = np.arange(xmin, xmax + cell_size, cell_size)
    rows = np.arange(ymin, ymax + cell_size, cell_size)

    polygons = []
    for x in cols:
        for y in rows:
            polygons.append(box(x, y, x + cell_size, y + cell_size))

    grid = gpd.GeoDataFrame(geometry=polygons, crs=poles.crs)

    # Count poles in each grid cell
    joined = gpd.sjoin(poles, grid, how="left", predicate="within")
    counts = joined.groupby("index_right").size()
    grid["density"] = counts
    grid["density"] = grid["density"].fillna(0)

    # Assign color to grid
    max_density = grid["density"].max()
    grid["color"] = grid["density"].apply(lambda d: density_color(d, max_density))

    # Save as GeoJSON
    grid.to_crs(epsg=4326).to_file(out_file, driver="GeoJSON")

def hexbin_graph_layer(G: nx.Graph, source_label: str, out_file: str, **kwargs):
    """
    Creates a shaded grid based on Dijkstra distance from a source node.

    Parameters:
        G : nx.Graph
            Graph with 'pos' in [lat, lon] per node and 'weight' per edge
        source_label : str
            Node to use as Dijkstra source
        out_file : str
            Output GeoJSON path
        cell_size : int (optional)
            Grid cell size in meters (default: 500)
        agg : str (optional)
            Aggregation mode: 'max', 'min', 'mean' (default: 'max')
    """
    cell_size = kwargs.get('cell_size', 500)
    agg = kwargs.get('agg', 'min')

    # Compute Dijkstra shortest path length from source
    dist = nx.single_source_dijkstra_path_length(G, source=source_label, weight='weight')

    # Build GeoDataFrame with node positions and computed metric
    nodes = []
    for node, data in G.nodes(data=True):
        if node in dist and 'pos' in data:
            lat, lon = data['pos']
            nodes.append({
                'label': node,
                'metric': dist[node],
                'geometry': Point(lon, lat)
            })

    gdf_nodes = gpd.GeoDataFrame(nodes, crs="EPSG:4326").to_crs(epsg=3857)

    # Create grid
    xmin, ymin, xmax, ymax = gdf_nodes.total_bounds
    cols = np.arange(xmin, xmax + cell_size, cell_size)
    rows = np.arange(ymin, ymax + cell_size, cell_size)
    grid_cells = [box(x, y, x + cell_size, y + cell_size) for x in cols for y in rows]
    grid = gpd.GeoDataFrame(geometry=grid_cells, crs=gdf_nodes.crs)

    # Join and aggregate
    joined = gpd.sjoin(gdf_nodes, grid, how="left", predicate="within")
    joined["index_right"] = joined["index_right"].astype("Int64")

    if agg == "min":
        agg_series = joined.groupby("index_right")["metric"].min()
    elif agg == "mean":
        agg_series = joined.groupby("index_right")["metric"].mean()
    else:
        agg_series = joined.groupby("index_right")["metric"].max()

    # agg_series.index = agg_series.index.astype("int64")
    grid["metric"] = np.nan
    grid.loc[agg_series.index, "metric"] = agg_series
    grid["metric"] = grid["metric"].fillna(0)

    # for i, v in agg_series.items(): grid.at[int(i), "metric"] = v
    

    # Optional: assign colors (if you have a coloring function)
    max_val = grid["metric"].max()
    grid["color"] = grid["metric"].apply(lambda v: density_color(v, max_val))
    grid["density"] = grid["metric"]

    # Export to GeoJSON in EPSG:4326 for visualization
    grid.to_crs(epsg=4326).to_file(out_file, driver="GeoJSON")






