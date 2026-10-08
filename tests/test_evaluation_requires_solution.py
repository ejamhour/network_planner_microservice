from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from app.routes_planning import _evaluation_payload, _solution_response


def test_evaluation_rejects_unsolved_planner_before_serializing_initial_state():
    planner = SimpleNamespace(rpl=None, to_result_dict=Mock())
    with pytest.raises(RuntimeError, match='No saved solution'):
        _evaluation_payload(planner, rank_threshold=2, primary_tech='lte')
    planner.to_result_dict.assert_not_called()


def test_solution_retrieval_rejects_unsolved_planner():
    planner = SimpleNamespace(rpl=None, to_result_dict=Mock())
    with pytest.raises(RuntimeError, match='No saved solution'):
        _solution_response(planner)
    planner.to_result_dict.assert_not_called()
