"""server.py - Re-export from package root for structured access."""
from plugins.neikos import _start_web_server

__all__ = ["_start_web_server"]
