import os
import io
import json
import base64
from PIL import Image
import customtkinter as ctk
from components_data import EMBEDDED_COMPONENTS

PROJECTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saved_projects")
AUTOSAVE_IDLE_MS = 3000  # the workspace is saved once the user has stopped working for this long


class HistoryManagerMixin:

  def get_history_state(self):
    """Captures a complete snapshot of the workspace for undo/redo."""
    state = {"components": [], "strokes": [], "texts": [], "_xf": tuple(getattr(self, "_zoom_xf", (1.0, 0.0, 0.0)))}

    for item_id, ref in self.image_refs.items():
      coords = self.canvas.coords(item_id)
      if not coords:
        continue
      state["components"].append({
          "path": ref["path"],
          "pil_img": ref.get("pil_img"),
          "orig_pil": ref.get("orig_pil"),
          "base": ref.get("base", 1.0),
          "crop": list(ref.get("crop", [0.0, 0.0, 0.0, 0.0])),
          "sx": ref.get("sx", 1.0), "sy": ref.get("sy", 1.0),
          "pcb": ref.get("pcb"),
          "x": coords[0],
          "y": coords[1],
          "scale": self.item_scales.get(item_id, 1.0),
          "rotation": self.item_rotations.get(item_id, 0)
      })

    for item_id in self.stroke_items():
      if not self.canvas.type(item_id):
        continue
      coords = self.canvas.coords(item_id)
      tags = self.canvas.gettags(item_id)
      stype = self.canvas.type(item_id)

      if "box_shape" in tags or "circle_shape" in tags or "star_shape" in tags:
        outline = self.canvas.itemcget(item_id, "outline")
        fill = self.canvas.itemcget(item_id, "fill")
      else:
        outline = self.canvas.itemcget(item_id, "fill")
        fill = ""

      state["strokes"].append({
          "type": stype,
          "coords": list(coords),
          "tags": list(tags),
          "width": self.stroke_w.get(item_id, 2.0),
          "outline": outline,
          "fill": fill
      })

    for item_id in self.canvas.find_withtag("text_note"):
      coords = self.canvas.coords(item_id)
      text = self.canvas.itemcget(item_id, "text")
      fill = self.canvas.itemcget(item_id, "fill")
      if coords and text:
        state["texts"].append({"x": coords[0], "y": coords[1], "text": text, "fill": fill,
                               "size": self.text_size_of(item_id), "hl": self.text_hl_of(item_id)})

    if hasattr(self, "get_inserted_pictures_snapshot"):
      state["pictures"] = self.get_inserted_pictures_snapshot()
    if hasattr(self, "get_tables_snapshot"):
      state["tables"] = self.get_tables_snapshot()

    return state

  def restore_history_state(self, state):
    """Restores the workspace to a historical snapshot."""
    state = self._apply_zoom_xf(state)
    self.canvas.delete("component", "wire", "box_shape", "circle_shape", "star_shape",
                       "spring_shape", "cable_shape", "text_note", "aa", "handle", "inserted_picture", "table_item", "table_ui", "text_hl")
    self.image_refs.clear()
    self.item_scales.clear()
    self.item_rotations.clear()
    self.stroke_w.clear()
    self.aa_ids.clear()
    self.aa_tk.clear()
    self.live.clear()
    self.selected_item = None
    self.text_meta_reset()
    if hasattr(self, "restore_inserted_pictures_snapshot"):
      self.restore_inserted_pictures_snapshot(state.get("pictures", []))

    for comp in state.get("components", []):
      path = comp["path"]
      pil_img = comp.get("pil_img")
      orig_pil = comp.get("orig_pil")

      if pil_img is None and path in EMBEDDED_COMPONENTS:
        try:
          raw = base64.b64decode(EMBEDDED_COMPONENTS[path].encode("utf-8"))
          pil_img = Image.open(io.BytesIO(raw)).convert("RGBA")
          orig_pil = pil_img.copy()
        except Exception:
          pass

      if pil_img is None:
        pil_img = Image.new("RGBA", (100, 100), (50, 50, 50, 255))
        orig_pil = pil_img.copy()

      item_id = self.canvas.create_image(comp["x"], comp["y"], anchor="center", tags="component")
      self.image_refs[item_id] = {
          "path": path,
          "pil_img": pil_img,
          "orig_pil": orig_pil,
          "base": comp.get("base", min(1.0, 190 / pil_img.width)),
          "crop": list(comp.get("crop", [0.0, 0.0, 0.0, 0.0])),
          "sx": comp.get("sx", 1.0), "sy": comp.get("sy", 1.0)
      }
      if comp.get("pcb"):
        self.image_refs[item_id]["pcb"] = comp["pcb"]
      self.item_scales[item_id] = comp["scale"]
      self.item_rotations[item_id] = comp["rotation"]
      self.render_image(item_id)

    for stroke in state.get("strokes", []):
      stype = stroke["type"]
      coords = stroke["coords"]
      tags = tuple(stroke["tags"])
      width = stroke["width"]
      outline = stroke.get("outline", "#e74c3c")
      fill = stroke.get("fill", "")
      is_wire = "wire" in tags  # only pen wires are smoothed

      if stype == "line":
        kw = dict(smooth=True, splinesteps=64) if is_wire else {}
        item_id = self.canvas.create_line(*coords, fill=outline, width=width, capstyle="round",
                                          joinstyle="round", tags=tags, **kw)
      elif stype == "polygon":
        item_id = self.canvas.create_polygon(*coords, outline=outline, fill=fill, width=width, tags=tags)
      elif stype == "oval":
        item_id = self.canvas.create_oval(*coords, outline=outline, fill=fill, width=width, tags=tags)
      else:
        continue

      self.stroke_w[item_id] = width

    for t in state.get("texts", []):
      self.text_create(t["x"], t["y"], t["text"], t.get("fill", t.get("color", "white")),
                       t.get("size"), t.get("hl"))

    if hasattr(self, "restore_tables_snapshot"):
      self.restore_tables_snapshot(state.get("tables", []))

    self.show_handles()
    self.update_workspace_counter()
    self.schedule_aa_all()

  def _apply_zoom_xf(self, state):
    """Snapshots are stored at the zoom they were taken at. Instead of rescaling the whole undo history on every zoom
    (slow), the snapshot being restored is converted to the current zoom here, one at a time."""
    a1, bx1, by1 = state.get("_xf", (1.0, 0.0, 0.0))
    a2, bx2, by2 = getattr(self, "_zoom_xf", (1.0, 0.0, 0.0))
    if not a1:
      return state
    f = a2 / a1
    dx, dy = bx2 - f * bx1, by2 - f * by1
    if abs(f - 1.0) < 1e-9 and abs(dx) < 1e-6 and abs(dy) < 1e-6:
      return state
    for comp in state.get("components", []):
      comp["x"], comp["y"] = f * comp["x"] + dx, f * comp["y"] + dy
      comp["scale"] *= f
    for st in state.get("strokes", []):
      st["coords"] = [f * v + (dx if i % 2 == 0 else dy) for i, v in enumerate(st["coords"])]
      st["width"] = st.get("width", 2.0) * f
    for t in state.get("texts", []):
      t["x"], t["y"] = f * t["x"] + dx, f * t["y"] + dy
    for p in state.get("pictures", []):
      p["x"], p["y"] = f * p["x"] + dx, f * p["y"] + dy
    for tb in state.get("tables", []):
      tb["ox"], tb["oy"] = f * tb["ox"] + dx, f * tb["oy"] + dy  # table sizes are zoom-independent, only the position moves
    state["_xf"] = (a2, bx2, by2)
    return state

  def begin_change(self):
    """Snapshots the current state before a modification begins."""
    self.pre_state = self.get_history_state()

  def _schedule_autosave(self):
    """Marks the workspace as changed and (re)starts the idle timer: the save happens when the user stops working."""
    self._dirty = True
    job = getattr(self, "_autosave_job", None)
    if job:
      self.after_cancel(job)
    self._autosave_job = self.after(AUTOSAVE_IDLE_MS, self._autosave)

  def flush_autosave(self):
    """Saves immediately if anything changed (called on quit, minimize, new/open/clear project)."""
    try:
      self.finish_text_edit()  # commits a text that is still being typed
    except Exception:
      pass
    job = getattr(self, "_autosave_job", None)
    if job:
      try:
        self.after_cancel(job)
      except Exception:
        pass
      self._autosave_job = None
    if getattr(self, "_dirty", False):
      try:
        self.autosave_project()
      except Exception as e:
        print(f"Autosave error: {e}")

  def _autosave(self):
    self._autosave_job = None
    try:
      self.autosave_project()
    except Exception as e:
      print(f"Autosave error: {e}")

  def commit_change(self):
    """Pushes pre-state to undo stack, clears redo, debounces autosave, flashes 'Updated'."""
    if getattr(self, "pre_state", None) is None:
      return
    self.undo_stack.append(self.pre_state)
    self.redo_stack.clear()
    self.pre_state = None

    self._schedule_autosave()

    try:
      if not hasattr(self, "_updated_lbl") or not self._updated_lbl.winfo_exists():
        self._updated_lbl = ctk.CTkLabel(
            self, text="Updated", font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#27ae60", fg_color="transparent"
        )
      self._updated_lbl.place(relx=1, rely=0, anchor="ne")
      self._updated_lbl.lift()
      if getattr(self, "_update_job", None):
        self.after_cancel(self._update_job)
      self._update_job = self.after(1000, lambda: self._updated_lbl.place_forget())
    except Exception as e:
      print(f"Updated label error: {e}")

  def perform_undo(self):
    if hasattr(self, "table_finish_edit"):
      self.table_finish_edit()  # text typed in a table cell becomes part of the history first
    if not self.undo_stack:
      return False
    self.redo_stack.append(self.get_history_state())
    self.restore_history_state(self.undo_stack.pop())
    self._schedule_autosave()
    return True

  def perform_redo(self):
    if hasattr(self, "table_finish_edit"):
      self.table_finish_edit()
    if not self.redo_stack:
      return False
    self.undo_stack.append(self.get_history_state())
    self.restore_history_state(self.redo_stack.pop())
    self._schedule_autosave()
    return True
