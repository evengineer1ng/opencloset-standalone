"""narrative/knower.py - Re-export from package root for structured access."""
from plugins.neikos import generate_hidden_knower, HiddenKnower, KnowerArchetype

__all__ = ["generate_hidden_knower", "HiddenKnower", "KnowerArchetype"]
