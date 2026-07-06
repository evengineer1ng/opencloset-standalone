"""loom — the two-question surface that generates a .oradio.

Not an app so much as a declaration with a face. Two questions:
    1. universe     (natural language — the intent)
    2. connections  (the parts — plugins)
Press generate, get one tiny .oradio. That's it.

The substance (preview / seed fingerprint / byte count / role inference) lives in pure
functions below so it can be tested headless; the Tk widgets are a thin shell over them.
Tkinter is stdlib, so this same surface runs anywhere Python does — which is the trippy
part: any device with compute can mint .oradios.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from loom.dotloom import Connection, Loom, loom_to_oradio, universe_seed

# ── palette (charcoal / cyan — the Radio OS aesthetic) ──────────────────────── #
BG, PANEL, FIELD, LINE = "#0e1013", "#16191f", "#0b0c0f", "#2a2f37"
TEXT, MUTED, ACCENT = "#eef1f4", "#8b94a3", "#5fd2e3"
ROLE_COLOR = {"world": "#e3b25f", "source": "#5fd2e3", "effector": "#d27fe0"}

UNIVERSE_EXAMPLES = [
    "a quiet house that notices me",
    "a universe where I can hear the thoughts of animals",
    "my trading book, but it talks back",
    "Westworld, if it forked here",
    "a seed in a dish, mutating until I see something",
    "hockey night, narrated to life — in Japanese",
]


# ── pure substance (testable without a display) ─────────────────────────────── #
def preview(universe: str, connections: List[Dict[str, Any]]) -> Tuple[str, int]:
    """The generated .oradio as YAML text + its byte size (tooth-and-nail, shown live)."""
    oradio = loom_to_oradio(Loom(
        universe=universe.strip(),
        connections=[Connection.parse(c) for c in connections],
    ))
    try:
        import yaml  # type: ignore
        text = yaml.safe_dump(oradio, sort_keys=False, allow_unicode=True)
    except ImportError:
        import json
        text = json.dumps(oradio, indent=2, ensure_ascii=False)
    return text, len(text.encode("utf-8"))


def identicon_cells(universe: str, grid: int = 5) -> List[List[str]]:
    """A deterministic visual fingerprint of the universe's seed — its face.

    Same universe -> same glyph (the seed made visible). Mirrored left/right so it reads
    as a face/sigil, not noise.
    """
    seed = universe_seed(universe)
    # hue from the seed; cells lit by the seed's bits, mirrored for symmetry
    hue = seed % 360
    on = _hsl_hex(hue, 0.55, 0.62)
    off = PANEL
    bits = seed
    cells = [[off] * grid for _ in range(grid)]
    half = (grid + 1) // 2
    for r in range(grid):
        for c in range(half):
            bits, lit = bits >> 1, bits & 1
            color = on if lit else off
            cells[r][c] = color
            cells[r][grid - 1 - c] = color
    return cells


def role_of(plugin: str, as_role: str = "") -> str:
    return Connection(plugin=plugin, as_role=as_role).role()


def installed_plugins() -> List[Dict[str, Any]]:
    """The club's currently-registered plugins — so the author can mix & match, not guess names.

    Reads the live registry: every world organ + telemetry source the engine knows how to
    build. Sensitive sources advertise what they'd read (the consent handshake), surfaced here
    so the palette can mark them.
    """
    from oradio_engine.registry import ORGAN_KINDS, SOURCE_KINDS, SOURCE_META
    items: List[Dict[str, Any]] = []
    for name in sorted(ORGAN_KINDS):
        items.append({"plugin": name, "role": "world", "sensitive": False, "reads": ""})
    for name in sorted(SOURCE_KINDS):
        m = SOURCE_META.get(name, {})
        items.append({"plugin": name, "role": "source",
                      "sensitive": bool(m.get("sensitive")), "reads": m.get("reads", "")})
    return items


def _hsl_hex(h: float, s: float, l: float) -> str:
    import colorsys
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360.0, l, s)
    return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"


# ── the surface ─────────────────────────────────────────────────────────────── #
class LoomApp:
    def __init__(self) -> None:
        import tkinter as tk
        self.tk = tk
        self.root = tk.Tk()
        self.root.title("loom")
        self.root.configure(bg=BG)
        self.root.geometry("980x760")

        self._conn_rows: List[Dict[str, Any]] = []
        self._ph_i = 0

        self._build()
        self._cycle_placeholder()
        self._refresh()

    # -- layout -- #
    def _build(self) -> None:
        tk = self.tk
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=22, pady=20)

        tk.Label(wrap, text="loom", font=("Segoe UI", 26, "bold"), fg=TEXT, bg=BG).pack(anchor="w")
        tk.Label(wrap, text="declare a universe.  connect its parts.  generate an .oradio.",
                 font=("Segoe UI", 11), fg=MUTED, bg=BG).pack(anchor="w", pady=(2, 16))

        # Q1 — universe (the hero) + seed fingerprint
        q1 = self._card(wrap, "1 · universe")
        row = tk.Frame(q1, bg=PANEL); row.pack(fill="x")
        self.universe = tk.Text(row, height=2, wrap="word", bg=FIELD, fg=TEXT,
                                insertbackground=ACCENT, relief="flat", font=("Segoe UI", 15),
                                padx=12, pady=10, highlightthickness=1, highlightbackground=LINE)
        self.universe.pack(side="left", fill="both", expand=True)
        self.universe.bind("<KeyRelease>", lambda _e: self._refresh())
        self.fingerprint = tk.Canvas(row, width=84, height=84, bg=PANEL, highlightthickness=0)
        self.fingerprint.pack(side="left", padx=(12, 0))

        # Q2 — connections
        q2 = self._card(wrap, "2 · connections")
        self.conn_holder = tk.Frame(q2, bg=PANEL); self.conn_holder.pack(fill="x")
        addbtn = tk.Label(q2, text="＋ add connection", font=("Segoe UI", 10, "bold"),
                          fg=ACCENT, bg=PANEL, cursor="hand2")
        addbtn.pack(anchor="w", pady=(8, 0))
        addbtn.bind("<Button-1>", lambda _e: self._add_conn())

        # preview + byte meter
        pv = self._card(wrap, "the .oradio it generates", grow=True)
        self.bytes_lbl = tk.Label(pv, text="", font=("Consolas", 10, "bold"), fg=ACCENT, bg=PANEL)
        self.bytes_lbl.pack(anchor="e")
        self.preview = tk.Text(pv, bg=FIELD, fg=TEXT, relief="flat", font=("Consolas", 10),
                               padx=12, pady=10, highlightthickness=1, highlightbackground=LINE)
        self.preview.pack(fill="both", expand=True, pady=(6, 0))

        # footer
        foot = tk.Frame(wrap, bg=BG); foot.pack(fill="x", pady=(14, 0))
        self.status = tk.Label(foot, text="", font=("Segoe UI", 10), fg=MUTED, bg=BG)
        self.status.pack(side="left")
        self._btn(foot, "generate .oradio", self._generate, primary=True).pack(side="right")
        self._btn(foot, "test run", self._test_run).pack(side="right", padx=(0, 8))

        self._add_conn("simulated_spatial_array")

    def _card(self, parent, title, grow=False):
        tk = self.tk
        outer = tk.Frame(parent, bg=BG); outer.pack(fill="both", expand=grow, pady=7)
        tk.Label(outer, text=title, font=("Segoe UI", 10, "bold"), fg=MUTED, bg=BG).pack(anchor="w", pady=(0, 4))
        card = tk.Frame(outer, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        card.pack(fill="both", expand=grow)
        inner = tk.Frame(card, bg=PANEL); inner.pack(fill="both", expand=grow, padx=14, pady=12)
        return inner

    def _btn(self, parent, text, cmd, primary=False):
        tk = self.tk
        b = tk.Label(parent, text=text, font=("Segoe UI", 11, "bold"),
                     fg="#06202a" if primary else TEXT, bg=ACCENT if primary else PANEL,
                     padx=16, pady=8, cursor="hand2")
        b.bind("<Button-1>", lambda _e: cmd())
        return b

    # -- connections -- #
    def _add_conn(self, plugin: str = "") -> None:
        tk = self.tk
        rowf = tk.Frame(self.conn_holder, bg=PANEL); rowf.pack(fill="x", pady=3)
        badge = tk.Label(rowf, text="·", width=9, font=("Segoe UI", 9, "bold"),
                         fg=BG, bg=MUTED, padx=4, pady=2)
        badge.pack(side="left")
        plug = tk.Entry(rowf, bg=FIELD, fg=TEXT, insertbackground=ACCENT, relief="flat",
                        font=("Consolas", 11), highlightthickness=1, highlightbackground=LINE)
        plug.insert(0, plugin); plug.pack(side="left", fill="x", expand=True, ipady=4, padx=(8, 6))
        src = tk.Entry(rowf, bg=FIELD, fg=MUTED, insertbackground=ACCENT, relief="flat",
                       font=("Consolas", 10), width=22, highlightthickness=1, highlightbackground=LINE)
        src.pack(side="left", ipady=4)
        self._placeholder(src, "github:owner/repo  (optional)")
        rm = tk.Label(rowf, text="✕", fg=MUTED, bg=PANEL, cursor="hand2", padx=8)
        rm.pack(side="left")
        entry = {"frame": rowf, "plugin": plug, "source": src, "badge": badge}
        rm.bind("<Button-1>", lambda _e: self._del_conn(entry))
        for w in (plug, src):
            w.bind("<KeyRelease>", lambda _e: self._refresh())
        self._conn_rows.append(entry)
        self._refresh()

    def _del_conn(self, entry) -> None:
        entry["frame"].destroy()
        self._conn_rows.remove(entry)
        self._refresh()

    def _connections(self) -> List[Dict[str, Any]]:
        out = []
        for e in self._conn_rows:
            name = e["plugin"].get().strip()
            if not name:
                continue
            c: Dict[str, Any] = {"plugin": name}
            src = e["source"].get().strip()
            if src and not src.startswith("github:owner/repo"):
                c["source"] = src
            out.append(c)
        return out

    # -- live refresh -- #
    def _refresh(self) -> None:
        universe = self.universe.get("1.0", "end").strip()
        conns = self._connections()
        text, nbytes = preview(universe or "untitled", conns)
        self.preview.delete("1.0", "end"); self.preview.insert("1.0", text)
        self.bytes_lbl.config(text=f"{nbytes} bytes")
        self._draw_fingerprint(universe or "untitled")
        for e in self._conn_rows:
            name = e["plugin"].get().strip()
            if name:
                r = role_of(name)
                e["badge"].config(text=r, bg=ROLE_COLOR.get(r, MUTED))
            else:
                e["badge"].config(text="·", bg=MUTED)

    def _draw_fingerprint(self, universe: str) -> None:
        cells = identicon_cells(universe)
        c = self.fingerprint; c.delete("all")
        n = len(cells); s = 84 // n
        for r in range(n):
            for col in range(n):
                c.create_rectangle(col*s, r*s, col*s+s, r*s+s, fill=cells[r][col], outline=cells[r][col])

    # -- placeholder helpers -- #
    def _placeholder(self, entry, text):
        entry.insert(0, text)
        def clr(_e):
            if entry.get() == text:
                entry.delete(0, "end"); entry.config(fg=TEXT)
        def res(_e):
            if not entry.get():
                entry.insert(0, text); entry.config(fg=MUTED)
        entry.bind("<FocusIn>", clr); entry.bind("<FocusOut>", res)

    def _cycle_placeholder(self) -> None:
        if not self.universe.get("1.0", "end").strip():
            self.universe.config(fg=MUTED)
            self.universe.delete("1.0", "end")
            self.universe.insert("1.0", UNIVERSE_EXAMPLES[self._ph_i % len(UNIVERSE_EXAMPLES)])
            self._ph_i += 1
            self.universe.bind("<FocusIn>", self._clear_ghost)
        self.root.after(2600, self._cycle_placeholder)

    def _clear_ghost(self, _e):
        if self.universe.cget("fg") == MUTED:
            self.universe.delete("1.0", "end"); self.universe.config(fg=TEXT)

    # -- actions -- #
    def _generate(self) -> None:
        from tkinter import filedialog
        from loom.dotloom import _slug
        universe = self._real_universe()
        oradio_text, nbytes = preview(universe or "untitled", self._connections())
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".oradio",
                                            initialfile=f"{_slug(universe)}.oradio",
                                            filetypes=[("oradio", "*.oradio")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(oradio_text)
        self.status.config(text=f"generated {path.split('/')[-1]} · {nbytes} bytes", fg=ACCENT)

    def _test_run(self) -> None:
        try:
            from oradio_engine.loader import open_oradio
            oradio = loom_to_oradio(Loom(universe=self._real_universe(),
                                         connections=[Connection.parse(c) for c in self._connections()]))
            result = open_oradio(oradio)
            if not result.ok:
                self.status.config(text=f"needs: {result.report.missing_required or result.report.asks}", fg=MUTED)
                return
            result.engine.run(steps=8)
            self.status.config(text=f"▶ ran 8 ticks · {len(result.engine.bus)} beats", fg=ACCENT)
        except Exception as exc:  # never crash the surface
            self.status.config(text=f"· {exc}", fg=MUTED)

    def _real_universe(self) -> str:
        return "" if self.universe.cget("fg") == MUTED else self.universe.get("1.0", "end").strip()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    LoomApp().run()


if __name__ == "__main__":
    main()
