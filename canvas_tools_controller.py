import io
import math
import base64
from tkinter import simpledialog
from PIL import Image, ImageColor, ImageDraw
from components_data import EMBEDDED_COMPONENTS


class CanvasToolsControllerMixin:

  def center_all_components(self):
    """Moves the whole project, exactly as it is, so its middle sits in the middle of the view."""
    c = self.canvas
    xs, ys = [], []
    for i in list(self.image_refs) + list(c.find_withtag("text_note")) + list(c.find_withtag("tbl_bg")) + list(c.find_withtag("inserted_picture")):
      bb = c.bbox(i)
      if bb:
        xs += [bb[0], bb[2]]
        ys += [bb[1], bb[3]]
    for i in self.stroke_items():
      co = c.coords(i)
      xs += co[0::2]
      ys += co[1::2]
    if not xs:
      self.toast("Nothing to centre.")
      return
    w, h = c.winfo_width(), c.winfo_height()
    dx = 5760 - (min(xs) + max(xs)) / 2  # 5760 = middle of the workspace (where the program opens)
    dy = (h / 2 if h > 1 else 400) - (min(ys) + max(ys)) / 2  # view is reset to y = 0 at the top below
    if hasattr(self, "table_deselect"):
      self.table_deselect()
    self.begin_change()
    for tag in ("component", "wire", "box_shape", "circle_shape", "star_shape", "spring_shape", "cable_shape", "text_note", "aa", "table_item", "inserted_picture"):
      c.move(tag, dx, dy)
    self.commit_change()
    self.center_view()  # scrollbar to the middle too
    self.show_handles()
    self.toast("🎯 Project moved to centre!")

  def spring_begin(self):
    prm = self.spring_params(self.selected_item)
    if prm is None:
      return
    S, E, W = prm[0], prm[1], prm[2]
    self.spring_init = (S, E, W)
    cx, cy = (S[0] + E[0]) / 2, (S[1] + E[1]) / 2
    self.rot_center = (cx, cy)
    self.box_ang = math.atan2(self.last_y - cy, self.last_x - cx)

  def spring_transform(self, x, y):
    if not self.spring_init:
      return
    (sx, sy), (ex, ey), W = self.spring_init
    L = math.hypot(ex - sx, ey - sy) or 1.0
    ux, uy = (ex - sx) / L, (ey - sy) / L
    rx, ry = uy, -ux
    op = self.box_op
    if op == "rotate":
      cx, cy = self.rot_center
      a = math.atan2(y - cy, x - cx) - self.box_ang
      cs, sn = math.cos(a), math.sin(a)
      rot = lambda px, py: (cx + (px - cx) * cs - (py - cy) * sn, cy + (px - cx) * sn + (py - cy) * cs)
      (s2x, s2y), (e2x, e2y) = rot(sx, sy), rot(ex, ey)
      new_w = W
    else:
      tlx, tly = sx - rx * W / 2, sy - ry * W / 2
      px, py = x - tlx, y - tly
      new_w = max(self.SPRING_MIN_W, px * rx + py * ry) if op in ("resize", "spring_w") else W
      new_l = (px * ux + py * uy) if op in ("resize", "spring_len") else L
      new_l = max(new_l, self.SPRING_MIN_L, new_w * self.SPRING_TILT * 1.5)
      s2x, s2y = tlx + rx * new_w / 2, tly + ry * new_w / 2
      e2x, e2y = s2x + ux * new_l, s2y + uy * new_l
    self.canvas.coords(self.selected_item, *self.make_spring_pts(s2x, s2y, e2x, e2y, width=new_w))

  def box_transform(self, x, y):
    if self.is_spring(self.selected_item):
      self.spring_transform(x, y)
      self.show_handles()
      return
    p = self.box_pts(self.selected_item)
    if not p:
      return

    if self.box_op == "rotate":
      cx, cy = getattr(self, "rot_center", (0, 0))
      a = math.atan2(y - cy, x - cx)
      d = a - self.box_ang
      self.box_ang = a
      cs, sn = math.cos(d), math.sin(d)
      pts = [(cx + (px - cx) * cs - (py - cy) * sn, cy + (px - cx) * sn + (py - cy) * cs) for px, py in p]
      self.canvas.coords(self.selected_item, *[v for pt in pts for v in pt])
    elif self.box_op == "resize":
      x1, y1 = getattr(self, "resize_anchor", (p[0][0], p[0][1]))
      nx2, ny2 = max(x1 + 10, x), max(y1 + 10, y)
      if self.is_box(self.selected_item):
        self.canvas.coords(self.selected_item, x1, y1, nx2, y1, nx2, ny2, x1, ny2)
      elif self.is_circle(self.selected_item):
        new_coords = self.make_circle_pts(x1, y1, nx2, ny2)
        self.canvas.coords(self.selected_item, *new_coords)
      elif self.is_star(self.selected_item):
        new_coords = self.make_star_pts(x1, y1, nx2, ny2)
        self.canvas.coords(self.selected_item, *new_coords)
    self.show_handles()

  def save_selected_as_component(self):
    item = self.selected_item
    if not item:
      return
    if item in self.image_refs and "pcb" in self.image_refs[item]:
      return self.pcb_export(item)  # PCB maps are exported as full-size PNG, not put in the component list
    base_name = simpledialog.askstring("Save Component", "Enter name for new component:", initialvalue="Custom Design")
    if not base_name:
      return
    base_name = base_name.strip().replace("_", " ")

    name = base_name
    counter = 1
    while name in EMBEDDED_COMPONENTS:
      name = f"{base_name} ({counter})"
      counter += 1

    try:
      if item in self.image_refs:
        ref = self.image_refs[item]
        img = ref["pil_img"].copy()
        crop = ref.get("crop", [0.0, 0.0, 0.0, 0.0])
        if any(crop):
          cw, ch = img.size
          img = img.crop((int(crop[0] * cw), int(crop[1] * ch), int((1 - crop[2]) * cw), int((1 - crop[3]) * ch)))
        kx, ky = ref.get("sx", 1.0), ref.get("sy", 1.0)
        if kx != 1.0 or ky != 1.0:
          img = img.resize((max(1, int(img.width * kx)), max(1, int(img.height * ky))), Image.Resampling.LANCZOS)
        rot = self.item_rotations.get(item, 0)
        if rot != 0:
          img = img.rotate(rot, expand=True, resample=Image.Resampling.BICUBIC)
        
        box = self.canvas.bbox(item)
        if box:
          x0, y0, x1, y1 = box
          d = ImageDraw.Draw(img)
          for txt_item in self.canvas.find_withtag("text_note"):
            tbox = self.canvas.bbox(txt_item)
            if tbox and not (tbox[2] < x0 or tbox[0] > x1 or tbox[3] < y0 or tbox[1] > y1):
              t_coords = self.canvas.coords(txt_item)
              t_str = self.canvas.itemcget(txt_item, "text")
              if t_coords and t_str:
                s = self.item_scales.get(item, 1.0) * ref.get("base", 1.0)
                tx = (t_coords[0] - x0) / (s if s > 0 else 1.0)
                ty = (t_coords[1] - y0) / (s if s > 0 else 1.0)
                d.text((tx, ty), t_str, fill="#e74c3c")

        buf = io.BytesIO()
        if "A" in img.getbands() or img.mode == "P":
          img.convert("RGBA").save(buf, "PNG")
        else:
          img.convert("RGB").save(buf, "JPEG", quality=92)
        data = buf.getvalue()
      else:
        box = self.canvas.bbox(item)
        if hasattr(self, "aa_ids"):
          aid = self.aa_ids.get(item)
          if aid:
            abox = self.canvas.bbox(aid)
            if abox:
              box = (min(box[0], abox[0]) if box else abox[0],
                     min(box[1], abox[1]) if box else abox[1],
                     max(box[2], abox[2]) if box else abox[2],
                     max(box[3], abox[3]) if box else abox[3])
        
        if not box:
          return
          
        x0, y0, x1, y1 = box
        pad = 15
        w_img = max(40, int(x1 - x0 + pad * 2))
        h_img = max(40, int(y1 - y0 + pad * 2))
        
        bg_color = ImageColor.getrgb(self.canvas.cget("bg"))
        img = Image.new("RGBA", (w_img, h_img), bg_color + (255,))
        d = ImageDraw.Draw(img)
        
        ox, oy = x0 - pad, y0 - pad
        coords = self.canvas.coords(item)
        stroke_w = self.stroke_w.get(item, 2.0)
        
        opt = "outline" if self.is_shape(item) and not self.is_spring(item) else ("fill" if self.canvas.type(item) == "line" or self.is_spring(item) else "outline")
        color = self.canvas.itemcget(item, opt)
        if not color:
          color = "#e74c3c"
          
        rel_coords = [(coords[i] - ox, coords[i+1] - oy) for i in range(0, len(coords)-1, 2)]
        if self.canvas.type(item) == "line" or self.is_spring(item) or "wire" in self.canvas.gettags(item):
          if len(rel_coords) >= 2:
            d.line(rel_coords, fill=color, width=max(2, int(stroke_w)), joint="curve")
        elif self.is_shape(item):
          if len(rel_coords) >= 3:
            d.polygon(rel_coords, outline=color, width=max(2, int(stroke_w)))

        for txt_item in self.canvas.find_withtag("text_note"):
          tbox = self.canvas.bbox(txt_item)
          if tbox and not (tbox[2] < x0 or tbox[0] > x1 or tbox[3] < y0 or tbox[1] > y1):
            t_coords = self.canvas.coords(txt_item)
            t_str = self.canvas.itemcget(txt_item, "text")
            if t_coords and t_str:
              tx, ty = t_coords[0] - ox, t_coords[1] - oy
              d.text((tx, ty), t_str, fill=color)
          
        buf = io.BytesIO()
        img.save(buf, "PNG")
        data = buf.getvalue()

      self.thumb_cache[name] = None
      self.temp_hidden_components.discard(name)
      self.save_component_to_module(name, base64.b64encode(data).decode("utf-8"))
      self.build_card(name, data)
      if self.component_cards:
        self.component_cards.insert(0, self.component_cards.pop())
      self.filter_components()
      print(f"Component '{name}' saved and written to data modules successfully!")
    except Exception as e:
      print(f"Error saving component to modules: {e}")

  def handle_action(self, name):
    if name == "delete":
      self.delete_selected()
      return
    if name == "save":
      self.save_selected_as_component()
      return
    if name == "text":
      text = simpledialog.askstring("Add Text Note", "Enter Label:")
      if text:
        self.begin_change()
        x, y = self.canvas.coords(self.selected_item) if self.selected_item else (self.canvas.canvasx(150), self.canvas.canvasy(150))
        self.canvas.create_text(x + 80, y, text=text, anchor="nw", font=("Arial", max(6, int(12 * getattr(self, "current_zoom_scale", 1.0))), "bold"), fill=self.pen_color, tags="text_note")
        self.commit_change()
        self.update_workspace_counter()
      return
    {
        "rotl": lambda: self.rotate_selected(15),
        "rotr": lambda: self.rotate_selected(-15),
        "zoomin": lambda: self.scale_specific_item(self.selected_item, 1.15),
        "zoomout": lambda: self.scale_specific_item(self.selected_item, 0.87),
    }.get(name, lambda: None)()

  def rotate_selected(self, delta):
    item = self.selected_item
    if item in self.image_refs:
      self.item_rotations[item] = (self.item_rotations.get(item, 0) + delta) % 360
      self.render_image(item)

  def rotate_drag(self, x, y):
    item = self.selected_item
    coords = self.canvas.coords(item)
    if len(coords) == 2:
      cx, cy = coords
      a = math.atan2(y - cy, x - cx)
      new = round(self.rot_start - math.degrees(a - self.box_ang)) % 360
      if new != self.item_rotations.get(item, 0):
        self.item_rotations[item] = new
        self.render_image(item)

  def scale_specific_item(self, item, factor):
    if item in self.image_refs:
      self.item_scales[item] = self.item_scales.get(item, 1.0) * factor
      self.render_image(item)

  def stretch_drag(self, dx, dy):
    """Stretches the selected component along one local axis only (right edge = width, bottom edge = height)."""
    item = self.selected_item
    ref = self.image_refs[item]
    a = math.radians(self.item_rotations.get(item, 0))
    s = self.item_scales.get(item, 1.0) * ref.get("base", 1.0)
    w, h = ref["pil_img"].size
    l, t, r, b = ref.get("crop", [0.0, 0.0, 0.0, 0.0])
    if self.box_op == "stretch_h":
      k = ref.get("sx", 1.0)
      cur = w * (1 - l - r) * s * k
      new = max(10.0, cur + dx * math.cos(a) - dy * math.sin(a))
      ref["sx"] = k * new / cur
      m = (new - cur) / 2
      self.canvas.move(item, m * math.cos(a), -m * math.sin(a))
    else:
      k = ref.get("sy", 1.0)
      cur = h * (1 - t - b) * s * k
      new = max(10.0, cur + dx * math.sin(a) + dy * math.cos(a))
      ref["sy"] = k * new / cur
      m = (new - cur) / 2
      self.canvas.move(item, m * math.sin(a), m * math.cos(a))
    self.render_image(item)

  def crop_drag(self, dx, dy):
    item = self.selected_item
    if item not in self.image_refs:
      return
    ref = self.image_refs[item]
    from canvas_graphics_geometry import CORNERS
    sx, sy, _ = CORNERS.get(self.crop_corner, (0, 0, ""))
    a = math.radians(self.item_rotations.get(item, 0))
    lx, ly = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
    s = self.item_scales.get(item, 1.0) * ref.get("base", 1.0)
    sw, sh = s * ref.get("sx", 1.0), s * ref.get("sy", 1.0)
    w, h = ref["pil_img"].size
    old = ref.setdefault("crop", [0.0, 0.0, 0.0, 0.0])
    l, t, r, b = old
    if sx < 0: l += lx / (w * sw)
    elif sx > 0: r -= lx / (w * sw)
    if sy < 0: t += ly / (h * sh)
    elif sy > 0: b -= ly / (h * sh)
    l = max(0, min(l, 0.9 - r)); r = max(0, min(r, 0.9 - l))
    t = max(0, min(t, 0.9 - b)); b = max(0, min(b, 0.9 - t))
    ax = ((l - old[0]) - (r - old[2])) * w * sw / 2
    ay = ((t - old[1]) - (b - old[3])) * h * sh / 2
    self.canvas.move(item, ax * math.cos(a) + ay * math.sin(a), -ax * math.sin(a) + ay * math.cos(a))
    ref["crop"] = [l, t, r, b]
    self.render_image(item)
