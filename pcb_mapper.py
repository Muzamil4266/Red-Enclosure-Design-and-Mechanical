import os
import math
import uuid
import queue
import threading
import customtkinter as ctk
from tkinter import filedialog
from PIL import Image, ImageDraw, ImageTk
import pcb_stitch

FIT_W = 1200                                   # a new PCB map opens about this wide (screen px at zoom 1.0)
NO_ENH = {"sharp": 0, "bright": 0, "detail": 0}
PHOTO_TYPES = [("Photos", "*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff")]


class PCBMapperMixin:
  """A PCB map is a normal entry in image_refs (so handles, crop, stretch, rotate, zoom, undo all work) with ref["pcb"].
  Its canvas item is only the anchor. The pixels you see are a 'tile': just the visible part of the full image,
  re-drawn from the original resolution (plus half-size copies) whenever the view, zoom or item changes."""

  def init_pcb(self):
    self.pcb_tiles = {}        # PCB item -> tile item showing the visible part
    self.pcb_tile_owner = {}   # tile item -> PCB item
    self.pcb_notes = {}        # PCB item -> [(x, y, text item)]  temporary "Image missing" markers
    self.pcb_notice = None
    self._pcb_writing = set()
    self._pcb_poll()

  # ------------------------------------------------------------------ viewport rendering
  def _pcb_poll(self):
    try:
      self.pcb_refresh()
    except Exception as e:
      print(f"PCB refresh error: {e}")
    self.after(60, self._pcb_poll)  # scrolling the view has no hook, so it is checked here (cheap when nothing changed)

  def pcb_key(self, item):
    c, ref = self.canvas, self.image_refs[item]
    return (tuple(c.coords(item)), self.item_scales[item], self.item_rotations[item], tuple(ref.get("crop", ())),
            ref.get("sx", 1.0), ref.get("sy", 1.0), c.canvasx(0), c.canvasy(0), c.winfo_width(), c.winfo_height(),
            id(ref["pil_img"]))

  def pcb_refresh(self):
    c = self.canvas
    for item, tid in list(self.pcb_tiles.items()):  # PCB was deleted / undone: remove its tile and markers
      if item not in self.image_refs:
        c.delete(tid)
        del self.pcb_tiles[item]
        self.pcb_tile_owner.pop(tid, None)
        self.pcb_clear_notes(item)
    for item, ref in self.image_refs.items():
      if "pcb" in ref and ref.get("pcb_key") != self.pcb_key(item):
        self.pcb_render(item)
    for item, notes in self.pcb_notes.items():
      for x, y, tid in notes:
        c.coords(tid, *self.pcb_to_canvas(item, x, y))

  def pcb_render(self, item):
    c, ref = self.canvas, self.image_refs[item]
    ref["pcb_key"] = self.pcb_key(item)
    vw, vh = c.winfo_width(), c.winfo_height()
    if vw < 2 or vh < 2:
      return
    x0, y0 = c.canvasx(0), c.canvasy(0)
    poly = [(px - x0, py - y0) for px, py in (self.corner_point(item, a, b) for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)))]
    tid = self.pcb_tiles.get(item)
    if max(p[0] for p in poly) < 0 or min(p[0] for p in poly) > vw or max(p[1] for p in poly) < 0 or min(p[1] for p in poly) > vh:
      if tid:
        c.itemconfig(tid, state="hidden")  # the board is off screen
      return

    img = ref["pil_img"]
    w, h = img.size
    l, t, r, b = ref.get("crop", [0.0, 0.0, 0.0, 0.0])
    s = self.item_scales[item] * ref.get("base", 1.0)
    zx, zy = s * ref.get("sx", 1.0), s * ref.get("sy", 1.0)  # screen px per photo px
    cx, cy = c.coords(item)
    ca, sa = math.cos(math.radians(self.item_rotations[item])), math.sin(math.radians(self.item_rotations[item]))

    # Half-size copies keep zoomed-out views sharp (no skipped pixels): pick the one nearest to 1 screen px per pixel
    zmin = min(zx, zy)
    k = int(math.log2(1 / zmin)) if zmin < 1 else 0
    k = max(0, min(k, int(math.log2(max(1, min(w, h) / 32)))))
    pyr = ref["pcb"]["pyr"]
    pyr.setdefault(0, img)
    for j in range(1, k + 1):
      if j not in pyr:
        pyr[j] = pyr[j - 1].reduce(2)
    f = 2 ** k

    # Output pixel (u, v) of the screen -> source pixel: undo the rotation, then the stretch/zoom, then the crop offset
    ox, oy = x0 - cx, y0 - cy
    u0, v0 = l * w + w * (1 - l - r) / 2, t * h + h * (1 - t - b) / 2
    kx, ky = f * zx, f * zy
    coef = (ca / kx, -sa / kx, (ox * ca - oy * sa) / kx + u0 / f,
            sa / ky, ca / ky, (ox * sa + oy * ca) / ky + v0 / f)
    fast = self._skip_aa or getattr(self, "_zoom_job", None) is not None  # zooming / holding undo: quicker, sharp again afterwards
    tile = pyr[k].transform((vw, vh), Image.Transform.AFFINE, coef,
                            resample=Image.Resampling.BILINEAR if fast else Image.Resampling.BICUBIC)
    mask = Image.new("L", (vw, vh), 0)
    ImageDraw.Draw(mask).polygon(poly, fill=255)
    tile.putalpha(mask)
    ref["tk_img"] = ImageTk.PhotoImage(tile)

    if tid is None:
      tid = c.create_image(x0, y0, anchor="nw", image=ref["tk_img"], tags="pcb_tile")
      c.tag_raise(tid, item)
      self.pcb_tiles[item] = tid
      self.pcb_tile_owner[tid] = item
    else:
      c.coords(tid, x0, y0)
      c.itemconfig(tid, image=ref["tk_img"], state="normal")

  # ------------------------------------------------------------------ geometry helpers
  def pcb_hit(self, item, x, y):
    """True if canvas point (x, y) is inside the (rotated, cropped, stretched) board."""
    ref = self.image_refs[item]
    w, h = ref["pil_img"].size
    l, t, r, b = ref.get("crop", [0.0, 0.0, 0.0, 0.0])
    s = self.item_scales[item] * ref.get("base", 1.0)
    cx, cy = self.canvas.coords(item)
    a = math.radians(self.item_rotations[item])
    dx, dy = x - cx, y - cy
    lx, ly = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
    return abs(lx) <= w * (1 - l - r) * s * ref.get("sx", 1.0) / 2 and abs(ly) <= h * (1 - t - b) * s * ref.get("sy", 1.0) / 2

  def pcb_owner(self, i):
    """Used by pick_item: a tile id means 'the board under the cursor', but only where the board really is."""
    item = self.pcb_tile_owner.get(i)
    if item is None:
      return i
    return item if item in self.image_refs and self.pcb_hit(item, self.last_x, self.last_y) else None

  def pcb_to_canvas(self, item, x, y):
    """Photo pixel (x, y) of the full image -> canvas coordinates."""
    ref = self.image_refs[item]
    w, h = ref["pil_img"].size
    l, t, r, b = ref.get("crop", [0.0, 0.0, 0.0, 0.0])
    s = self.item_scales[item] * ref.get("base", 1.0)
    lx = (x - (l * w + w * (1 - l - r) / 2)) * s * ref.get("sx", 1.0)
    ly = (y - (t * h + h * (1 - t - b) / 2)) * s * ref.get("sy", 1.0)
    a = math.radians(self.item_rotations[item])
    cx, cy = self.canvas.coords(item)
    return cx + lx * math.cos(a) + ly * math.sin(a), cy - lx * math.sin(a) + ly * math.cos(a)

  # ------------------------------------------------------------------ creating / loading a map
  def pcb_spawn(self, orig, fname, enh=None, x=None, y=None, scale=None, rot=0, crop=None, stretch=None):
    c = self.canvas
    enh = dict(enh or NO_ENH)
    img = pcb_stitch.enhance(orig, **enh)
    if x is None:
      x, y = c.canvasx(c.winfo_width() / 2), c.canvasy(c.winfo_height() / 2)
    item = c.create_image(x, y, anchor="center", tags="component")
    self.image_refs[item] = {
        "path": "PCB Map", "pil_img": img, "orig_pil": img, "base": FIT_W / orig.width,
        "crop": crop if crop else [0.0, 0.0, 0.0, 0.0],
        "sx": stretch[0] if stretch else 1.0, "sy": stretch[1] if stretch else 1.0,
        "pcb": {"file": fname, "orig": orig, "enh": enh, "pyr": {}}}
    self.item_scales[item] = scale if scale is not None else self.current_zoom_scale
    self.item_rotations[item] = rot
    self.render_image(item)
    return item

  def pcb_load(self, comp):
    """Called by set_state for a saved PCB map: its lossless PNG sits in the project folder."""
    d, name = getattr(self, "_state_dir", None), comp["pcb"]["file"]
    path = os.path.join(d, name) if d else ""
    if not os.path.isfile(path):
      self.toast(f"PCB image '{name}' is missing from the project folder.", 4000)
      return
    self.pcb_spawn(Image.open(path).convert("RGB"), name, comp["pcb"].get("enh"), comp["x"], comp["y"], comp["scale"],
                   comp["rotation"], comp.get("crop"), (comp.get("sx", 1.0), comp.get("sy", 1.0)))

  def pcb_write_files(self, folder):
    """Writes each map's original once as a lossless PNG (in the background); autosave only rewrites the small JSON."""
    for ref in list(self.image_refs.values()):
      pc = ref.get("pcb")
      path = os.path.join(folder, pc["file"]) if pc else None
      if path and not os.path.exists(path) and path not in self._pcb_writing:
        self._pcb_writing.add(path)
        threading.Thread(target=self._pcb_png, args=(pc["orig"], path), daemon=True).start()

  def _pcb_png(self, img, path):
    try:
      img.save(path + ".tmp", format="PNG", compress_level=3)
      os.replace(path + ".tmp", path)
    except Exception as e:
      print(f"PCB save error: {e}")
    self._pcb_writing.discard(path)

  def pcb_export(self, item):
    """The 'save' handle on a map: exports the cropped, enhanced board as a full-resolution PNG file."""
    path = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG image", "*.png")])
    if not path:
      return
    ref = self.image_refs[item]
    img = ref["pil_img"]
    l, t, r, b = ref.get("crop", [0.0, 0.0, 0.0, 0.0])
    w, h = img.size
    img.crop((int(l * w), int(t * h), int((1 - r) * w), int((1 - b) * h))).save(path, "PNG")
    self.toast("💾 PCB map exported!")

  # ------------------------------------------------------------------ PCB Mapper button
  def open_pcb_mapper(self):
    paths = filedialog.askopenfilenames(master=self, title="Select photos of the PCB", filetypes=PHOTO_TYPES)
    if not paths:
      return
    if len(paths) < 2:
      self.toast("Select at least 2 overlapping photos.")
      return

    win = ctk.CTkToplevel(self)
    win.title("PCB Mapper")
    win.geometry("400x180")
    win.resizable(False, False)
    win.transient(self)
    win.grab_set()
    ctk.CTkLabel(win, text=f"{len(paths)} photos selected", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(14, 4))
    status = ctk.CTkLabel(win, text="", wraplength=360)
    status.pack(pady=4)
    bar = ctk.CTkProgressBar(win, width=340)
    bar.set(0)
    bar.pack(pady=4)
    start = ctk.CTkButton(win, text="▶ Start stitching")
    start.pack(pady=8)

    q = queue.Queue()

    def work():
      try:
        q.put(("done", pcb_stitch.run(paths, lambda f, t: q.put(("p", f, t)))))
      except Exception as e:
        q.put(("err", str(e)))

    def poll():
      if not win.winfo_exists():
        return  # window closed: the result is dropped
      try:
        while True:
          m = q.get_nowait()
          if m[0] == "p":
            status.configure(text=m[2])
            if m[1] is None:
              if bar.cget("mode") != "indeterminate":
                bar.configure(mode="indeterminate")
                bar.start()
            else:
              bar.set(m[1])
          elif m[0] == "done":
            win.destroy()
            self.pcb_add(m[1])
            return
          else:
            bar.stop()
            status.configure(text="❌ " + m[1], text_color="#e74c3c")
            start.configure(text="Close", state="normal", command=win.destroy)
            return
      except queue.Empty:
        pass
      win.after(100, poll)

    def go():
      start.configure(state="disabled")
      threading.Thread(target=work, daemon=True).start()
      poll()
    start.configure(command=go)

  def pcb_add(self, res):
    self.finish_text_edit()
    self.begin_change()
    item = self.pcb_spawn(res["image"], "pcb_" + uuid.uuid4().hex[:8] + ".png")
    self.commit_change()
    self.selected_item = item
    self.show_handles()
    self.update_workspace_counter()

    c = self.canvas
    self.pcb_notes[item] = [(x, y, c.create_text(0, 0, text="⚠ Image missing", fill="#ff5252", font=("Arial", 13, "bold"),
                                                  state="disabled", tags="pcb_note")) for x, y in res["holes"]]
    self.pcb_refresh()
    if res["skipped"]:
      res["info"] += " Skipped (redundant): " + ", ".join(res["skipped"][:6]) + ("…" if len(res["skipped"]) > 6 else "")
    if res["warnings"]:
      self.pcb_show_notice(res["warnings"])
    self.toast("🧩 " + res["info"], 4000)

  def pcb_show_notice(self, lines):
    self.pcb_dismiss()
    box = ctk.CTkFrame(self.canvas_container, fg_color="#7b241c")
    box.place(relx=0.5, rely=0.12, anchor="n")
    ctk.CTkLabel(box, text="\n".join(lines), justify="left", wraplength=520, text_color="white",
                 font=ctk.CTkFont(size=12, weight="bold")).pack(padx=14, pady=(10, 4))
    ctk.CTkButton(box, text="Dismiss", width=80, height=24, fg_color="#c0392b", command=self.pcb_dismiss).pack(pady=(0, 10))
    self.pcb_notice = box

  def pcb_dismiss(self):
    if self.pcb_notice is not None:
      self.pcb_notice.destroy()
      self.pcb_notice = None
    for item in list(self.pcb_notes):
      self.pcb_clear_notes(item)

  def pcb_clear_notes(self, item):
    for _, _, tid in self.pcb_notes.pop(item, []):
      self.canvas.delete(tid)

  # ------------------------------------------------------------------ enhancement
  def open_pcb_enhance(self):
    item = self.selected_item
    ref = self.image_refs.get(item)
    if not ref or "pcb" not in ref:
      self.toast("Select a PCB map first.")
      return
    cur = ref["pcb"]["enh"]
    win = ctk.CTkToplevel(self)
    win.title("Enhance PCB")
    win.geometry("300x330")
    win.resizable(False, False)
    win.attributes("-topmost", True)
    sliders = {}
    for key, label, lo, hi in (("sharp", "Sharpness", 0, 100), ("detail", "Detail", 0, 100), ("bright", "Brightness", -50, 50)):
      ctk.CTkLabel(win, text=label, font=ctk.CTkFont(size=12, weight="bold")).pack(pady=(10, 0))
      sliders[key] = ctk.CTkSlider(win, from_=lo, to=hi, number_of_steps=hi - lo, width=240)
      sliders[key].set(cur[key])
      sliders[key].pack()

    def apply(reset=False):
      enh = dict(NO_ENH) if reset else {k: int(s.get()) for k, s in sliders.items()}
      win.destroy()
      self.toast("✨ Enhancing the full image...", 6000)
      self.update()
      self.begin_change()
      self.pcb_set_enh(item, enh)
      self.commit_change()
    ctk.CTkButton(win, text="✨ Apply", command=apply).pack(pady=(18, 4))
    ctk.CTkButton(win, text="↺ Reset (original)", fg_color="#34495e", command=lambda: apply(True)).pack(pady=4)
    ctk.CTkLabel(win, text="Works on the untouched original, so\nyou can change or undo it any time.",
                 font=ctk.CTkFont(size=10), text_color="gray").pack(pady=6)

  def pcb_set_enh(self, item, enh):
    ref = self.image_refs[item]
    pc = ref["pcb"]
    ref["pil_img"] = ref["orig_pil"] = pcb_stitch.enhance(pc["orig"], **enh)
    ref["pcb"] = {**pc, "enh": enh, "pyr": {}}  # new dict: older undo states keep their own image + settings
    self.render_image(item)
    self.show_handles()
