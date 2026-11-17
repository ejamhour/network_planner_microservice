import base64
from io import BytesIO
from PIL import Image

"""
geo_plot.py — Visualization helpers for GeoTIFF datasets.
Standalone plotting functions receiving numpy data and geographic bounds.
"""

def show_tiff(src, window, marks=None, legend=None, colors=None):
    """
    Plot a geographic sub-window of a rasterio dataset.

    Parameters
    ----------
    src : rasterio.DatasetReader
        Open GeoTIFF dataset.
    window : dict
        {'lon_w':..., 'lat_n':..., 'lon_e':..., 'lat_s':...}
    marks : list[(lat,lon),(lat,lon)], optional
        Link endpoints (optional).
    legend : dict[int -> str], optional
        Cover legend. If provided, categorical map is used.
    colors : dict[str -> (r,g,b)], optional
        RGB per class name. Required only if legend is provided.
    """
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from geopy.distance import geodesic
    import numpy as np
    from pyproj import Transformer

    # Geographic window
    lon_w = window["lon_w"]
    lon_e = window["lon_e"]
    lat_n = window["lat_n"]
    lat_s = window["lat_s"]

    # Convert to pixel coordinates

    # 1. Create the transformer from Lat/Lon to the File's CRS
    # always_xy=True ensures we use (Lon, Lat) -> (X, Y)
    transformer = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)

    # 2. Convert your window coordinates to the Projection's units (Meters)
    x_w, y_n = transformer.transform(window['lon_w'], window['lat_n'])
    x_e, y_s = transformer.transform(window['lon_e'], window['lat_s'])

    # 3. Get the pixel indices using the transformed Meter values
    row_start, col_start = src.index(x_w, y_n)
    row_stop,  col_stop  = src.index(x_e, y_s)

    # 4. Handle potential axis flipping (North-up rasters)
    r_min, r_max = sorted([row_start, row_stop])
    c_min, c_max = sorted([col_start, col_stop])

    # 5. Read the data
    data = src.read(1)[r_min:r_max, c_min:c_max]

    # Compute physical extents (in km)
    dy = geodesic((lat_n, lon_w), (lat_s, lon_w)).km
    dx = geodesic((lat_n, lon_w), (lat_n, lon_e)).km
    extent = (0, dx, 0, dy)

    # --- COVER CASE -----------------------------------------------------
    if legend is not None and colors is not None:

        # Sorted classes for stable color order
        classes = sorted(legend.keys())

        # Build color list in class-code order
        rgb_255 = [colors[legend[c]] for c in classes]
        rgb_norm = [(r/255, g/255, b/255) for r,g,b in rgb_255]

        cmap = ListedColormap(rgb_norm)

        # Normalize data to class indices
        class_to_index = {c: i for i, c in enumerate(classes)}
        idx_data = np.vectorize(lambda x: class_to_index.get(x, 0))(data)
        # idx_data = np.vectorize(class_to_index.get)(data)

        plt.imshow(idx_data, cmap=cmap,
                   interpolation='nearest', extent=extent)

        # Colorbar with labels
        cbar = plt.colorbar(
            ticks=list(range(len(classes))),
            shrink=0.6
        )
        cbar.ax.set_yticklabels([legend[c] for c in classes])

    # --- ELEVATION CASE -----------------------------------------------------
    else:        
        plt.imshow(data, extent=extent, cmap="terrain")
        plt.colorbar(label="Elevation (m)", shrink=0.6)

    if marks:
        (lat1, lon1), (lat2, lon2) = marks

        x1k = geodesic((lat_n, lon_w), (lat_n, lon1)).km
        y1k = geodesic((lat_s, lon_w), (lat1, lon_w)).km

        x2k = geodesic((lat_n, lon_w), (lat_n, lon2)).km
        y2k = geodesic((lat_s, lon_w), (lat2, lon_w)).km

        plt.plot([x1k, x2k], [y1k, y2k], color="black", linewidth=2)

        dx = x2k - x1k
        dy = y2k - y1k

        f = 0.12  # arrow size as fraction of link length

        plt.annotate(
            "",
            xy=(x1k + f*dx, y1k + f*dy),
            xytext=(x1k, y1k),
            arrowprops=dict(arrowstyle="-|>", color="blue", lw=2),
            zorder=3,
        )

        # RX: outward away from TX
        plt.annotate(
            "",
            xy=(x2k, y2k),
            xytext=(x2k - f*dx, y2k - f*dy),
            arrowprops=dict(arrowstyle="-|>", color="red", lw=2),
            zorder=3,
        )


    # Common labels
    plt.xlabel("kilometers")
    plt.ylabel("kilometers")

    # Title
    plt.title(
        f"N={round(lat_n,4)} W={round(lon_w,4)} "
        f"S={round(lat_s,4)} E={round(lon_e,4)}"
    )

    # --- Return PNG as base64 (Docker/REST-safe) -------------------------
    from io import BytesIO
    import base64

    buf = BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight")
    buf.seek(0)
    plt.close()

    return base64.b64encode(buf.read()).decode("utf-8")

def plot_profile(
    d, h, h_t, h_b, cover,
    legend, colors,
    los=None,
    **kwargs
):
    """
    Plot an elevation/coverage profile.

    Parameters
    ----------
    d : list[float]
        Distance (meters).
    h : list[float]
        Ground elevation.
    h_t : list[float]
        Tree elevation curve.
    h_b : list[float]
        Building elevation curve.
    cover : list[int]
        Coverage class codes along the path.
    legend : dict[int -> str]
        Mapping cover code → semantic name.
    colors : dict[str -> (r,g,b)]
        Mapping semantic name → RGB 0-255.
    los : list[float], optional
        Line-of-sight curve.
    dpi : int, optional
        Figure DPI (REST).
    figsize : tuple(px_w, px_h), optional
        Figure pixel size (REST).
    return base64-encoded PNG.
    """
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from io import BytesIO
    import base64
    from matplotlib.colors import Normalize


    # --- Figure setup ---------------------------------------------------
    figsize = kwargs.get('figsize', None)
    dpi = kwargs.get('dpi', None)
    fig = plt.figure(figsize=figsize, dpi=dpi)

    # --- Elevation curves ----------------------------------------------
    plt.plot(d, h, label="Elevation", color="black")

    used_classes = sorted(set(cover))

    legend_to_key = {v:k for k,v in legend.items()}

    # Tree curve only if class 10 exists
    if legend_to_key['tree_cover'] in used_classes:
        plt.plot(d, h_t, "g--", label="Trees")

    # Building curve only if class 50 exists
    if legend_to_key['built_up'] in used_classes:
        plt.plot(d, h_b, "r--", label="Build-up")

    # LOS curve
    if los is not None:
        plt.plot(d, los, "b--", label="Line-of-sight")

    # --- Coverage scatter ----------------------------------------------
    scatter_colors = []
    for c in cover:
        name = legend[c]
        r, g, b = colors[name]
        scatter_colors.append((r/255, g/255, b/255))

    plt.scatter(d, h, c=scatter_colors, marker='s', s=18)

    # --- Colorbar construction -----------------------------------------
    # Build categorical colormap for the legend only
    legend_colors = []
    tick_labels = []
    for code in used_classes:
        name = legend[code]
        r, g, b = colors[name]
        legend_colors.append((r/255, g/255, b/255))
        tick_labels.append(name)

    cmap = ListedColormap(legend_colors)
    # --- Colorbar construction -----------------------------------------

    # Build the mappable with a proper norm
    norm = Normalize(vmin=0, vmax=len(legend_colors))
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])  # Ensures compatibility across Matplotlib versions

    ax = plt.gca()  # Explicit Axes is required in headless backends

    # Center ticks in each color
    #ticks = [ (i + 0.5) / len(legend_colors) for i in range(len(legend_colors))  ]
    N = len(used_classes)
    ticks = [i + 0.5 for i in range(N)]



    cbar = plt.colorbar(
        sm,
        ax=ax,
        ticks=ticks,
        shrink=0.6
    )

    cbar.ax.set_yticklabels(tick_labels)

    # --- Axes labels ----------------------------------------------------
    plt.xlabel("distance (meters)")
    plt.ylabel("elevation (m)")
    plt.title("Elevation and Coverage Profile")
    plt.grid()
    plt.legend()


    # Return PNG as base64 (REST API)
    buf = BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', pad_inches=0.1)
    buf.seek(0)
    img_data = base64.b64encode(buf.read()).decode('utf-8')
    plt.close(fig)
    return img_data

def tile_direction(tx, rx):
    tx_lat, tx_lon = tx
    rx_lat, rx_lon = rx

    dlon = rx_lon - tx_lon
    dlat = rx_lat - tx_lat

    if abs(dlon) >= abs(dlat):
        return "horizontal", ("tx_rx" if dlon >= 0 else "rx_tx")
    else:
        return "vertical", ("tx_rx" if dlat <= 0 else "rx_tx")
    
def join_img_str(img_str1, img_str2, direction="horizontal"):
    img1 = Image.open(BytesIO(base64.b64decode(img_str1)))
    img2 = Image.open(BytesIO(base64.b64decode(img_str2)))

    img1 = img1.convert("RGB")
    img2 = img2.convert("RGB")

    if direction == "horizontal":
        new_w = img1.width + img2.width
        new_h = max(img1.height, img2.height)
        out = Image.new("RGB", (new_w, new_h))
        out.paste(img1, (0, 0))
        out.paste(img2, (img1.width, 0))

    elif direction == "vertical":
        new_w = max(img1.width, img2.width)
        new_h = img1.height + img2.height
        out = Image.new("RGB", (new_w, new_h))
        out.paste(img1, (0, 0))
        out.paste(img2, (0, img1.height))

    else:
        raise ValueError("direction must be 'horizontal' or 'vertical'")

    buf = BytesIO()
    out.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")