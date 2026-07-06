"""progression/ngp.py - Re-export from package root for structured access."""
from plugins.neikos import (
    BehavioralProfileSignature, compute_behavioral_signature,
    save_behavioral_profile, load_behavioral_profile,
    apply_profile_to_island,
)

__all__ = [
    "BehavioralProfileSignature", "compute_behavioral_signature",
    "save_behavioral_profile", "load_behavioral_profile",
    "apply_profile_to_island",
]
