"""Counts the components in components.db and, only if you ask, removes EXACT extra copies.

  python check_library.py            -> report only (changes nothing)
  python check_library.py --apply    -> makes a backup of components.db, then removes copies that are
                                        'Name (2)', 'Name (3)'... AND byte-for-byte the same picture as 'Name'

Pictures that merely share a name but look different are NEVER touched.
"""
import os, re, sys, shutil, sqlite3, hashlib, time
from collections import defaultdict

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "components.db")
base = lambda n: re.sub(r" \(\d+\)$", "", n)


def main(apply):
  if not os.path.isfile(DB):
    print("components.db not found next to this script.")
    return
  db = sqlite3.connect(DB)
  rows = []  # (position, name, sha, size)
  for name, pos, data in db.execute("SELECT name, position, data FROM components ORDER BY position"):
    b = bytes(data)
    rows.append((pos, name, hashlib.sha1(b).hexdigest(), len(b)))
  total = len(rows)
  groups = defaultdict(list)
  for r in rows:
    groups[(base(r[1]), r[2])].append(r)
  extra = [r for g in groups.values() for r in g[1:]]
  print(f"Components in the list (rows in database): {total}")
  print(f"Different pictures (name + exact bytes):   {len(groups)}")
  print(f"Exact extra copies (same name, same bytes): {len(extra)}")
  print(f"Database size: {os.path.getsize(DB) / 1024 / 1024:.1f} MB")
  if extra:
    print("\nFirst extra copies found:")
    for r in extra[:15]:
      print("  ", r[1])
  if not apply:
    if extra:
      print("\nNothing was changed. Run with --apply to remove only these exact extra copies (a backup is made first).")
    return
  if not extra:
    print("\nNothing to remove.")
    return
  backup = DB.replace(".db", time.strftime("_backup_%Y%m%d_%H%M%S.db"))
  db.close()
  shutil.copy2(DB, backup)
  print(f"\nBackup written: {os.path.basename(backup)}")
  db = sqlite3.connect(DB)
  with db:
    db.executemany("DELETE FROM components WHERE name=?", [(r[1],) for r in extra])
  db.execute("VACUUM")
  print(f"Removed {len(extra)} exact extra copies. Components now: {total - len(extra)}")


if __name__ == "__main__":
  main("--apply" in sys.argv)
