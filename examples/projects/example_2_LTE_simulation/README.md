# Example 2 — LTE 450 simulation

Open `../../notebooks/example_2_LTE_simulation.ipynb` and run its cells in order.

This project reuses example 1's cell-planning workflow with the corrected COPEL CSV: 86 leaf clients and 5 connected cells. The project CSV maps `lte_cell` to `cell_sector_3x120`, `cell` to `tower`, and `leaf` to `field`. IDs, coordinates, mounting heights, connectivity flags, and rank values are preserved. Each cell has three sectors at 0, 120, and 240 degrees.

All radios use 450 MHz. Transmit powers and the `lte_tower_node` metric are inherited from example 1. The original antenna models do not cover 450 MHz, so this simulation uses assumed fixed gains: 11 dBi for sectors and 0 dBi for clients. Sector beamwidth is used for geometric candidate filtering, with constant gain inside the sector. Replace these assumptions with equipment parameters before using results for an operational study.

The source coordinate formatting was repaired and validated against EPSG:31982 projected coordinates, with maximum disagreement below 0.7 mm. This checks coordinate consistency, not the real-world accuracy of the sites. Site mounting heights (7 m for clients; 31, 42, or 52 m for cells) override antenna defaults.

From the workspace root, run:

```sh
python run_examples.py --notebook example_2_LTE_simulation --workspace . --base-url http://network-service:8080 --geo-url http://features:8080 --features-url http://features:8080
```

Metric computation requires reachable network and geographic features services on the first run. The SDK saves `features.json` and `result.json` in this project. Start with candidate inspection before computing the full dataset.

## Long-running metric requests

Set `PLANNING_WORKER_REQUEST_TIMEOUT=1800` in the network-service container environment and restart the service before opening the notebook. The notebook uses `PlanningClient(..., timeout=1900.0)` and `compute_metrics(geo_timeout=300.0)` with the features URL from `PLANNING_GEO_URL` (default `http://features:8080`). The hub defaults to 600 seconds if its environment setting is omitted. Restarting the network service discards live sessions.

For an HTTP error, inspect `exception.response.text` to distinguish a worker connection failure from a timeout. Updated hubs report request timeouts as HTTP 504 and connection failures as HTTP 502, including the exception type. Check network-service container logs for worker errors or termination.

## Alternative metric: LTE 450 link margin

The active TOML embeds the custom spec also distributed as
`../../metrics/lte450_link_margin.toml`. The metric uses cached FSPL and
terrain diffraction plus scenario antenna gains and downlink transmit power.
It checks uplink with assumed client transmit power 20 dBm, matching this case.
It is intended only for direct cell-to-client candidates, not arbitrary relays.

Illustrative required receive powers are -110 dBm at the client and -115 dBm
at the cell. System loss is 3 dB, a flat unresolved clutter allowance is 6 dB,
and fade reserve is 10 dB. These are editable engineering assumptions, not
manufacturer sensitivity claims. Building and vegetation geometry are not
converted into calibrated losses, and interference, resource allocation,
capacity and throughput are not predicted. Existing fixed antenna gains remain
assumptions. The local features implementation calculates diffraction from DTM,
so the allowance does not double-count building diffraction in that term.

Cost = max(1, 2 - limiting reserved margin / 10 dB). Use rank_threshold=2 for
this direct-link study. Zero reserved margin maps to 2; 10 dB positive margin
to 1; -10 dB deficit to 3. This threshold has no relation to the previous
obstruction score's rank limits 10 or 50. In mesh scenarios accumulated cost
would require separate interpretation. Poor links remain candidates.

To apply to an already-open session, upload the custom TOML as the LTE metric,
rebuild candidates, compute metrics using the exact same features-service URL,
and solve again. Cache entries are reusable; metric changes need no geographic
extraction when all requests match. Save alternative results with a different
filename so the original study remains available. Existing result.json and
features.json were preserved when this metric was introduced.
