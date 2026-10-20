"""Local metric-cost browser, sharing the evaluation report controls."""
import copy
from .evaluation_view import EvaluationView


class MetricsView(EvaluationView):
    def __init__(self, metrics):
        if not isinstance(metrics.get("metrics"), list):
            raise ValueError("Metric response requires a metrics list")
        self.metrics = copy.deepcopy(metrics)
        super().__init__({"_report_kind": "metrics", "result_edges": self.metrics["metrics"]})

    def _repr_html_(self):
        return super()._repr_html_().replace('title="Planning evaluation report"', 'title="Link metrics report"', 1)
