"""core/types.py - Re-export from package root for structured access."""
from plugins.neikos import NkType, TYPE_MATRIX, _build_type_matrix, type_multiplier

__all__ = ["NkType", "TYPE_MATRIX", "_build_type_matrix", "type_multiplier"]
