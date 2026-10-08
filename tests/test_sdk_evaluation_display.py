from copy import deepcopy
from unittest.mock import Mock, patch
import pandas as pd
from cisei_planning_sdk.session import ScenarioSession
from cisei_planning_sdk.visualization import show_evaluation


def test_displays_summary_and_all_tables_without_mutation():
    report = {'quality_summary': {'good': 2}, 'quality': [{'site_id': 'a'}],
              'service': [], 'result_edges': [{'src': 'a', 'dst': 'b'}],
              'cell': {'client_service': [{'rank': 1}], 'cell_load': [{'served_clients': 2}]}}
    before = deepcopy(report)
    with patch('IPython.display.display') as display:
        assert show_evaluation(report) is None
    tables = [call.args[0] for call in display.call_args_list if isinstance(call.args[0], pd.DataFrame)]
    assert len(tables) == 6
    assert tables[0].to_dict('records') == [{'good': 2}]
    assert report == before


def test_session_evaluates_then_displays_without_saving():
    client = Mock()
    report = {'quality_summary': {'good': 1}}
    client._request.return_value = {'data': report}
    session = ScenarioSession(client=client, name='test', planner='cell', scenario_id='test')
    with patch('cisei_planning_sdk.visualization.show_evaluation') as render:
        assert session.show_evaluation(rank_threshold=2) is None
    assert session.evaluation is report
    render.assert_called_once_with(report)
    client._request.assert_called_once_with('get', '/planning/scenarios/test/evaluation',
        params={'rank_threshold': 2, 'primary_tech': 'lte', 'solution_kind': 'cell'})
