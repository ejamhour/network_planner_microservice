"""Refresh one Jupyter output while a repeater study runs on the service."""
from concurrent.futures import ThreadPoolExecutor, wait
from html import escape
from time import monotonic, sleep
from uuid import uuid4


def _box(state, elapsed):
    stages = {
        'preparing': 'Preparing pole inventory',
        'initial_links': 'Evaluating existing links',
        'selecting_node': 'Selecting next required interface',
        'link_study': 'Studying a candidate parent',
        'finalizing': 'Preparing expanded network',
    }
    status = state.get('status', 'running')
    label = {'complete': 'Complete', 'failed': 'Failed', 'interrupted': 'Notebook wait interrupted'}.get(
        status, stages.get(state.get('stage'), 'Waiting for planner status'))
    if status == 'complete':
        label = {'paused': 'Paused — rerun to expand', 'stalled': 'No more reachable interfaces'}.get(
            state.get('expansion_status'), label)
    lines = [f'<strong>Add repeaters — {escape(label)}</strong>']
    if 'required' in state:
        required = state['required']
        connected = state['connected']
        pending_label = 'unserved' if status == 'complete' and state.get('expansion_status') != 'paused' else 'pending'
        lines.append(
            f'<progress value="{connected}" max="{max(1, required)}"></progress> '
            f'{connected} / {required} required interfaces connected · {state["pending"]} {pending_label}'
        )
        lines.append(f'{state["repeaters"]} repeaters selected · {state["steps"]} node studies finished')
    if state.get('source'):
        lines.append(f'{escape(state["source"])} → {escape(state["destination"])} '
                     f'(parent {state["attempt"]} of {state["attempts"]})')
    minutes, seconds = divmod(int(elapsed), 60)
    clock = f'Elapsed: {minutes} min {seconds:02d} s'
    if 'last_update_s' in state and status == 'running':
        clock += f' · Last planning update: {int(state["last_update_s"])} s ago'
    lines.append(clock)
    if state.get('error'):
        lines.append(escape(state['error']))
    return '<div style="border:1px solid #888;padding:10px;border-radius:5px">' + '<br>'.join(lines) + '</div>'


def run_with_progress(client, path, body):
    try:
        from IPython import get_ipython
        from IPython.display import HTML, display
    except ImportError:
        return client._request('post', path, json=body)['data']
    if not getattr(get_ipython(), 'kernel', None):
        return client._request('post', path, json=body)['data']

    headers = {'X-Mesh-Run-ID': uuid4().hex}
    started = monotonic()
    state = {}
    handle = display(HTML(_box(state, 0)), display_id=True)

    def refresh():
        try:
            update = client._request('get', path + '/progress', headers=headers, timeout=3)['data']
        except Exception:
            # A slow or older server must not affect the planning request.
            return
        if update.get('status') != 'waiting':
            state.update(update)

    def render():
        handle.update(HTML(_box(state, monotonic() - started)))

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(client._request, 'post', path, json=body, headers=headers)
    try:
        while not wait([future], timeout=2).done:
            refresh()
            render()
        result = future.result()['data']
        refresh()
        state.update(status='complete')
        render()
        return result
    except BaseException as exc:
        if future.done():
            refresh()
        state.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                     error=str(exc))
        render()
        raise
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def run_expansion(client, path, body, *, progress=False):
    """Wait using short requests; RF work is owned by the server-side batch."""
    from requests import ConnectionError, HTTPError, Timeout

    handle = None
    started = monotonic()
    state = {}
    if progress:
        try:
            from IPython import get_ipython
            from IPython.display import HTML, display
            if getattr(get_ipython(), 'kernel', None):
                handle = display(HTML(_box(state, 0)), display_id=True)
        except ImportError:
            pass

    def render():
        if handle is not None:
            handle.update(HTML(_box(state, monotonic() - started)))

    def poll(url, **kwargs):
        for attempt in range(5):
            try:
                return client._request('get', url, **kwargs)['data']
            except (ConnectionError, Timeout, HTTPError) as exc:
                if isinstance(exc, HTTPError) and exc.response.status_code not in {502, 503, 504}:
                    raise
                if attempt == 4:
                    raise
                sleep(1)

    try:
        run_id = client._request('post', path, json=body, timeout=60)['data']['run_id']
        job = f'{path}/{run_id}'
        while True:
            state = poll(job, timeout=10)
            if state['status'] == 'complete':
                result = poll(job, params={'result': 'true'}, timeout=60)['result']
                render()
                return result
            if state['status'] in {'failed', 'cancelled'}:
                raise RuntimeError(state.get('error', 'Expansion cancelled by worker'))
            render()
            sleep(1)
    except BaseException as exc:
        state.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                     error=str(exc))
        render()
        raise
