import math
from tkinter import filedialog
from PIL import Image, ImageColor, ImageDraw, ImageGrab, ImageTk

AA = 4

class CanvasGraphicsRenderingMixin:

  def wire_image(self, item):
    """Red insulated copper wire: bare copper tips at free ends, insulation to the end where it touches another wire."""
    co = self.canvas.coords(item)
    if len(co) < 4:
      return None
    x1, y1, x2, y2 = co[0], co[1], co[-2], co[-1]
    L = math.hypot(x2 - x1, y2 - y1)
    bare = self.cable_ends(item)
    self.cable_flags[item] = bare
    if L < 4:
      return None
    w = max(3.0, self.stroke_w.get(item, 8.0))
    tip = min(w * 5, L / 3)
    ux, uy = (x2 - x1) / L, (y2 - y1) / L
    nx, ny = -uy, ux
    a, b = (tip if bare[0] else 0), L - (tip if bare[1] else 0)
    pad = w / 2 + 3
    min_x, min_y = min(x1, x2) - pad, min(y1, y2) - pad
    W, H = int(max(x1, x2) + pad - min_x) + 1, int(max(y1, y2) + pad - min_y) + 1
    if W > 2000 or H > 2000:
      return None
    ss = AA
    while ss > 1 and W * H * ss * ss > 5_000_000:
      ss -= 1
    img = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    P = lambda t, o: ((x1 + ux * t + nx * o - min_x) * ss, (y1 + uy * t + ny * o - min_y) * ss)

    def rect(t0, t1, o0, o1, fill):
      d.polygon([P(t0, o0), P(t1, o0), P(t1, o1), P(t0, o1)], fill=fill)

    ch = w * 0.225  # copper half-thickness
    for on, t0, t1 in ((bare[0], 0, a + 1), (bare[1], b - 1, L)):
      if on:
        rect(t0, t1, -ch, ch, (206, 131, 92, 255))
        rect(t0, t1, -ch * 0.8, -ch * 0.15, (250, 200, 150, 255))
        rect(t0, t1, ch * 0.4, ch, (185, 126, 96, 255))
    base = ImageColor.getrgb(self.canvas.itemcget(item, "fill"))[:3]
    red = base + (255,)
    light = tuple(int(v + (255 - v) * 0.3) for v in base) + (255,)
    dark = tuple(int(v * 0.78) for v in base) + (255,)
    rect(a, b, -w / 2, w / 2, red)
    for on, t in ((bare[0], a), (bare[1], b)):
      if not on:
        x, y = P(t, 0)
        r = w / 2 * ss
        d.ellipse((x - r, y - r, x + r, y + r), fill=red)
    rect(a, b, -w * 0.42, -w * 0.12, light)
    rect(a, b, w * 0.2, w * 0.5, dark)
    img = img.resize((W, H), Image.Resampling.LANCZOS)
    return img, min_x, min_y

  def refresh_cables(self):
    for i in self.canvas.find_withtag("cable_shape"):
      if i not in self.live and self.cable_ends(i) != self.cable_flags.get(i):
        self.aa_render(i)

  def aa_image(self, item):
    c = self.canvas
    if "cable_shape" in c.gettags(item):
      return self.wire_image(item)
    pts = self.stroke_points(item)
    if len(pts) < 2:
      return None
    w = max(1.5, self.stroke_w.get(item, 1.0))
    pad = w / 2 + 2
    min_x = min(p[0] for p in pts) - pad
    min_y = min(p[1] for p in pts) - pad
    max_x = max(p[0] for p in pts) + pad
    max_y = max(p[1] for p in pts) + pad
    
    W = int(max_x - min_x) + 1
    H = int(max_y - min_y) + 1
    
    if W < 8 or H < 8:
      return None

    if W > 2000 or H > 2000:
      return None

    ss = AA
    while ss > 1 and W * H * ss * ss > 5_000_000:
      ss -= 1
      
    try:
      mask = Image.new("L", (max(1, W * ss), max(1, H * ss)), 0)
      d = ImageDraw.Draw(mask)
      
      sp = [((x - min_x) * ss, (y - min_y) * ss) for x, y in pts]
      scaled_w = w

      d.line(sp, fill=255, width=max(1, round(scaled_w * ss)), joint="curve")
      r = scaled_w * ss / 2
      if len(sp) > 0:
        for ex, ey in (sp[0], sp[-1]):
          d.ellipse((ex - r, ey - r, ex + r, ey + r), fill=255)
          
      mask = mask.resize((max(1, W), max(1, H)), Image.Resampling.BILINEAR)
      opt = "outline" if self.is_shape(item) and not self.is_spring(item) else "fill"
      color_val = ImageColor.getrgb(c.itemcget(item, opt))
      img = Image.new("RGBA", (max(1, W), max(1, H)), color_val + (0,))
      img.putalpha(mask)
      return img, min_x, min_y
    except Exception:
      return None

  def aa_render(self, item):
    c = self.canvas
    if not c.type(item) or self._skip_aa:
      return
    res = self.aa_image(item)
    if res is None:
      aid = self.aa_ids.get(item)
      if aid:
        c.itemconfig(aid, state="hidden")
      c.itemconfig(item, state="normal", width=max(1, self.stroke_w.get(item, 1.0)))
      return
    img, x0, y0 = res
    tk_img = ImageTk.PhotoImage(img)
    aid = self.aa_ids.get(item)
    if aid is None or not c.type(aid):
      if aid is not None:
        try:
          c.delete(aid)
        except Exception:
          pass
      aid = c.create_image(x0, y0, image=tk_img, anchor="nw", tags="aa", state="normal")
      self.aa_ids[item] = aid
    else:
      c.coords(aid, x0, y0)
      c.itemconfig(aid, image=tk_img, state="normal")
    self.aa_tk[item] = tk_img
    c.itemconfig(item, state="hidden")

  def aa_live(self, item):
    c = self.canvas
    if item in self.live or not c.type(item):
      return
    self.live.add(item)
    c.itemconfig(item, state="normal", width=self.stroke_w.get(item, 1.0))
    aid = self.aa_ids.get(item)
    if aid:
      c.itemconfig(aid, state="hidden")

  def aa_finish(self):
    for i in list(self.live):
      self.aa_render(i)
    self.live.clear()

  def aa_all(self):
    self._aa_job = None
    for i in self.stroke_items():
      self.aa_render(i)

  def schedule_aa_all(self):
    if self._aa_job:
      self.after_cancel(self._aa_job)
    self._aa_job = self.after(150, self.aa_all)

  def draw_frame(self, pts):
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    frame = []
    for px, py in pts:
      d = math.hypot(px - cx, py - cy) or 1
      frame.append((px + (px - cx) / d * 7, py + (py - cy) / d * 7))
    self.canvas.create_polygon(*[v for p in frame for v in p], outline="white", fill="", dash=(4, 3),
                               state="disabled", tags="handle")
    return frame

  def draw_handle(self, x, y, icon, name, r=13):
    tags = ("handle", name)
    self.canvas.create_oval(x - r, y - r, x + r, y + r, fill="white", outline="#888888", tags=tags)
    self.canvas.create_text(x, y, text=icon, fill="#222222", font=("Arial", 12 if r > 10 else 9, "bold"), tags=tags)

  def show_handles(self):
    self.pcb_refresh()  # keeps PCB maps in step with every move / zoom / crop
    self.canvas.delete("handle")
    item = self.selected_item
    
    if self.is_picture(item):
      self.draw_picture_handles(item)
      return

    if not item or not (item in self.image_refs or self.is_shape(item)):
      return

    mid = lambda a, b: ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

    if item in self.image_refs:
      F = self.draw_frame([self.corner_point(item, sx, sy) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
      self.draw_handle(*F[0], "×", "delete")
      self.draw_handle(*F[1], "↻", "img_rotate")
      self.draw_handle(*F[3], "↙", "cropBL")
      self.draw_handle(*F[2], "↘", "cropBR")
      self.draw_handle(*mid(F[0], F[3]), "↔", "cropL", 9)
      self.draw_handle(*mid(F[1], F[2]), "↔", "cropR", 9)
      self.draw_handle(*mid(F[0], F[1]), "↕", "cropT", 9)
      self.draw_handle(*mid(F[3], F[2]), "↕", "cropB", 9)
      tm, cc = mid(F[0], F[1]), mid(F[0], F[2])
      dd = math.hypot(tm[0] - cc[0], tm[1] - cc[1]) or 1
      self.draw_handle(tm[0] + (tm[0] - cc[0]) / dd * 24, tm[1] + (tm[1] - cc[1]) / dd * 24, "💾", "save", 9)

      mr, mb = mid(F[1], F[2]), mid(F[3], F[2])
      dr = math.hypot(mr[0] - cc[0], mr[1] - cc[1]) or 1
      db = math.hypot(mb[0] - cc[0], mb[1] - cc[1]) or 1
      self.draw_handle(mr[0] + (mr[0] - cc[0]) / dr * 24, mr[1] + (mr[1] - cc[1]) / dr * 24, "⇔", "stretchH", 9)
      self.draw_handle(mb[0] + (mb[0] - cc[0]) / db * 24, mb[1] + (mb[1] - cc[1]) / db * 24, "⇕", "stretchV", 9)

      ref = self.image_refs[item]
      comp_name = ref.get("path", "Component")
      bx, by = mid(F[3], F[2])
      text_tags = ("handle", "component_name_label")
      self.canvas.create_text(bx + 1, by + 41, text=comp_name, anchor="n", font=("Arial", 11, "bold"), fill="#000000", tags=text_tags)
      self.canvas.create_text(bx, by + 40, text=comp_name, anchor="n", font=("Arial", 11, "bold"), fill="#f1c40f", tags=text_tags)

    elif self.is_box(item) or self.is_star(item):
      pts = self.box_pts(item)
      if not pts:
        return
      xs, ys = [p[0] for p in pts], [p[1] for p in pts]
      x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
      F = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
      self.draw_handle(*F[0], "×", "box_delete")
      self.draw_handle(*F[1], "↻", "box_rotate")
      self.draw_handle(*F[2], "⤡", "box_resize")
      self.draw_handle(*F[3], "💾", "save", 9)
    elif self.is_circle(item):
      pts = self.box_pts(item)
      if not pts:
        return
      xs, ys = [p[0] for p in pts], [p[1] for p in pts]
      x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
      self.draw_handle(x1, y1, "×", "box_delete")
      self.draw_handle(x2, y1, "↻", "box_rotate")
      self.draw_handle(x2, y2, "⤡", "box_resize")
      self.draw_handle(x1, y2, "💾", "save", 9)
    elif self.is_spring(item):
      prm = self.spring_params(item)
      if not prm:
        return
      (sx, sy), (ex, ey), W, (ux, uy), (rx, ry), L = prm
      h = W / 2
      TL, TR = (sx - rx * h, sy - ry * h), (sx + rx * h, sy + ry * h)
      BL, BR = (ex - rx * h, ey - ry * h), (ex + rx * h, ey + ry * h)
      self.canvas.create_polygon(*TL, *TR, *BR, *BL, outline="white", fill="", dash=(4, 3),
                                 state="disabled", tags="handle")
      self.draw_handle(*TL, "×", "box_delete")
      self.draw_handle(*TR, "↻", "box_rotate")
      self.draw_handle(*BR, "⤡", "box_resize")
      self.draw_handle(*BL, "💾", "save", 9)
      self.draw_handle((sx + ex) / 2 + rx * h, (sy + ey) / 2 + ry * h, "↔", "spring_w", 9)
      self.draw_handle(ex, ey, "↕", "spring_len", 9)

  def _pyramid_level(self, ref, cw0, ch0, tw, th):
    """Smallest half-size copy of the photo that is still clearly larger than the size we need on screen."""
    img = ref["pil_img"]
    pyr = ref.get("_pyr")
    if pyr is None or pyr.get("src") is not img:
      pyr = ref["_pyr"] = {"src": img, 0: img}
    j = 0
    while cw0 / 2 ** (j + 1) >= tw * 1.5 and ch0 / 2 ** (j + 1) >= th * 1.5 and min(img.size) / 2 ** (j + 1) >= 32:
      j += 1
    for k in range(1, j + 1):
      if k not in pyr:
        pyr[k] = pyr[k - 1].reduce(2)
    return pyr[j]

  def render_image(self, item_id):
    ref = self.image_refs[item_id]
    if "pcb" in ref:  # PCB maps draw only the visible part
      self.pcb_render(item_id)
      if item_id == self.selected_item:
        self.show_handles()
      return
    s = self.item_scales[item_id] * ref.get("base", 1.0)
    img = ref["pil_img"]
    crop = ref.get("crop", [0.0, 0.0, 0.0, 0.0])
    W0, H0 = img.size
    if any(crop):
      cw0 = int((1 - crop[2]) * W0) - int(crop[0] * W0)
      ch0 = int((1 - crop[3]) * H0) - int(crop[1] * H0)
    else:
      cw0, ch0 = W0, H0
    tw = max(10, int(cw0 * s * ref.get("sx", 1.0)))
    th = max(10, int(ch0 * s * ref.get("sy", 1.0)))

    src = self._pyramid_level(ref, cw0, ch0, tw, th)  # half-size copy when zoomed out: much faster than resizing the full photo
    if any(crop):
      sw, sh = src.size
      src = src.crop((int(crop[0] * sw), int(crop[1] * sh), int((1 - crop[2]) * sw), int((1 - crop[3]) * sh)))

    fast = self._skip_aa
    img = src.resize((tw, th), Image.Resampling.BILINEAR if fast else Image.Resampling.LANCZOS)
    ang = self.item_rotations[item_id] % 360
    if ang:
      img = img.rotate(ang, expand=True, resample=Image.Resampling.NEAREST if fast else Image.Resampling.BICUBIC)
    ref["tk_img"] = ImageTk.PhotoImage(img)
    self.canvas.itemconfig(item_id, image=ref["tk_img"])
    if item_id == self.selected_item:
      self.show_handles()

  def export_to_pdf(self):
    """Exports the workspace drawing and components into a clean PDF file."""
    path = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF Document", "*.pdf")])
    if not path:
      return

    c = self.canvas
    c.delete("handle")
    c.delete("hover_name")
    box = c.bbox("all")
    if box:
      x0, y0 = max(0, int(box[0]) - 20), max(0, int(box[1]) - 20)
      x1, y1 = int(box[2]) + 20, int(box[3]) + 20
      w, h = c.winfo_width(), c.winfo_height()
      if w <= 0 or h <= 0:
        w, h = 1200, 800
      
      sr = [float(v) for v in str(c.cget("scrollregion")).split()]
      out = Image.new("RGB", (x1 - x0, y1 - y0), c.cget("bg"))
      for ty in range(y0, y1, h):
        for tx in range(x0, x1, w):
          c.yview_moveto((ty - sr[1]) / (sr[3] - sr[1]))
          self.update()
          tile = ImageGrab.grab((c.winfo_rootx(), c.winfo_rooty(), c.winfo_rootx() + w, c.winfo_rooty() + h))
          out.paste(tile, (int(c.canvasx(0)) - x0, int(c.canvasy(0)) - y0))
      
      out.save(path, "PDF", resolution=100.0)
      c.yview_moveto(-sr[1] / (sr[3] - sr[1]))
    self.show_handles()
