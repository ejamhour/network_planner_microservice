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
