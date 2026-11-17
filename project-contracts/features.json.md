# Raw and enriched feature caches

`features.json` is a reusable cache generated during feature extraction.
The SDK imports it when opening a project and can save updated entries after
metric computation. A cache hit reuses the raw feature response. Edge metrics
and the topology are computed separately.

The document has `format = "cisei-feature-cache"`, `version = 1`, and an
`entries` array. Each entry contains:

| Field | Meaning |
| --- | --- |
| `source` | Features service URL. A trailing slash is ignored for matching. |
| `request.tx`, `request.rx` | Ordered transmitter and receiver positions, each `[latitude, longitude]` in degrees. |
| `request.tx_ha`, `request.rx_ha` | Endpoint mounting heights in metres. |
| `request.freq_mhz` | Positive link frequency in MHz. |
| `request.options` | Feature extraction options, represented as an object. |
| `features` | Raw features service response object. |

The cache matches on source, direction, positions, heights, frequency, and
options. Changing a metric formula leaves those keys intact. Moving an
antenna or changing its effective mounting height changes the key. Duplicate
keys are rejected.

## Measurements and enrichment

A measurement input uses `format = "cisei-link-measurements"`, `version = 1`,
and a `records` array. Each record carries the extraction `request`, an
optional `source`, and the following observation fields:

| Field | Meaning |
| --- | --- |
| `id` | Unique measurement ID. |
| `tx.name`, `rx.name` | Endpoint names. |
| `tx.pw` | Transmit power in dBm. |
| `tx.ant_gain`, `rx.ant_gain` | Endpoint gains in dBi. |
| `measures.rssi` | Measured RSSI in dBm. |
| `tx.pos`, `rx.pos`, `tx.ant_height`, `rx.ant_height` | Optional observation metadata. When provided, these values must match the extraction request. |

Omitting `source` requires exactly one matching cache entry across sources.
Reversing the direction changes the request. Internal wired connections do
not produce RF observations.

Enrichment writes a separate `enriched_features.json` using the same cache
format, with `observations` arrays on matched entries. Each array contains
the observation fields above. The enclosing cache entry carries the source,
request, and features. Training reads the observations together with those
raw features. See the [baseline recipe](baseline.json.md) and the
[measurement example](../examples/data/e08/measurements.json).
