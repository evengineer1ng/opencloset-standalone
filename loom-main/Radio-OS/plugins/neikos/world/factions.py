"""world/factions.py - Re-export from package root for structured access."""
from plugins.neikos import (
    generate_factions, diffuse_faction_influence, compute_gate_thresholds,
)

__all__ = ["generate_factions", "diffuse_faction_influence", "compute_gate_thresholds"]
