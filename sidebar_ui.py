import io
import math
import time
import queue
import base64
import threading
import customtkinter as ctk
from collections import deque, OrderedDict
from PIL import Image, ImageDraw, ImageTk
from components_data import EMBEDDED_COMPONENTS

BAR_H = 180                      # default bar height (px, ~4.7 cm)
MIN_BAR_H, MAX_BAR_H = 150, 360  # limits for the resize arrow
GRIP_H = 16                      # height of the resize strip
THUMB_OFFSET = 100               # thumbnail size = bar height - this
SCROLL_DIR = 1                   # set to -1 if the touchpad swipe feels reversed
SCROLL_SPEED = 1.5               # pixels per wheel/touchpad delta unit
GLIDE = 0.07                     # smooth-scroll time constant in seconds (smaller = snappier, bigger = floatier)
CARD_EXTRA = 28                  # card width = thumbnail size + this
CARD_GAP = 8                     # space between cards
END_W = 190                      # width of the LIST END marker at the end of the list
BUFFER = 2                       # extra cards drawn on each side of the visible ones
PREFETCH = 10                    # cards on each side whose pictures are prepared in advance
PHOTO_MAX = 250                  # screen-ready pictures kept in memory
GRIP_IDLE = ("gray70", "gray30")
GRIP_HOT = "#3498db"
CARD_COLOR = ("#e5e5e5", "#333333")   # (light, dark)
CARD_HOVER = ("#d0d0d0", "#444444")
TEXT_COLOR = ("black", "white")


class SidebarUIMixin:
  """The component bar is ONE continuous list of any length, drawn on a single canvas. Only the cards near the screen
  exist (all of them move together in one native step, so scrolling glides); pictures are prepared by a background
  thread. There is no length limit, and 'LIST END' marks the end."""

  def init_sidebar(self):
    self.bar_h = BAR_H
    self.sidebar_frame = ctk.CTkFrame(self, height=self.bar_h, corner_radius=0)
    self.sidebar_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
    self.sidebar_frame.pack_propagate(False)

    # Drag strip with arrows: drag up = taller bar, drag down = shorter bar
    self.resize_grip = ctk.CTkFrame(self.sidebar_frame, height=GRIP_H, corner_radius=0,
                                    fg_color=GRIP_IDLE, cursor="sb_v_double_arrow")
    self.resize_grip.pack(side="top", fill="x")
    arrows = ctk.CTkLabel(self.resize_grip, text="▲ ▼", height=GRIP_H, font=ctk.CTkFont(size=9))
    arrows.pack()
    for w in (self.resize_grip, arrows):
      w.bind("<Enter>", lambda e: self.resize_grip.configure(fg_color=GRIP_HOT))
      w.bind("<Leave>", lambda e: self.resize_grip.configure(fg_color=GRIP_IDLE))
      w.bind("<Button-1>", self.start_bar_resize)
      w.bind("<B1-Motion>", self.do_bar_resize)
      w.bind("<ButtonRelease-1>", self.stop_bar_resize)

    left = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
    left.pack(side="left", fill="y", padx=6, pady=6)

    self.search_entry = ctk.CTkEntry(left, placeholder_text="🔍 Search...", width=160)
    self.search_entry.pack(pady=(0, 5))
    self.search_entry.bind("<KeyRelease>", self.filter_components)

    self.add_btn = ctk.CTkButton(left, text="➕ Import", width=160, height=28, command=self.add_new_component)
    self.add_btn.pack(pady=(0, 5))

    self.counter_label = ctk.CTkLabel(
        left, text=f"Total saved: {len(EMBEDDED_COMPONENTS)}",
        font=ctk.CTkFont(size=11, weight="bold"), text_color=("gray40", "gray60")
    )
    self.counter_label.pack()

    # The list: a viewport (cards are placed inside it) and a horizontal scrollbar under it
    self.list_holder = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
    self.list_holder.pack(side="left", fill="both", expand=True, padx=(0, 6), pady=6)
    self.list_holder.grid_rowconfigure(0, weight=1)
    self.list_holder.grid_columnconfigure(0, weight=1)
    self._mode = ctk.get_appearance_mode()
    self.comp_inner_frame = ctk.CTkCanvas(self.list_holder, bg=self._vl_bg(), highlightthickness=0, height=10)
    self.comp_inner_frame.grid(row=0, column=0, sticky="nsew")
    self.list_scroll = ctk.CTkScrollbar(self.list_holder, orientation="horizontal", command=self._vl_scrollbar_cmd)
    self.list_scroll.grid(row=1, column=0, sticky="ew", pady=(2, 0))
    cv = self.comp_inner_frame
    cv.bind("<Configure>", lambda e: self._vl_schedule_layout())
    cv.bind("<Button-1>", self._vl_click)
    for ev in ("<Button-3>", "<Button-2>"):
      cv.bind(ev, self._vl_context)
    cv.bind("<Motion>", self._vl_motion)
    cv.bind("<Leave>", lambda e: self._vl_set_hover(None))
    self.bind_all("<MouseWheel>", lambda e: self._vl_wheel(e, e.delta), add="+")
    self.bind_all("<Shift-MouseWheel>", lambda e: self._vl_wheel(e, e.delta), add="+")
    for btn, d in (("4", 120), ("5", -120)):  # Linux wheel
      self.bind_all(f"<Button-{btn}>", lambda e, d=d: self._vl_wheel(e, d), add="+")

    self.temp_hidden_components = set()
    self.thumb_cache = {}
    self.pil_cache = {}
    self._thumb_dims = {}
    self._thumb_built = {}    # name -> pixel size its cached thumbnail was built for
    self._base_cache = {}     # name -> decoded image shared by copies on the canvas
    self.card_by_name = {}    # name -> card currently on screen
    self.component_cards = []  # (kept for older callers; the list no longer needs it)
    self._thumb_job = None
    self._pending_h = self.bar_h

    self._names = []          # names in the list (after search / hidden filtering), in display order
    self._offset = 0.0        # scroll position in pixels (smoothly animated)
    self._target = 0.0        # where the scroll animation is heading
    self._ioff = 0            # the whole-pixel offset the cards are actually drawn at
    self._anim_job = None
    self._anim_t = 0.0
    self._cards = {}          # list index -> (rect, image, text) canvas items
    self._name_idx = {}       # name -> index, for the cards that exist
    self._photos = OrderedDict()  # (name, id(pil), size) -> screen-ready PhotoImage
    self._view_count = None
    self._layout_job = None
    self._refresh_job = None
    self._hover = None
    self._queued = set()
    self._wanted = set()
    self._work_q = queue.Queue()
    self._done = deque()
    self._poll_on = False
    threading.Thread(target=self._thumb_worker, daemon=True).start()

  def toggle_sidebar(self):
    self._sidebar_hidden = not getattr(self, "_sidebar_hidden", False)
    if self._sidebar_hidden:
      self.sidebar_frame.grid_remove()
    else:
      self.sidebar_frame.grid()
    self.sidebar_toggle_btn.configure(text="🔼 Show Components" if self._sidebar_hidden else "🔽 Hide Components")

  # ---- bar resize ----
  def start_bar_resize(self, event):
    self._drag_y, self._drag_h = event.y_root, self.bar_h
    real = self.sidebar_frame.winfo_height()
    self._drag_scale = real / self.bar_h if real > 1 else 1.0
    self._grip_top = self.resize_grip.winfo_rooty()
    self.resize_grip.configure(fg_color=GRIP_HOT)
    self._ghost = ctk.CTkFrame(self, height=3, corner_radius=0, fg_color=GRIP_HOT)

  def do_bar_resize(self, event):
    # Only a guide line moves while dragging (cheap); the real resize happens once on release
    self._pending_h = int(max(MIN_BAR_H, min(MAX_BAR_H, self._drag_h + (self._drag_y - event.y_root) / self._drag_scale)))
    y = self._grip_top - (self._pending_h - self._drag_h) * self._drag_scale - self.winfo_rooty()
    self._ghost.place(x=0, y=int(y), relwidth=1)
    self._ghost.lift()

  def stop_bar_resize(self, event):
    self.resize_grip.configure(fg_color=GRIP_IDLE)
    self._ghost.destroy()
    if self._pending_h == self.bar_h:
      return
    self.bar_h = self._pending_h
    self.sidebar_frame.configure(height=self.bar_h)
    if self._thumb_job:
      self.after_cancel(self._thumb_job)
    self._thumb_job = self.after(50, self._start_thumb_refresh)  # cards are resized and refilled

  def _start_thumb_refresh(self):
    self._thumb_job = None
    self._photos.clear()
    self._vl_reset()

  def update_counter(self):
    if hasattr(self, "counter_label"):
      total = len(EMBEDDED_COMPONENTS)
      vc = getattr(self, "_view_count", None)
      extra = f"  ·  {vc} match" if vc is not None and vc != total else ""
      self.counter_label.configure(text=f"Total saved: {total}{extra}")

  def thumb_px(self):
    return self.bar_h - THUMB_OFFSET

  def thumb_size(self, dims):
    k = self.thumb_px() / max(dims)
    return (max(1, round(dims[0] * k)), max(1, round(dims[1] * k)))

  def _make_thumb_pil(self, name, need, data=None):
    """Decodes a small picture for the list (plain PIL, safe to call from the background thread)."""
    try:
      raw = data if data is not None else EMBEDDED_COMPONENTS.raw(name)
      if isinstance(raw, io.BytesIO):
        raw = raw.getvalue()
      elif hasattr(raw, "read"):
        raw = raw.read()
      elif isinstance(raw, str):
        try:
          raw = base64.b64decode(raw.encode("utf-8"))
        except Exception:
          raw = raw.encode("utf-8")
      try:  # raw bytes first; fall back to base64 text
        img = Image.open(io.BytesIO(raw))
      except Exception:
        img = Image.open(io.BytesIO(base64.b64decode(raw)))
      if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
      img.thumbnail((need, need), Image.Resampling.LANCZOS)  # JPEGs are decoded at reduced size
      return img.convert("RGBA")
    except Exception:
      img = Image.new("RGBA", (100, 100), (50, 50, 50, 255))
      d = ImageDraw.Draw(img)
      d.rectangle([10, 10, 90, 90], outline=(150, 150, 150), width=2)
      d.text((15, 42), name[:12], fill=(255, 255, 255))
      return img

  def _store_thumb(self, name, img, need):
    self._thumb_dims[name] = img.size
    self._thumb_built[name] = need
    self.pil_cache[name] = img  # last: readers that see the picture also see its size

  def get_thumb(self, name, data=None):
    """Small cached thumbnail as a CTkImage (used when a component is imported or saved)."""
    need = int(self.thumb_px() * 1.5)
    img = self.pil_cache.get(name)
    built = self._thumb_built.get(name, 0)
    if img is None or (built < need and max(img.size) >= built):
      img = self._make_thumb_pil(name, need, data)
      self._store_thumb(name, img, need)
    sz = self.thumb_size(self._thumb_dims.get(name, (100, 100)))
    return ctk.CTkImage(light_image=img, dark_image=img, size=sz)

  def build_card(self, name, image_bytes=None):
    """Kept for older callers: the list draws its own cards now; this only prepares the thumbnail."""
    if image_bytes is not None:
      self.get_thumb(name, image_bytes)
    return None

  # ---- background picture loading ----
  def _thumb_worker(self):
    while True:
      name = self._work_q.get()
      try:
        if name in self._wanted and not self._thumb_ready(name):
          need = int(self.thumb_px() * 1.5)
          self._store_thumb(name, self._make_thumb_pil(name, need), need)
          self._done.append(name)
      except Exception as e:
        print(f"Thumbnail error ({name}): {e}")
      finally:
        self._queued.discard(name)

  def _thumb_ready(self, name):
    img = self.pil_cache.get(name)
    if img is None:
      return False
    built = self._thumb_built.get(name, 0)
    return not (built < int(self.thumb_px() * 1.5) and max(img.size) >= built)

  def _vl_poll(self):
    self._poll_on = False
    while self._done:
      name = self._done.popleft()
      i = self._name_idx.get(name)
      if i is not None:
        self._vl_fill(i)
    if self._queued or self._done:
      self._poll_on = True
      self.after(40, self._vl_poll)

  def _vl_photo(self, name):
    pil = self.pil_cache[name]
    size = self.thumb_size(pil.size)
    key = (name, id(pil), size)
    ph = self._photos.pop(key, None)
    if ph is None:
      ph = ImageTk.PhotoImage(pil.resize(size, Image.Resampling.LANCZOS))
    self._photos[key] = ph
    while len(self._photos) > PHOTO_MAX:
      self._photos.popitem(last=False)
    return ph

  # ---- the continuous list ----
  def _vl_bg(self):
    try:
      return self.sidebar_frame._apply_appearance_mode(self.sidebar_frame.cget("fg_color"))
    except Exception:
      return "#2b2b2b" if ctk.get_appearance_mode() == "Dark" else "#dbdbdb"

  def _vl_col(self, pair):
    return pair[1] if ctk.get_appearance_mode() == "Dark" else pair[0]

  def _vl_card_w(self):
    return self.thumb_px() + CARD_EXTRA

  def _vl_step(self):
    return self._vl_card_w() + CARD_GAP

  def _vl_card_h(self):
    return self.thumb_px() + 52

  def _vl_total(self):
    return len(self._names) * self._vl_step() + END_W

  def _vl_max(self):
    return max(0.0, self._vl_total() - max(1, self.comp_inner_frame.winfo_width()))

  def _view_names(self):
    q = self.search_entry.get().lower().strip()
    return [n for n in EMBEDDED_COMPONENTS if n not in self.temp_hidden_components and (not q or q in n.lower())]

  def load_components_to_sidebar(self):
    """Re-reads the list from the database. The scroll position is kept."""
    self._names = self._view_names()
    self._view_count = len(self._names) if self.search_entry.get().strip() else None
    self.update_counter()
    self._vl_reset()

  def filter_components(self, event=None):
    """Search box / after adding something: back to the start of the list (slightly delayed so typing stays smooth)."""
    if self._refresh_job:
      self.after_cancel(self._refresh_job)
    self._refresh_job = self.after(120, self._apply_filter)

  def _apply_filter(self):
    self._refresh_job = None
    self._offset = self._target = 0.0
    self.load_components_to_sidebar()

  def vlist_remove(self, name):
    """A component was deleted / hidden: take it out of the list without jumping back to the start."""
    if name in self._names:
      self._names.remove(name)
    if self._view_count is not None:
      self._view_count = len(self._names)
    self.update_counter()
    self._vl_reset()

  def _vl_reset(self):
    """Throws away every drawn card and redraws the ones that belong on screen."""
    self.comp_inner_frame.delete("card")
    self._cards.clear()
    self._name_idx.clear()
    self._hover = None
    self._offset = max(0.0, min(self._offset, self._vl_max()))
    self._target = max(0.0, min(self._target, self._vl_max()))
    self._ioff = int(round(self._offset))
    self._vl_sync()

  def _vl_schedule_layout(self):
    if self._layout_job is None:
      self._layout_job = self.after_idle(self._vl_layout)

  def _vl_layout(self):
    self._layout_job = None
    mode = ctk.get_appearance_mode()
    if mode != self._mode:  # light/dark switched: redraw in the new colours
      self._mode = mode
      self.comp_inner_frame.configure(bg=self._vl_bg())
      self._vl_reset()
      return
    self._vl_sync()

  def _vl_make(self, i):
    c, n = self.comp_inner_frame, len(self._names)
    cw, ch, px, step = self._vl_card_w(), self._vl_card_h(), self.thumb_px(), self._vl_step()
    x = i * step - self._ioff
    tag = f"i{i}"
    if i >= n:  # the end marker
      r = c.create_rectangle(x + 4, 2, x + END_W - 8, 2 + ch, outline="#f1c40f", dash=(5, 4), width=2, tags=("card", tag))
      t = c.create_text(x + END_W / 2 - 2, 2 + ch / 2, text=f"LIST END\n\n{n} component{'s' if n != 1 else ''}", justify="center",
                        font=("Arial", 14, "bold"), fill="#f1c40f", tags=("card", tag))
      self._cards[i] = (r, None, t)
      return
    name = self._names[i]
    r = c.create_rectangle(x, 2, x + cw, 2 + ch, fill=self._vl_col(CARD_COLOR), outline="", tags=("card", tag))
    im = c.create_image(x + cw / 2, 8 + px / 2, anchor="center", tags=("card", tag))
    t = c.create_text(x + cw / 2, 8 + px + 6, text=name, width=px + 10, anchor="n", justify="center",
                      font=("Arial", 9, "bold"), fill=self._vl_col(TEXT_COLOR), tags=("card", tag))
    self._cards[i] = (r, im, t)
    self._name_idx[name] = i
    self._vl_fill(i)

  def _vl_fill(self, i):
    parts = self._cards.get(i)
    if parts is None or parts[1] is None or i >= len(self._names):
      return
    name = self._names[i]
    if self._thumb_ready(name):
      ph = self._vl_photo(name)
      self.comp_inner_frame.itemconfig(parts[1], image=ph)
      self._cards[i] = parts[:3] + (ph,)  # keep a reference so the picture is never freed while shown

  def _vl_sync(self):
    """Draws the cards that enter the view, deletes the ones that left, and queues pictures that are about to be needed."""
    c = self.comp_inner_frame
    vw = c.winfo_width()
    if vw < 2:
      return
    n, step = len(self._names), self._vl_step()
    first = max(0, int(self._ioff // step) - BUFFER)
    last = min(n, int((self._ioff + vw) // step) + BUFFER)
    for i in [i for i in self._cards if i < first or i > last]:
      c.delete(f"i{i}")
      parts = self._cards.pop(i)
      if i < n and self._name_idx.get(self._names[i]) == i:
        self._name_idx.pop(self._names[i], None)
      if self._hover == i:
        self._hover = None
    for i in range(first, last + 1):
      if i not in self._cards:
        self._vl_make(i)
    # pictures for the cards around the view are prepared in the background
    lo, hi = max(0, first - PREFETCH), min(n - 1, last + PREFETCH)
    want = [self._names[i] for i in range(lo, hi + 1)]
    self._wanted = set(want)
    centre = (first + last) // 2
    for i in sorted(range(lo, hi + 1), key=lambda k: abs(k - centre)):
      name = self._names[i]
      if name not in self._queued and not self._thumb_ready(name):
        self._queued.add(name)
        self._work_q.put(name)
    if self._queued and not self._poll_on:
      self._poll_on = True
      self.after(40, self._vl_poll)
    total = self._vl_total()
    if total <= vw:
      self.list_scroll.set(0, 1)
    else:
      self.list_scroll.set(self._ioff / total, min(1.0, (self._ioff + vw) / total))

  def _vl_apply(self, offset):
    """Moves the view to `offset`: every card shifts together in one native canvas step."""
    self._offset = offset
    new = int(round(offset))
    dx = new - self._ioff
    if dx:
      self.comp_inner_frame.move("card", -dx, 0)
      self._ioff = new
      self._vl_sync()

  def _vl_scroll_to(self, x, glide=True):
    x = max(0.0, min(float(x), self._vl_max()))
    self._target = x
    if not glide:
      self._cancel_anim()
      self._vl_apply(x)
    elif self._anim_job is None:
      self._anim_t = time.perf_counter()
      self._anim_job = self.after(8, self._vl_anim)

  def _cancel_anim(self):
    if self._anim_job:
      self.after_cancel(self._anim_job)
      self._anim_job = None

  def _vl_anim(self):
    self._anim_job = None
    now = time.perf_counter()
    dt = min(0.05, now - self._anim_t)
    self._anim_t = now
    diff = self._target - self._offset
    if abs(diff) < 0.6:
      self._vl_apply(self._target)
      return
    self._vl_apply(self._offset + diff * (1 - math.exp(-dt / GLIDE)))
    self._anim_job = self.after(8, self._vl_anim)

  def _vl_scrollbar_cmd(self, *args):
    total, vw = self._vl_total(), self.comp_inner_frame.winfo_width()
    if args and args[0] == "moveto":
      self._vl_scroll_to(float(args[1]) * total, glide=False)
    elif args and args[0] == "scroll":
      amount = int(args[1]) * (vw if args[2] == "pages" else self._vl_step())
      self._vl_scroll_to(self._target + amount)

  def _vl_inside(self, widget):
    while widget is not None and not isinstance(widget, str):
      if widget is self.list_holder:
        return True
      widget = getattr(widget, "master", None)
    return False

  def _vl_wheel(self, event, delta):
    if self._vl_inside(event.widget):
      self._vl_scroll_to(self._target - delta * SCROLL_SPEED * SCROLL_DIR)

  # ---- mouse on the cards ----
  def _vl_hit(self, x, y):
    n, step = len(self._names), self._vl_step()
    gx = x + self._ioff
    i = int(gx // step)
    if 0 <= i < n and gx - i * step <= self._vl_card_w() and 2 <= y <= 2 + self._vl_card_h():
      return i
    return None

  def _vl_click(self, event):
    i = self._vl_hit(event.x, event.y)
    if i is not None:
      name = self._names[i]
      self.add_component_recorded(name, EMBEDDED_COMPONENTS[name])

  def _vl_context(self, event):
    i = self._vl_hit(event.x, event.y)
    if i is not None:
      self.show_component_context_menu(event, self._names[i])

  def _vl_motion(self, event):
    self._vl_set_hover(self._vl_hit(event.x, event.y))

  def _vl_set_hover(self, i):
    if i == self._hover:
      return
    c = self.comp_inner_frame
    old = self._cards.get(self._hover)
    if old:
      c.itemconfig(old[0], fill=self._vl_col(CARD_COLOR))
    self._hover = i
    new = self._cards.get(i)
    if new:
      c.itemconfig(new[0], fill=self._vl_col(CARD_HOVER))
    c.configure(cursor="hand2" if new else "")
