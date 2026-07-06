"""The loom surface's substance is pure + testable; the Tk widgets are a thin shell over it.

(Importing loom.app does NOT import tkinter — it's lazy inside LoomApp.__init__ — so this
runs headless in CI.)
"""
from loom.app import identicon_cells, preview, role_of


def test_preview_generates_oradio_bytes():
    text, n = preview("a quiet house", [{"plugin": "simulated_spatial_array", "name": "array"}])
    assert n > 0 and "simulated_spatial_array" in text


def test_identicon_is_deterministic_and_symmetric():
    a = identicon_cells("hear animal thoughts")
    assert a == identicon_cells("hear animal thoughts")          # same universe, same face
    assert all(row[0] == row[-1] and row[1] == row[-2] for row in a)  # mirrored = a sigil, not noise
    assert identicon_cells("x") != identicon_cells("y")          # different universes, different faces


def test_role_inference_drives_the_badge():
    assert role_of("neikos") == "world"
    assert role_of("simulated_spatial_array") == "source"
    assert role_of("mystery") == "source"                        # default: just emit on the wire


def test_installed_plugins_expose_the_club_for_mixing():
    from loom.app import installed_plugins
    items = installed_plugins()
    names = {i["plugin"] for i in items}
    assert "simulated_spatial_array" in names                    # a known source is browsable
    assert any(i["role"] == "world" for i in items)              # organs show up too
    sensitive = [i for i in items if i["sensitive"]]
    assert sensitive and all(i["reads"] for i in sensitive)      # sensitive ones say what they read
