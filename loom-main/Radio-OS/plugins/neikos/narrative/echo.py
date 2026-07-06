"""narrative/echo.py - Re-export from package root for structured access."""
from plugins.neikos import generate_echo_events, EchoEvent

__all__ = ["generate_echo_events", "EchoEvent"]
