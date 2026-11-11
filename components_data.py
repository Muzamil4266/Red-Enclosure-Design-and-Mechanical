"""Component library stored in ONE SQLite file: components.db (next to the program).

Every component is one row: its name, its image bytes exactly as they were imported (a BLOB, never re-encoded or
resized), the original file name, the image format and size, and its position in the list. Nothing is written into
.py files and nothing is split across several files.

`EMBEDDED_COMPONENTS` still behaves like the old dict (name -> base64 string, read on demand), so the rest of the
program works unchanged.

First run only: components that already exist in the old storage (the `components_store` folder, or the old
components_part1.py / components_part2.py / data.py / data2.py files) are copied into the database once. Those old
files are only READ, never changed or written, and are not used again afterwards.
"""
import os
import io
import re
import ast
import json
import time
import base64
import hashlib
import sqlite3
import threading
from collections import Counter
from collections.abc import MutableMapping
from PIL import Image

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "components.db")
OLD_STORE_INDEX = os.path.join(BASE_DIR, "components_store", "index.json")
OLD_STORE_DIR = os.path.join(BASE_DIR, "components_store")
OLD_PY_FILES = ("components_part1.py", "components_part2.py", "data.py", "data2.py")  # read once, never written
EXT = {"JPEG": ".jpg", "MPO": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp", "GIF": ".gif", "TIFF": ".tif"}


def _image_info(raw):
  """(format, width, height) from the image header only (the pixels are not decoded)."""
  try:
    with Image.open(io.BytesIO(raw)) as im:
      return im.format, im.size[0], im.size[1]
  except Exception:
    return None, None, None


def _base_name(name):
  """'Resistor (3)' -> 'Resistor' (the number added to keep duplicates apart is not part of the real name)."""
  return re.sub(r" \(\d+\)$", "", name)


def _sha(raw):
  return hashlib.sha1(raw).hexdigest()


def _to_bytes(value):
  if isinstance(value, str):
    try:
      return base64.b64decode(value.encode("utf-8"), validate=True)
    except Exception:
      return value.encode("utf-8")
  return bytes(value)


class ComponentStore(MutableMapping):

  def __init__(self, path=DB_FILE):
    self._lock = threading.RLock()
    self._path = path
    self._db = sqlite3.connect(path, check_same_thread=False)
    self._db.execute("PRAGMA journal_mode=DELETE")  # one plain .db file, no -wal / -shm companions
    self._db.execute("PRAGMA synchronous=NORMAL")
    self._db.executescript("""
      CREATE TABLE IF NOT EXISTS components (
        name     TEXT PRIMARY KEY,
        data     BLOB NOT NULL,
        filename TEXT,
        fmt      TEXT,
        width    INTEGER,
        height   INTEGER,
        position INTEGER NOT NULL,
        added    REAL
      );
      CREATE INDEX IF NOT EXISTS idx_components_position ON components(position);
      CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    self._db.commit()
    self._migrate_once()

  # ---------------------------------------------------------------- one-time copy of the old storage
  def _meta(self, key):
    row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None

  def _migrate_once(self):
    """Copies the old storage into the database. EVERY component is kept: when a name is already taken the copy
    gets a number ('Name (2)'). Runs again once for libraries migrated by the older version (which skipped
    duplicates); items already in the database are recognised by name + exact bytes and are not added twice."""
    if self._meta("migrated_keep_all"):
      return
    with self._lock:
      have = Counter((_base_name(n), _sha(bytes(d))) for n, d in self._db.execute("SELECT name, data FROM components"))
    found = 0
    claimed = {}  # (name, bytes) -> where it came from; the same picture kept in TWO old places is ONE component

    def add(name, raw, src):
      nonlocal found
      key = (_base_name(name), _sha(raw))
      if have[key] > 0:  # this exact component is already in the database
        have[key] -= 1
        claimed.setdefault(key, "db")
        return
      if claimed.get(key, src) != src:  # same name + same picture already taken from another old location
        return
      self._insert(self._unique(name), raw, None, front=False)
      claimed[key] = src
      found += 1

    with self._lock, self._db:
      if os.path.isfile(OLD_STORE_INDEX):  # the components_store folder (previous storage)
        try:
          with open(OLD_STORE_INDEX, "r", encoding="utf-8") as f:
            items = json.load(f).get("items", [])
          for name, fname in items:
            p = os.path.join(OLD_STORE_DIR, fname)
            if os.path.isfile(p):
              with open(p, "rb") as f:
                add(name, f.read(), "store")
        except Exception as e:
          print(f"Migration: could not read components_store ({e})")
      for part in OLD_PY_FILES:  # very old .py storage: read line by line, never modified
        path = os.path.join(BASE_DIR, part)
        if not os.path.isfile(path):
          continue
        try:
          with open(path, "r", encoding="utf-8") as f:
            for line in f:
              line = line.strip()
              if not line or line[0] in "#}" or line.startswith("PART_"):
                continue
              try:
                (name, b64), = ast.literal_eval("{" + line.rstrip(",") + "}").items()
                if isinstance(name, str):
                  add(name, _to_bytes(b64), part)
              except Exception:
                pass
        except Exception as e:
          print(f"Migration: could not read {part} ({e})")
      self._db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('migrated_keep_all', ?)", (str(found),))
    if found:
      print(f"Component library moved into {os.path.basename(self._path)}: {found} components added (duplicates kept).")

  # ---------------------------------------------------------------- internals
  def _exists(self, name):
    return self._db.execute("SELECT 1 FROM components WHERE name=?", (name,)).fetchone() is not None

  def _unique(self, name):
    """`name` if free, otherwise 'Name (2)', 'Name (3)'... (the first free one)."""
    if not self._exists(name):
      return name
    base, n = _base_name(name), 2
    while self._exists(f"{base} ({n})"):
      n += 1
    return f"{base} ({n})"

  def unique_name(self, name):
    with self._lock:
      return self._unique(name)

  def _position(self, front):
    lo, hi = self._db.execute("SELECT MIN(position), MAX(position) FROM components").fetchone()
    if lo is None:
      return 0
    return lo - 1 if front else hi + 1

  def _insert(self, name, raw, filename, front):
    fmt, w, h = _image_info(raw)
    self._db.execute(
        "INSERT OR REPLACE INTO components(name, data, filename, fmt, width, height, position, added) VALUES(?,?,?,?,?,?,?,?)",
        (name, sqlite3.Binary(raw), filename, fmt, w, h, self._position(front), time.time()))

  # ---------------------------------------------------------------- dict-like API
  def raw(self, name):
    """The stored image bytes, exactly as they were imported."""
    with self._lock:
      row = self._db.execute("SELECT data FROM components WHERE name=?", (name,)).fetchone()
    if row is None:
      raise KeyError(name)
    return bytes(row[0])

  def put(self, name, value, front=False, filename=None, replace=False):
    """Adds a component and returns the name it was stored under. A name that already exists is NEVER overwritten
    (the new one becomes 'Name (2)') unless replace=True. `value` is the image bytes (untouched) or base64."""
    raw = _to_bytes(value)
    with self._lock, self._db:
      if not replace:
        name = self._unique(name)
      if filename is None:
        row = self._db.execute("SELECT filename FROM components WHERE name=?", (name,)).fetchone()
        filename = row[0] if row else None
      self._insert(name, raw, filename, front)
    return name

  def rename(self, old, new):
    with self._lock, self._db:
      self._db.execute("UPDATE components SET name=? WHERE name=?", (new, old))

  def remove(self, name):
    with self._lock, self._db:
      self._db.execute("DELETE FROM components WHERE name=?", (name,))

  def __getitem__(self, name):
    return base64.b64encode(self.raw(name)).decode("ascii")

  def __setitem__(self, name, value):
    self.put(name, value, replace=True)

  def __delitem__(self, name):
    if name not in self:
      raise KeyError(name)
    self.remove(name)

  def __contains__(self, name):
    with self._lock:
      return self._exists(name)

  def __iter__(self):
    with self._lock:
      names = [r[0] for r in self._db.execute("SELECT name FROM components ORDER BY position")]
    return iter(names)

  def __len__(self):
    with self._lock:
      return self._db.execute("SELECT COUNT(*) FROM components").fetchone()[0]

  def clear(self):  # never wipe the library by accident
    raise RuntimeError("ComponentStore.clear() is disabled; use remove(name)")

  # ---------------------------------------------------------------- export
  def export_all(self, folder, progress=None):
    """Writes every component's stored bytes to `folder` as a file, byte for byte (same format, same pixels, no
    resizing or re-encoding). File names are the original names when known. Returns (written, [(name, error)])."""
    os.makedirs(folder, exist_ok=True)
    with self._lock:
      rows = self._db.execute("SELECT name, filename, fmt FROM components ORDER BY position").fetchall()
    used, written, failed = set(), 0, []
    for i, (name, filename, fmt) in enumerate(rows):
      try:
        raw = self.raw(name)
        if not fmt:
          fmt = _image_info(raw)[0]
        base = os.path.basename(filename) if filename else name
        base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .") or "component"
        stem, ext = os.path.splitext(base)
        if not ext:
          ext = EXT.get(fmt, ".img")
        path, n = os.path.join(folder, stem + ext), 2
        while path.lower() in used or os.path.exists(path):
          path = os.path.join(folder, f"{stem} ({n}){ext}")
          n += 1
        used.add(path.lower())
        with open(path, "wb") as f:
          f.write(raw)
        written += 1
      except Exception as e:
        failed.append((name, str(e)))
      if progress:
        progress(i + 1, len(rows))
    return written, failed


EMBEDDED_COMPONENTS = ComponentStore()
