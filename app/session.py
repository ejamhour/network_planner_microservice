class UserRuntime:
    """
    Per-worker planning runtime.

    The hub creates one worker process per user/session. This object stores the
    in-memory planning state owned by that worker: prepared scenarios and the
    latest planner execution results. It intentionally does not create or own
    RF/GDAL state; real feature extraction is delegated to the features service
    through the planning routes.
    """

    def __init__(self):
        self.planning_scenarios = {}
        self.planning_planners = {}
