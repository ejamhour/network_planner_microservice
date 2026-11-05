"""Live notebook controls for studying a replacement path."""

def mesh_path_panel(session, result, geo_base_url, *, node=None):
    import ipywidgets as widgets
    from IPython.display import display
    from .mesh_artifacts import save_mesh_artifacts
    from .mesh_progress import run_expansion
    from .mesh_studies_view import MeshStudiesView, proposal_view

    report = result['mesh_network']
    defaults = session._mesh_network_body()
    path = f'/planning/scenarios/{session.scenario_id}/mesh'
    current = result
    proposal = None
    checkpoint = None
    source = widgets.Dropdown(description='Radio')
    parent = widgets.Dropdown(description='Parent')
    candidates = widgets.BoundedIntText(value=defaults.get('max_candidates', 5),
                                       min=1, max=50, description='Candidates')
    repeaters = widgets.BoundedIntText(value=defaults.get('max_repeaters', 5),
                                      min=0, max=20, description='Repeaters')
    corridor = widgets.FloatText(value=defaults.get('corridor_m', 1500), description='Corridor (m)')
    run = widgets.Button(description='Try path', button_style='primary')
    apply = widgets.Button(description='Apply proposal', disabled=True)
    output = widgets.Output()
    history = widgets.Output()

    def request_options(source_id=None):
        return session.client._request('post', path + '/path_options', json={
            **defaults, 'checkpoint': checkpoint, 'path_source': source_id})['data']

    def refresh():
        nonlocal checkpoint
        checkpoint = dict(current['mesh_network'])
        solution = checkpoint.get('solution_scenario')
        snapshots = checkpoint['iterations']
        if solution and current['planned_edges'] != snapshots[-1]['result']['planned_edges']:
            checkpoint['path_baseline'] = {
                'scenario': {**solution, 'net': {'candidate_edges': current['planned_edges']}},
                'nodes': checkpoint.get('solution_nodes'),
                'result': {k: v for k, v in current.items() if k != 'mesh_network'},
            }
        update_history()

    def update_history():
        steps = current['mesh_network']['steps']
        preferred = source.value or node
        selected = preferred if any(
            step['source'] == preferred or step['source'].rsplit(':d', 1)[0] == preferred
            for step in steps) else None
        with history:
            history.clear_output(wait=True)
            if steps:
                display(MeshStudiesView(current, node=selected))

    def load_parents(_=None):
        run.disabled = True
        apply.disabled = True
        parent.options = []
        if source.value is None:
            return
        update_history()
        with output:
            output.clear_output(wait=True)
            try:
                rows = request_options(source.value)
                parent.options = [(f"{row['id']} · rank {row['rank']:.2f}", row['id']) for row in rows]
                run.disabled = not rows
                if not rows:
                    print('No compatible connected parent outside this branch.')
            except Exception as exc:
                print(str(exc))

    def try_path(_):
        nonlocal proposal, checkpoint
        run.disabled = apply.disabled = source.disabled = parent.disabled = True
        with output:
            output.clear_output(wait=True)
            try:
                print(f'Studying {source.value} via {parent.value}…')
                proposal = run_expansion(session.client, path + '/expansions', {
                    **defaults, 'geo_base_url': geo_base_url, 'checkpoint': checkpoint,
                    'path_source': source.value, 'path_parent': parent.value,
                    'max_candidates': candidates.value, 'max_repeaters': repeaters.value,
                    'corridor_m': corridor.value}, progress=False)
                step = proposal['step']
                trial = step['attempts'][0]
                previous = step['previous_path']
                output.clear_output(wait=True)
                if previous:
                    worst = max(edge['metric'] for edge in previous)
                    print(f"Current path · rank {step['previous_rank']:.2f} · worst segment {worst:.2f}")
                    print(' → '.join([edge['src'] for edge in previous] + [previous[-1]['dst']]))
                else:
                    print('Current path · unconnected')
                display(proposal_view(trial['result'], accepted=trial['accepted'],
                                      total_rank=trial['total_rank']))
                checkpoint = dict(checkpoint, steps=[*checkpoint['steps'], step])
                current['mesh_network'] = dict(current['mesh_network'], steps=checkpoint['steps'])
                session.result = current
                if session.project is not None:
                    session.project.save_result(current)
                update_history()
                apply.disabled = not proposal['can_apply']
                if session.project is not None:
                    session.save_feature_cache()
            except Exception as exc:
                print(str(exc))
            finally:
                run.disabled = source.disabled = parent.disabled = False

    def apply_path(_):
        nonlocal current
        apply.disabled = True
        with output:
            try:
                current = session.client._request('post',
                    f"{path}/expansions/{proposal['run_id']}/apply_path")['data']
                session.result = session.mesh_preparation = current
                session.candidate_edges = session.metrics = session.solution = session.evaluation = None
                if session.project is not None:
                    save_mesh_artifacts(session.project, current)
                    session.save_feature_cache()
                refresh()
                print(f"Applied to iteration {len(checkpoint['iterations']) - 1}.")
            except Exception as exc:
                print(str(exc))
                apply.disabled = False

    refresh()
    source.options = request_options()
    matches = [value for value in source.options if value == node or value.rsplit(':d', 1)[0] == node]
    if len(matches) == 1:
        source.value = matches[0]
    source.observe(load_parents, names='value')
    parent.observe(lambda _: setattr(apply, 'disabled', True), names='value')
    load_parents()
    run.on_click(try_path)
    apply.on_click(apply_path)
    return widgets.VBox([widgets.HTML('<b>Improve path</b>'), widgets.HBox([source, parent]),
                         widgets.HBox([candidates, repeaters, corridor]),
                         widgets.HBox([run, apply]), output, history])
