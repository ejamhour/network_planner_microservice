# Refactored Geo client

This refactor separates the original notebook client into two layers.

## Files

- `geo_service_client.py`: pure REST client. No Jupyter, no `display()`, no PIL image objects. Use this inside other microservices.
- `async_geo_service_pool.py`: async feature-request pool used by the network service.
- The previous Jupyter adapter and pure-client example are archived in `cisei_lib.legacy.io`.

## Microservice-to-microservice use

```python
from cisei_lib.io.geo_service_client import GeoServiceClient

geo = GeoServiceClient(base_url="http://features-service:8080")

geo.link(
    tx=(-25.42, -49.26),
    rx=(-25.43, -49.27),
    tx_ha=7,
    rx_ha=7,
    freq_mhz=900,
)

features = geo.link_features().payload
```

## Archived notebook adapter

```python
from cisei_lib.legacy.io.geo_notebook import GeoNotebook

geo = GeoNotebook(base_url="http://localhost:8080")
geo.link_features()
```

To get the raw result from a notebook method:

```python
result = geo.link_features(display_result=False)
print(result.payload)
```
