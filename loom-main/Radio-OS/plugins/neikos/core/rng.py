"""core/rng.py - Re-export from package root for structured access."""
from plugins.neikos import SeededRNG, _det_hash

__all__ = ["SeededRNG", "_det_hash"]
