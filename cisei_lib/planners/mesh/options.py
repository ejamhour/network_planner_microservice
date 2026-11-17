"""Shared mesh option definitions for HTTP requests and staged input checks."""
from typing import Literal

from pydantic import BaseModel, Field


class MeshLinkOptions(BaseModel):
    max_repeaters: int = Field(default=5, ge=0, le=20)
    max_candidates: int = Field(default=5, ge=1, le=50)
    segment_threshold: float = Field(default=2.0, ge=1, allow_inf_nan=False)
    search_heuristic: Literal["sector_band", "dual_cone"] = "sector_band"
    search_midpoint: bool = True
    assume_symmetric_features: bool = False


class MeshNetworkOptions(MeshLinkOptions):
    parent_candidates: int = Field(default=3, ge=1, le=20)
    corridor_m: float = Field(default=1500.0, ge=0, allow_inf_nan=False)
    final_degree: int = Field(default=3, ge=1, le=50)
