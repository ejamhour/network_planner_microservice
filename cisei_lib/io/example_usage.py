"""Example usage of the refactored Geo client."""

from geo_service_client import GeoServiceClient


client = GeoServiceClient(base_url="http://localhost:8080", timeout=20)

# Optional, if your backend uses register/user runtime isolation:
# client.register("edgard")

result = client.link(
    tx=(-25.42771011522744, -49.266616179984396),
    rx=(-25.42800000000000, -49.267000000000000),
    tx_ha=7,
    rx_ha=7,
    freq_mhz=900,
)

print(result.kind)
print(result.payload)

features = client.link_features()
print(features.payload)
