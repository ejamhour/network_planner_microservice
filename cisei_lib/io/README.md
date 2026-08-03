# Refactored Geo client

This refactor separates the original notebook client into two layers.

## Files

- `geo_service_client.py`: pure REST client. No Jupyter, no `display()`, no PIL image objects. Use this inside other microservices.
- `geo_notebook.py`: Jupyter adapter. Uses `GeoServiceClient` internally and only handles presentation.
- `example_usage.py`: minimal pure-client example.

## Microservice-to-microservice use

```python
from geo_service_client import GeoServiceClient

geo = GeoServiceClient(base_url="http://planning-service:8080")

geo.link(
    tx=(-25.42, -49.26),
    rx=(-25.43, -49.27),
    tx_ha=7,
    rx_ha=7,
    freq_mhz=900,
)

features = geo.link_features().payload
```

## Notebook use

```python
from geo_notebook import GeoNotebook

geo = GeoNotebook(base_url="http://localhost:8080")
geo.link_features()
```

To get the raw result from a notebook method:

```python
result = geo.link_features(display_result=False)
print(result.payload)
```
