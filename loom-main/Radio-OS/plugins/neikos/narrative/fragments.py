"""narrative/fragments.py - Re-export from package root for structured access."""
from plugins.neikos import (
    FRAGMENT_POOL, NarrativeFragment, FragmentType, generate_island_fragments,
)

__all__ = ["FRAGMENT_POOL", "NarrativeFragment", "FragmentType", "generate_island_fragments"]
