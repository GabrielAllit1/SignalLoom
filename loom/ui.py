from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable
import json
import math
import platform
import threading
import time
import tkinter as tk

from .ai import DEFAULT_MODEL, QwenClient
from .analyze import analyze, make_deliverables, pretty_json
from .flow import SUPPORTED_SERVICES, load_services, route_payload, save_services, workflow_template
from .parse import extract_text
from .store import export_text, recent_results, save_document, save_result

APP = "SignalLoom"
TAG = "InvoiceOps Copilot for extraction, review, approval routing, and AP handoff."
CONTACT = "Gabriel Allit | SALT19 LLC | salt19.com"
DOI = "10.5281/zenodo.20580153"
ORCID = "0009-0008-2365-226X"

# Light glassmorphism palette. Tkinter cannot blur like native acrylic, so the
# design uses layered pale panels, soft borders, and translucent working overlay.
BG = "#edf7ff"
BG_2 = "#f7fbff"
PANEL = "#ffffff"
GLASS = "#f3faff"
GLASS_2 = "#e8f5ff"
SOFT = "#ddf7ff"
LINE = "#c7dff2"
LINE_2 = "#d9e9f7"
TEXT = "#18314c"
MUTED = "#64748b"
CYAN = "#06b6d4"
BLUE = "#2563eb"
BLUE_2 = "#1d4ed8"
GREEN = "#059669"
AMBER = "#d97706"
RED = "#dc2626"
USER_BUBBLE = "#dbeafe"
AI_BUBBLE = "#ffffff"
CARD_BG = "#f8fdff"
CHAT_BG = "#f8fcff"
ASSISTANT_INK = "#20354d"
USER_BG = "#e8f1ff"
USER_LINE = "#b9d2ff"
MONO = "Consolas"
FONT = "Segoe UI Variable"
FALLBACK_FONT = "Segoe UI"


def _font(size: int, weight: str = "normal") -> tuple[str, int, str]:
    return (FONT, size, weight)


class Glass(tk.Frame):
    def __init__(self, master: tk.Misc, **kwargs: Any) -> None:
        super().__init__(master, bg=PANEL, highlightbackground=LINE, highlightthickness=1, bd=0, **kwargs)


class Button(tk.Button):
    def __init__(self, master: tk.Misc, text: str, command: Callable[[], None] | None = None, primary: bool = False, **kwargs: Any) -> None:
        base = BLUE if primary else GLASS_2
        fg = "#ffffff" if primary else TEXT
        hover = BLUE_2 if primary else "#d7ecff"
        super().__init__(
            master,
            text=text,
            command=command,
            bg=base,
            fg=fg,
            activebackground=hover,
            activeforeground=fg,
            relief="flat",
            bd=0,
            padx=16,
            pady=9,
            cursor="hand2",
            font=_font(10, "bold" if primary else "normal"),
            **kwargs,
        )
        self.bind("<Enter>", lambda _e: self.configure(bg=hover))
        self.bind("<Leave>", lambda _e: self.configure(bg=base))


class Pill(tk.Button):
    def __init__(self, master: tk.Misc, text: str, command: Callable[[], None]) -> None:
        super().__init__(
            master,
            text=text,
            command=command,
            bg="#eef8ff",
            fg=TEXT,
            activebackground="#dff5ff",
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=13,
            pady=7,
            cursor="hand2",
            font=_font(9),
        )



class SlimScrollbar(tk.Canvas):
    """Modern no-arrow vertical scrollbar for scrollable canvases."""

    def __init__(self, master: tk.Misc, canvas: tk.Canvas, **kwargs: Any) -> None:
        super().__init__(master, width=8, bg=CHAT_BG, highlightthickness=0, bd=0, **kwargs)
        self.canvas = canvas
        self.first = 0.0
        self.last = 1.0
        self.drag_start_y: int | None = None
        self.drag_start_first = 0.0
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda _e: setattr(self, "drag_start_y", None))
        self.bind("<Configure>", lambda _e: self._draw())

    def set(self, first: str, last: str) -> None:
        self.first = max(0.0, min(1.0, float(first)))
        self.last = max(0.0, min(1.0, float(last)))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        h = max(self.winfo_height(), 1)
        if self.last - self.first >= 0.995:
            return
        y1 = int(h * self.first) + 3
        y2 = int(h * self.last) - 3
        if y2 - y1 < 34:
            y2 = min(h - 3, y1 + 34)
        self.create_rectangle(2, y1, 6, y2, fill="#b7d6ee", outline="")

    def _press(self, event: tk.Event) -> None:
        self.drag_start_y = int(event.y)
        self.drag_start_first = self.first
        h = max(self.winfo_height(), 1)
        target = max(0.0, min(1.0, event.y / h))
        if not (self.first <= target <= self.last):
            self.canvas.yview_moveto(target)

    def _drag(self, event: tk.Event) -> None:
        if self.drag_start_y is None:
            return
        h = max(self.winfo_height(), 1)
        visible = max(0.02, self.last - self.first)
        delta = (int(event.y) - self.drag_start_y) / h
        self.canvas.yview_moveto(max(0.0, min(1.0 - visible, self.drag_start_first + delta)))


class Nav(tk.Button):
    def __init__(self, master: tk.Misc, text: str, command: Callable[[], None]) -> None:
        super().__init__(
            master,
            text=text,
            command=command,
            anchor="w",
            bg=BG_2,
            fg=MUTED,
            activebackground=SOFT,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=18,
            pady=10,
            cursor="hand2",
            font=_font(10),
        )
        self.selected = False
        self.bind("<Enter>", lambda _e: None if self.selected else self.configure(bg="#eef8ff", fg=TEXT))
        self.bind("<Leave>", lambda _e: None if self.selected else self.configure(bg=BG_2, fg=MUTED))

    def set_selected(self, on: bool) -> None:
        self.selected = on
        self.configure(bg=SOFT if on else BG_2, fg=TEXT if on else MUTED)


class LoadOverlay(tk.Toplevel):
    """Borderless activity window used for long local AI tasks.

    The background color is made transparent on Windows via -transparentcolor.
    On platforms without that attribute it remains a small pale activity card,
    but still borderless and never forced to stay above other applications.
    """

    TRANSPARENT = "#ff00ff"

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master)
        self.withdraw()
        self.overrideredirect(True)
        self.wm_overrideredirect(True)
        self.configure(bg=self.TRANSPARENT)
        self.geometry("420x300+260+180")
        self.resizable(False, False)
        self.attributes("-alpha", 0.92)
        try:
            self.attributes("-transparentcolor", self.TRANSPARENT)
        except tk.TclError:
            pass
        try:
            self.attributes("-topmost", False)
        except tk.TclError:
            pass
        self.percent: int | None = None
        self.text = "Working"
        self.tick = 0
        self.started = time.monotonic()
        self.canvas = tk.Canvas(self, width=420, height=300, bg=self.TRANSPARENT, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

    def _place_near_master(self) -> None:
        try:
            self.update_idletasks()
            x = self.master.winfo_rootx() + max(120, int(self.master.winfo_width() * 0.32))
            y = self.master.winfo_rooty() + max(90, int(self.master.winfo_height() * 0.20))
            self.geometry(f"420x300+{x}+{y}")
        except Exception:
            pass

    def start(self, text: str, percent: int | None = None) -> None:
        self.started = time.monotonic()
        self.percent = percent
        self.text = text
        self._place_near_master()
        self.wm_overrideredirect(True)
        try:
            self.attributes("-topmost", False)
        except tk.TclError:
            pass
        self.deiconify()
        self.lift(self.master)
        self.after(40, self._draw)

    def update_progress(self, percent: int | None, text: str) -> None:
        self.percent = None if percent is None else max(0, min(int(percent), 100))
        self.text = text
        if self.winfo_viewable():
            self._draw_once()

    def finish(self, text: str = "Complete") -> None:
        self.percent = 100
        self.text = text
        self._draw_once()
        self.after(520, self.withdraw)

    def _draw_once(self) -> None:
        self.tick += 1
        c = self.canvas
        c.delete("all")
        cx, cy = 210, 125
        pulse = 1.0 + 0.065 * math.sin(self.tick / 4)

        # Holographic glow and neural field.
        for r, color in [(118, "#ccf7ff"), (94, "#e0fbff"), (74, "#f8ffff")]:
            c.create_oval(cx - r * pulse, cy - r * 0.72 * pulse, cx + r * pulse, cy + r * 0.72 * pulse, fill=color, outline="")
        c.create_oval(cx - 92 * pulse, cy - 64 * pulse, cx + 92 * pulse, cy + 64 * pulse, outline=CYAN, width=2)
        nodes: list[tuple[float, float]] = []
        for i in range(38):
            ang = i * 0.73 + self.tick * 0.03
            rad = 26 + (i % 6) * 11
            x = cx + math.cos(ang) * rad * (1.55 if i % 2 else 1.05)
            y = cy + math.sin(ang * 1.17) * rad * 0.78
            nodes.append((x, y))
        for i in range(len(nodes)):
            x1, y1 = nodes[i]
            x2, y2 = nodes[(i + 7) % len(nodes)]
            c.create_line(x1, y1, x2, y2, fill="#7dd3fc", width=1)
        for x, y in nodes:
            r = 2.5 + 0.6 * math.sin(self.tick / 5 + x)
            c.create_oval(x - r, y - r, x + r, y + r, fill="#0891b2", outline="#e0ffff")

        # Readable floating glass strip.
        c.create_rectangle(52, 226, 368, 277, fill="#ffffff", outline="#bcecff")
        c.create_text(210, 241, text=self.text, fill=TEXT, font=(FALLBACK_FONT, 11, "bold"))
        elapsed = max(0, int(time.monotonic() - self.started))
        if self.percent is None:
            # Unknown-duration tasks use an animated activity bar, not a fake percent.
            pos = (self.tick * 7) % 240
            c.create_rectangle(88, 258, 332, 264, fill="#dff7ff", outline="")
            c.create_rectangle(88 + pos, 258, min(332, 88 + pos + 64), 264, fill=CYAN, outline="")
            c.create_text(210, 274, text=f"working in background • {elapsed}s", fill=MUTED, font=(FALLBACK_FONT, 8))
        else:
            pct = self.percent
            c.create_rectangle(88, 258, 332, 264, fill="#dff7ff", outline="")
            c.create_rectangle(88, 258, 88 + int(244 * pct / 100), 264, fill=CYAN, outline="")
            c.create_text(210, 274, text=f"{pct}% • app can run in background", fill=MUTED, font=(FALLBACK_FONT, 8))

    def _draw(self) -> None:
        if not self.winfo_viewable():
            return
        self._draw_once()
        self.after(90, self._draw)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP} — InvoiceOps Copilot")
        self.geometry("1420x900")
        self.minsize(1140, 740)
        self.configure(bg=BG)
        self._set_icon()
        self.client = QwenClient(model=DEFAULT_MODEL)
        self.current_result: Any = None
        self.current_source = ""
        self.source_name = tk.StringVar(value="No source loaded")
        self.schema = tk.StringVar(value="invoice")
        self.status = tk.StringVar(value="Checking local AI…")
        self.activity = tk.StringVar(value="Ready")
        self.services: dict[str, tk.StringVar] = {}
        self.cards: list[dict[str, Any]] = []
        self.chat_labels: list[tuple[tk.Label, str]] = []
        self.nav: dict[str, Nav] = {}
        self.pages: dict[str, tk.Frame] = {}
        self.overlay = LoadOverlay(self)
        self._style()
        self._shell()
        self.show("Dashboard")
        self.after(300, self.refresh_status)

    def _set_icon(self) -> None:
        icon = Path(__file__).resolve().parents[1] / "assets" / "signalloom.png"
        try:
            self._icon_img = tk.PhotoImage(file=str(icon))
            self.iconphoto(True, self._icon_img)
        except Exception:
            pass

    def _style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TCombobox", fieldbackground=PANEL, background=PANEL, foreground=TEXT, arrowcolor=BLUE, bordercolor=LINE)
        style.configure("Horizontal.TProgressbar", troughcolor="#e5f4ff", background=CYAN, bordercolor=LINE)
        style.configure("Vertical.TScrollbar", troughcolor=CHAT_BG, background="#b7d6ee", bordercolor=CHAT_BG, lightcolor=CHAT_BG, darkcolor=CHAT_BG, arrowcolor=CHAT_BG, relief="flat")

    def _shell(self) -> None:
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._paint)
        self.root_frame = tk.Frame(self.canvas, bg=BG)
        self.canvas_window = self.canvas.create_window(0, 0, anchor="nw", window=self.root_frame)

        self.sidebar = tk.Frame(self.root_frame, bg=BG_2, width=228, highlightbackground=LINE, highlightthickness=1)
        self.sidebar.pack(side="left", fill="y", padx=(14, 0), pady=14)
        self.sidebar.pack_propagate(False)

        brand = tk.Frame(self.sidebar, bg=BG_2)
        brand.pack(fill="x", padx=18, pady=(18, 14))
        tk.Label(brand, text="SignalLoom", bg=BG_2, fg=TEXT, font=_font(18, "bold")).pack(anchor="w")
        tk.Label(brand, text="InvoiceOps Copilot", bg=BG_2, fg=MUTED, font=_font(9)).pack(anchor="w")
        tk.Frame(self.sidebar, bg=LINE, height=1).pack(fill="x", padx=14, pady=(4, 8))

        for name in ["Dashboard", "Ingest", "Extraction", "Review", "Copilot", "Routing", "Services", "Setup", "Models", "History", "Info"]:
            btn = Nav(self.sidebar, self._nav_text(name), lambda n=name: self.show(n))
            btn.pack(fill="x", padx=10, pady=2)
            self.nav[name] = btn
        tk.Frame(self.sidebar, bg=BG_2).pack(fill="both", expand=True)
        tk.Label(self.sidebar, textvariable=self.status, bg=BG_2, fg=MUTED, wraplength=190, justify="left", font=_font(8)).pack(fill="x", padx=16, pady=(10, 12))

        self.main = tk.Frame(self.root_frame, bg=BG)
        self.main.pack(side="left", fill="both", expand=True, padx=14, pady=14)
        self._topbar()
        self.host = tk.Frame(self.main, bg=BG)
        self.host.pack(fill="both", expand=True)
        self._pages()
        self._footer()

    def _paint(self, event: tk.Event) -> None:
        self.canvas.delete("bg")
        w, h = max(event.width, 1), max(event.height, 1)
        self.canvas.itemconfigure(self.canvas_window, width=w, height=h)
        self.canvas.create_oval(w - 560, -190, w + 120, 440, fill="#dff7ff", outline="", tags="bg")
        self.canvas.create_oval(-260, h - 380, 410, h + 190, fill="#eef2ff", outline="", tags="bg")
        self.canvas.create_oval(int(w * .36), -260, int(w * .80), 260, fill="#f9fdff", outline="", tags="bg")
        for i in range(50):
            x = (i * 193) % w
            y = (i * 107) % h
            self.canvas.create_oval(x, y, x + 2, y + 2, fill="#bdefff", outline="", tags="bg")
        self.canvas.tag_lower("bg")

    def _topbar(self) -> None:
        top = Glass(self.main)
        top.pack(fill="x", pady=(0, 12))
        left = tk.Frame(top, bg=PANEL)
        left.pack(side="left", fill="x", expand=True, padx=18, pady=14)
        tk.Label(left, text="Local Invoice Intelligence", bg=PANEL, fg=TEXT, font=_font(22, "bold")).pack(anchor="w")
        tk.Label(left, text=TAG, bg=PANEL, fg=MUTED, font=_font(10)).pack(anchor="w")
        right = tk.Frame(top, bg=PANEL)
        right.pack(side="right", padx=18, pady=12)
        tk.Label(right, text="Schema", bg=PANEL, fg=MUTED, font=_font(8, "bold")).pack(anchor="e")
        ttk.Combobox(right, textvariable=self.schema, values=["invoice", "excel_workbook", "universal_document", "contract", "email_thread", "resume", "job_role"], width=24).pack(anchor="e")

    def _footer(self) -> None:
        foot = tk.Frame(self.main, bg=BG)
        foot.pack(fill="x", pady=(10, 0))
        tk.Label(foot, textvariable=self.activity, bg=BG, fg=MUTED, font=_font(9)).pack(side="left")
        tk.Label(foot, text="source-faithful • local-first • review before routing", bg=BG, fg=MUTED, font=_font(9)).pack(side="right")

    def _nav_text(self, name: str) -> str:
        labels = {
            "Dashboard": "Overview",
            "Ingest": "Ingest",
            "Extraction": "Extraction",
            "Review": "Invoice Review",
            "Copilot": "Qwen Copilot",
            "Routing": "Routing",
            "Services": "Services",
            "Setup": "Local AI Setup",
            "Models": "Models",
            "History": "History",
            "Info": "About",
        }
        return f"  {labels.get(name, name)}"

    def _pages(self) -> None:
        self._dashboard()
        self._ingest()
        self._extraction()
        self._review()
        self._copilot()
        self._routing()
        self._services()
        self._setup()
        self._models()
        self._history()
        self._info()

    def show(self, name: str) -> None:
        for child in self.host.winfo_children():
            child.pack_forget()
        self.pages[name].pack(fill="both", expand=True)
        for key, btn in self.nav.items():
            btn.set_selected(key == name)
        if name == "History":
            self.load_history()
        elif name == "Models":
            self.render_model_status()
        elif name == "Review":
            self.render_review()
        elif name == "Routing":
            self.render_routing()
        elif name == "Copilot":
            self.render_cards()

    def card(self, master: tk.Misc, title: str, subtitle: str = "") -> Glass:
        frame = Glass(master)
        tk.Label(frame, text=title, bg=PANEL, fg=TEXT, font=_font(13, "bold")).pack(anchor="w", padx=16, pady=(14, 2))
        if subtitle:
            tk.Label(frame, text=subtitle, bg=PANEL, fg=MUTED, wraplength=940, justify="left", font=_font(9)).pack(anchor="w", padx=16, pady=(0, 10))
        return frame

    def text(self, master: tk.Misc, mono: bool = False, wrap: str = "word") -> tk.Text:
        family = MONO if mono else FALLBACK_FONT
        box = tk.Text(master, bg=GLASS, fg=TEXT, insertbackground=TEXT, relief="flat", padx=12, pady=12, wrap=wrap, font=(family, 10), undo=True, highlightbackground=LINE_2, highlightthickness=1)
        box.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        return box

    def _dashboard(self) -> None:
        page = tk.Frame(self.host, bg=BG)
        self.pages["Dashboard"] = page
        hero = self.card(page, "Marketing invoice workbench", "Reduce manual invoice review by turning files into validated AP packets, approval messages, exception lists, and webhook-ready routing payloads.")
        hero.pack(fill="x", pady=(0, 12))
        actions = tk.Frame(hero, bg=PANEL)
        actions.pack(fill="x", padx=16, pady=(0, 16))
        Button(actions, "Go to Ingest", lambda: self.show("Ingest"), primary=True).pack(side="left")
        Button(actions, "Ask Qwen", lambda: self.show("Copilot")).pack(side="left", padx=8)
        Button(actions, "Review workflow", lambda: self.show("Review")).pack(side="left")
        grid = tk.Frame(page, bg=BG)
        grid.pack(fill="x")
        for i in range(3):
            grid.columnconfigure(i, weight=1)
        self.dash_status = self.card(grid, "1. Local AI", "Run Qwen locally through Ollama.")
        self.dash_status.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 12))
        self.dash_review = self.card(grid, "2. Invoice Review", "Find missing fields, risk flags, duplicates, and approval route.")
        self.dash_review.grid(row=0, column=1, sticky="nsew", padx=8, pady=(0, 12))
        self.dash_route = self.card(grid, "3. AP Handoff", "Save cards or route JSON to Slack, n8n, Make, Zapier, or HTTPS.")
        self.dash_route.grid(row=0, column=2, sticky="nsew", padx=(8, 0), pady=(0, 12))
        flow_card = self.card(page, "Recommended operating flow", "Designed for marketing + IT invoice automation discovery.")
        flow_card.pack(fill="both", expand=True, pady=(0, 12))
        flow = self.text(flow_card, mono=False)
        flow.insert("end", "Capture → Match → Approve → Pay\n\n")
        flow.insert("end", "1. Capture: ingest invoice, email, PDF, DOCX, CSV, JSON, or Excel workbook and preserve source text plus hash.\n")
        flow.insert("end", "2. Match: compare invoice data against PO, receipt, department, campaign, payment terms, and prior records.\n")
        flow.insert("end", "3. Approve: identify exceptions, missing data, approval thresholds, duplicate risk, and recommended reviewer route.\n")
        flow.insert("end", "4. Pay: create reviewed AP packets, Slack/Teams approval messages, and webhook payloads for n8n, Make, Zapier, or ERP handoff.\n")
        flow.insert("end", "5. Audit: keep source hash, review notes, generated cards, and history so finance can trace every handoff.\n")
        flow.configure(state="disabled")

    def _ingest(self) -> None:
        page = tk.Frame(self.host, bg=BG)
        self.pages["Ingest"] = page
        top = self.card(page, "Start with a source document", "Use this one importer for real invoices, workbooks, PDFs, email exports, logs, or JSON. The Overview button only brings you here; this button opens the file picker.")
        top.pack(fill="x", pady=(0, 12))
        row = tk.Frame(top, bg=PANEL)
        row.pack(fill="x", padx=16, pady=(0, 16))
        Button(row, "Open Invoice / Workbook", self.open_file, primary=True).pack(side="left")
        Button(row, "Load Demo Sample", self.load_sample).pack(side="left", padx=8)
        Button(row, "Export JSON", self.export_json).pack(side="left")
        Button(row, "Run Webhook", self.run_trigger).pack(side="left", padx=8)
        tk.Label(row, textvariable=self.source_name, bg=PANEL, fg=MUTED, font=_font(9)).pack(side="left", padx=14)
        split = tk.PanedWindow(page, orient="horizontal", bg=BG, sashwidth=6, bd=0)
        split.pack(fill="both", expand=True)
        left = self.card(split, "Preserved Source", "Normalized source text with source order retained.")
        right = self.card(split, "Structured Extraction", "JSON output with invoice review, records, evidence, and deliverables.")
        split.add(left, minsize=460)
        split.add(right, minsize=460)
        self.source_box = self.text(left, mono=True)
        self.json_box = self.text(right, mono=True, wrap="none")

    def _extraction(self) -> None:
        page = tk.Frame(self.host, bg=BG)
        self.pages["Extraction"] = page
        grid = tk.Frame(page, bg=BG)
        grid.pack(fill="both", expand=True)
        for i in range(3):
            grid.columnconfigure(i, weight=1)
        self.src_metric = self.metric(grid, "Source", "No file")
        self.schema_metric = self.metric(grid, "Schema", self.schema.get())
        self.model_metric = self.metric(grid, "Model", DEFAULT_MODEL)
        self.src_metric.grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=(0, 12))
        self.schema_metric.grid(row=0, column=1, sticky="ew", padx=8, pady=(0, 12))
        self.model_metric.grid(row=0, column=2, sticky="ew", padx=(8, 0), pady=(0, 12))
        notes = self.card(grid, "Copilot Notes", "Source-grounded confidence notes and next-step recommendations.")
        notes.grid(row=1, column=0, columnspan=3, sticky="nsew")
        grid.rowconfigure(1, weight=1)
        self.notes_box = self.text(notes)
        self.notes_box.insert("end", "Load a document from Ingest.\n")

    def metric(self, master: tk.Misc, title: str, value: str) -> Glass:
        frame = Glass(master)
        tk.Label(frame, text=title.upper(), bg=PANEL, fg=MUTED, font=_font(8, "bold")).pack(anchor="w", padx=14, pady=(12, 2))
        lbl = tk.Label(frame, text=value, bg=PANEL, fg=TEXT, font=_font(13, "bold"), anchor="w")
        lbl.pack(fill="x", padx=14, pady=(0, 12))
        frame.value = lbl  # type: ignore[attr-defined]
        return frame

    def _review(self) -> None:
        page = tk.Frame(self.host, bg=BG)
        self.pages["Review"] = page
        top = self.card(page, "Invoice Review", "Validate fields, identify exceptions, and prepare the approval route before AP handoff.")
        top.pack(fill="x", pady=(0, 12))
        btns = tk.Frame(top, bg=PANEL)
        btns.pack(fill="x", padx=16, pady=(0, 16))
        Button(btns, "Generate AP Packet", lambda: self.add_deliverable("ap_packet"), primary=True).pack(side="left")
        Button(btns, "Slack Approval", lambda: self.add_deliverable("slack_approval")).pack(side="left", padx=8)
        Button(btns, "Mark Needs Review", self.mark_needs_review).pack(side="left")
        Button(btns, "Ask Qwen About This", lambda: self.show("Copilot")).pack(side="left", padx=8)
        review_card = self.card(page, "Review details", "Required fields, exceptions, and recommended route.")
        review_card.pack(fill="both", expand=True)
        self.review_box = self.text(review_card, mono=True)

    def _copilot(self) -> None:
        page = tk.Frame(self.host, bg=BG)
        self.pages["Copilot"] = page
        split = tk.PanedWindow(page, orient="horizontal", bg=BG, sashwidth=6, bd=0)
        split.pack(fill="both", expand=True)
        left = self.card(split, "Ask Qwen", "Ask source-grounded invoice operations questions. Deliverable cards appear when Qwen creates reusable work product.")
        right = self.card(split, "Deliverable Cards", "Open, revise, save, or route reusable outputs.")
        split.add(left, minsize=520)
        split.add(right, minsize=320)
        self.after(220, lambda s=split: self._set_copilot_sash(s))
        quick = tk.Frame(left, bg=PANEL)
        quick.pack(fill="x", padx=16, pady=(0, 8))
        for text in ["What is missing?", "Create AP packet", "Draft Slack approval", "Workflow to reduce manual hours"]:
            Pill(quick, text, lambda t=text: self.quick_ask(t)).pack(side="left", padx=(0, 6))

        self.chat_labels: list[tuple[tk.Widget, str]] = []
        chat_shell = tk.Frame(left, bg=CHAT_BG, highlightthickness=0, bd=0)
        chat_shell.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        chat_shell.pack_propagate(False)
        self.chat_canvas = tk.Canvas(chat_shell, bg=CHAT_BG, highlightthickness=0, bd=0)
        self.chat_canvas.pack(side="left", fill="both", expand=True)
        self.chat_scroll = SlimScrollbar(chat_shell, self.chat_canvas)
        self.chat_scroll.pack(side="right", fill="y", padx=(0, 3), pady=6)
        self.chat_canvas.configure(yscrollcommand=self.chat_scroll.set)
        self.chat_frame = tk.Frame(self.chat_canvas, bg=CHAT_BG)
        self.chat_window = self.chat_canvas.create_window((0, 0), window=self.chat_frame, anchor="nw")
        self.chat_frame.bind("<Configure>", lambda _e: self.chat_canvas.configure(scrollregion=self.chat_canvas.bbox("all")))
        self.chat_canvas.bind("<Configure>", self._resize_chat)
        self.chat_canvas.bind_all("<MouseWheel>", self._chat_mousewheel, add="+")
        self._chat_msg("assistant", "Load an invoice or workbook, then ask Qwen to find issues, draft approvals, produce AP packets, or design a workflow that reduces manual review time. Responses stream directly here as the local model answers.")

        input_wrap = tk.Frame(left, bg="#ffffff", highlightbackground="#c7e8f7", highlightthickness=1, bd=0)
        input_wrap.pack(fill="x", padx=16, pady=(0, 16))
        self.chat_input = tk.Text(input_wrap, height=3, bg="#ffffff", fg=TEXT, relief="flat", insertbackground=TEXT, font=_font(10), wrap="word", padx=12, pady=10, undo=True)
        self.chat_input.pack(side="left", fill="x", expand=True)
        self.chat_input.bind("<Return>", self._chat_return)
        self.chat_input.bind("<Shift-Return>", lambda _e: None)
        Button(input_wrap, "Send", self.ask_qwen, primary=True).pack(side="left", padx=(8, 10), pady=10)
        self.cards_host = tk.Frame(right, bg=PANEL)
        self.cards_host.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    def _routing(self) -> None:
        page = tk.Frame(self.host, bg=BG)
        self.pages["Routing"] = page
        top = self.card(page, "Workflow Routing", "Preview the AP handoff packet and send only when the human reviewer clicks a route button.")
        top.pack(fill="x", pady=(0, 12))
        row = tk.Frame(top, bg=PANEL)
        row.pack(fill="x", padx=16, pady=(0, 16))
        Button(row, "Send Current JSON", self.run_trigger, primary=True).pack(side="left")
        Button(row, "Save AP Packet", lambda: self.save_card_by_id("ap_packet")).pack(side="left", padx=8)
        Button(row, "Open Services", lambda: self.show("Services")).pack(side="left")
        route_card = self.card(page, "Routing preview", "Structured packet sent to webhook destinations.")
        route_card.pack(fill="both", expand=True)
        self.route_box = self.text(route_card, mono=True)

    def _services(self) -> None:
        page = tk.Frame(self.host, bg=BG)
        self.pages["Services"] = page
        intro = self.card(page, "Optional services", "Credentials stay local under ~/.signalloom/services.json. Routing is manual-click only.")
        intro.pack(fill="x", pady=(0, 12))
        form = self.card(page, "Service credentials", "Popular workflow targets for marketing invoice approvals.")
        form.pack(fill="both", expand=True)
        cfg = load_services()
        for name, desc in SUPPORTED_SERVICES.items():
            row = tk.Frame(form, bg=PANEL)
            row.pack(fill="x", padx=16, pady=6)
            tk.Label(row, text=name, bg=PANEL, fg=TEXT, width=18, anchor="w", font=_font(10, "bold")).pack(side="left")
            var = tk.StringVar(value=cfg.get(name, ""))
            self.services[name] = var
            tk.Entry(row, textvariable=var, show="*" if "key" in name.lower() else "", bg=GLASS, fg=TEXT, relief="flat", insertbackground=TEXT, width=58, highlightbackground=LINE, highlightthickness=1).pack(side="left", ipady=8, padx=8)
            tk.Label(row, text=desc, bg=PANEL, fg=MUTED, anchor="w", justify="left", wraplength=420).pack(side="left", fill="x", expand=True)
        Button(form, "Save Local Settings", self.save_services, primary=True).pack(anchor="w", padx=16, pady=16)

    def _setup(self) -> None:
        page = tk.Frame(self.host, bg=BG)
        self.pages["Setup"] = page
        card = self.card(page, "One-click Local AI Setup", "Shows a borderless holographic activity overlay while install/start/download work continues in the background.")
        card.pack(fill="both", expand=True)
        body = self.text(card)
        body.insert("end", f"Default model: {DEFAULT_MODEL}\n\n")
        body.insert("end", "Click the button below. SignalLoom will detect Ollama, start the service, pull qwen3:8b if missing, and verify readiness. The activity overlay is borderless and not forced to stay in front; you can keep using other apps while it works.\n\n")
        body.insert("end", "Unknown-duration work uses an activity animation instead of a fake percent. Real model download progress is shown when Ollama reports bytes downloaded.\n\n")
        body.insert("end", f"Platform: {platform.platform()}\n")
        body.configure(state="disabled")
        Button(card, "One-Click Local AI Setup / Repair", self.prepare_model, primary=True).pack(anchor="w", padx=28, pady=(0, 20))

    def _models(self) -> None:
        page = tk.Frame(self.host, bg=BG)
        self.pages["Models"] = page
        card = self.card(page, "Local AI Status", "Live Ollama service, CLI, version, and installed model list.")
        card.pack(fill="both", expand=True)
        self.model_box = self.text(card, mono=True)
        actions = tk.Frame(card, bg=PANEL)
        actions.pack(fill="x", padx=16, pady=(0, 16))
        Button(actions, "Refresh", self.render_model_status).pack(side="left")
        Button(actions, "Repair / Pull qwen3:8b", self.prepare_model, primary=True).pack(side="left", padx=8)

    def _history(self) -> None:
        page = tk.Frame(self.host, bg=BG)
        self.pages["History"] = page
        hist_card = self.card(page, "Recent Extractions", "Local results stored under ~/.signalloom/signalloom.db.")
        hist_card.pack(fill="both", expand=True)
        self.history_box = self.text(hist_card, mono=True)

    def _info(self) -> None:
        page = tk.Frame(self.host, bg=BG)
        self.pages["Info"] = page
        about = self.card(page, "About SignalLoom", "Attribution, product purpose, invoice automation playbook, and license.")
        about.pack(fill="both", expand=True)
        text = self.text(about)
        text.insert("end", f"{APP}\n{TAG}\n\n")
        text.insert("end", "Purpose\n")
        text.insert("end", "SignalLoom is a local-first InvoiceOps copilot for marketing, finance, and IT teams. It turns invoice files and workbooks into source-faithful extraction records, review packets, approval messages, exception checklists, and webhook-ready AP handoff payloads.\n\n")
        text.insert("end", "Operating Model: Capture → Match → Approve → Pay\n")
        text.insert("end", "• Capture: read invoices from files, Excel workbooks, email exports, PDFs, CSV, JSON, and text while preserving source text and SHA-256 evidence.\n")
        text.insert("end", "• Match: compare invoice facts to PO, receipt, vendor, campaign, cost center, payment terms, department, and approver expectations.\n")
        text.insert("end", "• Approve: detect missing fields, duplicate risk, mismatched status terms, threshold exceptions, and routing gaps.\n")
        text.insert("end", "• Pay: generate reviewed AP packets and route approved JSON to Slack, n8n, Make, Zapier, or a generic HTTPS workflow endpoint.\n\n")
        text.insert("end", "Manual Work This Reduces\n")
        text.insert("end", "• Manual data entry and repeated invoice re-keying.\n")
        text.insert("end", "• Month-end hunts for missing POs, unmatched invoices, and unresolved approval status.\n")
        text.insert("end", "• Human middleware between procurement, marketing, AP, and accounting systems.\n")
        text.insert("end", "• Audit-trail gaps around edits, approvals, vendor details, and exception routing.\n\n")
        text.insert("end", "AI Marketing Operations Fit\n")
        text.insert("end", "The app is designed to sit beside workflow tools such as Slack, n8n, Make, Zapier, spreadsheet trackers, and accounting/ERP handoff processes. Qwen helps users ask operational questions, create AP packets, draft approval language, and revise deliverables before routing.\n\n")
        text.insert("end", f"Creator / contact: {CONTACT}\nEvoMind DOI: {DOI}\nORCID: {ORCID}\n\n")
        text.insert("end", "Coffee License\nUse, modify, and share this app for lawful purposes. Keep attribution to SALT19 LLC and Gabriel Allit in derivative builds. If this saves time or helps your team, buy the creator a coffee or cite the DOI. No warranty is provided.\n")
        text.configure(state="disabled")

    def refresh_status(self) -> None:
        status = self.client.status()
        self.status.set(status.message)
        self.activity.set(status.message)
        self.after(20000, self.refresh_status)

    def render_model_status(self) -> None:
        if not hasattr(self, "model_box"):
            return
        s = self.client.status(detailed=True)
        lines = [
            "LOCAL AI STATUS",
            "===============",
            f"Service reachable: {s.reachable}",
            f"CLI detected:      {bool(s.cli_path)}",
            f"CLI path:          {s.cli_path or 'not found'}",
            f"Version:           {s.version or 'unknown'}",
            f"Default model:     {self.client.model}",
            f"Model available:   {s.model_ready}",
            "",
            "Installed models:",
        ]
        lines.extend([f"  - {name}" for name in s.models] or ["  none detected"])
        lines.extend(["", "Message:", s.message])
        self.model_box.configure(state="normal")
        self.model_box.delete("1.0", "end")
        self.model_box.insert("end", "\n".join(lines))
        self.model_box.configure(state="disabled")

    def prepare_model(self) -> None:
        self.overlay.start("Preparing local AI", percent=None)
        self.activity.set("Preparing local AI…")

        def progress(pct: int | None, text: str) -> None:
            # Percent is reliable only for actual model download progress. Setup
            # phases are treated as activity states to avoid the old 3% hang feel.
            known = pct if "pull" in text.lower() or "download" in text.lower() or pct >= 90 else None
            self.after(0, lambda: (self.overlay.update_progress(known, text), self.activity.set(text)))

        def work() -> None:
            msg = self.client.ensure_ready(progress=progress)
            self.after(0, lambda: (self.overlay.finish(msg), self.activity.set(msg), self.refresh_status(), self.render_model_status(), messagebox.showinfo(APP, msg)))

        threading.Thread(target=work, daemon=True).start()

    def open_file(self) -> None:
        path = filedialog.askopenfilename(title="Open source document", filetypes=[("Supported", "*.txt *.md *.csv *.json *.xml *.html *.htm *.pdf *.docx *.xlsx *.xlsm *.xltx *.xltm *.log"), ("All files", "*.*")])
        if path:
            self.process_file(path)

    def load_sample(self) -> None:
        sample = Path(__file__).resolve().parents[1] / "samples" / "invoice_ops.csv"
        self.process_file(str(sample))

    def process_file(self, path: str) -> None:
        name = Path(path).name
        self.source_name.set(name)
        self.overlay.start(f"Reading {name}", percent=None)
        self.activity.set(f"Reading {name}…")

        def work() -> None:
            try:
                self.after(0, lambda: self.overlay.update_progress(None, "Parsing source"))
                doc = extract_text(path)
                save_document(doc)
                self.after(0, lambda: self.overlay.update_progress(None, "Analyzing invoice workflow"))
                result = analyze(doc, self.schema.get() or "invoice", self.client)
                save_result(result)
                self.current_result = result
                self.current_source = doc.text
                self.cards = list(result.deliverables)
                self.after(0, lambda: self.show_result(result))
            except Exception as exc:
                self.after(0, lambda: (self.overlay.finish("Error"), messagebox.showerror(APP, str(exc))))
            finally:
                self.after(0, lambda: self.activity.set("Ready"))

        threading.Thread(target=work, daemon=True).start()

    def show_result(self, result: Any) -> None:
        self.overlay.finish("Extraction complete")
        self.source_box.delete("1.0", "end")
        self.source_box.insert("end", result.preserved_text)
        self.json_box.delete("1.0", "end")
        self.json_box.insert("end", result.to_json())
        self.notes_box.delete("1.0", "end")
        self.notes_box.insert("end", "\n".join(f"• {note}" for note in result.confidence_notes))
        self.src_metric.value.configure(text=result.source_name)  # type: ignore[attr-defined]
        self.schema_metric.value.configure(text=result.schema_name)  # type: ignore[attr-defined]
        self.model_metric.value.configure(text=result.model)  # type: ignore[attr-defined]
        self.render_review()
        self.render_cards()
        self.render_routing()
        self.show("Review")

    def render_review(self) -> None:
        if not hasattr(self, "review_box"):
            return
        self.review_box.configure(state="normal")
        self.review_box.delete("1.0", "end")
        if not self.current_result:
            self.review_box.insert("end", "No invoice loaded yet.")
        else:
            self.review_box.insert("end", pretty_json(self.current_result.review))
        self.review_box.configure(state="disabled")

    def render_routing(self) -> None:
        if not hasattr(self, "route_box"):
            return
        self.route_box.configure(state="normal")
        self.route_box.delete("1.0", "end")
        if self.current_result:
            payload = {"review": self.current_result.review, "structured_data": self.current_result.structured_data, "deliverables": self.current_result.deliverables}
        else:
            payload = workflow_template()
        self.route_box.insert("end", pretty_json(payload))
        self.route_box.configure(state="disabled")


    def _set_copilot_sash(self, split: tk.PanedWindow) -> None:
        """Give the chat pane priority without hiding the deliverable card rail."""
        try:
            width = max(split.winfo_width(), 1)
            if width <= 1:
                return
            # Keep the right card rail useful while making the chat the primary workspace.
            right_target = 360 if width >= 1120 else 310
            x = max(520, width - right_target)
            split.sash_place(0, x, 1)
        except Exception:
            pass

    def _chat_mousewheel(self, event: tk.Event) -> None:
        if not hasattr(self, "chat_canvas"):
            return
        try:
            under = self.winfo_containing(event.x_root, event.y_root)
            node = under
            inside = False
            while node is not None:
                if node == self.chat_canvas or node == getattr(self, "chat_frame", None):
                    inside = True
                    break
                node = getattr(node, "master", None)
            if inside:
                self.chat_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass

    def _chat_return(self, event: tk.Event) -> str | None:
        # Enter sends. Shift+Enter keeps the default newline behavior.
        if event.state & 0x0001:
            return None
        self.ask_qwen()
        return "break"

    def _chat_wrap(self, role: str) -> int:
        try:
            width = int(self.chat_canvas.winfo_width())
            if width <= 12:
                width = 760
        except Exception:
            width = 760
        ratio = 0.72 if role == "user" else 0.96
        gutter = 96 if role == "user" else 58
        return max(210, min(980, int(max(width, 260) * ratio) - gutter))

    def _chat_chars(self, role: str) -> int:
        # Segoe UI 10pt averages roughly 7 px per character. This estimate is
        # used only to size borderless read-only Text widgets so messages do not
        # clip vertically while the outer chat canvas remains the only scroller.
        return max(28, int(self._chat_wrap(role) / 7.2))

    def _estimate_chat_lines(self, text: str, role: str) -> int:
        chars = self._chat_chars(role)
        lines = 0
        for paragraph in self._normalize_chat_text(text).split("\n"):
            if not paragraph:
                lines += 1
                continue
            # Markdown tables and long machine tokens are common in invoice work.
            # Count them by character width so a long table separator cannot
            # silently clip outside the pane.
            lines += max(1, math.ceil(len(paragraph) / chars))
        return max(1, min(180, lines + 1))

    def _resize_chat(self, event: tk.Event) -> None:
        self.chat_canvas.itemconfigure(self.chat_window, width=max(event.width - 12, 220))
        for widget, role in getattr(self, "chat_labels", []):
            try:
                if isinstance(widget, tk.Text):
                    widget.configure(width=self._chat_chars(role), height=self._estimate_chat_lines(widget.get("1.0", "end-1c"), role))
                else:
                    widget.configure(wraplength=self._chat_wrap(role))
            except tk.TclError:
                pass

    def _normalize_chat_text(self, text: str) -> str:
        cleaned = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        cleaned = cleaned.replace("```markdown", "```").replace("```text", "```")
        # Remove empty streaming artifacts and normalize excessive vertical gaps.
        while "\n\n\n" in cleaned:
            cleaned = cleaned.replace("\n\n\n", "\n\n")
        return cleaned or "No response returned."

    def _chat_text(self, master: tk.Misc, text: str, role: str, *, error: bool = False) -> tk.Text:
        bg = USER_BG if role == "user" else CHAT_BG
        fg = RED if error else (TEXT if role == "user" else ASSISTANT_INK)
        box = tk.Text(
            master,
            bg=bg,
            fg=fg,
            relief="flat",
            bd=0,
            highlightthickness=0,
            insertbackground=fg,
            font=_font(10),
            wrap="char",
            padx=0,
            pady=0,
            cursor="arrow",
            takefocus=0,
            exportselection=False,
            width=self._chat_chars(role),
            height=self._estimate_chat_lines(text, role),
        )
        box.insert("1.0", self._normalize_chat_text(text))
        box.configure(state="disabled")
        return box

    def _set_chat_text(self, widget: tk.Widget, text: str, role: str = "assistant", *, error: bool = False) -> None:
        cleaned = self._normalize_chat_text(text)
        if isinstance(widget, tk.Text):
            widget.configure(state="normal", fg=RED if error else (TEXT if role == "user" else ASSISTANT_INK))
            widget.delete("1.0", "end")
            widget.insert("1.0", cleaned)
            widget.configure(height=self._estimate_chat_lines(cleaned, role), width=self._chat_chars(role), state="disabled")
        else:
            widget.configure(text=cleaned, fg=RED if error else ASSISTANT_INK, wraplength=self._chat_wrap(role))

    def _chat_msg(self, role: str, text: str) -> None:
        text = self._normalize_chat_text(text)
        row = tk.Frame(self.chat_frame, bg=CHAT_BG)
        row.pack(fill="x", padx=10, pady=(8, 4))

        if role == "user":
            bubble = tk.Frame(row, bg=USER_BG, highlightbackground=USER_LINE, highlightthickness=1, bd=0)
            bubble.pack(side="right", anchor="e", padx=(72, 12), pady=2)
            body = self._chat_text(bubble, text, "user")
            body.pack(anchor="w", padx=14, pady=10)
        else:
            # Assistant messages intentionally mirror modern lab chat surfaces:
            # no bubble border, no terminal frame, no hard rectangle. Only
            # deliverable cards get a bordered container because they are files.
            bubble = tk.Frame(row, bg=CHAT_BG, bd=0, highlightthickness=0)
            bubble.pack(side="left", anchor="w", padx=(12, 18), pady=2, fill="x")
            body = self._chat_text(bubble, text, "assistant")
            body.pack(anchor="w", fill="x", padx=0, pady=(2, 8))

        self.chat_labels.append((body, role))
        self.after(20, lambda: self.chat_canvas.yview_moveto(1.0))

    def _chat_placeholder(self, text: str = "Thinking…") -> tk.Text:
        row = tk.Frame(self.chat_frame, bg=CHAT_BG)
        row.pack(fill="x", padx=10, pady=(8, 4))
        bubble = tk.Frame(row, bg=CHAT_BG, bd=0, highlightthickness=0)
        bubble.pack(side="left", anchor="w", padx=(12, 18), pady=2, fill="x")
        label = self._chat_text(bubble, text, "assistant")
        label.pack(anchor="w", fill="x", padx=0, pady=(2, 8))
        self.chat_labels.append((label, "assistant"))
        self.after(20, lambda: self.chat_canvas.yview_moveto(1.0))
        return label

    def _update_chat_message(self, label: tk.Widget, text: str, error: bool = False) -> None:
        self._set_chat_text(label, text, "assistant", error=error)
        self.after(20, lambda: self.chat_canvas.yview_moveto(1.0))

    def _chat_card(self, card: dict[str, Any]) -> None:
        row = tk.Frame(self.chat_frame, bg=CHAT_BG)
        row.pack(fill="x", padx=12, pady=8)
        frame = tk.Frame(row, bg="#ffffff", highlightbackground="#bdefff", highlightthickness=1, bd=0)
        frame.pack(side="left", anchor="w", padx=(12, 24), fill="x")
        tk.Label(frame, text=f"Deliverable: {card.get('title', 'Card')}", bg="#ffffff", fg=TEXT, font=_font(10, "bold")).pack(anchor="w", padx=14, pady=(12, 3))
        preview_full = str(card.get("body", "")).strip()
        preview = preview_full[:260].replace("\n", " ") + ("…" if len(preview_full) > 260 else "")
        body = tk.Label(frame, text=preview, bg="#ffffff", fg=MUTED, wraplength=self._chat_wrap("assistant"), justify="left", font=_font(9))
        body.pack(anchor="w", fill="x", padx=14)
        self.chat_labels.append((body, "assistant"))
        row2 = tk.Frame(frame, bg="#ffffff")
        row2.pack(anchor="w", padx=14, pady=12)
        Button(row2, "Open", lambda c=card: self.view_card(c)).pack(side="left")
        Button(row2, "Save", lambda c=card: self.save_card(c)).pack(side="left", padx=6)
        Button(row2, "Revise", lambda c=card: self.revise_card(c)).pack(side="left")
        self.after(20, lambda: self.chat_canvas.yview_moveto(1.0))

    def render_cards(self) -> None:
        if not hasattr(self, "cards_host"):
            return
        for child in self.cards_host.winfo_children():
            child.destroy()
        if not self.cards:
            tk.Label(self.cards_host, text="No cards yet. Ingest an invoice or ask Qwen for a deliverable.", bg=PANEL, fg=MUTED, wraplength=360, justify="left", font=_font(10)).pack(anchor="w")
            return
        for card in self.cards:
            frame = tk.Frame(self.cards_host, bg=CARD_BG, highlightbackground=LINE, highlightthickness=1)
            frame.pack(fill="x", pady=(0, 10))
            tk.Label(frame, text=card.get("title", "Deliverable"), bg=CARD_BG, fg=TEXT, font=_font(11, "bold")).pack(anchor="w", padx=12, pady=(10, 2))
            preview = str(card.get("body", ""))[:190].replace("\n", " ")
            tk.Label(frame, text=preview + ("…" if len(str(card.get("body", ""))) > 190 else ""), bg=CARD_BG, fg=MUTED, wraplength=380, justify="left", font=_font(9)).pack(anchor="w", padx=12)
            row = tk.Frame(frame, bg=CARD_BG)
            row.pack(fill="x", padx=12, pady=10)
            Button(row, "Open", lambda c=card: self.view_card(c)).pack(side="left")
            Button(row, "Save", lambda c=card: self.save_card(c)).pack(side="left", padx=6)
            Button(row, "Revise", lambda c=card: self.revise_card(c)).pack(side="left")

    def add_deliverable(self, card_id: str) -> None:
        if not self.current_result:
            messagebox.showwarning(APP, "Ingest an invoice first.")
            return
        for card in self.current_result.deliverables:
            if card.get("id") == card_id:
                self.cards.append(card)
                self.render_cards()
                self.show("Copilot")
                self._chat_card(card)
                return

    def mark_needs_review(self) -> None:
        if not self.current_result:
            return
        self.current_result.review.setdefault("risk_flags", []).append("human_marked_needs_review")
        self.current_result.review["payment_ready"] = False
        self.current_result.review["recommended_action"] = "Human reviewer marked this invoice as needing review. Resolve notes before routing."
        self.current_result.deliverables = make_deliverables(self.current_result.to_dict(), self.current_result.review)
        self.cards = list(self.current_result.deliverables)
        self.render_review()
        self.render_cards()

    def quick_ask(self, text: str) -> None:
        self.chat_input.delete("1.0", "end")
        self.chat_input.insert("1.0", text)
        self.ask_qwen()

    def ask_qwen(self) -> None:
        prompt = self.chat_input.get("1.0", "end").strip()
        if not prompt:
            return
        self.chat_input.delete("1.0", "end")
        self._chat_msg("user", prompt)
        placeholder = self._chat_placeholder("Qwen is starting…")
        context = self.current_result.to_dict() if self.current_result else {"workflow_template": workflow_template()}
        self.activity.set("Qwen is streaming an answer…")

        def stream_update(text: str) -> None:
            self.after(0, lambda t=text: self._update_chat_message(placeholder, t))

        def work() -> None:
            try:
                answer = self.client.stream_chat(prompt, context, stream_update)
            except Exception as exc:
                answer = (
                    "**Local AI response failed**\n"
                    f"- Error: {exc}\n"
                    "- SignalLoom kept the app responsive. Try Local AI Setup, then ask again.\n"
                    "- You can still use deterministic review cards and routing payloads.\n"
                )
                self.after(0, lambda: self._update_chat_message(placeholder, answer, error=True))
                self.after(0, lambda: self.activity.set("Qwen answer failed"))
                return
            self.after(0, lambda: self._chat_done(prompt, answer, placeholder))

        threading.Thread(target=work, daemon=True).start()


    def _chat_done(self, prompt: str, answer: str, placeholder: tk.Widget | None = None) -> None:
        self.activity.set("Answer ready")
        if placeholder is not None:
            self._update_chat_message(placeholder, answer)
        else:
            self._chat_msg("assistant", answer)
        lower = prompt.lower()
        if any(word in lower for word in ["packet", "slack", "approval", "message", "workflow", "save", "deliverable", "revise"]):
            card = {"id": f"qwen_{len(self.cards)+1}", "title": "Qwen Draft", "kind": "markdown", "body": answer}
            self.cards.append(card)
            self.render_cards()
            self._chat_card(card)

    def revise_card(self, card: dict[str, Any]) -> None:
        win = tk.Toplevel(self)
        win.title("Revise deliverable")
        win.geometry("720x520")
        win.configure(bg=BG)
        tk.Label(win, text=f"Revise: {card.get('title')}", bg=BG, fg=TEXT, font=_font(16, "bold")).pack(anchor="w", padx=18, pady=(18, 6))
        tk.Label(win, text="Describe the correction or improvement. Qwen will rewrite the card and keep it editable.", bg=BG, fg=MUTED, font=_font(9)).pack(anchor="w", padx=18, pady=(0, 10))
        entry = tk.Text(win, bg=PANEL, fg=TEXT, relief="flat", padx=12, pady=12, height=6, font=_font(10), highlightbackground=LINE, highlightthickness=1)
        entry.pack(fill="x", padx=18)
        output = tk.Text(win, bg=PANEL, fg=TEXT, relief="flat", padx=12, pady=12, font=_font(10), highlightbackground=LINE, highlightthickness=1)
        output.pack(fill="both", expand=True, padx=18, pady=12)

        def apply_text(text: str) -> None:
            card["body"] = text
            output.delete("1.0", "end")
            output.insert("end", text)
            self.render_cards()

        def run() -> None:
            request = entry.get("1.0", "end").strip() or "Revise this deliverable for clarity, accuracy, and business-ready formatting."
            context = {"card": card, "current_result": self.current_result.to_dict() if self.current_result else {}}
            self.overlay.start("Revising card", percent=None)

            def work() -> None:
                revised = self.client.chat(f"Revise this deliverable. Request: {request}", context)
                self.after(0, lambda: (self.overlay.finish("Revision ready"), apply_text(revised)))

            threading.Thread(target=work, daemon=True).start()

        Button(win, "Revise with Qwen", run, primary=True).pack(anchor="w", padx=18, pady=(0, 14))

    def view_card(self, card: dict[str, Any]) -> None:
        win = tk.Toplevel(self)
        win.title(str(card.get("title", "Deliverable")))
        win.geometry("780x580")
        win.configure(bg=BG)
        tk.Label(win, text=str(card.get("title", "Deliverable")), bg=BG, fg=TEXT, font=_font(16, "bold")).pack(anchor="w", padx=18, pady=(18, 6))
        box = tk.Text(win, bg=PANEL, fg=TEXT, relief="flat", padx=12, pady=12, font=(MONO, 10), highlightbackground=LINE, highlightthickness=1)
        box.pack(fill="both", expand=True, padx=18, pady=(0, 12))
        box.insert("end", str(card.get("body", "")))
        row = tk.Frame(win, bg=BG)
        row.pack(fill="x", padx=18, pady=(0, 18))
        Button(row, "Save", lambda: self.save_card(card), primary=True).pack(side="left")
        Button(row, "Close", win.destroy).pack(side="left", padx=8)

    def save_card(self, card: dict[str, Any]) -> None:
        suffix = ".json" if card.get("kind") == "json" else ".md"
        path = filedialog.asksaveasfilename(initialfile=f"{card.get('id', 'deliverable')}{suffix}", defaultextension=suffix, filetypes=[("Markdown", "*.md"), ("JSON", "*.json"), ("Text", "*.txt"), ("All", "*.*")])
        if path:
            Path(path).write_text(str(card.get("body", "")), encoding="utf-8")
            self.activity.set(f"Saved {Path(path).name}")

    def save_card_by_id(self, card_id: str) -> None:
        for card in self.cards:
            if card.get("id") == card_id:
                path = export_text(str(card.get("id")), str(card.get("body", "")), ".md")
                messagebox.showinfo(APP, f"Saved: {path}")
                return
        messagebox.showwarning(APP, "No AP packet card is available yet.")

    def save_services(self) -> None:
        save_services({k: v.get().strip() for k, v in self.services.items() if v.get().strip()})
        messagebox.showinfo(APP, "Service settings saved locally.")

    def export_json(self) -> None:
        if not self.current_result:
            messagebox.showwarning(APP, "No result to export yet.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path:
            Path(path).write_text(self.current_result.to_json(), encoding="utf-8")
            self.activity.set(f"Exported {Path(path).name}")

    def run_trigger(self) -> None:
        if not self.current_result:
            messagebox.showwarning(APP, "Ingest a document first.")
            return
        try:
            key, response = route_payload(self.current_result.to_dict(), load_services())
            messagebox.showinfo(APP, f"Triggered {key}: {response}")
        except Exception as exc:
            messagebox.showerror(APP, f"Trigger failed: {exc}")

    def load_history(self) -> None:
        self.history_box.configure(state="normal")
        self.history_box.delete("1.0", "end")
        try:
            rows = recent_results(40)
        except Exception as exc:
            self.history_box.insert("end", f"Could not read local history: {exc}")
            return
        if not rows:
            self.history_box.insert("end", "No history yet.")
        for row in rows:
            self.history_box.insert("end", f"{row.get('created_at')} | {row.get('source_name')} | {row.get('schema_name')} | {row.get('model')} | ready={row.get('review', {}).get('payment_ready')}\n")
        self.history_box.configure(state="disabled")


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
