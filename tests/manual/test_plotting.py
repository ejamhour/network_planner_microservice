from cisei_lib.core.profiles.geo_info import geoInfo
import matplotlib.pyplot as plt
from pathlib import Path

from cisei_lib.core.plot.geo_plot import plot_profile
import base64
from pathlib import Path

def test_fake_profile():
    d = [0, 100, 200, 300]
    h = [100, 105, 110, 120]
    h_t = [110, 115, 120, 130]
    h_b = [102, 108, 112, 122]
    cover = [10, 10, 50, 50]

    legend = {
        10: "tree_cover",
        50: "built_up"
    }

    colors = {
        "tree_cover": (0, 100, 0),
        "built_up":   (255, 0, 0)
    }

    # Generate PNG inside Docker
    img_b64 = plot_profile(
        d, h, h_t, h_b, cover,
        legend, colors, los=None
    )

    out = Path(__file__).parent / "fake_profile.png"
    out.write_bytes(base64.b64decode(img_b64))
    print("Saved:", out)



def test_plots():
    gi = geoInfo()

    here = Path(__file__).parent

    start = (-26.178913, -53.072063)
    end   = (-26.161617, -53.015026)

    print("Saving COVER map...")
    img = gi.show_map(start, end, cover=True)
    (here / "cover_map.png").write_bytes(base64.b64decode(img))

    print("Saving ELEVATION map...")
    img = gi.show_map(start, end, cover=False)
    (here / "elevation_map.png").write_bytes(base64.b64decode(img))

    print("Saving LINK PROFILE...")
    img = gi.show_link(start, end, ha_s=7, ha_d=7)
    (here / "profile.png").write_bytes(base64.b64decode(img))

    print("Saved images in:", here)

if __name__ == "__main__":
    test_plots()
    # test_fake_profile()

