"""progression/tiers.py - Re-export from package root for structured access."""
from plugins.neikos import (
    ContainmentTier, TierCharacteristics, TIER_CHARACTERISTICS,
    compute_containment_tier,
)

__all__ = [
    "ContainmentTier", "TierCharacteristics", "TIER_CHARACTERISTICS",
    "compute_containment_tier",
]
