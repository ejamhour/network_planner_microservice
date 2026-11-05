from concurrent.futures import wait
from threading import Event
from types import SimpleNamespace

import pytest

from cisei_planning_sdk import mesh_progress


@pytest.mark.parametrize('fails', [False, True])
def test_updates_one_notebook_box_and_preserves_planner_result(monkeypatch, fails):
    ipython = pytest.importorskip('IPython')
    from IPython import display

    updates = []
    opened = []
    poll_seen = Event()
    run_id = {}
    monkeypatch.setattr(ipython, 'get_ipython', lambda: SimpleNamespace(kernel=True))
    monkeypatch.setattr(mesh_progress, 'wait', lambda futures, timeout: wait(futures, timeout=0.01))

    def show(html, *, display_id):
        assert display_id is True
        opened.append(html.data)
        return SimpleNamespace(update=lambda new: updates.append(new.data))

    def request(method, path, *, headers, **kwargs):
        if method == 'post':
            run_id.update(headers)
            assert poll_seen.wait(5)
            if fails:
                raise RuntimeError('RF failed <detail>')
            return {'data': {'result': 'unchanged'}}
        assert path.endswith('/progress')
        assert headers == run_id
        poll_seen.set()
        return {'data': dict(status='running', stage='link_study', required=3,
                             connected=1, pending=2, repeaters=0, steps=0,
                             source='a:d0:i0', destination='b:d0:i0', attempt=1, attempts=2)}

    monkeypatch.setattr(display, 'display', show)
    client = SimpleNamespace(_request=request)
    if fails:
        with pytest.raises(RuntimeError, match='RF failed'):
            mesh_progress.run_with_progress(client, '/mesh/add_repeaters', {})
        assert 'RF failed &lt;detail&gt;' in updates[-1]
    else:
        assert mesh_progress.run_with_progress(client, '/mesh/add_repeaters', {}) == {'result': 'unchanged'}
        assert 'Complete' in updates[-1]
    assert len(opened) == 1
    assert any('1 / 3 required interfaces connected' in html for html in updates)
    assert any('a:d0:i0 → b:d0:i0' in html for html in updates)
