import json
import base64
import customtkinter as ctk
from tkinter import filedialog
from components_data import EMBEDDED_COMPONENTS
from serialization_projects import APP_TITLE

class SerializationCoreMixin:

  def get_state(self):
    st = {"components": [], "wires": [], "boxes": [], "circles": [], "stars": [], "springs": [], "cables": [], "texts": []}
    
    # 1. Capture Components
    for i, ref in self.image_refs.items():
      c = self.canvas.coords(i)
      if c:
        st["components"].append({
            "id": i, "path": ref["path"], "x": c[0], "y": c[1],
            "scale": self.item_scales.get(i, 1.0), "rotation": self.item_rotations.get(i, 0),
            "crop": ref.get("crop", [0.0, 0.0, 0.0, 0.0]),
            "sx": ref.get("sx", 1.0), "sy": ref.get("sy", 1.0),
            "pcb": {"file": ref["pcb"]["file"], "enh": ref["pcb"]["enh"]} if "pcb" in ref else None
        })

    # 2. Capture Shapes & Wires
    tag_map = {
        "wire": "wires",
        "box_shape": "boxes",
        "circle_shape": "circles",
        "star_shape": "stars",
        "spring_shape": "springs",
        "cable_shape": "cables"
    }

    for tag, key in tag_map.items():
      for i in self.canvas.find_withtag(tag):
        c = self.canvas.coords(i)
        if c:
          opt = "outline" if self.is_shape(i) and not self.is_spring(i) else "fill"
          st[key].append({"coords": c, "color": self.canvas.itemcget(i, opt), "width": self.stroke_w.get(i, 2.0)})

    # 3. Capture Text Notes
    for i in self.canvas.find_withtag("text_note"):
      c = self.canvas.coords(i)
      txt = self.canvas.itemcget(i, "text")
      if c and txt:
        st["texts"].append({"x": c[0], "y": c[1], "text": txt, "color": self.canvas.itemcget(i, "fill"),
                            "size": self.text_size_of(i), "hl": self.text_hl_of(i)})

    if hasattr(self, "get_inserted_pictures_state"):
      st["pictures"] = self.get_inserted_pictures_state()
    if hasattr(self, "get_tables_state"):
      st["tables"] = self.get_tables_state()

    st["zoom"] = getattr(self, "current_zoom_scale", 1.0)
    st["bg"] = self.canvas.cget("bg")
    st["bg_type"] = getattr(self, "current_bg_type", "solid")
    return st

  def set_state(self, s):
    self.canvas.delete("component", "wire", "box_shape", "circle_shape", "star_shape", "spring_shape", "cable_shape", "text_note", "aa", "handle", "inserted_picture", "table_item", "table_ui", "text_hl")
    self.image_refs.clear()
    self.item_scales.clear()
    self.item_rotations.clear()
    self.aa_ids.clear()
    self.aa_tk.clear()
    self.stroke_w.clear()
    if hasattr(self, "live"):
      self.live.clear()

    if s.get("bg"):
      self.canvas.configure(bg=s["bg"])
      self.canvas.delete("bg_grid")
      self.current_bg_type = s.get("bg_type", "solid")
      if self.current_bg_type in ("line_grid", "dot_grid"):
        self.draw_background_grid("line" if self.current_bg_type == "line_grid" else "dot")

    z = s.get("zoom", 1.0)  # restore zoom so slider/zoom keep working on opened projects
    self.current_zoom_scale = z
    if hasattr(self, "univ_zoom_slider"):
      v = 50.0 if z == 1.0 else (50 * (z - 0.02) / 0.98 if z < 1 else 50 + 50 * (z - 1) / 5)
      v = max(0.0, min(100.0, v))
      self.univ_zoom_slider.set(v)
      self.zoom_label.configure(text=f"Zoom: {int(v)}%")

    if hasattr(self, "restore_inserted_pictures_state"):
      self.restore_inserted_pictures_state(s.get("pictures"))  # clears old ones first

    for c in s.get("components", []):
      if c.get("pcb"):
        self.pcb_load(c)
      elif c["path"] in EMBEDDED_COMPONENTS:
        self.spawn_embedded_component(c["path"], EMBEDDED_COMPONENTS[c["path"]], c["x"], c["y"], c["scale"], c["rotation"], c.get("crop"), (c.get("sx", 1.0), c.get("sy", 1.0)))
    for w in s.get("wires", []):
      i = self.canvas.create_line(*w["coords"], fill=w["color"], width=w.get("width", 2.0), capstyle="round", joinstyle="round", smooth=True, splinesteps=64, tags="wire")
      self.stroke_w[i] = w.get("width", 2.0)
    for b in s.get("boxes", []):
      i = self.canvas.create_polygon(*b["coords"], outline=b["color"], fill="", width=b.get("width", 2.0), tags="box_shape")
      self.stroke_w[i] = b.get("width", 2.0)
    for c in s.get("circles", []):
      i = self.canvas.create_polygon(*c["coords"], outline=c["color"], fill="", width=c.get("width", 2.0), tags="circle_shape")
      self.stroke_w[i] = c.get("width", 2.0)
    for sr in s.get("stars", []):
      i = self.canvas.create_polygon(*sr["coords"], outline=sr["color"], fill="", width=sr.get("width", 2.0), tags="star_shape")
      self.stroke_w[i] = sr.get("width", 2.0)
    for sp in s.get("springs", []):
      i = self.canvas.create_line(*sp["coords"], fill=sp["color"], width=sp.get("width", 2.0), capstyle="round", joinstyle="round", tags="spring_shape")
      self.stroke_w[i] = sp.get("width", 2.0)
    for cb in s.get("cables", []):
      i = self.canvas.create_line(*cb["coords"], fill=cb["color"], width=cb.get("width", 8.0), capstyle="butt", tags="cable_shape")
      self.stroke_w[i] = cb.get("width", 8.0)
    self.text_meta_reset()
    for t in s.get("texts", []):
      self.text_create(t["x"], t["y"], t["text"], t["color"], t.get("size"), t.get("hl"))
    
    if hasattr(self, "restore_tables_state"):
      self.restore_tables_state(s.get("tables"))  # clears old tables first, then draws the saved ones

    self.selected_item = None
    if hasattr(self, "update_workspace_counter"):
      self.update_workspace_counter()

  def save_circuit(self):
    p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
    if p:
      try:
        with open(p, "w") as f:
          json.dump(self.get_state(), f)
        self.toast("💾 Circuit file saved successfully!")
      except Exception as e:
        print("Save error:", e)

  def load_circuit(self):
    p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
    if p:
      try:
        if hasattr(self, "flush_autosave"):
          self.flush_autosave()  # keep what is on the workspace now
        with open(p, "r") as f:
          st = json.load(f)
        self.set_state(st)
        self.current_project = None  # loaded file is a new, auto-saved project
        self.title(APP_TITLE)
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.pre_state = None
        if hasattr(self, "schedule_aa_all"):
          self.schedule_aa_all()
        if hasattr(self, "_schedule_autosave"):
          self._schedule_autosave()  # loaded file becomes its own auto-saved project
        self.toast("📂 Circuit file loaded!")
      except Exception as e:
        print("Load error:", e)

  def toast(self, text, ms=2200):
    lbl = ctk.CTkLabel(self.canvas_container, text=text, fg_color="#34495e", text_color="white", corner_radius=8, font=ctk.CTkFont(size=13, weight="bold"))
    lbl.place(relx=0.5, rely=0.9, anchor="center")
    self.after(ms, lbl.destroy)
