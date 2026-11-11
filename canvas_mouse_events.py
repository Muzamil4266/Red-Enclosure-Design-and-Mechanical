"""
Canvas Mouse Events Mixin for Red Circuit.
Handles mouse clicks, drags, releases, shape creation, and mode-based selection logic.
Fixes lag and detached handles when dragging box, circle, star, or line shapes.
"""
import math

class CanvasMouseEventsMixin:

  def clear_hover(self, event=None):
    self.canvas.delete("hover_name")
    self._hover_item = None

  def on_canvas_hover(self, event):
    if hasattr(self, "table_hover"):
      self.table_hover(event)
    if hasattr(self, "text_hover"):
      self.text_hover(event)  # I-beam over written text
    x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
    hit = None
    for i in reversed(self.canvas.find_overlapping(x, y, x, y)):
      if i in self.image_refs:
        hit = i
        break
    if hit == self._hover_item:
      return
    self.canvas.delete("hover_name")
    self._hover_item = hit
    if hit is None or hit == self.selected_item:
      return
    x1, y1, x2, y2 = self.canvas.bbox(hit)
    name = self.image_refs[hit].get("path", "Component")
    t = self.canvas.create_text((x1 + x2) / 2, y2 + 10, text=name, anchor="n", font=("Arial", 11, "bold"),
                                fill="#f1c40f", state="disabled", tags="hover_name")
    bx1, by1, bx2, by2 = self.canvas.bbox(t)
    r = self.canvas.create_rectangle(bx1 - 6, by1 - 3, bx2 + 6, by2 + 3, fill="#1b2631", outline="#f1c40f",
                                     state="disabled", tags="hover_name")
    self.canvas.tag_raise(t, r)

  def on_canvas_press(self, event):
    self._flush_dot()
    self.clear_hover()
    self._stroke_start = None
    if hasattr(self, "text_press") and self.text_press(event):
      return  # the click is on a written text: caret placed, drag selection started
    self.finish_text_edit()
    self.last_x = self.canvas.canvasx(event.x)
    self.last_y = self.canvas.canvasy(event.y)
    self.press_x = self.last_x
    self.press_y = self.last_y
    self.dragging = False
    self.box_op = None
    self.crop_corner = None

    if self.mode == "select" and hasattr(self, "table_press") and "handle" not in self.canvas.gettags("current") \
        and self.table_press(self.last_x, self.last_y):
      return  # the click belonged to a table (cell, border or table handle)
    if hasattr(self, "table_deselect"):
      self.table_deselect()

    tags = self.canvas.gettags("current")

    # Handle interactions (rotate/resize/delete handles on selected objects)
    if "handle" in tags:
      self.begin_change()
      if "box_delete" in tags or "delete" in tags:
        self.delete_selected()
        return
      elif "save" in tags:
        self.save_selected_as_component()
        return
      elif "spring_w" in tags or "spring_len" in tags:
        self.aa_live(self.selected_item)
        self.spring_begin()
        self.box_op = "spring_w" if "spring_w" in tags else "spring_len"
        return
      elif "stretchH" in tags or "stretchV" in tags:
        self.box_op = "stretch_h" if "stretchH" in tags else "stretch_v"
        return
      elif "img_rotate" in tags:
        coords = self.canvas.coords(self.selected_item)
        if len(coords) == 2:
          cx, cy = coords
          self.box_ang = math.atan2(self.last_y - cy, self.last_x - cx)
          self.rot_start = self.picture_rotation(self.selected_item) if self.is_picture(self.selected_item) else self.item_rotations.get(self.selected_item, 0)
          self.box_op = "img_rotate"
        return
      elif "box_rotate" in tags:
        self.aa_live(self.selected_item)
        if self.is_spring(self.selected_item):
          self.spring_begin()
          self.box_op = "rotate"
          return
        pts = self.box_pts(self.selected_item)
        if pts:
          if self.is_spring(self.selected_item):
            cx, cy = (pts[0][0] + pts[-1][0]) / 2, (pts[0][1] + pts[-1][1]) / 2
          else:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
          self.rot_center = (cx, cy)
          self.box_ang = math.atan2(self.last_y - cy, self.last_x - cx)
          self.box_op = "rotate"
        return
      elif "box_resize" in tags:
        self.aa_live(self.selected_item)
        if self.is_spring(self.selected_item):
          self.spring_begin()
          self.box_op = "resize"
          return
        pts = self.box_pts(self.selected_item)
        if pts:
          self.resize_anchor = pts[0] if self.is_spring(self.selected_item) else (min([p[0] for p in pts]), min([p[1] for p in pts]))
        self.box_op = "resize"
        return
      else:
        from canvas_graphics_geometry import CORNERS
        for tag in tags:
          if tag in CORNERS:
            self.crop_corner = tag
            return
        if len(tags) > 1:
          self.handle_action(tags[1])
        return

    # --- MODE BASED EXECUTION ---
    if self.mode == "select":
      top_item = self.pick_item()
      if top_item and (top_item in self.image_refs or self.is_shape(top_item) or self.is_picture(top_item)):
        self.selected_item = top_item
        self.dragging = True
        self.begin_change()
        if self.is_shape(top_item):
          self.aa_live(top_item)
      else:
        self.selected_item = None
      self.show_handles()

    elif self.mode in ("draw", "straight_line", "cable"):
      self.selected_item = None
      self.show_handles()
      self.begin_change()
      self._stroke_start = (self.last_x, self.last_y)

    elif self.mode == "box":
      self.selected_item = None
      self.show_handles()
      self.begin_change()
      x, y = self.last_x, self.last_y
      self.temp_box_id = self.canvas.create_polygon(
          x, y, x, y, x, y, x, y, outline=self.pen_color, fill="", width=self.tool_size, tags="box_shape"
      )
      self.stroke_w[self.temp_box_id] = self.tool_size
      self.live.add(self.temp_box_id)

    elif self.mode == "circle":
      self.selected_item = None
      self.show_handles()
      self.begin_change()
      x, y = self.last_x, self.last_y
      pts = self.make_circle_pts(x, y, x, y)
      self.temp_circle_id = self.canvas.create_polygon(
          *pts, outline=self.pen_color, fill="", width=self.tool_size, tags="circle_shape"
      )
      self.stroke_w[self.temp_circle_id] = self.tool_size
      self.live.add(self.temp_circle_id)

    elif self.mode == "star":
      self.selected_item = None
      self.show_handles()
      self.begin_change()
      x, y = self.last_x, self.last_y
      pts = self.make_star_pts(x, y, x, y)
      self.temp_star_id = self.canvas.create_polygon(
          *pts, outline=self.pen_color, fill="", width=self.tool_size, tags="star_shape"
      )
      self.stroke_w[self.temp_star_id] = self.tool_size
      self.live.add(self.temp_star_id)

    elif self.mode == "spring":
      self.selected_item = None
      self.show_handles()
      self.begin_change()
      x, y = self.last_x, self.last_y
      self.temp_spring_id = self.canvas.create_line(
          *self.make_spring_pts(x, y, x, y + 1, width=4), fill=self.pen_color, width=self.tool_size,
          capstyle="round", joinstyle="round", tags="spring_shape"
      )
      self.stroke_w[self.temp_spring_id] = self.tool_size
      self.live.add(self.temp_spring_id)

    elif self.mode == "eraser":
      self.begin_change()
      self.erase_at(self.last_x, self.last_y)
    
    if hasattr(self, "update_workspace_counter"):
      self.update_workspace_counter()

  def on_canvas_drag(self, event):
    x = self.canvas.canvasx(event.x)
    y = self.canvas.canvasy(event.y)

    if getattr(self, "_text_sel_active", False):
      self.text_drag(x, y)
      self.last_x, self.last_y = x, y
      return

    if getattr(self, "_table_op", None) is not None:
      self.table_drag(x, y)
      self.last_x, self.last_y = x, y
      return

    if self.is_picture(self.selected_item) and self.picture_handle_drag(x, y):
      self.last_x, self.last_y = x, y
      return

    if self.box_op == "img_rotate" and self.selected_item in self.image_refs:
      self.rotate_drag(x, y)
    elif self.box_op in ("stretch_h", "stretch_v") and self.selected_item in self.image_refs:
      self.stretch_drag(x - self.last_x, y - self.last_y)
    elif self.box_op and self.is_shape(self.selected_item):
      self.box_transform(x, y)
    elif self.crop_corner:
      self.crop_drag(x - self.last_x, y - self.last_y)
    elif self.mode in ("draw", "straight_line", "cable") and self._stroke_start is not None and not self.temp_line_id:
      sx, sy = self._stroke_start
      if math.hypot(x - sx, y - sy) >= 2:
        self.temp_line_id = (self.new_cable_line if self.mode == "cable" else self.new_pen_line)(sx, sy, x, y)
    elif self.mode == "draw" and self.temp_line_id:
      coords = list(self.canvas.coords(self.temp_line_id))
      coords.extend([x, y])
      self.canvas.coords(self.temp_line_id, *coords)
    elif self.mode in ("straight_line", "cable") and self.temp_line_id:
      c = self.canvas.coords(self.temp_line_id)
      self.canvas.coords(self.temp_line_id, c[0], c[1], x, y)
    elif self.mode == "box" and self.temp_box_id:
      l, r = sorted((self.press_x, x))
      t, b = sorted((self.press_y, y))
      self.canvas.coords(self.temp_box_id, l, t, r, t, r, b, l, b)
    elif self.mode == "circle" and self.temp_circle_id:
      pts = self.make_circle_pts(self.press_x, self.press_y, x, y)
      self.canvas.coords(self.temp_circle_id, *pts)
    elif self.mode == "star" and self.temp_star_id:
      pts = self.make_star_pts(self.press_x, self.press_y, x, y)
      self.canvas.coords(self.temp_star_id, *pts)
    elif self.mode == "spring" and self.temp_spring_id:
      l, r = sorted((self.press_x, x))
      t, b = sorted((self.press_y, y))
      mx = (l + r) / 2
      self.canvas.coords(self.temp_spring_id, *self.make_spring_pts(mx, t, mx, max(b, t + 1), width=max(r - l, 4)))
    elif self.mode == "eraser":
      self.erase_at(x, y)
    elif self.dragging and self.selected_item:
      dx, dy = x - self.last_x, y - self.last_y
      self.canvas.move(self.selected_item, dx, dy)
      aid = getattr(self, "aa_ids", {}).get(self.selected_item)
      if aid:
        self.canvas.move(aid, dx, dy)
      self.show_handles()
    self.last_x, self.last_y = x, y

  def on_canvas_release(self, event):
    if getattr(self, "_text_sel_active", False):
      self.text_release()
      return
    if getattr(self, "_table_op", None) is not None:
      self.table_release(event)
      return
    dot = None
    if self._stroke_start is not None and self.temp_line_id is None and self.mode != "cable":
      sx, sy = self._stroke_start
      dot = self.temp_line_id = self.new_pen_line(sx, sy, sx, sy)
    self._stroke_start = None

    if self.temp_box_id:
      pts = self.box_pts(self.temp_box_id)
      if abs(pts[2][0] - pts[0][0]) < 6 or abs(pts[2][1] - pts[0][1]) < 6:
        self.remove_item(self.temp_box_id)
        self.temp_box_id = None
      else:
        self.selected_item = None
    elif self.temp_circle_id:
      if self.too_small(self.temp_circle_id):
        self.remove_item(self.temp_circle_id)
      else:
        self.selected_item = None
    elif self.temp_star_id:
      if self.too_small(self.temp_star_id):
        self.remove_item(self.temp_star_id)
      else:
        self.selected_item = None
    elif self.temp_spring_id:
      prm = self.spring_params(self.temp_spring_id)
      if not prm or prm[2] < 14 or prm[5] < 30:
        self.remove_item(self.temp_spring_id)
      else:
        self.selected_item = None

    if self.temp_line_id and self.mode == "cable":
      cc = self.canvas.coords(self.temp_line_id)
      if len(cc) >= 4 and math.hypot(cc[-2] - cc[0], cc[-1] - cc[1]) < 10:
        self.remove_item(self.temp_line_id)
    self.temp_line_id = None
    self.temp_box_id = None
    self.temp_circle_id = None
    self.temp_star_id = None
    self.temp_spring_id = None
    self.crop_corner = None
    self.box_op = None
    self.aa_finish()
    self.refresh_cables()
    if dot is not None:
      self._dot_item = dot
      self._dot_job = self.after(520, self._flush_dot)
    else:
      self.commit_change()
    self.show_handles()
    if hasattr(self, "update_workspace_counter"):
      self.update_workspace_counter()
