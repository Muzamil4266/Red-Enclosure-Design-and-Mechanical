"""
analyze_project.py - builds project_map.txt describing the whole program WITHOUT running it.

Usage (from your project folder, or pass the folder as an argument):
    python analyze_project.py
    python analyze_project.py "C:\\path\\to\\Red Circuit Project"

Upload the generated project_map.txt. It contains only names, signatures, first docstring lines and
detected problems - no code bodies and no data values (so big base64 data files stay tiny).
"""
import ast
import os
import sys
from collections import defaultdict

SKIP_DIRS = {"__pycache__", ".git", "venv", ".venv", "env", "node_modules", "build", "dist",
             "saved_projects", "site-packages"}


def first_line(doc):
    return doc.strip().splitlines()[0].strip() if doc and doc.strip() else ""


def signature(fn):
    a = fn.args
    names = [x.arg for x in a.posonlyargs + a.args]
    if a.vararg:
        names.append("*" + a.vararg.arg)
    names += [x.arg for x in a.kwonlyargs]
    if a.kwarg:
        names.append("**" + a.kwarg.arg)
    return f"{fn.name}({', '.join(names)})"


def inherited_names():
    """Attribute names that exist on the tk / customtkinter window classes (so they are not 'undefined')."""
    names = set()
    try:
        import tkinter
        names |= set(dir(tkinter.Tk)) | set(dir(tkinter.Toplevel))
    except Exception:
        pass
    try:
        import customtkinter as ctk
        names |= set(dir(ctk.CTk)) | set(dir(ctk.CTkToplevel))
    except Exception:
        pass
    return names


def main():
    root = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.getcwd()
    files = []
    for d, dirs, fs in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in fs:
            if f.endswith(".py") and f != os.path.basename(__file__):
                files.append(os.path.join(d, f))
    files.sort(key=lambda p: os.path.relpath(p, root).lower())

    local_modules = {os.path.splitext(os.path.basename(p))[0] for p in files}
    info = {}          # rel path -> dict
    errors = []
    all_methods = defaultdict(list)     # method name -> [(file, class)]
    class_methods = {}                  # class name -> set(method names)
    class_file = {}                     # class name -> file
    assigned_attrs = set()
    class_level_names = set()
    used_self = defaultdict(list)       # attr -> [(file, line)]
    getattr_names = defaultdict(list)   # string -> [(file, line)]

    for path in files:
        rel = os.path.relpath(path, root)
        try:
            src = open(path, "r", encoding="utf-8", errors="replace").read()
            tree = ast.parse(src)
        except SyntaxError as e:
            errors.append(f"{rel}: SYNTAX ERROR line {e.lineno}: {e.msg}")
            continue
        entry = {"lines": src.count("\n") + 1, "size": os.path.getsize(path),
                 "doc": first_line(ast.get_docstring(tree)), "local_imports": set(), "ext_imports": set(),
                 "classes": [], "functions": [], "constants": []}
        for node in tree.body:
            if isinstance(node, ast.Import):
                for a in node.names:
                    top = a.name.split(".")[0]
                    (entry["local_imports"] if top in local_modules else entry["ext_imports"]).add(top)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                (entry["local_imports"] if top in local_modules else entry["ext_imports"]).add(top)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                entry["functions"].append((signature(node), node.lineno, first_line(ast.get_docstring(node))))
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id.isupper():
                        entry["constants"].append(t.id)
            elif isinstance(node, ast.ClassDef):
                bases = [ast.unparse(b) for b in node.bases]
                methods = []
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.append((signature(sub), sub.lineno, first_line(ast.get_docstring(sub))))
                        all_methods[sub.name].append((rel, node.name))
                    elif isinstance(sub, ast.Assign):
                        for t in sub.targets:
                            if isinstance(t, ast.Name):
                                class_level_names.add(t.id)
                entry["classes"].append({"name": node.name, "bases": bases, "line": node.lineno,
                                         "doc": first_line(ast.get_docstring(node)), "methods": methods})
                class_methods[node.name] = {m[0].split("(")[0] for m in methods}
                class_file[node.name] = rel
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self":
                if isinstance(n.ctx, ast.Store):
                    assigned_attrs.add(n.attr)
                else:
                    used_self[n.attr].append((rel, n.lineno))
            elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("getattr", "hasattr")
                  and len(n.args) >= 2 and isinstance(n.args[0], ast.Name) and n.args[0].id == "self"
                  and isinstance(n.args[1], ast.Constant) and isinstance(n.args[1].value, str)):
                getattr_names[n.args[1].value].append((rel, n.lineno))
        info[rel] = entry

    main_cls = None
    for rel, e in info.items():
        for c in e["classes"]:
            if main_cls is None or len(c["bases"]) > len(main_cls[1]["bases"]):
                main_cls = (rel, c)
    problems = list(errors)
    wiring = []
    if main_cls and len(main_cls[1]["bases"]) > 1:
        rel, c = main_cls
        wiring.append(f"Main app class: {c['name']} in {rel} with {len(c['bases'])} base classes")
        seen = defaultdict(list)
        for b in c["bases"]:
            if b not in class_methods:
                if not b.startswith(("ctk.", "tk.")):
                    problems.append(f"BASE CLASS NOT FOUND in project: {b} (is the file missing or misnamed?)")
                continue
            wiring.append(f"  {b:<34} from {class_file[b]}  ({len(class_methods[b])} methods)")
            for m in class_methods[b]:
                if not (m.startswith("__") and m.endswith("__")):
                    seen[m].append(b)
        for m, owners in sorted(seen.items()):
            if len(owners) > 1:
                problems.append(f"DUPLICATE METHOD '{m}' defined in {', '.join(owners)} (the first one in the base list wins)")
        main_defined = set(seen) | class_methods.get(c["name"], set())
    else:
        main_defined = set(all_methods)

    for name, places in sorted(getattr_names.items()):
        if name not in all_methods and name not in assigned_attrs and name not in class_level_names:
            where = ", ".join(f"{f}:{ln}" for f, ln in places[:3])
            problems.append(f"SILENT NO-OP: getattr/hasattr(self, '{name}') but no such method exists anywhere ({where})")

    inh = inherited_names()
    known = set(all_methods) | assigned_attrs | class_level_names | inh
    for attr, places in sorted(used_self.items()):
        if attr not in known:
            where = ", ".join(f"{f}:{ln}" for f, ln in places[:3])
            problems.append(f"UNDEFINED self.{attr} (used, never defined/assigned) at {where}")

    out = []
    total_lines = sum(e["lines"] for e in info.values())
    out.append(f"PROJECT MAP  |  root: {root}")
    out.append(f"{len(info)} python files, {total_lines} lines total\n")
    out.append("=" * 70 + "\nWIRING\n" + "=" * 70)
    out += wiring or ["(no multi-mixin main class detected)"]
    out.append("\n" + "=" * 70 + f"\nPROBLEMS FOUND ({len(problems)})\n" + "=" * 70)
    out += problems or ["none detected"]
    out.append("\n" + "=" * 70 + "\nFILE MAP\n" + "=" * 70)
    for rel, e in info.items():
        out.append(f"\n### {rel}  ({e['lines']} lines, {e['size'] // 1024} KB)")
        if e["doc"]:
            out.append(f"  purpose: {e['doc']}")
        if e["local_imports"]:
            out.append(f"  uses project modules: {', '.join(sorted(e['local_imports']))}")
        if e["ext_imports"]:
            out.append(f"  uses libraries: {', '.join(sorted(e['ext_imports']))}")
        if e["constants"]:
            out.append(f"  constants: {', '.join(e['constants'][:15])}{' ...' if len(e['constants']) > 15 else ''}")
        for c in e["classes"]:
            out.append(f"  class {c['name']}({', '.join(c['bases'])})  line {c['line']}" + (f"  - {c['doc']}" if c["doc"] else ""))
            for sig, ln, doc in c["methods"]:
                out.append(f"      {sig}  :{ln}" + (f"  - {doc}" if doc else ""))
        for sig, ln, doc in e["functions"]:
            out.append(f"  def {sig}  :{ln}" + (f"  - {doc}" if doc else ""))

    report = os.path.join(root, "project_map.txt")
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"Done: {len(info)} files analysed, {len(problems)} problem(s) found.")
    print(f"Report written to: {report}")
    for p in problems[:15]:
        print("  -", p)
    if len(problems) > 15:
        print(f"  ... and {len(problems) - 15} more in the report")


if __name__ == "__main__":
    main()
