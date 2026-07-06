"""loom — candidate 2: the DECLARATION skin (live).

Not a form. A declaration. The lower panel shows the `.loom` (what you declared), not
the generated `.oradio` YAML (exhaust). Connections are attached pills, not settings
rows. No step numbers, no wizard. Big universe, big connections, a big seed-face.

Live, this pass:
  · the seed-face morphs on every keystroke (deterministic — it IS the universe's seed),
  · the example text is a BACKGROUND hint, not foreground — it never erases what you type,
  · the .loom declaration updates as you type,
  · your club's installed plugins are browsable — click one to attach it (no more guessing names).

Run:  python -m loom.app2     (candidate 1 is still `python -m loom.app`)
"""
from __future__ import annotations

from typing import Any, Dict, List

from loom.app import (ACCENT, BG, FIELD, LINE, MUTED, PANEL, ROLE_COLOR, TEXT,
                      UNIVERSE_EXAMPLES, identicon_cells, installed_plugins,
                      preview, role_of)


def declaration_text(universe: str, connections: List[Any]) -> str:
    """The `.loom` itself — the artifact, the way the author declared it."""
    out = ["Universe:", f"    {universe or '…'}", "", "Connections:"]
    names = [(c.get('plugin', '') if isinstance(c, dict) else str(c)) for c in connections]
    out += [f"    {n}" for n in names] if names else ["    …"]
    return "\n".join(out)


class LoomDeclaration:
    def __init__(self) -> None:
        import tkinter as tk
        self.tk = tk
        self.root = tk.Tk()
        self.root.title("loom")
        self.root.configure(bg=BG)
        self.root.geometry("960x900")
        self._conns: List[Dict[str, str]] = []
        self._ph_i = 0
        self._build()
        self._cycle_hint()
        self._add("simulated_spatial_array")

    def _build(self) -> None:
        tk = self.tk
        w = tk.Frame(self.root, bg=BG); w.pack(fill="both", expand=True, padx=30, pady=24)

        tk.Label(w, text="loom", font=("Segoe UI", 22, "bold"), fg=MUTED, bg=BG).pack(anchor="w")

        top = tk.Frame(w, bg=BG); top.pack(fill="x", pady=(16, 0))
        left = tk.Frame(top, bg=BG); left.pack(side="left", fill="both", expand=True)

        tk.Label(left, text="Universe", font=("Segoe UI", 30, "bold"), fg=TEXT, bg=BG).pack(anchor="w")
        box = tk.Frame(left, bg=FIELD, highlightthickness=1, highlightbackground=LINE)
        box.pack(fill="x", pady=(8, 0))
        self.universe = tk.Text(box, height=2, wrap="word", bg=FIELD, fg=TEXT, insertbackground=ACCENT,
                                relief="flat", font=("Segoe UI", 17), padx=14, pady=12, highlightthickness=0)
        self.universe.pack(fill="x")
        self.universe.bind("<KeyRelease>", lambda _e: self._refresh())
        # the example is a BACKGROUND hint placed OVER the empty box — never inserted into the
        # value, so it can never erase what you type. It just sits behind the cursor until you do.
        self.hint = tk.Label(box, text="", fg=MUTED, bg=FIELD, font=("Segoe UI", 17),
                             anchor="nw", justify="left", wraplength=560)
        self.hint.place(x=16, y=12)
        self.hint.bind("<Button-1>", lambda _e: self.universe.focus_set())

        # the big seed-face — morphs deterministically on every keystroke
        self.sig = tk.Canvas(top, width=150, height=150, bg=BG, highlightthickness=0)
        self.sig.pack(side="left", padx=(24, 0))

        tk.Label(w, text="Connections", font=("Segoe UI", 30, "bold"), fg=TEXT, bg=BG).pack(anchor="w", pady=(24, 8))
        self.chips = tk.Frame(w, bg=BG); self.chips.pack(fill="x")
        self.adder = tk.Entry(w, bg=FIELD, fg=TEXT, insertbackground=ACCENT, relief="flat",
                              font=("Consolas", 12), highlightthickness=1, highlightbackground=LINE)
        self.adder.pack(fill="x", ipady=7, pady=(10, 0))
        self._ghost(self.adder, "type a plugin (or github:owner/repo) and press enter…")
        self.adder.bind("<Return>", lambda _e: self._add_from_entry())

        # your club — browsable, click to attach (so you mix & match instead of guessing names)
        tk.Label(w, text="in your club  ·  click to attach", font=("Segoe UI", 10, "bold"),
                 fg=MUTED, bg=BG).pack(anchor="w", pady=(18, 6))
        self.palette = tk.Frame(w, bg=BG); self.palette.pack(fill="x")
        self._build_palette()

        # the declaration is the artifact
        head = tk.Frame(w, bg=BG); head.pack(fill="x", pady=(22, 6))
        tk.Label(head, text=".loom", font=("Consolas", 12, "bold"), fg=MUTED, bg=BG).pack(side="left")
        self.size = tk.Label(head, text="", font=("Consolas", 12, "bold"), fg=ACCENT, bg=BG)
        self.size.pack(side="right")
        self.decl = tk.Text(w, height=7, bg=PANEL, fg=TEXT, relief="flat", font=("Consolas", 12),
                            padx=14, pady=12, highlightthickness=1, highlightbackground=LINE)
        self.decl.pack(fill="both", expand=True)

        foot = tk.Frame(w, bg=BG); foot.pack(fill="x", pady=(16, 0))
        self.status = tk.Label(foot, text="", font=("Segoe UI", 10), fg=MUTED, bg=BG); self.status.pack(side="left")
        self._btn(foot, "generate", self._generate, primary=True).pack(side="right")
        self._btn(foot, "test run", self._run).pack(side="right", padx=(0, 8))

    def _btn(self, parent, text, cmd, primary=False):
        b = self.tk.Label(parent, text=text, font=("Segoe UI", 12, "bold"),
                          fg="#06202a" if primary else TEXT, bg=ACCENT if primary else PANEL,
                          padx=20, pady=9, cursor="hand2")
        b.bind("<Button-1>", lambda _e: cmd()); return b

    # -- club palette -- #
    def _build_palette(self) -> None:
        tk = self.tk
        cols = 3
        for i, item in enumerate(installed_plugins()):
            edge = ROLE_COLOR.get(item["role"], MUTED)
            label = ("🔒 " if item["sensitive"] else "") + item["plugin"]
            chip = tk.Label(self.palette, text=label, font=("Consolas", 11), fg=TEXT, bg=PANEL,
                            padx=10, pady=5, cursor="hand2", anchor="w",
                            highlightthickness=1, highlightbackground=edge)
            chip.grid(row=i // cols, column=i % cols, sticky="ew", padx=4, pady=4)
            chip.bind("<Button-1>", lambda _e, p=item["plugin"]: self._add(p))
            tip = item["reads"] or f"{item['plugin']} · {item['role']}"
            chip.bind("<Enter>", lambda _e, t=tip: self.status.config(text=t, fg=MUTED))
            chip.bind("<Leave>", lambda _e: self.status.config(text=""))
        for c in range(cols):
            self.palette.columnconfigure(c, weight=1)

    # -- pills -- #
    def _add_from_entry(self):
        val = self.adder.get().strip()
        if not val or val.startswith("type a plugin"):
            return
        if val.startswith("github:"):
            self._add(val.split("/")[-1], source=val)
        else:
            self._add(val)
        self.adder.delete(0, "end")

    def _add(self, plugin: str, source: str = "") -> None:
        tk = self.tk
        r = role_of(plugin)
        chip = tk.Frame(self.chips, bg=PANEL, highlightthickness=1, highlightbackground=ROLE_COLOR.get(r, LINE))
        chip.pack(side="left", padx=(0, 8), pady=4)
        tk.Frame(chip, bg=ROLE_COLOR.get(r, MUTED), width=4).pack(side="left", fill="y")
        tk.Label(chip, text=plugin, font=("Consolas", 12), fg=TEXT, bg=PANEL, padx=8, pady=5).pack(side="left")
        x = tk.Label(chip, text="✕", font=("Segoe UI", 9), fg=MUTED, bg=PANEL, padx=6, cursor="hand2"); x.pack(side="left")
        entry = {"plugin": plugin, "source": source, "chip": chip}
        x.bind("<Button-1>", lambda _e: self._del(entry))
        self._conns.append(entry)
        self._refresh()

    def _del(self, entry):
        entry["chip"].destroy(); self._conns.remove(entry); self._refresh()

    def _conn_dicts(self):
        out = []
        for e in self._conns:
            c = {"plugin": e["plugin"]}
            if e["source"]:
                c["source"] = e["source"]
            out.append(c)
        return out

    # -- live refresh -- #
    def _refresh(self):
        u = self._universe()
        if u:
            self.hint.place_forget()                 # you're typing — the hint gets out of the way
        else:
            self.hint.place(x=16, y=12)
        conns = self._conn_dicts()
        self.decl.delete("1.0", "end"); self.decl.insert("1.0", declaration_text(u, conns))
        _t, n = preview(u or "untitled", conns)
        self.size.config(text=f"→ {n} byte .oradio")
        self._draw(u or "untitled")

    def _draw(self, u):
        cells = identicon_cells(u); c = self.sig; c.delete("all")
        m = len(cells); s = 150 // m
        for r in range(m):
            for col in range(m):
                c.create_rectangle(col*s, r*s, col*s+s, r*s+s, fill=cells[r][col], outline=cells[r][col])

    # -- background hint (cycles only while the box is empty; never inserted into the value) -- #
    def _cycle_hint(self):
        if not self._universe():
            self.hint.config(text=UNIVERSE_EXAMPLES[self._ph_i % len(UNIVERSE_EXAMPLES)])
            self.hint.place(x=16, y=12)
            self._ph_i += 1
        self.root.after(2600, self._cycle_hint)

    def _ghost(self, e, text):
        e.insert(0, text); e.config(fg=MUTED)
        e.bind("<FocusIn>", lambda _ev: (e.get() == text) and (e.delete(0, "end"), e.config(fg=TEXT)))
        e.bind("<FocusOut>", lambda _ev: (not e.get()) and (e.insert(0, text), e.config(fg=MUTED)))

    def _universe(self):
        return self.universe.get("1.0", "end").strip()

    # -- actions -- #
    def _generate(self):
        from tkinter import filedialog
        from loom.dotloom import _slug
        u = self._universe()
        text, n = preview(u or "untitled", self._conn_dicts())
        p = filedialog.asksaveasfilename(parent=self.root, defaultextension=".oradio",
                                         initialfile=f"{_slug(u)}.oradio", filetypes=[("oradio", "*.oradio")])
        if not p:
            return
        open(p, "w", encoding="utf-8").write(text)
        self.status.config(text=f"generated · {n} bytes", fg=ACCENT)

    def _run(self):
        try:
            from oradio_engine.loader import open_oradio
            from loom.dotloom import Loom, Connection, loom_to_oradio
            res = open_oradio(loom_to_oradio(Loom(self._universe(),
                              [Connection.parse(c) for c in self._conn_dicts()])))
            if not res.ok:
                self.status.config(text=f"needs: {res.report.missing_required or res.report.asks}", fg=MUTED); return
            res.engine.run(steps=8)
            self.status.config(text=f"▶ {len(res.engine.bus)} beats", fg=ACCENT)
        except Exception as exc:
            self.status.config(text=f"· {exc}", fg=MUTED)

    def run(self):
        self.root.mainloop()


def main() -> None:
    LoomDeclaration().run()


if __name__ == "__main__":
    main()
