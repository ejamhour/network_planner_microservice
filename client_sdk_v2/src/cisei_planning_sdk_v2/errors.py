"""Structured conditions for caller/UI decisions, without interactive prompts."""
import requests


class ActiveProjectExists(requests.HTTPError):
    """Close the named active project explicitly before opening another."""
    def __init__(self, detail, *, response=None):
        self.detail = detail
        self.active_project_id = detail.get("active_project_id")
        super().__init__(detail["message"], response=response)
