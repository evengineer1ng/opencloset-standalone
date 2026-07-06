#!/usr/bin/env python3
from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path
from typing import Dict, List

from ribbon_shell_launcher import ShellLauncher
from ribbon_shell_models import LaunchItem, assemble_catalog, filter_items
from ribbon_shell_theme import PALETTES, RibbonStateMachine


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
RADIO_ROOT = Path(__file__).resolve().parent
PAGE_SIZE = 4


def mix(hex_a: str, hex_b: str, amount: float) -> str:
    amount = max(0.0, min(1.0, amount))
    a = tuple(int(hex_a[idx : idx + 2], 16) for idx in (1, 3, 5))
    b = tuple(int(hex_b[idx : idx + 2], 16) for idx in (1, 3, 5))
    rgb = [round((1.0 - amount) * left + amount * right) for left, right in zip(a, b)]
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


class RibbonShellApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("RibbonOS Shell")
        self.root.configure(bg="#000000")
        self.root.minsize(1120, 760)
        self.root.geometry(self._centered_geometry(1360, 860))
        self.root.after(120, self._present_window)

        self.theme = RibbonStateMachine()
        self.launcher = ShellLauncher(WORKSPACE_ROOT, RADIO_ROOT)
        self.catalog = assemble_catalog(WORKSPACE_ROOT, RADIO_ROOT)
        self.bucket_var = tk.StringVar(value="All")
        self.query_var = tk.StringVar()
        self.status_var = tk.StringVar(value="RibbonOS shell ready.")
        self.clock_var = tk.StringVar()
        self.page_index = 0
        self.items: List[LaunchItem] = []
        self.card_frames: List[tk.Frame] = []
        self.category_buttons: Dict[str, tk.Button] = {}

        self.canvas = tk.Canvas(self.root, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.stage = tk.Frame(self.canvas, bd=0, highlightthickness=0)
        self.top_panel = tk.Frame(self.canvas, bd=0, highlightthickness=0)
        self.dock = tk.Frame(self.canvas, bd=0, highlightthickness=0)
        self.stage_window = self.canvas.create_window(0, 0, anchor="nw", window=self.stage)
        self.top_window = self.canvas.create_window(0, 0, anchor="nw", window=self.top_panel)
        self.dock_window = self.canvas.create_window(0, 0, anchor="sw", window=self.dock)

        self._build_stage()
        self._build_top_panel()
        self._build_dock()
        self._bind_events()
        self.refresh_cards(reset_page=True)
        self._layout()
        self._apply_theme(force=True)
        self._tick()

    def _centered_geometry(self, width: int, height: int) -> str:
        screen_w = max(1280, self.root.winfo_screenwidth())
        screen_h = max(800, self.root.winfo_screenheight())
        left = max(16, (screen_w - width) // 2)
        top = max(16, (screen_h - height) // 2)
        return f"{width}x{height}+{left}+{top}"

    def _build_stage(self) -> None:
        self.hero = tk.Frame(self.stage, bd=0, highlightthickness=0)
        self.hero.pack(fill="both", expand=True, padx=32, pady=(24, 24))

        self.stage_badge = tk.Label(self.hero, text="RibbonOS Shell", font=("Segoe UI", 13, "bold"), anchor="w")
        self.stage_badge.pack(anchor="nw")
        self.stage_whisper = tk.Label(
            self.hero,
            text="Carousel overlay lives in the middle and recedes with inactivity.",
            font=("Segoe UI", 10),
            anchor="w",
            justify="left",
        )
        self.stage_whisper.pack(anchor="nw", pady=(4, 0))

    def _build_top_panel(self) -> None:
        brand_row = tk.Frame(self.top_panel, bd=0, highlightthickness=0)
        brand_row.pack(fill="x", padx=28, pady=(18, 10))
        self.brand = tk.Label(brand_row, text="RibbonOS", font=("Segoe UI", 24, "bold"))
        self.brand.pack(side="left")
        self.clock = tk.Label(brand_row, textvariable=self.clock_var, font=("Segoe UI", 11))
        self.clock.pack(side="right")

        self.topline = tk.Label(
            self.top_panel,
            text="Shell host for simulator, stations, Audio CLI, OpenCloset, and .oradio artifacts.",
            font=("Segoe UI", 11),
            anchor="w",
        )
        self.topline.pack(fill="x", padx=28)

        tools = tk.Frame(self.top_panel, bd=0, highlightthickness=0)
        tools.pack(fill="x", padx=28, pady=(14, 8))
        self.search = tk.Entry(tools, textvariable=self.query_var, width=30, font=("Segoe UI", 12), relief="flat")
        self.search.pack(side="left")
        self.sim_btn = tk.Button(tools, text="Open Simulator", command=self._open_simulator, relief="flat", bd=0)
        self.sim_btn.pack(side="right")
        self.palette_btn = tk.Button(tools, text="Style", command=self._cycle_palette, relief="flat", bd=0)
        self.palette_btn.pack(side="right", padx=(0, 10))

        categories = tk.Frame(self.top_panel, bd=0, highlightthickness=0)
        categories.pack(fill="x", padx=28, pady=(6, 18))
        for name in ("All", "Station", "Oradio", "Tools"):
            btn = tk.Button(categories, text=name, command=lambda value=name: self._select_bucket(value), relief="flat", bd=0)
            btn.pack(side="left", padx=(0, 10))
            self.category_buttons[name] = btn

    def _build_dock(self) -> None:
        nav_row = tk.Frame(self.dock, bd=0, highlightthickness=0)
        nav_row.pack(fill="x", padx=26, pady=(18, 10))
        self.prev_btn = tk.Button(nav_row, text="←", command=lambda: self._turn_page(-1), relief="flat", bd=0)
        self.prev_btn.pack(side="left")
        self.page_label = tk.Label(nav_row, text="Page 1", font=("Segoe UI", 11, "bold"))
        self.page_label.pack(side="left", padx=12)
        self.next_btn = tk.Button(nav_row, text="→", command=lambda: self._turn_page(1), relief="flat", bd=0)
        self.next_btn.pack(side="left")
        self.status = tk.Label(nav_row, textvariable=self.status_var, font=("Segoe UI", 10), anchor="e")
        self.status.pack(side="right")

        self.cards_row = tk.Frame(self.dock, bd=0, highlightthickness=0)
        self.cards_row.pack(fill="x", padx=22, pady=(0, 18))

    def _bind_events(self) -> None:
        self.root.bind("<Configure>", lambda _e: self._layout())
        self.root.bind("<Escape>", lambda _e: self.root.destroy())
        self.root.bind("<F11>", lambda _e: self._toggle_fullscreen())
        self.root.bind("<Motion>", lambda _e: self._wake())
        self.root.bind("<Button-1>", lambda _e: self._wake())
        self.root.bind("<Key>", lambda _e: self._wake())
        self.root.bind("<Left>", lambda _e: self._turn_page(-1))
        self.root.bind("<Right>", lambda _e: self._turn_page(1))
        self.query_var.trace_add("write", lambda *_: self.refresh_cards(reset_page=True))

    def _toggle_fullscreen(self) -> None:
        current = bool(self.root.attributes("-fullscreen"))
        self.root.attributes("-fullscreen", not current)
        self._wake()

    def _present_window(self) -> None:
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def _select_bucket(self, bucket: str) -> None:
        self.bucket_var.set(bucket)
        self.refresh_cards(reset_page=True)
        self._wake()

    def _cycle_palette(self) -> None:
        names = list(PALETTES)
        idx = names.index(self.theme.palette_name)
        self.theme.set_palette(names[(idx + 1) % len(names)])
        self.status_var.set(f"Theme switched to {self.theme.palette_name}.")
        self._apply_theme(force=True)
        self._wake()

    def _wake(self) -> None:
        self.theme.note_activity()
        self._update_overlay_visibility()

    def _open_simulator(self) -> None:
        self.theme.note_launch()
        self.status_var.set("Launching simulator...")
        tool = next(item for item in self.catalog if item.item_id == "tool:simulator_manager")
        self.launcher.launch(tool)
        self._apply_theme(force=True)

    def _turn_page(self, delta: int) -> None:
        page_count = max(1, (len(self.items) + PAGE_SIZE - 1) // PAGE_SIZE)
        next_page = max(0, min(page_count - 1, self.page_index + delta))
        if next_page == self.page_index:
            return
        self.page_index = next_page
        self._render_cards()
        self._wake()

    def refresh_cards(self, reset_page: bool = False) -> None:
        self.items = filter_items(self.catalog, self.bucket_var.get(), self.query_var.get())
        if reset_page:
            self.page_index = 0
        self._render_cards()

    def _render_cards(self) -> None:
        for child in self.cards_row.winfo_children():
            child.destroy()
        self.card_frames = []
        page_count = max(1, (len(self.items) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page_index = max(0, min(self.page_index, page_count - 1))
        self.page_label.configure(text=f"Page {self.page_index + 1} / {page_count}")
        self.prev_btn.configure(state="normal" if self.page_index > 0 else "disabled")
        self.next_btn.configure(state="normal" if self.page_index < page_count - 1 else "disabled")

        start = self.page_index * PAGE_SIZE
        visible = self.items[start : start + PAGE_SIZE]
        if not visible:
            self._build_empty_card()
            self.status_var.set("No launchables match the current filter.")
            self._apply_theme(force=True)
            return

        for idx, item in enumerate(visible):
            frame = self._build_card(idx, item)
            frame.pack(side="left", fill="y", padx=10)
            self.card_frames.append(frame)
        self.status_var.set(f"{len(self.items)} launchables ready.")
        self._apply_theme(force=True)

    def _build_empty_card(self) -> None:
        frame = tk.Frame(self.cards_row, width=1180, height=240, bd=0, highlightthickness=1)
        frame.pack_propagate(False)
        frame.pack(fill="x", padx=8)
        tk.Label(frame, text="No launchables match this filter.", font=("Segoe UI", 18, "bold")).pack(anchor="w", padx=24, pady=(44, 10))
        tk.Label(frame, text="Try All, or clear the search field.", font=("Segoe UI", 12)).pack(anchor="w", padx=24)
        self.card_frames = [frame]

    def _build_card(self, idx: int, item: LaunchItem) -> tk.Frame:
        card = tk.Frame(self.cards_row, width=300, height=248, bd=0, highlightthickness=1)
        card.pack_propagate(False)
        tag = tk.Label(card, text=item.kind.upper(), font=("Segoe UI", 9, "bold"), padx=8, pady=4)
        tag.pack(anchor="w", padx=18, pady=(18, 10))
        tk.Label(card, text=item.title, font=("Segoe UI", 21, "bold"), justify="left", wraplength=250, anchor="w").pack(fill="x", padx=18)
        tk.Label(card, text=item.subtitle, font=("Segoe UI", 11), justify="left", wraplength=250, anchor="w").pack(fill="x", padx=18, pady=(10, 4))
        tk.Label(card, text=item.category, font=("Segoe UI", 10, "italic"), anchor="w").pack(fill="x", padx=18)
        rail = tk.Canvas(card, height=12, bd=0, highlightthickness=0)
        rail.pack(fill="x", padx=18, pady=(18, 0))
        rail.create_line(0, 6, 180, 6, width=5, capstyle="round")
        actions = tk.Frame(card, bd=0, highlightthickness=0)
        actions.pack(side="bottom", fill="x", padx=14, pady=16)
        tk.Button(actions, text="Launch", command=lambda i=item: self._launch(i), relief="flat", bd=0).pack(side="left")
        tk.Button(actions, text="Reveal", command=lambda i=item: self.launcher.reveal(i), relief="flat", bd=0).pack(side="left", padx=(8, 0))
        card._tag = tag  # type: ignore[attr-defined]
        card._rail = rail  # type: ignore[attr-defined]
        card._actions = actions  # type: ignore[attr-defined]
        return card

    def _launch(self, item: LaunchItem) -> None:
        self.theme.note_launch()
        self.status_var.set(f"Launching {item.title}...")
        self.launcher.launch(item)
        self._apply_theme(force=True)

    def _layout(self) -> None:
        width = max(1, self.root.winfo_width())
        height = max(1, self.root.winfo_height())
        self.canvas.coords(self.stage_window, 0, 0)
        self.canvas.itemconfigure(self.stage_window, width=width, height=height)
        self.canvas.coords(self.top_window, 0, 0)
        self.canvas.itemconfigure(self.top_window, width=width)
        self.canvas.coords(self.dock_window, width / 2, height * 0.57)
        self.canvas.itemconfigure(self.dock_window, width=width)
        self._update_overlay_visibility()

    def _update_overlay_visibility(self) -> None:
        top_state = "hidden" if self.theme.phase == "DIM" else "normal"
        self.canvas.itemconfigure(self.top_window, state=top_state)
        self.canvas.itemconfigure(self.dock_window, state="normal")
        self._apply_theme(force=True)

    def _apply_theme(self, force: bool = False) -> None:
        palette = self.theme.palette
        overlay_hint = self.theme.overlay_alpha_hint()
        overlay_mix = 0.04 + (overlay_hint * 0.18)
        panel_mix = 0.08 + (overlay_hint * 0.20)
        text_mix = 0.30 + (overlay_hint * 0.70)
        muted_mix = 0.18 + (overlay_hint * 0.55)
        overlay_bg = mix(palette.bg, palette.overlay, overlay_mix)
        panel_bg = mix(palette.bg, palette.card, panel_mix)
        hero_bg = mix(palette.bg, palette.card, 0.03)
        text_fg = mix(palette.bg, palette.text, text_mix)
        muted_fg = mix(palette.bg, palette.muted, muted_mix)

        self.root.configure(bg=palette.bg)
        self.canvas.configure(bg=palette.bg)
        self.stage.configure(bg=palette.bg)
        self.hero.configure(bg=hero_bg)
        self.stage_badge.configure(bg=hero_bg, fg=muted_fg)
        self.stage_whisper.configure(bg=hero_bg, fg=muted_fg)

        for frame in (self.top_panel, self.dock):
            frame.configure(bg=overlay_bg)
        for row in self.top_panel.winfo_children():
            if isinstance(row, tk.Frame):
                row.configure(bg=overlay_bg)
        for row in self.dock.winfo_children():
            if isinstance(row, tk.Frame):
                row.configure(bg=overlay_bg)

        self.brand.configure(bg=overlay_bg, fg=text_fg)
        self.clock.configure(bg=overlay_bg, fg=muted_fg)
        self.topline.configure(bg=overlay_bg, fg=muted_fg)
        self.page_label.configure(bg=overlay_bg, fg=text_fg)
        self.status.configure(bg=overlay_bg, fg=muted_fg)

        self.search.configure(bg=panel_bg, fg=text_fg, insertbackground=text_fg)
        self.sim_btn.configure(bg=mix(panel_bg, palette.accent, 0.88), fg=palette.bg, activebackground=palette.ribbon_a, activeforeground=palette.bg, padx=14, pady=8)
        self.palette_btn.configure(bg=panel_bg, fg=text_fg, activebackground=palette.card, activeforeground=text_fg, padx=14, pady=8)
        for btn in (self.prev_btn, self.next_btn):
            btn.configure(bg=panel_bg, fg=text_fg, activebackground=palette.card, activeforeground=text_fg, padx=12, pady=6, disabledforeground=palette.line)

        for name, button in self.category_buttons.items():
            active = name == self.bucket_var.get()
            bg = mix(panel_bg, palette.accent, 0.90) if active else panel_bg
            fg = palette.bg if active else text_fg
            button.configure(bg=bg, fg=fg, activebackground=palette.ribbon_a, activeforeground=palette.bg, padx=12, pady=8)

        for frame in self.card_frames:
            frame.configure(bg=panel_bg, highlightbackground=palette.line)
            tag = getattr(frame, "_tag", None)
            rail = getattr(frame, "_rail", None)
            actions = getattr(frame, "_actions", None)
            if isinstance(tag, tk.Label):
                kind = str(tag.cget("text"))
                chip_bg = palette.accent if kind == "STATION" else palette.ribbon_b if kind == "ORADIO" else palette.ribbon_c
                tag.configure(bg=chip_bg, fg=palette.bg)
            if isinstance(rail, tk.Canvas):
                rail.configure(bg=panel_bg)
                rail.delete("all")
                rail.create_line(4, 6, 220, 6, width=5, capstyle="round", fill=mix(palette.bg, palette.ribbon_a, 0.55 + overlay_hint * 0.35))
                rail.create_line(136, 6, 258, 6, width=3, capstyle="round", fill=mix(palette.bg, palette.ribbon_b, 0.45 + overlay_hint * 0.35))
            if isinstance(actions, tk.Frame):
                actions.configure(bg=panel_bg)
            for child in frame.winfo_children():
                if isinstance(child, tk.Label) and child is not tag:
                    child.configure(bg=panel_bg, fg=text_fg if "italic" not in str(child.cget("font")) else muted_fg)
                if isinstance(child, tk.Frame):
                    child.configure(bg=panel_bg)
                    for btn in child.winfo_children():
                        btn.configure(bg=mix(panel_bg, palette.accent, 0.88), fg=palette.bg, activebackground=palette.ribbon_a, activeforeground=palette.bg, padx=12, pady=8)

    def _draw_ribbon(self) -> None:
        self.canvas.delete("ribbon")
        width = max(1, self.root.winfo_width())
        height = max(1, self.root.winfo_height())
        palette = self.theme.palette
        curves = self.theme.ribbon_geometry(width, height)
        for points, color, band_width in zip(curves, (palette.ribbon_c, palette.ribbon_b, palette.ribbon_a), (34, 26, 18)):
            flat = [coord for point in points for coord in point]
            self.canvas.create_line(*flat, fill=mix(palette.bg, color, 0.72), width=band_width + 12, smooth=True, splinesteps=24, tags="ribbon")
            self.canvas.create_line(*flat, fill=color, width=band_width, smooth=True, splinesteps=24, tags="ribbon")

    def _tick(self) -> None:
        self.theme.tick()
        self.clock_var.set(time.strftime("%I:%M %p"))
        self._draw_ribbon()
        self._update_overlay_visibility()
        self.root.after(33, self._tick)

    def run(self) -> int:
        self.root.mainloop()
        return 0


def main() -> int:
    app = RibbonShellApp()
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
