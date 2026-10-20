# Example 3 — LTE complemented by Wi-SUN mesh

This directory contains only the editable `extension.toml` recipe. Source and
destination paths are supplied in the notebook. Run example 2 and save its
result before preparing this study. The notebook uses its `result.json` by
default; explicitly choose `result_link_margin.json` if that is the evaluated
baseline you intend to extend. Review the baseline metric and rule threshold.

Ordered first-match rules upgrade LTE clients whose source-result rank <= 2
and move the remaining LTE clients to mesh-only devices. `rank_lte` means
less-than-or-equal, not a technology selector. Towers are unchanged. Profile
and CSV tag predicates are ANDed; matching is against original rows only.
The helper does not invent a device rank if several interface ranks exist;
use tag/profile rules instead for such source profiles.

Preparation generates nodes.csv, a fully resolved scenario.toml, an independent
copy of features.json when available, and replacement_report.json. Existing
outputs cause an error: use a new project destination for another alternative.
Changed devices use the replacement profile's initial connected/can_route/rank
values. The updated good-client rule explicitly marks gateways connected and copies their validated source rank; RPL preserves these initial ranks. Mesh-only devices remain unconnected and have infinite initial rank. Omitted actions still follow profile defaults. The recipe
remains separately editable. Changing it requires preparing a new destination.

Wi-SUN settings are assumptions: 915 MHz, 23 dBm, 3 dBi omni, -105 dBm required
receive power, 3 dB system loss, 6 dB clutter allowance and 10 dB fade reserve.
The link-margin score is not calibrated and does not model interference,
throughput or capacity. Mesh candidates use a 5 km range and six nearest
neighbors per source. LTE candidates preserve the source sector geometry;
zero-cost same-device edges enable LTE-to-mesh forwarding.

New 915 MHz links require features extraction. Existing LTE features can be
reused with the same endpoint coordinates, heights, frequency and service URL.
A copied cache is not a guarantee of a fully cached run.

The notebook's combined rank threshold is 4, an illustrative accumulated routing
cost cutoff permitting additional hops. The replacement rule's threshold 2
selects good primary clients; the combined threshold is not an equivalent RF
service guarantee. Compare connectivity, per-link costs, hop counts and quality
reports; do not infer improvement merely from changing the threshold. Hybrids
that are not used are intentionally retained: automatic pruning is not part of
this generic first-match replacement workflow.

## Connected and rank actions (SDK 0.1.33+)

The good-client rule now sets `connected = true` and `rank = "copy"`.
A rule may instead use a finite numeric rank to make a gateway more or less
attractive. Omitted actions use replacement-profile defaults. Invalid combinations
(finite rank on an unconnected device, or a missing/unusable copied rank) fail
before generated files are written. The report records source rank, assigned
initial rank, initial connectivity, and the rank action.

Wi-SUN candidates now target only unconnected devices. This omits redundant
links between connected gateways but keeps gateway-to-secondary and
secondary-to-secondary candidates. New connected radios inherit the source
primary route as an initial condition; consult the source result for that
upstream path. The derived graph need not draw the preserved LTE upstream edges.

Existing generated scenario.toml/nodes.csv/results are NOT rewritten by editing
the recipe. To test these changed rules, create a new destination project, copy
this extension.toml there, and call prepare() once. Retain the existing study
for comparison.

## Rerunning preparation (SDK 0.1.34+)

`prepare()` reuses an existing generated study when its source inputs, result,
and recipe match. To apply changed rules in the same directory, use
`prepare(result=source.load_result(), overwrite=True)`. This validates first,
retains destination feature extractions and merges source entries, archives old
scenario/nodes/report/results and standard HTML views under preparation_backups,
then regenerates the inputs. Old result.json and standard HTML views are removed
from the active project after backup to avoid showing a stale solution. Reopen
the project and rebuild candidates afterward. No new destination is required.
