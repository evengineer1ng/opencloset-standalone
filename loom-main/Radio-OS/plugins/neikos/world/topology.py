"""world/topology.py - Re-export from package root for structured access."""
from plugins.neikos import (
    generate_island_topology, generate_island_name,
    _region_biome, _add_edge, _generate_name,
    _SYLLABLES_A, _SYLLABLES_B, _REGION_BIOME_OFFSETS,
)

__all__ = [
    "generate_island_topology", "generate_island_name",
]
