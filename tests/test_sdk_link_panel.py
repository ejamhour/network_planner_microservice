from types import SimpleNamespace
from unittest.mock import Mock

from cisei_planning_sdk.link_panel import LinkInspectionPanel


def make_panel(edges=None):
    scenario = Mock()
    geo = SimpleNamespace(**{name: Mock(return_value={"view": name}) for name in
                            ["link_profile", "link_area", "lulc_fresnel", "dem_profiles",
                             "bldg_prepare", "bldg_fresnel", "bldg_profile", "link_features"]})
    edges = edges if edges is not None else [
        {"src": "tower:d0:i0", "dst": "client:d0:i0"},
        {"src": "tower:d0:i1", "dst": "other:d0:i0"},
    ]
    panel = LinkInspectionPanel(scenario, geo, edges)
    return panel, scenario, geo


def test_selection_navigation_and_shared_session_inspection():
    panel, scenario, geo = make_panel()
    assert panel.current["dst"] == "client:d0:i0"
    assert panel.previous.disabled and not panel.next.disabled
    panel.next.click()
    assert panel.links.value == 1 and panel.next.disabled
    panel.previous.click()
    assert panel.links.value == 0
    panel.links.value = 1
    panel.view.value = "terrain"
    geo.link_area.assert_called_once_with("DTM")
    # Another cell can change geo: every view/refresh must prepare our link.
    scenario.link.reset_mock()
    panel.refresh.click()
    scenario.link.assert_called_once_with(geo, "tower:d0:i1", "other:d0:i0")
    panel.view.value = "building_heights"
    geo.bldg_prepare.assert_called_once()
    geo.bldg_profile.assert_called_once()


def test_failed_preparation_preserves_selection_and_can_retry():
    panel, scenario, geo = make_panel()
    scenario.link.side_effect = RuntimeError("service unavailable <retry>")
    panel.next.click()
    assert panel.current["dst"] == "client:d0:i0"
    assert panel.links.value == 0
    assert "&lt;retry&gt;" in panel.status.value
    assert not panel.links.disabled
    assert geo.link_profile.call_count == 1
    scenario.link.side_effect = None
    panel.next.click()
    assert panel.links.value == 1


def test_failed_view_retains_new_link_and_reenables_controls():
    panel, scenario, geo = make_panel()
    geo.link_profile.side_effect = RuntimeError("plot unavailable")
    panel.next.click()
    assert panel.current["dst"] == "other:d0:i0"
    assert "plot unavailable" in panel.status.value
    assert not panel.refresh.disabled
    geo.link_profile.side_effect = None
    panel.refresh.click()
    assert "Could not" not in panel.status.value


def test_empty_list_makes_no_service_calls():
    panel, scenario, geo = make_panel([])
    scenario.link.assert_not_called()
    geo.link_profile.assert_not_called()
    assert panel.links.disabled and panel.previous.disabled and panel.next.disabled
    assert panel.current is None


def test_notebook_adapter_is_unwrapped_without_owning_session():
    panel, scenario, geo = make_panel()
    adapter = SimpleNamespace(client=geo)
    second = LinkInspectionPanel(scenario, adapter, panel.browser.edges)
    assert second.browser.geo is geo


def test_endpoint_filter_supports_typed_ids_incoming_links_and_navigation():
    edges = [
        {"src": "tower:d0:i0", "dst": "a:d0:i0"},
        {"src": "other:d0:i0", "dst": "b:d0:i0"},
        {"src": "a:d0:i0", "dst": "tower:d0:i1"},
        {"src": "other:d0:i0", "dst": "a:d0:i0"},
    ]
    panel, scenario, geo = make_panel(edges)
    assert panel.endpoint.value == "tower"
    assert list(panel.links.options) == [(panel._label(i, edges[i]), i) for i in (0, 2)]
    panel.next.click()
    assert panel.links.value == 2
    scenario.link.reset_mock()
    panel.endpoint.value = "a"
    assert panel._visible_indices == [0, 2, 3]
    scenario.link.assert_not_called()  # Filtering alone does not extract profiles.
    assert panel.links.value == 2
    panel.next.click()
    assert panel.links.value == 3 and panel.next.disabled
    panel.endpoint.value = "missing"
    assert panel.links.value is None and panel.refresh.disabled
    assert "No links" in panel.status.value
    panel.endpoint.value = ""
    assert panel._visible_indices == [0, 1, 2, 3]
    assert "4 of 4" in panel.count.value


def test_failure_after_switching_endpoint_does_not_restore_hidden_link():
    panel, scenario, geo = make_panel()
    panel.endpoint.value = "other"
    scenario.link.side_effect = RuntimeError("failed")
    panel.refresh.click()
    assert panel.links.value is None
    assert "Could not" in panel.status.value
    panel.endpoint.value = "tower"
    assert panel.links.value == 0


def test_endpoint_dropdown_and_text_stay_synchronized_and_compact():
    panel, scenario, geo = make_panel()
    panel.endpoint_list.value = "other"
    assert panel.endpoint.value == "other"
    assert panel._visible_indices == [1]
    panel.endpoint.value = "client"
    assert panel.endpoint_list.value == "client"
    assert panel._visible_indices == [0]
    panel.endpoint.value = "missing"
    assert panel.endpoint_list.value is None
    assert not panel._visible_indices
    panel.endpoint_list.value = ""
    assert panel.endpoint.value == ""
    assert panel._visible_indices == [0, 1]
    assert panel.endpoint_list.layout.width == "320px"
    assert panel.links.rows <= 6
