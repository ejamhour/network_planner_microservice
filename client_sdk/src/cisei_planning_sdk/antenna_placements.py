"""Compatibility imports for project placement handling."""

try:
    from ._placements import apply_antenna_placements, placement_source
except ModuleNotFoundError as exc:
    if exc.name != f"{__package__}._placements":
        raise
    from cisei_lib.project_io.placements import apply_antenna_placements, placement_source

__all__ = ["apply_antenna_placements", "placement_source"]
