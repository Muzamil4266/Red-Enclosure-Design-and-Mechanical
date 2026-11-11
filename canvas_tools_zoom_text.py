import math
import time
from collections import deque
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import simpledialog

ZOOM_MIN, ZOOM_MAX = 0.02, 6.0
ZOOM_FRAME_MS = 16      # one animation frame (~60 fps)
ZOOM_EASE = 0.35        # fraction of the remaining zoom distance covered per frame (log space)
ZOOM_TAGS = ("component", "wire", "box_shape", "circle_shape", "star_shape", "spring_shape",
             "cable_shape", "text_note", "aa", "inserted_picture", "table_item")
TEXT_COLOR = "#ffffff"        # written text and its blinking caret are always white
TEXT_SELECT_BG = "#1f6aa5"    # background of selected text
TEXT_BASE_SIZE = 12           # point size of a written text at zoom 100%
TEXT_MIN_SIZE, TEXT_MAX_SIZE, TEXT_SIZE_STEP = 6, 120, 2   # limits and step of A- / A+
# (name, highlight colour, colour while the mouse is over it); white text stays readable on all of them
HIGHLIGHT_COLORS = (("Red", "#e74c3c", "#ff7b6b"), ("Yellow", "#d4ac0d", "#f7d34a"),
                    ("Pink", "#e84393", "#ff7cbc"), ("Green", "#27ae60", "#5fd68e"),
                    ("Blue", "#2980b9", "#62b3ee"))


class CanvasToolsZoomTextMixin:

  def update_workspace_counter(self):
    """Updates the counter to count ONLY component instances spawned from the sidebar component list."""
    if hasattr(self, "component_counter_label") and self.component_counter_label:
      count = len(self.image_refs)
      self.component_counter_label.configure(text=f"Components: {count}")

  # ------------------------------------------------------------------ zoom entry points
  def on_two_finger_component_zoom(self, event):
    if self.selected_item and self.selected_item in self.image_refs:
      factor = 1.1 if event.delta > 0 else 0.9
      self.begin_change()
      self.scale_specific_item(self.selected_item, factor)
      self.commit_change()
    elif self.is_picture(self.selected_item):
      self.begin_change()
      self.scale_picture(self.selected_item, 1.1 if event.delta > 0 else 0.9)
      self.commit_change()
    elif not self.selected_item:  # nothing selected: wheel / pinch zooms the whole workspace (animated)
      base = self._zoom_goal if getattr(self, "_zoom_anim_job", None) else self.current_zoom_scale
      self._zoom_from_slider = False
      self.smooth_zoom(base * (1.1 if event.delta > 0 else 1 / 1.1))

  def on_universal_slider_zoom(self, value):
    val = float(value)
    if val == 50.0:
      target = 1.0
    elif val < 50.0:
      target = 0.02 + (val / 50.0) * 0.98
    else:
      target = 1.0 + (val - 50.0) / 50.0 * 5.0
    self._zoom_from_slider = True  # the user is holding the slider: never move it for them
    self.smooth_zoom(target)

  def _sync_zoom_slider(self):
    z = self.current_zoom_scale
    v = 50.0 if z == 1.0 else (50 * (z - 0.02) / 0.98 if z < 1 else 50 + 50 * (z - 1) / 5)
    v = max(0.0, min(100.0, v))
    self.univ_zoom_slider.set(v)
    self.zoom_label.configure(text=f"Zoom: {int(v)}%")

  def smooth_zoom(self, target):
    """Animates the zoom towards target: many wheel / slider events just move the goal, one loop chases it."""
    self._zoom_goal = max(ZOOM_MIN, min(ZOOM_MAX, target))
    if getattr(self, "_zoom_anim_job", None) is None:
      self._zoom_anim_job = self.after(1, self._zoom_anim_step)

  def _zoom_anim_step(self):
    self._zoom_anim_job = None
    goal, cur = self._zoom_goal, self.current_zoom_scale
    ratio = goal / cur
    nxt = goal if abs(ratio - 1.0) < 0.003 else cur * ratio ** ZOOM_EASE
    self.apply_zoom(nxt)
    if abs(nxt - goal) > 1e-9:
      self._zoom_anim_job = self.after(ZOOM_FRAME_MS, self._zoom_anim_step)
    elif not getattr(self, "_zoom_from_slider", False):
      self._sync_zoom_slider()

  # ------------------------------------------------------------------ the zoom itself
  def rescale_history(self, cx, cy, f):
    """Legacy eager rescale of the undo/redo stacks (no longer used by zoom: history is converted lazily on restore)."""
    tx = lambda v: cx + (v - cx) * f
    ty = lambda v: cy + (v - cy) * f

    def fix(st):
      for comp in st.get("components", []):
        comp["x"], comp["y"] = tx(comp["x"]), ty(comp["y"])
        comp["scale"] *= f
      for key in ("wires", "boxes", "circles", "stars", "springs", "strokes"):
        for it in st.get(key, []):
          it["coords"] = [tx(v) if i % 2 == 0 else ty(v) for i, v in enumerate(it["coords"])]
          if "width" in it:
            it["width"] *= f
          if "size" in it:
            it["size"] *= f
      for t in st.get("texts", []):
        t["x"], t["y"] = tx(t["x"]), ty(t["y"])

    for st in self.undo_stack + self.redo_stack:
      fix(st)
    if self.pre_state:
      fix(self.pre_state)

  def _zoom_begin(self):
    """Start of a zoom gesture: everything that only needs doing ONCE per gesture."""
    c = self.canvas
    self._zoom_gen = getattr(self, "_zoom_gen", 0) + 1  # stops a background redraw still running from the last gesture
    self._zoom_queue = None
    box = c.bbox(*ZOOM_TAGS)
    self._zoom_anchor_pt = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) if box else \
        (c.canvasx(c.winfo_width() / 2), c.canvasy(c.winfo_height() / 2))
    self._zoom_total = 1.0
    self._zoom_font = None
    self.clear_hover()
    c.delete("handle")                      # handles come back when zooming stops
    if hasattr(self, "table_zoom_begin"):
      self.table_zoom_begin()               # table handles come back when zooming stops
    c.itemconfigure("aa", state="hidden")   # smooth strokes are redrawn when zooming stops
    for i in self.stroke_items():
      c.itemconfig(i, state="normal")       # plain lines are shown (and scaled) while zooming

  def _zoom_fix_anchors(self):
    """Things that sit on the screen (grid image, PCB view tiles) must not be moved by the canvas scaling."""
    c = self.canvas
    item, pos = getattr(self, "_grid_item", None), getattr(self, "_grid_pos", None)
    if item is not None and pos and c.type(item):
      c.coords(item, *pos)
    x0, y0 = c.canvasx(0), c.canvasy(0)
    for tid in getattr(self, "pcb_tiles", {}).values():
      c.coords(tid, x0, y0)

  def apply_zoom(self, target):
    """Zooms the whole project around its own centre so nothing drifts.
    Per call only cheap work is done; the expensive redraws happen while idle (see _zoom_live_render / finalize_zoom)."""
    c = self.canvas
    target = max(ZOOM_MIN, min(ZOOM_MAX, target))
    factor = target / self.current_zoom_scale
    if abs(factor - 1.0) < 1e-6:
      return
    if self._zoom_job is None or getattr(self, "_zoom_anchor_pt", None) is None:  # start of a zoom gesture
      self._zoom_begin()
    cx, cy = self._zoom_anchor_pt
    self.current_zoom_scale = target
    self._zoom_total *= factor

    # cumulative world transform (used to bring old undo/redo snapshots to the current zoom when they are restored)
    a, bx, by = getattr(self, "_zoom_xf", (1.0, 0.0, 0.0))
    self._zoom_xf = (a * factor, bx * factor + cx * (1 - factor), by * factor + cy * (1 - factor))
    o = getattr(self, "_grid_origin", None)
    if o is not None:
      o[0], o[1] = cx + (o[0] - cx) * factor, cy + (o[1] - cy) * factor

    c.scale("all", cx, cy, factor, factor)
    self._zoom_fix_anchors()
    for item in self.image_refs:
      self.item_scales[item] *= factor
    for i in self.stroke_items():
      w = self.stroke_w.get(i, 1.0) * factor
      self.stroke_w[i] = w
      c.itemconfig(i, width=w)
    self.text_apply_zoom_fonts()  # every text has its own size x zoom; highlight boxes follow the text

    if not getattr(self, "_zoom_live_job", None):  # quick low-quality redraw so sizes follow the gesture
      self._zoom_live_job = self.after(50, self._zoom_live_render)
    if self._zoom_job:
      self.after_cancel(self._zoom_job)
    self._zoom_job = self.after(150, self.finalize_zoom)

  def _image_on_screen(self, item):
    """Cheap test (no rendering): does this component touch the visible part of the workspace?"""
    c = self.canvas
    ref = self.image_refs.get(item)
    try:
      cx, cy = c.coords(item)
    except ValueError:
      return True
    if ref is None:
      return True
    w, h = ref["pil_img"].size
    l, t, r, b = ref.get("crop", [0.0, 0.0, 0.0, 0.0])
    s = self.item_scales.get(item, 1.0) * ref.get("base", 1.0)
    rad = math.hypot(w * (1 - l - r) * s * ref.get("sx", 1.0), h * (1 - t - b) * s * ref.get("sy", 1.0)) / 2 + 4
    x0, y0 = c.canvasx(0), c.canvasy(0)
    return cx + rad >= x0 and cx - rad <= x0 + c.winfo_width() and cy + rad >= y0 and cy - rad <= y0 + c.winfo_height()

  def _bbox_on_screen(self, item):
    c = self.canvas
    bb = c.bbox(item)
    if not bb:
      return False
    x0, y0 = c.canvasx(0), c.canvasy(0)
    return bb[2] >= x0 and bb[0] <= x0 + c.winfo_width() and bb[3] >= y0 and bb[1] <= y0 + c.winfo_height()

  def _zoom_live_render(self):
    """~20 times a second while zooming: fast low-quality redraw of the components that are visible."""
    self._zoom_live_job = None
    old, self._skip_aa = self._skip_aa, True
    sel, self.selected_item = self.selected_item, None  # no handle redraw while zooming
    try:
      for item in list(self.image_refs):
        if "pcb" in self.image_refs[item]:
          continue  # PCB maps are drawn by their own viewport poll
        if self._image_on_screen(item):
          self.render_image(item)
      if hasattr(self, "rescale_inserted_pictures"):
        self.rescale_inserted_pictures(fast=True, visible_only=True)  # quick low-quality pictures
      if hasattr(self, "table_zoom_live"):
        self.table_zoom_live()  # table line widths and fonts follow the zoom
    finally:
      self._skip_aa = old
      self.selected_item = sel
    if hasattr(self, "grid_refresh"):
      self.grid_refresh()

  def finalize_zoom(self):
    """Zooming stopped: full quality for what you see first, everything else a little at a time (no freeze)."""
    self._zoom_job = None
    if getattr(self, "_zoom_live_job", None):
      self.after_cancel(self._zoom_live_job)
      self._zoom_live_job = None
    self._zoom_anchor_pt, self._zoom_total = None, 1.0
    gen = getattr(self, "_zoom_gen", 0)

    off_images = []
    for item in list(self.image_refs):
      if self._image_on_screen(item):
        self.render_image(item)
      else:
        off_images.append(item)
    self.show_handles()
    if hasattr(self, "table_zoom_end"):
      self.table_zoom_end()  # tables are laid out again at the final zoom

    visible, hidden = [], []
    for i in self.stroke_items():
      (visible if self._bbox_on_screen(i) else hidden).append(i)
    off_pics = self.rescale_inserted_pictures(fast=False, visible_only=True) if hasattr(self, "rescale_inserted_pictures") else []
    self._zoom_queue = deque([("s", i) for i in visible] + [("p", i) for i in off_pics] +
                             [("i", i) for i in off_images] + [("s", i) for i in hidden])
    self._zoom_bg_pass(gen)
    if hasattr(self, "grid_refresh"):
      self.grid_refresh()
    if getattr(self, "current_project", None) or self.image_refs or self.stroke_items() or getattr(self, "_picture_items", None):
      self._schedule_autosave()  # zoom changes are saved too

  def _zoom_bg_pass(self, gen):
    """Smooth strokes + off-screen components, in 12 ms slices so the window stays responsive."""
    q = getattr(self, "_zoom_queue", None)
    if gen != getattr(self, "_zoom_gen", 0) or not q:
      return
    c, t0 = self.canvas, time.perf_counter()
    while q and time.perf_counter() - t0 < 0.012:
      kind, item = q.popleft()
      try:
        if kind == "i":
          if item in self.image_refs:
            self.render_image(item)
        elif kind == "p":
          self.rescale_picture(item)
        elif c.type(item) and item not in self.live:
          self.aa_render(item)
      except Exception as e:
        print(f"Zoom redraw error: {e}")
    if q:
      self.after(3, self._zoom_bg_pass, gen)

  # ------------------------------------------------------------------ text notes
  def text_meta_reset(self):
    """Forgets the size / highlight data of all written texts (their canvas items were deleted)."""
    self._text_meta = {}
    self._text_px = {}
    self._text_hl_rects = {}
    self._text_fonts = getattr(self, "_text_fonts", {})
    if not hasattr(self, "_text_base_size"):
      self._text_base_size = TEXT_BASE_SIZE  # size of the next new text: remembers the last A+ / A- choice
    self.canvas.delete("text_hl")

  def _tmeta(self, item):
    """{"size": point size at zoom 100%, "hl": [[first, end, colour], ...]} of one written text."""
    if not hasattr(self, "_text_meta"):
      self.text_meta_reset()
    return self._text_meta.setdefault(item, {"size": TEXT_BASE_SIZE, "hl": []})

  def text_size_of(self, item):
    return self._tmeta(item)["size"]

  def text_hl_of(self, item):
    return [list(r) for r in self._tmeta(item)["hl"]]

  def _text_font(self, size):
    return ("Arial", max(4, int(size * self.current_zoom_scale)), "bold")

  def text_create(self, x, y, text, fill, size=None, hl=None):
    """Creates one written text with its size and highlights (used by new texts, undo/redo and project loading)."""
    size = int(size) if size else TEXT_BASE_SIZE
    item = self.canvas.create_text(x, y, text=text, anchor="nw", fill=fill, tags="text_note",
                                   font=self._text_font(size))
    meta = self._tmeta(item)
    meta["size"] = size
    meta["hl"] = [[int(a), int(b), col] for a, b, col in (hl or [])]
    self._text_px[item] = max(4, int(size * self.current_zoom_scale))
    self.text_hl_redraw(item)
    return item

  def _text_forget(self, item):
    for r in getattr(self, "_text_hl_rects", {}).pop(item, []):
      self.canvas.delete(r)
    getattr(self, "_text_meta", {}).pop(item, None)
    getattr(self, "_text_px", {}).pop(item, None)

  # ---- highlight boxes (drawn behind the text, one box per highlighted line piece)
  def text_hl_redraw(self, item):
    c = self.canvas
    rects = getattr(self, "_text_hl_rects", None)
    if rects is None:
      self.text_meta_reset()
      rects = self._text_hl_rects
    for r in rects.pop(item, []):
      c.delete(r)
    if not c.type(item):
      return
    hl = self._tmeta(item)["hl"]
    if not hl:
      return
    spec = c.itemcget(item, "font")
    font = self._text_fonts.get(spec)
    if font is None:
      font = self._text_fonts[spec] = tkfont.Font(root=c, font=spec)
    line_h = font.metrics("linespace")
    x, y = c.coords(item)[:2]
    made, start = [], 0
    for k, line in enumerate(c.itemcget(item, "text").split("\n")):
      end = start + len(line)
      for a, b, col in hl:
        lo, hi = max(a, start), min(b, end)
        if lo < hi:
          x0 = x + font.measure(line[:lo - start])
          x1 = x + font.measure(line[:hi - start])
          rid = c.create_rectangle(x0, y + k * line_h, x1, y + (k + 1) * line_h, fill=col, outline="",
                                   width=0, state="disabled", tags=("text_hl",))
          c.tag_lower(rid, item)
          made.append(rid)
      start = end + 1
    rects[item] = made

  def text_hl_gc(self):
    """Removes highlight boxes whose text no longer exists (for example after Clear Workspace)."""
    rects = getattr(self, "_text_hl_rects", None)
    if not rects:
      return
    for item in list(rects):
      if not self.canvas.type(item):
        self._text_forget(item)

  def text_apply_zoom_fonts(self):
    """Called on every zoom step: each text gets its own size x zoom, highlight boxes follow."""
    c = self.canvas
    if not hasattr(self, "_text_meta"):
      self.text_meta_reset()
    z = self.current_zoom_scale
    for i in c.find_withtag("text_note"):
      px = max(4, int(self._tmeta(i)["size"] * z))
      if self._text_px.get(i) != px:  # font changes are slow: only when the size really changed
        self._text_px[i] = px
        c.itemconfig(i, font=("Arial", px, "bold"))
    for i, m in list(self._text_meta.items()):
      if m["hl"] and c.type(i):
        self.text_hl_redraw(i)

  def _text_look(self):
    """White caret, white text colour and readable selection colours for the text tool."""
    self.canvas.configure(insertbackground=TEXT_COLOR, insertwidth=2, selectbackground=TEXT_SELECT_BG,
                          selectforeground=TEXT_COLOR, selectborderwidth=0)

  def init_text_tools(self):
    """Runs once (called by text_hover the first time the mouse is over the canvas): binds the right-click menu."""
    if getattr(self, "_text_tools_ready", False):
      return
    self._text_tools_ready = True
    self._text_sel_active = False
    self._text_anchor = 0
    self._text_before = ""
    self._text_attr_before = None
    self._text_cursor_on = False
    self._text_prev_cursor = ""
    self._text_look()
    events = ("<Button-3>",) + (("<Button-2>",) if sys.platform == "darwin" else ())
    for ev in events:
      self.canvas.bind(ev, self.on_text_right_click, add="+")

  def _text_at(self, x, y):
    """Topmost written text under the canvas point (x, y), or None."""
    c = self.canvas
    for i in reversed(c.find_overlapping(x - 2, y - 2, x + 2, y + 2)):
      if "text_note" in c.gettags(i):
        return i
    return None

  def text_hover(self, event):
    """Called from the mouse hover: I-beam cursor over written text."""
    self.init_text_tools()
    self.text_hl_gc()
    c = self.canvas
    over = (self._text_at(c.canvasx(event.x), c.canvasy(event.y)) is not None and
            (getattr(self, "mode", "select") == "select" or self.editing_text is not None))
    if over and not self._text_cursor_on:
      self._text_prev_cursor = c.cget("cursor")
      c.configure(cursor="xterm")
      self._text_cursor_on = True
    elif not over and self._text_cursor_on:
      c.configure(cursor=self._text_prev_cursor)
      self._text_cursor_on = False

  def _text_attrs(self, item):
    meta = self._tmeta(item)
    return meta["size"], [list(r) for r in meta["hl"]]

  def _text_begin_edit(self, item, pre=None):
    self.text_pre = pre if pre is not None else self.get_history_state()  # same format the undo stack uses
    self._text_before = self.canvas.itemcget(item, "text")
    self._text_attr_before = self._text_attrs(item)
    self.editing_text = item
    self._text_look()
    self.canvas.select_clear()
    self.canvas.focus_set()
    self.canvas.focus(item)

  # ---- editing primitives (keep the highlight ranges in step with the text)
  def _text_insert(self, item, i, s):
    c = self.canvas
    c.insert(item, i, s)
    n = len(s)
    for r in self._tmeta(item)["hl"]:
      if r[0] >= i:
        r[0] += n
        r[1] += n
      elif r[1] > i:
        r[1] += n  # typing inside a highlight extends it
    c.icursor(item, i + n)
    self.text_hl_redraw(item)

  def _text_delete(self, item, a, b):
    """Deletes the characters a..b (both included)."""
    c = self.canvas
    n = b - a + 1
    c.dchars(item, a, b)
    adj = lambda p: p if p <= a else (a if p <= b else p - n)
    out = []
    for s, e, col in self._tmeta(item)["hl"]:
      s2, e2 = adj(s), adj(e)
      if s2 < e2:
        out.append([s2, e2, col])
    self._tmeta(item)["hl"] = out
    c.icursor(item, a)
    self.text_hl_redraw(item)

  # ---- selection helpers
  def _text_selection(self, item):
    """(first, last) character indexes of the selected text of item (last included), or None."""
    c = self.canvas
    sel = c.select_item()
    if sel is None or str(sel) != str(item):
      return None
    try:
      return c.index(item, "sel.first"), c.index(item, "sel.last")
    except tk.TclError:
      return None

  def _text_delete_selection(self, item):
    sel = self._text_selection(item)
    if sel is None:
      return False
    self.canvas.select_clear()
    self._text_delete(item, sel[0], sel[1])
    return True

  def _text_select_word(self, item, idx):
    c = self.canvas
    s = c.itemcget(item, "text")
    n = len(s)
    c.select_clear()
    if n == 0 or s[min(idx, n - 1)].isspace():
      c.icursor(item, idx)
      self._text_anchor = idx
      return
    i = min(idx, n - 1)
    left = i
    while left > 0 and not s[left - 1].isspace():
      left -= 1
    right = i
    while right < n - 1 and not s[right + 1].isspace():
      right += 1
    c.select_from(item, left)
    c.select_to(item, right)
    c.icursor(item, right + 1)
    self._text_anchor = left

  def _clipboard_text(self):
    try:
      return self.clipboard_get()
    except tk.TclError:
      return ""

  # ---- clipboard actions (keyboard shortcuts and the right-click menu)
  def text_copy(self):
    item = self.editing_text
    if item is None:
      return
    sel = self._text_selection(item)
    if sel is None:
      return
    self.clipboard_clear()
    self.clipboard_append(self.canvas.itemcget(item, "text")[sel[0]:sel[1] + 1])

  def text_cut(self):
    item = self.editing_text
    if item is None or self._text_selection(item) is None:
      return
    self.text_copy()
    self._text_delete_selection(item)

  def text_paste(self):
    item = self.editing_text
    if item is None:
      return
    s = self._clipboard_text().replace("\r\n", "\n").replace("\r", "\n")
    if not s:
      return
    self._text_delete_selection(item)
    self._text_insert(item, self.canvas.index(item, "insert"), s)

  def text_select_all(self):
    item = self.editing_text
    if item is None:
      return
    n = len(self.canvas.itemcget(item, "text"))
    if n:
      self.canvas.select_from(item, 0)
      self.canvas.select_to(item, n - 1)
      self.canvas.icursor(item, n)
      self._text_anchor = 0

  # ---- highlight and A+ / A-
  def text_set_highlight(self, color):
    """Highlights the selected characters with color (None removes the highlight from them)."""
    item = self.editing_text
    if item is None:
      return
    sel = self._text_selection(item)
    if sel is None:
      return
    a, b = sel[0], sel[1] + 1
    out = []
    for s, e, col in self._tmeta(item)["hl"]:
      if e <= a or s >= b:
        out.append([s, e, col])
        continue
      if s < a:
        out.append([s, a, col])
      if e > b:
        out.append([b, e, col])
    if color:
      out.append([a, b, color])
    out.sort()
    self._tmeta(item)["hl"] = out
    self.canvas.select_clear()  # so the colour can be seen
    self.text_hl_redraw(item)

  def text_size_change(self, delta):
    """A+ / A-: the written text gets bigger / smaller and the next new texts use the same size."""
    item = self.editing_text
    if item is None:
      return
    meta = self._tmeta(item)
    new = max(TEXT_MIN_SIZE, min(TEXT_MAX_SIZE, meta["size"] + delta))
    meta["size"] = new
    self._text_base_size = new
    self._text_px[item] = max(4, int(new * self.current_zoom_scale))
    self.canvas.itemconfig(item, font=self._text_font(new))
    self.text_hl_redraw(item)

  # ---- right-click menu
  def on_text_right_click(self, event):
    if getattr(self, "_table_edit", None) is not None:
      return  # a table cell has its own editing
    c = self.canvas
    x, y = c.canvasx(event.x), c.canvasy(event.y)
    if self.editing_text is None:
      hit = self._text_at(x, y)
      if hit is None:
        return
      self._text_begin_edit(hit)  # right click on written text starts editing it
      c.icursor(hit, c.index(hit, f"@{x},{y}"))
    self._text_show_menu(event.x_root, event.y_root)
    return "break"

  def _text_show_menu(self, xr, yr):
    item = self.editing_text
    if item is None:
      return
    c = self.canvas
    has_sel = self._text_selection(item) is not None
    on = "normal" if has_sel else "disabled"
    style = dict(bg="#1b1b1b", fg="white", activebackground="#1f6aa5", activeforeground="white",
                 disabledforeground="#777777")
    menu = tk.Menu(c, tearoff=0, **style)

    def run(fn, again=False):
      def go():
        fn()
        c.focus_set()
        if again:  # A+ / A-: the menu opens again so the size can be changed many times
          c.after(40, lambda: self._text_show_menu(xr, yr))
      return go

    menu.add_command(label="✂️ Cut        Ctrl+X", command=run(self.text_cut), state=on)
    menu.add_command(label="📄 Copy       Ctrl+C", command=run(self.text_copy), state=on)
    menu.add_command(label="📋 Paste      Ctrl+V", command=run(self.text_paste),
                     state="normal" if self._clipboard_text() else "disabled")
    menu.add_separator()

    hl_menu = tk.Menu(menu, tearoff=0, **style)
    for name, col, hot in HIGHLIGHT_COLORS:
      hl_menu.add_command(label=f"    {name}        ", background=col, foreground="white",
                          activebackground=hot, activeforeground="white",
                          command=run(lambda col=col: self.text_set_highlight(col)))
    hl_menu.add_separator()
    hl_menu.add_command(label="✖ Remove highlight", command=run(lambda: self.text_set_highlight(None)))
    menu.add_cascade(label="🖍️ Highlight", menu=hl_menu, state=on)

    menu.add_command(label="A+   Bigger text", command=run(lambda: self.text_size_change(TEXT_SIZE_STEP), True))
    menu.add_command(label="A-   Smaller text", command=run(lambda: self.text_size_change(-TEXT_SIZE_STEP), True))
    menu.add_separator()
    menu.add_command(label="🔤 Select All  Ctrl+A", command=run(self.text_select_all))
    try:
      menu.tk_popup(xr, yr)
    finally:
      menu.grab_release()

  # ---- mouse: caret placement and drag selection (called from canvas_mouse_events)
  def text_press(self, event):
    """True when the click belongs to a text: the caret is placed there and a drag selection starts."""
    self.init_text_tools()
    c = self.canvas
    x, y = c.canvasx(event.x), c.canvasy(event.y)
    hit = self._text_at(x, y)
    if self.editing_text is not None:
      if hit != self.editing_text:
        return False  # clicking elsewhere finishes the edit as before
    else:
      if hit is None or getattr(self, "mode", "") != "select" or getattr(self, "_table_edit", None) is not None:
        return False
      if "handle" in c.gettags("current"):
        return False
      self.selected_item = None
      self.show_handles()
      if hasattr(self, "table_deselect"):
        self.table_deselect()
      self._text_begin_edit(hit)
    item = self.editing_text
    idx = c.index(item, f"@{x},{y}")
    c.icursor(item, idx)
    c.select_clear()
    self._text_anchor = idx
    self._text_sel_active = True
    self.dragging = False
    self.box_op = None
    self.crop_corner = None
    self.last_x, self.last_y = x, y
    return True

  def text_drag(self, x, y):
    item = self.editing_text
    if item is None:
      return
    c = self.canvas
    idx = c.index(item, f"@{x},{y}")
    a = self._text_anchor
    c.icursor(item, idx)
    if idx > a:
      c.select_from(item, a)
      c.select_to(item, idx - 1)
    elif idx < a:
      c.select_from(item, a)
      c.select_to(item, idx)
    else:
      c.select_clear()

  def text_release(self):
    self._text_sel_active = False

  # ---- double click, typing, finishing
  def on_canvas_double_click(self, event):
    if hasattr(self, "table_double_click") and self.table_double_click(event):
      return  # double click on a table: the cell is already being edited
    self.init_text_tools()
    self.finish_text_edit()
    if self._dot_item is not None:
      if self._dot_job:
        self.after_cancel(self._dot_job)
        self._dot_job = None
      dot, self._dot_item = self._dot_item, None
      self.remove_item(dot)
      self.pre_state = None
    c = self.canvas
    x, y = c.canvasx(event.x), c.canvasy(event.y)
    items = c.find_withtag("current")
    pre = self.get_history_state()  # strokes are kept when undoing a text edit
    existing = bool(items) and "text_note" in c.gettags(items[0])
    if existing:
      item = items[0]
    else:
      item = self.text_create(x, y, "", TEXT_COLOR, getattr(self, "_text_base_size", TEXT_BASE_SIZE))
    self._text_begin_edit(item, pre)
    if existing:
      self._text_select_word(item, c.index(item, f"@{x},{y}"))  # double click on a written text selects the word
    else:
      c.icursor(item, "end")
      self._text_anchor = 0
    self._text_sel_active = True  # the release of this click must not run the normal release code
    self.update_workspace_counter()

  def on_text_key(self, e):
    if getattr(self, "_table_edit", None) is not None:
      return self.table_key(e)  # typing inside a table cell
    item = self.editing_text
    if item is None:
      return
    c = self.canvas
    key = e.keysym
    if (e.state & 0x4) and key.lower() in ("c", "x", "v", "a"):
      {"c": self.text_copy, "x": self.text_cut, "v": self.text_paste, "a": self.text_select_all}[key.lower()]()
      return "break"
    i = c.index(item, "insert")
    if key == "BackSpace":
      if not self._text_delete_selection(item) and i > 0:
        self._text_delete(item, i - 1, i - 1)
    elif key == "Delete":
      if not self._text_delete_selection(item) and i < len(c.itemcget(item, "text")):
        self._text_delete(item, i, i)
    elif key == "Left":
      c.select_clear()
      c.icursor(item, max(0, i - 1))
    elif key == "Right":
      c.select_clear()
      c.icursor(item, i + 1)
    elif key == "Return":
      self._text_delete_selection(item)
      self._text_insert(item, c.index(item, "insert"), "\n")
    elif key == "Escape":
      self.finish_text_edit()
    elif e.char and e.char.isprintable():
      self._text_delete_selection(item)
      self._text_insert(item, c.index(item, "insert"), e.char)
    return "break"

  def finish_text_edit(self):
    if hasattr(self, "table_finish_edit"):
      self.table_finish_edit()  # a table cell being typed in is finished too
    item = self.editing_text
    if item is None:
      return
    self.editing_text = None
    c = self.canvas
    c.select_clear()
    c.focus("")
    changed = False
    if c.type(item):
      text = c.itemcget(item, "text")
      if not text.strip():
        c.delete(item)
        self._text_forget(item)
        changed = bool(self._text_before.strip())  # an existing text that was emptied
      else:
        changed = text != self._text_before or self._text_attrs(item) != self._text_attr_before
    if changed:  # an undo step only when the text, its size or its highlight really changed
      self.pre_state = self.text_pre
      self.commit_change()
    self.update_workspace_counter()
