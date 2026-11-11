import math

CORNERS = {"cropBL": (-1, 1, "↙"), "cropBR": (1, 1, "↘"), "cropL": (-1, 0, "↔"), "cropR": (1, 0, "↔"),
           "cropT": (0, -1, "↕"), "cropB": (0, 1, "↕")}

class CanvasGraphicsGeometryMixin:

  SPRING_HALF_TURNS = 15
  SPRING_STEPS = 12
  SPRING_TILT = 0.28
  SPRING_MIN_W = 24
  SPRING_MIN_L = 30

  def corner_point(self, item, sx, sy):
    ref = self.image_refs[item]
    s = self.item_scales[item] * ref.get("base", 1.0)
    w, h = ref["pil_img"].size
    l, t, r, b = ref.get("crop", [0.0, 0.0, 0.0, 0.0])
    kx, ky = ref.get("sx", 1.0), ref.get("sy", 1.0)
    lx, ly = sx * w * (1 - l - r) * s * kx / 2, sy * h * (1 - t - b) * s * ky / 2
    a = math.radians(self.item_rotations[item])
    cx, cy = self.canvas.coords(item)
    return cx + lx * math.cos(a) + ly * math.sin(a), cy - lx * math.sin(a) + ly * math.cos(a)

  def is_box(self, item):
    return bool(item) and "box_shape" in self.canvas.gettags(item)

  def is_circle(self, item):
    return bool(item) and "circle_shape" in self.canvas.gettags(item)

  def is_star(self, item):
    return bool(item) and "star_shape" in self.canvas.gettags(item)

  def is_spring(self, item):
    return bool(item) and "spring_shape" in self.canvas.gettags(item)

  def is_shape(self, item):
    return self.is_box(item) or self.is_circle(item) or self.is_star(item) or self.is_spring(item)

  def stroke_items(self):
    return [i for t in ("wire", "box_shape", "circle_shape", "star_shape", "spring_shape", "cable_shape") for i in self.canvas.find_withtag(t)]

  def cable_ends(self, item):
    """(start_is_bare, end_is_bare): an end is insulated when it touches another cable."""
    c = self.canvas.coords(item)
    if len(c) < 4:
      return (True, True)
    w = self.stroke_w.get(item, 8.0)
    out = []
    for ex, ey in ((c[0], c[1]), (c[-2], c[-1])):
      touch = False
      for o in self.canvas.find_withtag("cable_shape"):
        co = self.canvas.coords(o)
        if o == item or len(co) < 4:
          continue
        dx, dy = co[-2] - co[0], co[-1] - co[1]
        n = dx * dx + dy * dy
        t = max(0.0, min(1.0, ((ex - co[0]) * dx + (ey - co[1]) * dy) / n)) if n else 0.0
        if math.hypot(ex - (co[0] + t * dx), ey - (co[1] + t * dy)) <= (w + self.stroke_w.get(o, w)) / 2 + 1:
          touch = True
          break
      out.append(not touch)
    return tuple(out)

  def box_pts(self, item):
    c = self.canvas.coords(item) if item else []
    return [(c[i], c[i + 1]) for i in range(0, len(c), 2)] if len(c) >= 6 else []

  def make_circle_pts(self, x1, y1, x2, y2, steps=36):
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    rx, ry = abs(x2 - x1) / 2, abs(y2 - y1) / 2
    pts = []
    for i in range(steps):
      angle = i * 2 * math.pi / steps
      pts.extend([cx + rx * math.cos(angle), cy + ry * math.sin(angle)])
    return pts

  def make_star_pts(self, x1, y1, x2, y2, points=5):
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    outer_r = min(abs(x2 - x1), abs(y2 - y1)) / 2
    inner_r = outer_r * 0.4
    pts = []
    for i in range(2 * points):
      r = outer_r if i % 2 == 0 else inner_r
      angle = i * math.pi / points - math.pi / 2
      pts.extend([cx + r * math.cos(angle), cy + r * math.sin(angle)])
    return pts

  def make_spring_pts(self, x1, y1, x2, y2, width=None):
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy)
    if L < 1.0:
      dx, dy, L = 0.0, 1.0, 1.0
    ux, uy = dx / L, dy / L
    rx, ry = uy, -ux
    R = (width if width is not None else max(12.0, L * 0.35)) / 2
    k = R * self.SPRING_TILT
    n = self.SPRING_HALF_TURNS * self.SPRING_STEPS
    theta_end = self.SPRING_HALF_TURNS * math.pi
    pts = []
    for i in range(n + 1):
      th = theta_end * i / n
      v = k + (L - 2 * k) * i / n - k * math.cos(th)
      h = R * math.sin(th)
      pts.extend([x1 + ux * v + rx * h, y1 + uy * v + ry * h])
    return pts

  def spring_params(self, item):
    p = self.box_pts(item)
    if not p:
      return None
    (sx, sy), (ex, ey) = p[0], p[-1]
    L = math.hypot(ex - sx, ey - sy) or 1.0
    ux, uy = (ex - sx) / L, (ey - sy) / L
    rx, ry = uy, -ux
    W = 2 * max(abs((x - sx) * rx + (y - sy) * ry) for x, y in p)
    return (sx, sy), (ex, ey), W, (ux, uy), (rx, ry), L

  def stroke_points(self, item):
    c = self.canvas.coords(item)
    pts = [(c[i], c[i + 1]) for i in range(0, len(c) - 1, 2)]
    if self.is_spring(item):
      return pts
    if self.is_shape(item):
      return pts + pts[:1]
    if len(pts) < 3:
      return pts
    mid = lambda a, b: ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    n = 8 if len(pts) < 300 else 3
    out = [pts[0], mid(pts[0], pts[1])]
    for i in range(1, len(pts) - 1):
      a, p, e = mid(pts[i - 1], pts[i]), pts[i], mid(pts[i], pts[i + 1])
      for k in range(1, n + 1):
        t = k / n
        out.append(((1 - t) ** 2 * a[0] + 2 * t * (1 - t) * p[0] + t * t * e[0],
                    (1 - t) ** 2 * a[1] + 2 * t * (1 - t) * p[1] + t * t * e[1]))
    out.append(pts[-1])
    return out
