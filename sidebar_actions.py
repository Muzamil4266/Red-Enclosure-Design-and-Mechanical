import io
import os
import queue
import threading
import base64
import customtkinter as ctk
from tkinter import filedialog
from PIL import Image, ImageTk
from components_data import EMBEDDED_COMPONENTS

MAX_BASE_SIDE = 1024   # decoded size kept in RAM per component (display size is unaffected)
BASE_CACHE_MAX = 24    # decoded images shared between copies of the same component


class SidebarActionsMixin:

  def save_component_to_module(self, name, data, filename=None):
    """Stores a component in the SQLite database (components.db). `data` is the image bytes, kept exactly as given."""
    EMBEDDED_COMPONENTS.put(name, data, front=True, filename=filename, replace=True)  # callers pass an already-unique name
    self._forget_caches(name)
    self.update_counter()

  def _forget_caches(self, name):
    for d in (self.thumb_cache, self.pil_cache, self._thumb_dims, self._thumb_built, self._base_cache):
      d.pop(name, None)

  def drop_card(self, name):
    self.vlist_remove(name)

  def remove_permanently(self, name):
    try:
      EMBEDDED_COMPONENTS.remove(name)
      self._forget_caches(name)
      self.drop_card(name)
      self.update_counter()
    except Exception as e:
      print(f"Error removing component: {e}")

  def rename_component(self, old, new):
    new = new.strip()
    if not new or new == old or old not in EMBEDDED_COMPONENTS:
      return
    new = EMBEDDED_COMPONENTS.unique_name(new)  # a taken name gets a number, nothing is overwritten
    EMBEDDED_COMPONENTS.rename(old, new)
    for d in (self.thumb_cache, self.pil_cache, self._thumb_dims, self._thumb_built, self._base_cache):
      if old in d:
        d[new] = d.pop(old)
    if old in self.temp_hidden_components:
      self.temp_hidden_components.discard(old)
      self.temp_hidden_components.add(new)
    for ref in self.image_refs.values():
      if ref["path"] == old:
        ref["path"] = new
    for st in self.undo_stack + self.redo_stack + ([self.pre_state] if self.pre_state else []):
      for c in st.get("components", []):
        if c["path"] == old:
          c["path"] = new
    self.load_components_to_sidebar()

  def remove_from_list_session(self, name):
    self.temp_hidden_components.add(name)
    self.drop_card(name)

  def show_component_context_menu(self, event, display_name):
    popup = ctk.CTkToplevel(self)
    popup.title("")
    popup.geometry("200x200")
    popup.resizable(False, False)
    popup.attributes("-topmost", True)
    x = max(10, self.winfo_pointerx() - 190)
    y = max(10, self.winfo_pointery() - 20)
    popup.geometry(f"+{x}+{y}")

    ctk.CTkLabel(popup, text=display_name, wraplength=180, font=ctk.CTkFont(size=11, weight="bold"), text_color="orange").pack(pady=(6, 4))
    name_entry = ctk.CTkEntry(popup, width=180, height=24)
    name_entry.insert(0, display_name)
    name_entry.pack(pady=2)
    ctk.CTkButton(
        popup, text="✏️ Rename", width=180, height=24, fg_color="#27ae60", hover_color="#2ecc71",
        command=lambda: [self.rename_component(display_name, name_entry.get()), popup.destroy()]
    ).pack(pady=2)
    ctk.CTkButton(
        popup, text="🗑️ Remove from list", width=180, height=24, fg_color="#34495e", hover_color="#2c3e50",
        command=lambda: [self.remove_from_list_session(display_name), popup.destroy()]
    ).pack(pady=2)
    ctk.CTkButton(
        popup, text="❌ Permanent delete", width=180, height=24, fg_color="#c0392b", hover_color="#e74c3c",
        command=lambda: [self.remove_permanently(display_name), popup.destroy()]
    ).pack(pady=2)

  def prepare_import_bytes(self, path):
    """The file's bytes exactly as they are on disk (no resizing, no re-encoding). Raises if it is not a readable image."""
    with open(path, "rb") as f:
      raw = f.read()
    with Image.open(io.BytesIO(raw)) as img:
      img.verify()
    return raw

  def add_new_component(self):
    file_paths = filedialog.askopenfilenames(
        master=self, title="Select Component Images (multiple allowed)",
        filetypes=[("Image Files", "*.png *.jpg *.jpeg *.bmp *.webp")]
    )
    for file_path in reversed(file_paths):
      try:
        data = self.prepare_import_bytes(file_path)
        name = os.path.splitext(os.path.basename(file_path))[0].replace("_", " ")
        name = EMBEDDED_COMPONENTS.unique_name(name)  # same name already in the library: keep both, number the new one
        self.get_thumb(name, data)
        self.save_component_to_module(name, data, filename=os.path.basename(file_path))
      except Exception as e:
        print(f"Import error ({file_path}): {e}")
    self.filter_components()

  def find_smart_spawn_position(self):
    cx = self.canvas.canvasx(self.canvas.winfo_width() / 2)
    cy = self.canvas.canvasy(self.canvas.winfo_height() / 2)
    xs = [self.canvas.coords(i)[0] for i in self.image_refs]
    x = max(xs) + 160 if xs else cx
    if x > 11000:
      x = cx
    return x, cy

  def render_image(self, item_id):
    ref = self.image_refs[item_id]
    s = self.item_scales[item_id] * ref.get("base", 1.0)
    img = ref["pil_img"]
    crop = ref.get("crop", [0.0, 0.0, 0.0, 0.0])
    if any(crop):
      cw, ch = img.size
      img = img.crop((int(crop[0] * cw), int(crop[1] * ch), int((1 - crop[2]) * cw), int((1 - crop[3]) * ch)))
    img = img.resize((max(10, int(img.width * s)), max(10, int(img.height * s))), Image.Resampling.LANCZOS)
    img = img.rotate(self.item_rotations[item_id], expand=True, resample=Image.Resampling.BICUBIC)
    ref["tk_img"] = ImageTk.PhotoImage(img)
    self.canvas.itemconfig(item_id, image=ref["tk_img"])
    if item_id == self.selected_item:
      self.show_handles()

  def _get_base(self, name, image_data):
    """Decoded RGBA image for a component, shared by every copy placed on the canvas (never mutated)."""
    base = self._base_cache.pop(name, None)  # re-inserted below => most recently used
    if base is None:
      raw = image_data
      if isinstance(raw, io.BytesIO):
        raw = raw.getvalue()
      elif hasattr(raw, "read"):
        raw = raw.read()
      elif isinstance(raw, str):
        try:
          raw = base64.b64decode(raw.encode("utf-8"))
        except Exception:
          raw = raw.encode("utf-8")

      # Try loading raw bytes directly first; fallback to base64 decoding if that fails
      def decode(buf):
        img = Image.open(io.BytesIO(buf))
        try:
          img.draft("RGB", (MAX_BASE_SIDE * 2, MAX_BASE_SIDE * 2))  # JPEG: decode a reduced size straight away (big originals stay fast)
        except Exception:
          pass
        return img.convert("RGBA")
      try:
        base = decode(raw if isinstance(raw, bytes) else str(raw).encode())
      except Exception:
        if isinstance(raw, bytes):
          base = decode(base64.b64decode(raw))
        else:
          raise
      if max(base.size) > MAX_BASE_SIDE:
        base.thumbnail((MAX_BASE_SIDE, MAX_BASE_SIDE), Image.Resampling.LANCZOS)
    self._base_cache[name] = base
    while len(self._base_cache) > BASE_CACHE_MAX:
      self._base_cache.pop(next(iter(self._base_cache)))
    return base

  def spawn_embedded_component(self, name, image_data, x=None, y=None, scale=1.0, rotation=0, crop=None, stretch=None):
    try:
      base = self._get_base(name, image_data)
      if x is None or y is None:
        x, y = self.find_smart_spawn_position()
      item_id = self.canvas.create_image(x, y, anchor="center", tags="component")
      self.image_refs[item_id] = {
          "path": name, "pil_img": base, "orig_pil": base,
          "base": min(1.0, 190 / base.width),
          "crop": crop if crop else [0.0, 0.0, 0.0, 0.0],
          "sx": stretch[0] if stretch else 1.0, "sy": stretch[1] if stretch else 1.0
      }
      self.item_scales[item_id] = scale
      self.item_rotations[item_id] = rotation
      self.render_image(item_id)
      return item_id
    except Exception as e:
      print(f"Error spawning embedded image: {e}")

  def add_component_recorded(self, name, data):
    self.finish_text_edit()
    self.begin_change()
    self.spawn_embedded_component(name, data)
    self.commit_change()
    if hasattr(self, "update_workspace_counter"):
      self.update_workspace_counter()

  def add_component(self, path_or_data):
    pass

  def export_component_photos(self):
    """One click: writes every component photo to a folder on the PC, byte for byte (original format and full resolution)."""
    total = len(EMBEDDED_COMPONENTS)
    if not total:
      self.toast("No components to export.")
      return
    if getattr(self, "_export_busy", False):
      self.toast("An export is already running.")
      return
    parent = filedialog.askdirectory(master=self, title="Choose where to save the exported photos")
    if not parent:
      return
    dest, n = os.path.join(parent, "Component Photos"), 2
    while os.path.exists(dest):  # always a new folder: nothing is overwritten
      dest = os.path.join(parent, f"Component Photos ({n})")
      n += 1
    self._export_busy = True
    q = queue.Queue()
    lbl = ctk.CTkLabel(self.canvas_container, text=f"⬇ Exporting 0 / {total} photos...", fg_color="#34495e", text_color="white",
                       corner_radius=8, font=ctk.CTkFont(size=13, weight="bold"))
    lbl.place(relx=0.5, rely=0.9, anchor="center")

    def work():
      try:
        q.put(("done", EMBEDDED_COMPONENTS.export_all(dest, lambda i, t: q.put(("p", i, t)))))
      except Exception as e:
        q.put(("err", str(e)))

    def poll():
      try:
        while True:
          m = q.get_nowait()
          if m[0] == "p":
            lbl.configure(text=f"⬇ Exporting {m[1]} / {m[2]} photos...")
          else:
            self._export_busy = False
            lbl.destroy()
            if m[0] == "err":
              self.toast("❌ Export failed: " + m[1], 5000)
            else:
              written, failed = m[1]
              self.toast(f"✔ {written} photos exported to: {dest}" + (f"  ({len(failed)} failed)" if failed else ""), 6000)
              try:
                os.startfile(dest)  # opens the folder (Windows)
              except Exception:
                pass
            return
      except queue.Empty:
        pass
      self.after(100, poll)
    threading.Thread(target=work, daemon=True).start()
    poll()

  def set_mode(self, mode):
    self.mode = mode
    self.highlight_toolbar_button(mode)
    self.show_handles()
