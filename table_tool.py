"""
Insert Table Mixin for Red Enclosure.

The toolbar button "Insert Table" (right after Spring) opens a Word-style grid picker. The chosen table
is drawn on the workspace and works like a small Word table:

  * click a cell and type (blinking cursor); Tab / Shift+Tab move between cells,
    Tab in the very last cell adds a new row; Up / Down move to the cell above / below
  * drag a column border or a row border to make that column wider or that row taller
  * handles around the selected table: move, delete, resize the whole table, add row, add column
  * right click: insert / delete rows and columns, distribute evenly, shade a cell, colours, font size
  * rows grow by themselves when the text needs more room
  * zooms with the workspace, undo / redo, saved with the project (see the hooks in the other modules)

Sizes are stored in zoom-independent units (pixels at 100 % zoom). The position comes from an invisible anchor
rectangle that the canvas scales together with everything else, so a table never drifts when zooming.
Table interaction works in Select mode (inserting a table switches to it).
"""
import copy
import tkinter as tk
import tkinter.font as tkfont
import customtkinter as ctk

TBL_MIN_W = 30.0       # smallest column width (units = pixels at 100 % zoom)
TBL_MIN_H = 18.0       # smallest row height
TBL_DEF_W = 100.0
TBL_DEF_H = 32.0
TBL_FONT = 12
TBL_PAD = 5.0
TBL_LINE = 2.0
GRID_COLS, GRID_ROWS = 10, 8   # the Word-style picker
MAX_ROWS, MAX_COLS = 100, 20
HIT = 5                # px: how close to a border counts as "on the border"
HANDLE = 16            # px: size of the small handles
UI_COLOR = "#f1c40f"
CELL_SEL = "#3498db"
BROWN, BROWN_HOVER = "#8B4513", "#6B3410"

CURSORS = {"col": "sb_h_double_arrow", "row": "sb_v_double_arrow", "move": "fleur", "resize": "size_nw_se",
           "cell": "xterm", "delete": "hand2", "addrow": "hand2", "addcol": "hand2"}


class TableToolMixin:

  # ------------------------------------------------------------------ setup / helpers
  def init_table_tool(self):
    self._tables = []
    self._table_next = 1
    self._table_sel = None      # id of the selected table
    self._table_cell = None     # (row, col) of the selected cell
    self._table_edit = None     # (table id, row, col) while typing
    self._table_idx = 0         # cursor position inside the cell text
    self._table_pre = None      # history snapshot taken when typing started
    self._table_changed = False
    self._table_op = None       # running mouse operation (border drag, move, resize...)
    self._table_cursor_set = False
    self._table_picker = None
    for ev in ("<Button-3>", "<Button-2>"):
      self.canvas.bind(ev, self.table_context, add="+")

  def _table_by_id(self, tid):
    for t in self._tables:
      if t["id"] == tid:
        return t
    return None

  def _table_origin(self, t):
    """Top-left corner on the canvas, read from the anchor rectangle (it is scaled by the zoom)."""
    bg = self.canvas.find_withtag(f"tbl{t['id']}&&tbl_bg")
    if bg:
      co = self.canvas.coords(bg[0])
      if len(co) >= 2:
        return co[0], co[1]
    return t["ox"], t["oy"]

  def _table_text_color(self):
    try:
      r, g, b = self.winfo_rgb(self.canvas.cget("bg"))
      lum = (0.299 * r + 0.587 * g + 0.114 * b) / 65535.0
    except Exception:
      lum = 0.0
    return "#000000" if lum > 0.55 else "#ffffff"

  @staticmethod
  def _table_new_cell():
    return {"text": "", "fill": ""}

  def _table_validate(self, t):
    """Makes loaded data safe: all lists have matching sizes, all fields exist."""
    t["cw"] = [max(TBL_MIN_W, float(w)) for w in t.get("cw", [])] or [TBL_DEF_W]
    t["rh"] = [max(TBL_MIN_H, float(h)) for h in t.get("rh", [])] or [TBL_DEF_H]
    R, C = len(t["rh"]), len(t["cw"])
    cells = t.get("cells", [])
    fixed = []
    for r in range(R):
      row = cells[r] if r < len(cells) and isinstance(cells[r], list) else []
      new_row = []
      for k in range(C):
        src = row[k] if k < len(row) and isinstance(row[k], dict) else {}
        new_row.append({"text": str(src.get("text", "")), "fill": src.get("fill", "") or ""})
      fixed.append(new_row)
    t["cells"] = fixed
    t.setdefault("color", "#e74c3c")
    t.setdefault("tcolor", "#ffffff")
    t["fs"] = max(6, min(48, int(t.get("fs", TBL_FONT))))
    t["lw"] = float(t.get("lw", TBL_LINE))
    t["ox"] = float(t.get("ox", 0.0))
    t["oy"] = float(t.get("oy", 0.0))

  # ------------------------------------------------------------------ inserting
  def table_insert(self, rows, cols):
    rows = max(1, min(MAX_ROWS, int(rows)))
    cols = max(1, min(MAX_COLS, int(cols)))
    c = self.canvas
    z = self.current_zoom_scale
    view_w, view_h = max(300, c.winfo_width()), max(300, c.winfo_height())
    cw = max(TBL_MIN_W, min(TBL_DEF_W, (view_w * 0.85 / z) / cols))
    W, H = cw * cols * z, TBL_DEF_H * rows * z
    ox = c.canvasx(view_w / 2) - W / 2
    oy = c.canvasy(view_h / 2) - H / 2
    if H > view_h * 0.8:
      oy = c.canvasy(0) + 30
    self.finish_text_edit()
    self.table_deselect()
    if hasattr(self, "set_mode"):
      self.set_mode("select")
    self.begin_change()
    t = {"id": self._table_next, "ox": ox, "oy": oy, "cw": [cw] * cols, "rh": [TBL_DEF_H] * rows,
         "cells": [[self._table_new_cell() for _ in range(cols)] for _ in range(rows)],
         "color": getattr(self, "pen_color", "#e74c3c"), "tcolor": self._table_text_color(),
         "fs": TBL_FONT, "lw": TBL_LINE}
    self._table_next += 1
    self._tables.append(t)
    self.table_draw(t)
    self.commit_change()
    self.selected_item = None
    if hasattr(self, "show_handles"):
      self.show_handles()
    self._table_sel = t["id"]
    self.canvas.focus_set()
    self._table_begin_edit(t, 0, 0)
    self.table_draw(t)

  def open_table_picker(self):
    win = getattr(self, "_table_picker", None)
    if win is not None and win.winfo_exists():
      win.lift()
      win.focus_force()
      return
    win = ctk.CTkToplevel(self)
    win.title("Insert Table")
    win.resizable(False, False)
    win.attributes("-topmost", True)
    self._table_picker = win
    CELL, PADG = 26, 6

    ctk.CTkLabel(win, text="📊 Insert Table", font=ctk.CTkFont(size=14, weight="bold"),
                 text_color="#e0a070").pack(pady=(12, 2))
    info = ctk.CTkLabel(win, text="Move over the grid and click", font=ctk.CTkFont(size=12), text_color="#a0a0a0")
    info.pack()
    gc = tk.Canvas(win, width=GRID_COLS * CELL + PADG * 2, height=GRID_ROWS * CELL + PADG * 2,
                   bg="#1b1b1b", highlightthickness=0)
    gc.pack(padx=12, pady=8)
    cells = {}
    for r in range(GRID_ROWS):
      for k in range(GRID_COLS):
        x0, y0 = PADG + k * CELL, PADG + r * CELL
        cells[(r, k)] = gc.create_rectangle(x0 + 1, y0 + 1, x0 + CELL - 3, y0 + CELL - 3,
                                            outline="#555555", fill="#101010")

    def paint(rows, cols):
      for (r, k), i in cells.items():
        on = r < rows and k < cols
        gc.itemconfigure(i, fill=BROWN if on else "#101010", outline="#e0a070" if on else "#555555")

    def where(e):
      k = int((e.x - PADG) // CELL) + 1
      r = int((e.y - PADG) // CELL) + 1
      if not (1 <= k <= GRID_COLS and 1 <= r <= GRID_ROWS):
        return 0, 0
      return r, k

    def on_move(e):
      r, k = where(e)
      paint(r, k)
      info.configure(text=f"{k} columns × {r} rows" if r and k else "Move over the grid and click")

    def pick(rows, cols):
      win.destroy()
      self.table_insert(rows, cols)

    def on_click(e):
      r, k = where(e)
      if r and k:
        pick(r, k)

    gc.bind("<Motion>", on_move)
    gc.bind("<Button-1>", on_click)
    gc.bind("<Leave>", lambda e: (paint(0, 0), info.configure(text="Move over the grid and click")))

    row = ctk.CTkFrame(win, fg_color="transparent")
    row.pack(pady=(0, 12))
    ctk.CTkLabel(row, text="Rows").pack(side="left", padx=(6, 3))
    rows_e = ctk.CTkEntry(row, width=52)
    rows_e.insert(0, "3")
    rows_e.pack(side="left")
    ctk.CTkLabel(row, text="Columns").pack(side="left", padx=(10, 3))
    cols_e = ctk.CTkEntry(row, width=52)
    cols_e.insert(0, "3")
    cols_e.pack(side="left")

    def custom():
      try:
        rr, cc = int(rows_e.get()), int(cols_e.get())
      except ValueError:
        info.configure(text="Enter whole numbers")
        return
      if not (1 <= rr <= MAX_ROWS and 1 <= cc <= MAX_COLS):
        info.configure(text=f"Rows 1-{MAX_ROWS}, columns 1-{MAX_COLS}")
        return
      pick(rr, cc)

    ctk.CTkButton(row, text="Insert", width=70, fg_color=BROWN, hover_color=BROWN_HOVER,
                  command=custom).pack(side="left", padx=(12, 6))
    win.bind("<Escape>", lambda e: win.destroy())

    self.update_idletasks()
    btn = getattr(self, "insert_table_btn", None)
    if btn is not None:
      x, y = btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height() + 4
    else:
      x, y = self.winfo_rootx() + 80, self.winfo_rooty() + 100
    win.geometry(f"+{x}+{y}")
    win.after(150, win.focus_force)

  # ------------------------------------------------------------------ drawing
  def table_draw(self, t):
    """(Re)draws one table at the current zoom from its model. Rows grow when the text needs more room."""
    c = self.canvas
    z = self.current_zoom_scale
    tag = f"tbl{t['id']}"
    ox, oy = self._table_origin(t)
    t["ox"], t["oy"] = ox, oy
    c.delete(tag)

    fsz = max(4, int(round(t["fs"] * z)))
    font = ("Arial", fsz)
    line_h = tkfont.Font(family="Arial", size=fsz).metrics("linespace")
    pad = max(2.0, TBL_PAD * z)
    R, C = len(t["rh"]), len(t["cw"])
    colpx = [w * z for w in t["cw"]]
    rowpx = [h * z for h in t["rh"]]
    editing = self._table_edit if (self._table_edit and self._table_edit[0] == t["id"]) else None

    texts = {}
    for r in range(R):
      for k in range(C):
        cell = t["cells"][r][k]
        is_edit = editing is not None and editing[1] == r and editing[2] == k
        if cell["text"] or is_edit:
          tid = c.create_text(0, 0, text=cell["text"], anchor="nw", fill=t["tcolor"], font=font,
                              width=max(8, int(colpx[k] - 2 * pad)), tags=(tag, "table_item", "tbl_text"))
          texts[(r, k)] = tid
          bb = c.bbox(tid)
          h = (bb[3] - bb[1]) if (bb and cell["text"]) else line_h
          rowpx[r] = max(rowpx[r], h + 2 * pad)

    xs = [ox]
    for w in colpx:
      xs.append(xs[-1] + w)
    ys = [oy]
    for h in rowpx:
      ys.append(ys[-1] + h)
    t["_xs"], t["_ys"], t["_rowpx"], t["_texts"] = xs, ys, rowpx, texts

    c.create_rectangle(xs[0], ys[0], xs[-1], ys[-1], outline="", fill="", tags=(tag, "table_item", "tbl_bg"))
    for r in range(R):
      for k in range(C):
        fill = t["cells"][r][k]["fill"]
        if fill:
          c.create_rectangle(xs[k], ys[r], xs[k + 1], ys[r + 1], fill=fill, outline="",
                             tags=(tag, "table_item", "tbl_fill"))
    lw = max(1.0, t["lw"] * z)
    for i in range(R + 1):
      c.create_line(xs[0], ys[i], xs[-1], ys[i], fill=t["color"], width=lw, capstyle="projecting",
                    tags=(tag, "table_item", "tbl_line"))
    for j in range(C + 1):
      c.create_line(xs[j], ys[0], xs[j], ys[-1], fill=t["color"], width=lw, capstyle="projecting",
                    tags=(tag, "table_item", "tbl_line"))
    for (r, k), tid in texts.items():
      c.coords(tid, xs[k] + pad, ys[r] + pad)
    for tid in texts.values():
      c.tag_raise(tid)

    if editing is not None:
      tid = texts.get((editing[1], editing[2]))
      if tid:
        c.focus(tid)
        c.icursor(tid, self._table_idx)
    if self._table_sel == t["id"]:
      self._table_draw_ui(t)

  def _table_handles(self, t):
    xs, ys = t["_xs"], t["_ys"]
    x0, y0, x1, y1 = xs[0], ys[0], xs[-1], ys[-1]
    h = HANDLE
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    return {
        "move": (x0 - h - 4, y0 - h - 4, x0 - 4, y0 - 4),
        "delete": (x1 + 4, y0 - h - 4, x1 + h + 4, y0 - 4),
        "resize": (x1 + 4, y1 + 4, x1 + h + 4, y1 + h + 4),
        "addrow": (mx - h / 2, y1 + 4, mx + h / 2, y1 + 4 + h),
        "addcol": (x1 + 4, my - h / 2, x1 + 4 + h, my + h / 2),
    }

  def _table_draw_ui(self, t):
    c = self.canvas
    tag = f"tbl{t['id']}"
    c.delete(f"{tag}&&table_ui")
    xs, ys = t["_xs"], t["_ys"]
    c.create_rectangle(xs[0] - 2, ys[0] - 2, xs[-1] + 2, ys[-1] + 2, outline=UI_COLOR, dash=(4, 3), width=1,
                       fill="", tags=(tag, "table_ui"))
    cell = self._table_cell
    if cell and cell[0] < len(ys) - 1 and cell[1] < len(xs) - 1:
      r, k = cell
      c.create_rectangle(xs[k] + 2, ys[r] + 2, xs[k + 1] - 2, ys[r + 1] - 2, outline=CELL_SEL, width=2,
                         fill="", tags=(tag, "table_ui"))
    symbols = {"move": "✥", "delete": "✕", "resize": "⤡", "addrow": "+", "addcol": "+"}
    for name, (a, b, c2, d) in self._table_handles(t).items():
      c.create_rectangle(a, b, c2, d, fill="#1b1b1b", outline=UI_COLOR, width=1, tags=(tag, "table_ui"))
      c.create_text((a + c2) / 2, (b + d) / 2, text=symbols[name], fill=UI_COLOR, font=("Arial", 9, "bold"),
                    tags=(tag, "table_ui"))

  # ------------------------------------------------------------------ hit testing
  def _table_cell_at(self, t, x, y):
    xs, ys = t["_xs"], t["_ys"]
    k = 0
    for j in range(len(xs) - 1):
      if x >= xs[j]:
        k = j
    r = 0
    for i in range(len(ys) - 1):
      if y >= ys[i]:
        r = i
    return r, k

  def _table_hit(self, x, y):
    """Returns (table, kind, arg) for the topmost table part under the point, or None."""
    for t in reversed(self._tables):
      if "_xs" not in t:
        continue
      xs, ys = t["_xs"], t["_ys"]
      if t["id"] == self._table_sel:
        for name, (a, b, c2, d) in self._table_handles(t).items():
          if a <= x <= c2 and b <= y <= d:
            return t, name, None
      if xs[0] - HIT <= x <= xs[-1] + HIT and ys[0] - HIT <= y <= ys[-1] + HIT:
        for j in range(1, len(xs)):
          if abs(x - xs[j]) <= HIT:
            return t, "col", j
        for i in range(1, len(ys)):
          if abs(y - ys[i]) <= HIT:
            return t, "row", i
        if xs[0] <= x <= xs[-1] and ys[0] <= y <= ys[-1]:
          return t, "cell", self._table_cell_at(t, x, y)
    return None

  def table_hover(self, event):
    if not self._tables and not self._table_cursor_set:
      return
    kind = None
    if getattr(self, "mode", "select") == "select":
      hit = self._table_hit(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
      kind = hit[1] if hit else None
    cur = CURSORS.get(kind, "")
    try:
      if cur:
        self.canvas.configure(cursor=cur)
        self._table_cursor_set = True
      elif self._table_cursor_set:
        self.canvas.configure(cursor="")
        self._table_cursor_set = False
    except tk.TclError:
      self.canvas.configure(cursor="fleur" if kind == "resize" else "")

  # ------------------------------------------------------------------ mouse
  def table_press(self, x, y):
    """Called by the canvas press handler (Select mode). True = the click belonged to a table."""
    hit = self._table_hit(x, y)
    if hit is None:
      return False
    t, kind, arg = hit
    z = self.current_zoom_scale
    self.selected_item = None
    if hasattr(self, "show_handles"):
      self.show_handles()
    self.canvas.focus_set()
    self._table_op = {"kind": "noop", "id": t["id"]}

    if kind == "cell":
      self._table_click_cell(t, arg, x, y)
    elif kind == "col":
      self._table_select(t, self._table_cell)
      self.begin_change()
      self._table_op = {"kind": "col", "id": t["id"], "j": arg, "x0": x, "w0": t["cw"][arg - 1], "moved": False}
    elif kind == "row":
      self._table_select(t, self._table_cell)
      self.begin_change()
      self._table_op = {"kind": "row", "id": t["id"], "i": arg, "y0": y, "h0": t["_rowpx"][arg - 1] / z,
                        "moved": False}
    elif kind == "move":
      self._table_select(t, self._table_cell)
      self.begin_change()
      self._table_op = {"kind": "move", "id": t["id"], "x0": x, "y0": y, "moved": False}
    elif kind == "resize":
      self._table_select(t, self._table_cell)
      self.begin_change()
      self._table_op = {"kind": "resize", "id": t["id"], "ox": t["_xs"][0], "oy": t["_ys"][0],
                        "W": max(1.0, sum(t["cw"]) * z), "H": max(1.0, sum(t["rh"]) * z),
                        "w0": list(t["cw"]), "h0": list(t["rh"]), "moved": False}
    elif kind == "delete":
      self.table_delete(t)
    elif kind == "addrow":
      self._add_row(t, len(t["rh"]), len(t["rh"]) - 1)
    elif kind == "addcol":
      self._add_col(t, len(t["cw"]), len(t["cw"]) - 1)
    return True

  def table_drag(self, x, y):
    op = self._table_op
    t = self._table_by_id(op.get("id")) if op else None
    if op is None or t is None:
      return
    z = self.current_zoom_scale
    kind = op["kind"]
    if kind == "col":
      new = max(TBL_MIN_W, op["w0"] + (x - op["x0"]) / z)
      if abs(new - t["cw"][op["j"] - 1]) > 1e-6:
        t["cw"][op["j"] - 1] = new
        op["moved"] = True
        self.table_draw(t)
    elif kind == "row":
      new = max(TBL_MIN_H, op["h0"] + (y - op["y0"]) / z)
      if abs(new - t["rh"][op["i"] - 1]) > 1e-6:
        t["rh"][op["i"] - 1] = new
        op["moved"] = True
        self.table_draw(t)
    elif kind == "move":
      dx, dy = x - op["x0"], y - op["y0"]
      if dx or dy:
        self.canvas.move(f"tbl{t['id']}", dx, dy)
        t["_xs"] = [v + dx for v in t["_xs"]]
        t["_ys"] = [v + dy for v in t["_ys"]]
        op["x0"], op["y0"] = x, y
        op["moved"] = True
    elif kind == "resize":
      kx = max(0.1, (x - op["ox"]) / op["W"])
      ky = max(0.1, (y - op["oy"]) / op["H"])
      t["cw"] = [max(TBL_MIN_W, w * kx) for w in op["w0"]]
      t["rh"] = [max(TBL_MIN_H, h * ky) for h in op["h0"]]
      op["moved"] = True
      self.table_draw(t)

  def table_release(self, event=None):
    op, self._table_op = self._table_op, None
    if op is None:
      return
    if op["kind"] in ("col", "row", "move", "resize"):
      t = self._table_by_id(op.get("id"))
      if op.get("moved"):
        if t is not None:
          self.table_draw(t)
        self.commit_change()
      else:
        self.pre_state = None

  def table_double_click(self, event):
    """True = the double click was on a table (editing already started with the first click)."""
    if getattr(self, "mode", "select") != "select":
      return False
    x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
    if self._table_hit(x, y) is None:
      self.table_deselect()
      return False
    return True

  # ------------------------------------------------------------------ selection / editing
  def table_deselect(self):
    if self._table_sel is None and self._table_edit is None:
      return
    self.table_finish_edit()
    tid, self._table_sel, self._table_cell = self._table_sel, None, None
    if tid is not None:
      self.canvas.delete(f"tbl{tid}&&table_ui")

  def _table_select(self, t, cell):
    self.table_finish_edit()
    prev = self._table_sel
    if prev is not None and prev != t["id"]:
      self.canvas.delete(f"tbl{prev}&&table_ui")
    self._table_sel = t["id"]
    self._table_cell = cell
    self.table_draw(t)

  def _table_begin_edit(self, t, r, k):
    self._table_edit = (t["id"], r, k)
    self._table_cell = (r, k)
    self._table_idx = len(t["cells"][r][k]["text"])
    self._table_pre = self.get_history_state()   # one undo step for everything typed in this visit
    self._table_changed = False

  def _table_click_cell(self, t, rc, x, y):
    r, k = rc
    self.table_finish_edit()
    prev = self._table_sel
    if prev is not None and prev != t["id"]:
      self.canvas.delete(f"tbl{prev}&&table_ui")
    self._table_sel = t["id"]
    self._table_begin_edit(t, r, k)
    self.table_draw(t)
    tid = t["_texts"].get((r, k))
    if tid and t["cells"][r][k]["text"]:
      try:
        self._table_idx = int(self.canvas.index(tid, f"@{x},{y}"))
        self.canvas.icursor(tid, self._table_idx)
      except Exception:
        pass

  def table_finish_edit(self):
    """Leaves the cell being typed in. Everything typed becomes ONE undo step."""
    if self._table_edit is None:
      return
    tid = self._table_edit[0]
    self._table_edit = None
    changed, pre = self._table_changed, self._table_pre
    self._table_changed, self._table_pre = False, None
    t = self._table_by_id(tid)
    if t is not None:
      self.table_draw(t)   # drops the temporary empty text item
    if changed and pre is not None:
      self.pre_state = pre
      self.commit_change()

  def _table_goto(self, t, r, k):
    self.table_finish_edit()
    self._table_sel = t["id"]
    self._table_begin_edit(t, r, k)
    self.table_draw(t)

  def table_key(self, e):
    edit = self._table_edit
    t = self._table_by_id(edit[0]) if edit else None
    if t is None:
      self._table_edit = None
      return None
    r, k = edit[1], edit[2]
    R, C = len(t["rh"]), len(t["cw"])
    cell = t["cells"][r][k]
    s = cell["text"]
    i = max(0, min(self._table_idx, len(s)))
    key = e.keysym
    ctrl = bool(e.state & 0x4)
    changed = False

    if ctrl and key.lower() == "v":
      try:
        clip = self.clipboard_get()
      except tk.TclError:
        clip = ""
      if clip:
        s, i, changed = s[:i] + clip + s[i:], i + len(clip), True
    elif ctrl:
      return None                       # other Ctrl shortcuts keep working
    elif key == "BackSpace":
      if i > 0:
        s, i, changed = s[:i - 1] + s[i:], i - 1, True
    elif key == "Delete":
      if i < len(s):
        s, changed = s[:i] + s[i + 1:], True
    elif key == "Left":
      i = max(0, i - 1)
    elif key == "Right":
      i = min(len(s), i + 1)
    elif key == "Home":
      i = 0
    elif key == "End":
      i = len(s)
    elif key in ("Up", "Down"):
      nr = r - 1 if key == "Up" else r + 1
      if 0 <= nr < R:
        self._table_goto(t, nr, k)
      return "break"
    elif key in ("Tab", "ISO_Left_Tab"):
      back = key == "ISO_Left_Tab" or bool(e.state & 0x1)
      if back:
        target = (r, k - 1) if k > 0 else ((r - 1, C - 1) if r > 0 else None)
      elif k < C - 1:
        target = (r, k + 1)
      elif r < R - 1:
        target = (r + 1, 0)
      else:
        self._add_row(t, R, R - 1)       # Tab in the last cell adds a row, like Word
        target = (R, 0) if len(t["rh"]) > R else None
      if target:
        self._table_goto(t, target[0], target[1])
      return "break"
    elif key == "Return":
      s, i, changed = s[:i] + "\n" + s[i:], i + 1, True
    elif key == "Escape":
      self.table_finish_edit()
      return "break"
    elif e.char and e.char.isprintable():
      s, i, changed = s[:i] + e.char + s[i:], i + len(e.char), True
    else:
      return None

    self._table_idx = i
    if changed:
      cell["text"] = s
      self._table_changed = True
      self.table_draw(t)
    else:
      tid = t["_texts"].get((r, k))
      if tid:
        self.canvas.icursor(tid, i)
    return "break"

  # ------------------------------------------------------------------ editing operations (undoable)
  def _table_do(self, t, fn):
    self.table_finish_edit()
    self.begin_change()
    fn(t)
    self.table_draw(t)
    self.commit_change()

  def _row_insert(self, t, at, src):
    t["rh"].insert(at, t["rh"][src])
    t["cells"].insert(at, [self._table_new_cell() for _ in t["cw"]])

  def _col_insert(self, t, at, src):
    t["cw"].insert(at, t["cw"][src])
    for row in t["cells"]:
      row.insert(at, self._table_new_cell())

  def _add_row(self, t, at, src):
    if len(t["rh"]) >= MAX_ROWS:
      self.toast(f"A table can have at most {MAX_ROWS} rows")
      return
    self._table_do(t, lambda tt: self._row_insert(tt, at, src))

  def _add_col(self, t, at, src):
    if len(t["cw"]) >= MAX_COLS:
      self.toast(f"A table can have at most {MAX_COLS} columns")
      return
    self._table_do(t, lambda tt: self._col_insert(tt, at, src))

  def _row_delete(self, t, r):
    if len(t["rh"]) > 1:
      del t["rh"][r]
      del t["cells"][r]

  def _col_delete(self, t, k):
    if len(t["cw"]) > 1:
      del t["cw"][k]
      for row in t["cells"]:
        del row[k]

  def _rows_even(self, t):
    avg = max(TBL_MIN_H, sum(t["rh"]) / len(t["rh"]))
    t["rh"] = [avg] * len(t["rh"])

  def _cols_even(self, t):
    avg = max(TBL_MIN_W, sum(t["cw"]) / len(t["cw"]))
    t["cw"] = [avg] * len(t["cw"])

  def table_delete(self, t):
    self.table_finish_edit()
    self.begin_change()
    self.canvas.delete(f"tbl{t['id']}")
    if t in self._tables:
      self._tables.remove(t)
    if self._table_sel == t["id"]:
      self._table_sel, self._table_cell = None, None
    self.commit_change()

  def table_context(self, event):
    """Right-click menu on a table."""
    if not self._tables:
      return
    x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
    hit = self._table_hit(x, y)
    if hit is None:
      return
    t = hit[0]
    r, k = self._table_cell_at(t, x, y)
    self.selected_item = None
    if hasattr(self, "show_handles"):
      self.show_handles()
    self._table_select(t, (r, k))
    R, C = len(t["rh"]), len(t["cw"])
    pen = getattr(self, "pen_color", "#e74c3c")

    def set_cell_fill(color):
      t["cells"][r][k]["fill"] = color

    def set_attr(name, value):
      t[name] = value

    def bump_font(d):
      t["fs"] = max(6, min(48, t["fs"] + d))

    m = tk.Menu(self, tearoff=0, bg="#1b1b1b", fg="white", activebackground=BROWN, activeforeground="white",
                font=("Arial", 10))
    m.add_command(label="⬆  Insert Row Above", command=lambda: self._add_row(t, r, r))
    m.add_command(label="⬇  Insert Row Below", command=lambda: self._add_row(t, r + 1, r))
    m.add_command(label="⬅  Insert Column Left", command=lambda: self._add_col(t, k, k))
    m.add_command(label="➡  Insert Column Right", command=lambda: self._add_col(t, k + 1, k))
    m.add_separator()
    m.add_command(label="✖  Delete Row", state="normal" if R > 1 else "disabled",
                  command=lambda: self._table_do(t, lambda tt: self._row_delete(tt, r)))
    m.add_command(label="✖  Delete Column", state="normal" if C > 1 else "disabled",
                  command=lambda: self._table_do(t, lambda tt: self._col_delete(tt, k)))
    m.add_separator()
    m.add_command(label="↕  Distribute Rows Evenly", command=lambda: self._table_do(t, self._rows_even))
    m.add_command(label="↔  Distribute Columns Evenly", command=lambda: self._table_do(t, self._cols_even))
    m.add_separator()
    m.add_command(label="🎨  Shade This Cell (pen colour)",
                  command=lambda: self._table_do(t, lambda tt: set_cell_fill(pen)))
    m.add_command(label="⬜  Clear Cell Shading",
                  command=lambda: self._table_do(t, lambda tt: set_cell_fill("")))
    m.add_command(label="🖍  Border Colour = pen colour",
                  command=lambda: self._table_do(t, lambda tt: set_attr("color", pen)))
    m.add_command(label="🔤  Text Colour = pen colour",
                  command=lambda: self._table_do(t, lambda tt: set_attr("tcolor", pen)))
    m.add_command(label="A+  Bigger Text", command=lambda: self._table_do(t, lambda tt: bump_font(2)))
    m.add_command(label="A-  Smaller Text", command=lambda: self._table_do(t, lambda tt: bump_font(-2)))
    m.add_separator()
    m.add_command(label="🗑  Delete Table", command=lambda: self.table_delete(t))
    try:
      m.tk_popup(event.x_root, event.y_root)
    finally:
      m.grab_release()

  # ------------------------------------------------------------------ zoom hooks
  def table_zoom_begin(self):
    self.canvas.delete("table_ui")

  def table_zoom_live(self):
    """Quick pass while zooming: line widths, fonts and text wrap follow the zoom (no re-layout)."""
    if not self._tables:
      return
    c, z = self.canvas, self.current_zoom_scale
    pad = max(2.0, TBL_PAD * z)
    for t in self._tables:
      c.itemconfig(f"tbl{t['id']}&&tbl_line", width=max(1.0, t["lw"] * z))
      font = ("Arial", max(4, int(round(t["fs"] * z))))
      for (r, k), tid in t.get("_texts", {}).items():
        if k < len(t["cw"]):
          c.itemconfig(tid, font=font, width=max(8, int(t["cw"][k] * z - 2 * pad)))

  def table_zoom_end(self):
    """Zooming stopped: full re-layout at the final zoom."""
    for t in self._tables:
      self.table_draw(t)

  # ------------------------------------------------------------------ history / project data
  def get_tables_snapshot(self):
    """Cheap in-memory copy for undo / redo (position is the canvas position at the current zoom)."""
    out = []
    for t in getattr(self, "_tables", []):
      ox, oy = self._table_origin(t)
      d = {key: copy.deepcopy(val) for key, val in t.items() if not key.startswith("_")}
      d["ox"], d["oy"] = ox, oy
      out.append(d)
    return out

  def get_tables_state(self):
    """Same data for saving into a project file, stamped with the zoom it was saved at."""
    z = self.current_zoom_scale
    out = self.get_tables_snapshot()
    for d in out:
      d["zoom"] = z
    return out

  def restore_tables_snapshot(self, snap):
    """Replaces every table with the given ones (undo, redo, project open, new project)."""
    self.canvas.delete("table_item", "table_ui")
    self._tables = []
    self._table_sel = self._table_cell = self._table_edit = self._table_op = self._table_pre = None
    self._table_changed = False
    cur = self.current_zoom_scale
    top = 0
    for d in snap or []:
      try:
        t = copy.deepcopy(d)
        z0 = t.pop("zoom", None)
        self._table_validate(t)
        if z0 and abs(z0 - cur) > 1e-9:
          ratio = cur / z0
          t["ox"] *= ratio
          t["oy"] *= ratio
        t["id"] = int(t.get("id", 0)) or (top + 1)
        top = max(top, t["id"])
        self._tables.append(t)
      except Exception as e:
        print(f"Table restore error: {e}")
    self._table_next = max(getattr(self, "_table_next", 1), top + 1)
    for t in self._tables:
      self.table_draw(t)

  def restore_tables_state(self, state):
    self.restore_tables_snapshot(state)
