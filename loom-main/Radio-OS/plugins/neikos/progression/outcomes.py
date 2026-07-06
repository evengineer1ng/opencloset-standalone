"""progression/outcomes.py - Re-export from package root for structured access."""
from plugins.neikos import (
    compute_outcome_band, describe_outcome_band,
    compute_narrative_role, NARRATIVE_OUTCOME_ROLES,
)

__all__ = [
    "compute_outcome_band", "describe_outcome_band",
    "compute_narrative_role", "NARRATIVE_OUTCOME_ROLES",
]
