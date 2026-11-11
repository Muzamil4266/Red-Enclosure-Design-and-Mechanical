import math

class CanvasMouseUtilsMixin:

  def pick_item(self, tol=5):
    x, y = self.last_x, self.last_y
    # Shapes (their real items are hidden after antialiasing, so test geometry directly)
    for i in reversed(self.stroke_items()):
      if not self.is_shape(i):
        continue
      if self.is_spring(i):
        prm = self.spring_params(i)
        if prm:
          (sx, sy), _, W, (ux, uy), (rx, ry), L = prm
          a, b = (x - sx) * ux + (y - sy) * uy, (x - sx) * rx + (y - sy) * ry
          if -tol <= a <= L + tol and abs(b) <= W / 2 + tol:
            return i
        continue
      pts = self.stroke_points(i)
      lim = tol + self.stroke_w.get(i, 2.0) / 2
      for k in range(len(pts) - 1):
        x1, y1 = pts[k]
        x2, y2 = pts[k + 1]
        dx, dy = x2 - x1, y2 - y1
        n = dx * dx + dy * dy
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / n)) if n else 0.0
        if math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)) <= lim:
          return i
    for i in map(self.pcb_owner, self.canvas.find_withtag("current")):
      if i in self.image_refs:
        return i
    for i in map(self.pcb_owner, reversed(self.canvas.find_overlapping(x - tol, y - tol, x + tol, y + tol))):
      if i in self.image_refs:
        return i
    for i in reversed(self.stroke_items()):  # click inside a box/circle/star
      if self.is_shape(i) and not self.is_spring(i):
        p, inside, j = self.box_pts(i), False, -1
        for k in range(len(p)):
          (x1, y1), (x2, y2) = p[k], p[j]
          if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
          j = k
        if inside:
          return i
    return self.pick_picture(x, y, tol)  # pictures sit behind everything, so they are tested last

  def too_small(self, item, min_size=6):
    pts = self.box_pts(item)
    if not pts:
      return True
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return max(xs) - min(xs) < min_size or max(ys) - min(ys) < min_size

  def new_pen_line(self, x1, y1, x2, y2):
    item = self.canvas.create_line(x1, y1, x2, y2, fill=self.pen_color, width=self.tool_size, capstyle="round",
                                   joinstyle="round", smooth=True, splinesteps=64, tags="wire")
    self.stroke_w[item] = self.tool_size
    self.live.add(item)
    return item

  def new_cable_line(self, x1, y1, x2, y2):
    w = max(6, self.tool_size * 2 + 2) * self.current_zoom_scale
    item = self.canvas.create_line(x1, y1, x2, y2, fill=self.pen_color, width=w, capstyle="butt", tags="cable_shape")
    self.stroke_w[item] = w
    self.live.add(item)
    return item

  def _flush_dot(self):
    if self._dot_job:
      self.after_cancel(self._dot_job)
      self._dot_job = None
    if self._dot_item is not None:
      self._dot_item = None
      self.commit_change()

  HOLD_DELAY = 450
  REPEAT_MS = 50

  def _left_button_down(self):
    try:
      import ctypes
      user32 = ctypes.windll.user32
      vk = 0x02 if user32.GetSystemMetrics(23) else 0x01
      return bool(user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
      return True

  def start_history(self, kind, event=None):
    self.stop_history()
    self._hist_kind = kind
    self._hist_down = True
    self._hist_step()
    if self._hist_down:
      self._hist_job = self.after(self.HOLD_DELAY, self._hist_repeat)

  def _hist_step(self):
    self._flush_dot()
    self.finish_text_edit()
    done = self.perform_undo() if self._hist_kind == "undo" else self.perform_redo()
    if hasattr(self, "update_workspace_counter"):
      self.update_workspace_counter()
    return done

  def _hist_repeat(self):
    self._hist_job = None
    if not self._hist_down or not self._left_button_down():
      self.stop_history()
      return
    self._skip_aa = True
    try:
      done = self._hist_step()
    except Exception as e:
      print(f"History repeat error: {e}")
      done = False
    if done and self._hist_down:
      self._hist_job = self.after(self.REPEAT_MS, self._hist_repeat)
    else:
      self.stop_history()

  def stop_history(self, event=None):
    if self._hist_job:
      self.after_cancel(self._hist_job)
      self._hist_job = None
    self._hist_down = False
    if self._skip_aa:
      self._skip_aa = False
      for item in list(self.image_refs):
        self.render_image(item)
      self.aa_all()
      self.show_handles()

  def remove_item(self, item):
    self.canvas.delete(item)
    aid = self.aa_ids.pop(item, None)
    if aid:
      self.canvas.delete(aid)
    self.aa_tk.pop(item, None)
    self.stroke_w.pop(item, None)
    self.cable_flags.pop(item, None)
    self.live.discard(item)
    self.image_refs.pop(item, None)
    self.item_scales.pop(item, None)
    self.item_rotations.pop(item, None)
    if hasattr(self, "_picture_items"):
      self._picture_items.pop(item, None)
    if item == self.selected_item:
      self.selected_item = None
    if hasattr(self, "update_workspace_counter"):
      self.update_workspace_counter()

  def erase_at(self, x, y):
    r = self.eraser_size
    hit_stroke = False

    # 1. Strokes first (pen, line, wire, shapes): point-to-segment distance
    for item in self.stroke_items():
      try:
        pts = self.stroke_points(item)
      except Exception:
        pts = self.box_pts(item)
      if len(pts) < 2:
        continue
      reach = r + self.stroke_w.get(item, 2.0) / 2
      for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        dx, dy = x2 - x1, y2 - y1
        n = dx * dx + dy * dy
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / n)) if n else 0.0
        if math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)) <= reach:
          self.remove_item(item)
          hit_stroke = True
          break

    # 2. Components and texts only when no drawing was hit, so erasing a wire never removes the component under it
    if hit_stroke:
      return
    for item in self.canvas.find_overlapping(x - r, y - r, x + r, y + r):
      tags = self.canvas.gettags(item)
      if "bg_grid" in tags or "handle" in tags or "hover_name" in tags or "aa" in tags:
        continue
      if (item in self.image_refs and "pcb" not in self.image_refs[item]) or "text_note" in tags:  # eraser never wipes a PCB map
        self.remove_item(item)

  def delete_selected(self):
    if self.selected_item:
      self.begin_change()
      self.remove_item(self.selected_item)
      self.show_handles()
      self.commit_change()
