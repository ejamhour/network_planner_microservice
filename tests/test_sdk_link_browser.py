from copy import deepcopy
from unittest.mock import Mock
import pytest
from cisei_planning_sdk.session import ScenarioSession


@pytest.fixture
def session():
    config = {"sites": [
        {"site_id": label, "lat": lat, "lon": lon,
         "devices": [{"device_id": label + '-device', "mount_height_m": height}],
         "interfaces": [{"interface_id": label + '-if', "device_id": label + '-device',
                         "tech": 'lte', "freq_mhz": 915}]}
        for label, lat, lon, height in [('a', -25, -49, 31), ('b', -26, -48, 7)]
    ]}
    client = Mock()
    client._request.return_value = {"data": config}
    return ScenarioSession(client=client, name='test', planner='graph', scenario_id='test')


def test_link_uses_current_configuration_and_does_not_own_geo(session):
    geo = Mock()
    result = session.link(geo, 'a-if', 'b-if')
    geo.link.assert_called_once_with((-25, -49), (-26, -48), tx_ha=31, rx_ha=7, freq_mhz=915)
    assert result is geo.link.return_value
    geo.register.assert_not_called()
    geo.close_worker.assert_not_called()
    session.client._request.return_value['data']['sites'][0]['devices'][0]['mount_height_m'] = 42
    session.link(geo, 'a-if', 'b-if')
    assert geo.link.call_args.kwargs['tx_ha'] == 42


@pytest.mark.parametrize('field,value', [('tech', 'wisun'), ('freq_mhz', 900)])
def test_incompatible_interfaces_do_not_call_features(session, field, value):
    session.client._request.return_value['data']['sites'][1]['interfaces'][0][field] = value
    geo = Mock()
    with pytest.raises(ValueError, match='mismatch'):
        session.link(geo, 'a-if', 'b-if')
    geo.link.assert_not_called()


def test_unknown_interface_and_missing_height(session):
    geo = Mock()
    with pytest.raises(ValueError, match='Unknown interface'):
        session.link(geo, 'a', 'b-if')
    del session.client._request.return_value['data']['sites'][0]['devices'][0]['mount_height_m']
    with pytest.raises(ValueError, match='mount height'):
        session.link(geo, 'a-if', 'b-if')
    geo.link.assert_not_called()


@pytest.mark.parametrize('key', ['candidate_edges', 'planned_edges'])
def test_browser_accepts_both_lists_and_preserves_input(session, key):
    response = {key: [{'src': 'a-if', 'dst': 'b-if', 'metric': 2}, {'src': 'b-if', 'dst': 'a-if'}]}
    before = deepcopy(response)
    geo = Mock()
    browser = session.browse_links(geo, response[key])
    assert browser.current is None
    geo.link.assert_not_called()
    browser.next()
    assert browser.current == response[key][0]
    browser.next()
    assert browser.index == 1
    with pytest.raises(StopIteration):
        browser.next()
    browser.previous()
    assert browser.index == 0
    assert response == before


def test_failed_selection_keeps_position(session):
    geo = Mock()
    browser = session.browse_links(geo, [{'src': 'a-if', 'dst': 'b-if'}, {'src': 'missing', 'dst': 'b-if'}])
    browser.next()
    with pytest.raises(ValueError):
        browser.next()
    assert browser.index == 0
    geo.link.side_effect = RuntimeError('service unavailable')
    with pytest.raises(RuntimeError):
        browser.select(0)
    assert browser.index == 0


def test_internal_empty_and_invalid_edges(session):
    geo = Mock()
    with pytest.raises(ValueError, match='Internal'):
        session.browse_links(geo, [{'src': 'a-if', 'dst': 'b-if', 'kind': 'internal'}]).next()
    with pytest.raises(StopIteration):
        session.browse_links(geo, []).next()
    with pytest.raises(ValueError):
        session.browse_links(geo, [{'src': 'a-if'}])
    geo.link.assert_not_called()


def test_candidate_links_retrieve_and_filter_without_building(session):
    rows = [{'src': 'a-if', 'dst': 'b-if', 'kind': 'rf'},
            {'src': 'a-if', 'dst': 'a-other', 'kind': 'internal'}]
    session.client._request.return_value = {'data': {'candidate_edges': rows}}
    links = session.get_candidate_links()
    assert links == rows[:1]
    links[0]['src'] = 'changed'
    assert rows[0]['src'] == 'a-if'
    assert session.client._request.call_args.args == ('get', '/planning/scenarios/test/candidate_edges')


def test_solution_links_filter_internal_edges_without_kind(session):
    session.client._request.side_effect = [
        {'data': {'planned_edges': [{'src': 'a-if', 'dst': 'b-if', 'metric': 2},
                                   {'src': 'a-other', 'dst': 'a-if', 'metric': 0}]}},
        {'data': {'candidate_edges': [{'src': 'a-if', 'dst': 'b-if', 'kind': 'rf'},
                                     {'src': 'a-if', 'dst': 'a-other', 'kind': 'internal'}]}},
    ]
    assert session.get_solution_links() == [{'src': 'a-if', 'dst': 'b-if', 'metric': 2}]
    assert all(call.args[0] == 'get' for call in session.client._request.call_args_list)
