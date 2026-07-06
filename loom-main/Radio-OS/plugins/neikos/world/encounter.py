"""world/encounter.py - Re-export from package root for structured access."""
from plugins.neikos import (
    generate_encounter_tables, roll_encounter, generate_ai_trainers,
)

__all__ = ["generate_encounter_tables", "roll_encounter", "generate_ai_trainers"]
