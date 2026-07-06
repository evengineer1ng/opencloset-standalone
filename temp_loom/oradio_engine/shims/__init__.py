"""Shims — thin adapters from sovereign backends to the SimulationOrgan contract.

Each shim owns *no* world logic; it translates the five-verb contract onto an
existing engine's real seam (verified against that engine's tests). Adding an organ
to the federation means writing one of these, never rewriting the backend.
"""
