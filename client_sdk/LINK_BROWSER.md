# Link inspection in Jupyter

Install `cisei-planning-sdk[notebook]` (0.1.24 or later). Restart the notebook
kernel after upgrading. Jupyter widget support is included in the notebook extra.

With an existing scenario and caller-owned features client, open the panel:

```python
candidate_panel = scenario.show_link_browser(geo, scenario.get_candidate_links())
```

For an already solved planning:

```python
solution_panel = scenario.show_link_browser(geo, scenario.get_solution_links())
```

Select any link in the list or use Previous and Next. The first link's profile
is shown when the panel opens. The Show menu switches between link profile,
land cover/terrain/surface maps, land cover along the link, terrain and surface
profiles, building footprints, building heights, and link features. The Show
button repeats the current inspection. Output updates in one area.

The selected link dictionary is available as `candidate_panel.current` or
`solution_panel.current`. Interface IDs appear in the list to distinguish
multiple links between the same sites. Empty lists disable the controls.
Service errors appear in the panel and can be retried; failed link preparation
preserves the previous selection.

The panel uses the existing features client and session. It does not register
or close that session, compute network metrics, or solve a planning. Each
inspection prepares its selected link again, since another cell or panel may
have changed the shared features session. Interactive controls require a live
notebook kernel. The original `browse_links()` API remains available.

`get_solution_links()` currently reads the live worker's solution; loading a
local result file alone does not restore that worker's solution.
