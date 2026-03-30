import os, re, shutil, json

WORKSPACE_PATH = "./workspace"


# ═══════════════════════════════════════════════
# INTERNAL HELPERS (not exposed as tools)
# ═══════════════════════════════════════════════

def _resolve(path: str, workspace_path: str = WORKSPACE_PATH) -> str:
    workspace = os.path.realpath(workspace_path)
    resolved = os.path.realpath(os.path.join(workspace, path))
    if not resolved.startswith(workspace):
        raise PermissionError(f"Access outside workspace is not allowed: {path}")
    return resolved


def _write_lines(full_path, lines):
    with open(full_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _find_function_bounds(lines, function_name):
    func_start = None
    func_indent = None

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        # Python: def func_name(
        if stripped.startswith(f"def {function_name}(") or stripped.startswith(f"def {function_name} ("):
            func_start = i
            func_indent = len(line) - len(stripped)
            break
        # JS: method(, async method(, function name(
        if (stripped.startswith(f"{function_name}(")
            or stripped.startswith(f"async {function_name}(")
            or stripped.startswith(f"function {function_name}(")):
            func_start = i
            func_indent = len(line) - len(stripped)
            break

    if func_start is None:
        return None, None

    func_end = func_start + 1
    while func_end < len(lines):
        line = lines[func_end]
        if line.strip() == "":
            func_end += 1
            continue
        current_indent = len(line) - len(line.lstrip())
        if current_indent <= func_indent:
            break
        func_end += 1

    return func_start, func_end


def _find_class_end(lines, class_name):
    class_start = None
    class_indent = None

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if (stripped.startswith(f"class {class_name}(") or stripped.startswith(f"class {class_name}:")
            or stripped.startswith(f"class {class_name} {{") or stripped.startswith(f"class {class_name}{{")):
            class_start = i
            class_indent = len(line) - len(stripped)
            break

    if class_start is None:
        return None

    insert_at = class_start + 1
    for i in range(class_start + 1, len(lines)):
        line = lines[i]
        if line.strip() == "":
            insert_at = i + 1
            continue
        current_indent = len(line) - len(line.lstrip())
        if current_indent <= class_indent:
            break
        insert_at = i + 1

    return insert_at


def _get_file_structure(path, workspace_path):
    """Extract imports, classes, functions, exports from a file. Multi-language."""
    try:
        full_path = _resolve(path, workspace_path)
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
        lines = content.split("\n")
    except Exception:
        return None

    imports = []
    classes = []
    functions = []
    exports = []
    constructors = []

    is_py = path.endswith(".py")
    is_js = path.endswith((".js", ".ts", ".tsx", ".jsx"))

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Imports
        if is_py and (stripped.startswith("import ") or stripped.startswith("from ")):
            imports.append(stripped)
        elif is_js and ("require(" in stripped or stripped.startswith("import ")):
            imports.append(stripped)

        # Classes
        if stripped.startswith("class "):
            if is_py:
                name = stripped.split("(")[0].split(":")[0].replace("class ", "").strip()
            else:
                name = re.match(r"class\s+(\w+)", stripped)
                name = name.group(1) if name else stripped
            classes.append(name)

        # Functions with signatures
        if is_py and stripped.startswith("def "):
            sig_match = re.match(r"def\s+(\w+)\(([^)]*)\)", stripped)
            if sig_match:
                fname = sig_match.group(1)
                params = sig_match.group(2)
                indent = len(line) - len(line.lstrip())
                parent = classes[-1] if indent > 0 and classes else None
                functions.append({"name": fname, "params": params, "class": parent, "line": i})

        if is_js:
            # constructor(params)
            if stripped.startswith("constructor("):
                params_match = re.match(r"constructor\(([^)]*)\)", stripped)
                if params_match and classes:
                    constructors.append({"class": classes[-1], "params": params_match.group(1)})

            # Regular methods: methodName(params) {
            method_match = re.match(r"(async\s+)?(\w+)\(([^)]*)\)\s*\{?", stripped)
            if method_match and not stripped.startswith(("if", "for", "while", "switch", "catch", "//", "/*", "return", "const", "let", "var")):
                fname = method_match.group(2)
                params = method_match.group(3)
                indent = len(line) - len(line.lstrip())
                parent = classes[-1] if indent > 0 and classes else None
                is_async = bool(method_match.group(1))
                functions.append({"name": fname, "params": params, "class": parent, "line": i, "async": is_async})

            # function name(params)
            func_match = re.match(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\(([^)]*)\)", stripped)
            if func_match:
                functions.append({"name": func_match.group(1), "params": func_match.group(2), "class": None, "line": i})

        # Exports
        if is_js:
            if "module.exports" in stripped:
                exports.append(stripped)
            elif stripped.startswith("export "):
                exports.append(stripped)
        if is_py and stripped.startswith("__all__"):
            exports.append(stripped)

    return {
        "path": path,
        "lines": len(lines),
        "imports": imports,
        "classes": classes,
        "functions": functions,
        "constructors": constructors,
        "exports": exports
    }


def _resolve_import_path(raw_path, importing_file, workspace_path):
    """Resolve a relative import to an actual file in workspace."""
    dir_of_importer = os.path.dirname(importing_file)
    base = os.path.normpath(os.path.join(dir_of_importer, raw_path)).replace("\\", "/")
    extensions = ["", ".js", ".ts", ".tsx", ".jsx", ".py", "/index.js", "/index.ts"]
    for ext in extensions:
        candidate = base + ext
        full = os.path.join(os.path.realpath(workspace_path), candidate)
        if os.path.isfile(full):
            return candidate
    return None


def _extract_import_paths(content, file_path):
    """Extract relative import paths from file content."""
    paths = []
    is_py = file_path.endswith(".py")

    if is_py:
        for match in re.finditer(r'^from\s+([\w.]+)\s+import', content, re.MULTILINE):
            module = match.group(1)
            paths.append(module.replace(".", "/") + ".py")
    else:
        # require('./path')
        for match in re.finditer(r"require\(['\"](\.[^'\"]+)['\"]\)", content):
            paths.append(match.group(1))
        # import ... from './path'
        for match in re.finditer(r"from\s+['\"](\.[^'\"]+)['\"]", content):
            paths.append(match.group(1))

    return paths


# ═══════════════════════════════════════════════
# TOOL 1: CREATE — Create new files
# ═══════════════════════════════════════════════

def create_file(path: str, content: str, workspace_path: str = WORKSPACE_PATH) -> dict:
    """Create a new file with the given content. Creates directories as needed."""
    try:
        full_path = _resolve(path, workspace_path)

        if content and not content.endswith("\n"):
            content += "\n"

        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "path": path, "action": "created"}
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════
# TOOL 2: READ — Intelligent context reader
# ═══════════════════════════════════════════════

def read_context(path: str, workspace_path: str = WORKSPACE_PATH) -> dict:
    """
    Read a file AND all its dependency context automatically.
    
    Returns:
        - target_content: full content of the requested file (if exists)
        - target_structure: parsed structure (imports, classes, functions, exports)
        - dependencies: for each import, the structure of the imported file
        - workspace_summary: brief summary of all other files in workspace
    """
    try:
        result = {"success": True, "path": path}
        workspace = os.path.realpath(workspace_path)

        # 1. Read target file (if exists)
        full_path = _resolve(path, workspace_path)
        if os.path.isfile(full_path):
            with open(full_path, "r", encoding="utf-8") as f:
                result["target_content"] = f.read()
            result["target_structure"] = _get_file_structure(path, workspace_path)
        else:
            result["target_content"] = None
            result["target_structure"] = None

        # 2. Read dependency files (files imported by target)
        dependencies = {}
        if result["target_content"]:
            import_paths = _extract_import_paths(result["target_content"], path)
            for raw_imp in import_paths:
                resolved = _resolve_import_path(raw_imp, path, workspace_path)
                if resolved:
                    dep_full = os.path.join(workspace, resolved)
                    if os.path.isfile(dep_full):
                        structure = _get_file_structure(resolved, workspace_path)
                        if structure:
                            # Include full content for direct dependencies
                            with open(dep_full, "r", encoding="utf-8") as f:
                                structure["content"] = f.read()
                            dependencies[resolved] = structure

        result["dependencies"] = dependencies

        # 3. Workspace summary (all other files, structure only)
        workspace_files = []
        for root, dirs, filenames in os.walk(workspace):
            for name in filenames:
                abs_path = os.path.join(root, name)
                rel_path = os.path.relpath(abs_path, workspace).replace("\\", "/")
                if rel_path == path or rel_path in dependencies:
                    continue
                if name.endswith(('.py', '.js', '.ts', '.tsx', '.jsx')):
                    structure = _get_file_structure(rel_path, workspace_path)
                    if structure:
                        # Only summary, no content
                        workspace_files.append({
                            "path": rel_path,
                            "classes": structure["classes"],
                            "exports": structure["exports"],
                            "constructors": structure.get("constructors", [])
                        })

        result["workspace_summary"] = workspace_files
        return result

    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════
# TOOL 3: SMART_EDIT — Modify existing files
# ═══════════════════════════════════════════════

def smart_edit(path: str, action: str, content: str, target: str = None, class_name: str = None, workspace_path: str = WORKSPACE_PATH) -> dict:
    """
    Modify an existing file. File MUST already exist (use create_file for new files).
    
    Actions:
        add_function     — add function to end of file, or inside class if class_name provided
        replace_function — replace function by name (target = function name)
        add_import       — add import at top (skips duplicates)
        add_to_class     — add code block inside a class (class_name required)
        insert_at        — insert content at a specific line (target = line number as string). AVOID — line numbers shift after edits.
        append           — add content to end of file
    """
    try:
        full_path = _resolve(path, workspace_path)

        if content and not content.endswith("\n"):
            content += "\n"

        # Reject create — use create_file tool instead
        if action == "create":
            return {"success": False, "error": "Use create_file tool for new files, not smart_edit action='create'"}

        if not os.path.exists(full_path):
            return {"success": False, "error": f"File not found: {path}. Use create_file for new files."}

        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # ADD_IMPORT
        if action == "add_import":
            for line in lines:
                if line.strip() == content.strip():
                    return {"success": True, "path": path, "action": "import_exists"}

            last_import = 0
            for i, line in enumerate(lines):
                stripped = line.strip()
                if (stripped.startswith("import ") or stripped.startswith("from ")
                    or "require(" in stripped):
                    last_import = i + 1

            lines.insert(last_import, content)
            _write_lines(full_path, lines)
            return {"success": True, "path": path, "action": "import_added"}

        # REPLACE_FUNCTION
        if action == "replace_function":
            if not target:
                return {"success": False, "error": "target (function name) required"}

            start, end = _find_function_bounds(lines, target)
            if start is None:
                # Provide helpful context about what DOES exist
                existing = _get_file_structure(path, workspace_path)
                existing_names = []
                if existing:
                    existing_names = [f["name"] for f in existing.get("functions", [])]
                return {"success": False, 
                        "error": f"Function '{target}' not found in {path}. Existing functions: {existing_names}"}

            lines[start:end] = [content]
            _write_lines(full_path, lines)
            return {"success": True, "path": path, "action": "replaced", "function": target}

        # ADD_FUNCTION
        if action == "add_function":
            if class_name:
                insert_at = _find_class_end(lines, class_name)
                if insert_at is None:
                    existing = _get_file_structure(path, workspace_path)
                    existing_classes = existing.get("classes", []) if existing else []
                    return {"success": False,
                            "error": f"Class '{class_name}' not found in {path}. Existing classes: {existing_classes}"}
                lines.insert(insert_at, "\n" + content)
            else:
                lines.append("\n" + content)

            _write_lines(full_path, lines)
            return {"success": True, "path": path, "action": "added"}

        # ADD_TO_CLASS
        if action == "add_to_class":
            if not class_name:
                return {"success": False, "error": "class_name required"}

            insert_at = _find_class_end(lines, class_name)
            if insert_at is None:
                existing = _get_file_structure(path, workspace_path)
                existing_classes = existing.get("classes", []) if existing else []
                return {"success": False,
                        "error": f"Class '{class_name}' not found in {path}. Existing classes: {existing_classes}"}

            lines.insert(insert_at, "\n" + content)
            _write_lines(full_path, lines)
            return {"success": True, "path": path, "action": "added_to_class", "class": class_name}

        # INSERT_AT
        if action == "insert_at":
            line_number = int(target) if target else 1
            if line_number < 1 or line_number > len(lines) + 1:
                return {"success": False, "error": f"Line {line_number} out of range (file has {len(lines)} lines)"}

            lines.insert(line_number - 1, content)
            _write_lines(full_path, lines)
            return {"success": True, "path": path, "action": "inserted", "line": line_number}

        # APPEND
        if action == "append":
            lines.append("\n" + content)
            _write_lines(full_path, lines)
            return {"success": True, "path": path, "action": "appended"}

        return {"success": False, "error": f"Unknown action: {action}"}

    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════
# UTILITY (used by other agents, not by coder)
# ═══════════════════════════════════════════════

def read_file(path: str, workspace_path: str = WORKSPACE_PATH) -> dict:
    """Simple file read — used by ValidatorAgent and ExecutorAgent internally."""
    try:
        full_path = _resolve(path, workspace_path)
        with open(full_path, "r", encoding="utf-8") as f:
            return {"success": True, "content": f.read()}
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except FileNotFoundError:
        return {"success": False, "error": f"File not found: {path}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def file_summary(path: str, workspace_path: str = WORKSPACE_PATH) -> dict:
    """Get file structure — used by ValidatorAgent and ExecutorAgent internally."""
    structure = _get_file_structure(path, workspace_path)
    if structure:
        return {"success": True, "summary": structure, "content": json.dumps(structure, indent=2)}
    return {"success": False, "error": f"Could not read: {path}"}


def list_files(path: str = ".", workspace_path: str = WORKSPACE_PATH) -> dict:
    """List files in workspace — used by ExecutorAgent internally."""
    try:
        full_path = _resolve(path, workspace_path)
        if not os.path.exists(full_path):
            return {"success": False, "error": f"Path not found: {path}"}
        workspace = os.path.realpath(workspace_path)
        files = []
        for root, dirs, filenames in os.walk(full_path):
            for name in filenames:
                abs_path = os.path.join(root, name)
                files.append(os.path.relpath(abs_path, workspace).replace("\\", "/"))
        return {"success": True, "files": files}
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def delete_file(path: str, workspace_path: str = WORKSPACE_PATH) -> dict:
    """Delete a file — used internally."""
    try:
        full_path = _resolve(path, workspace_path)
        if not os.path.exists(full_path):
            return {"success": False, "error": f"Path not found: {path}"}
        if os.path.isdir(full_path):
            shutil.rmtree(full_path)
        else:
            os.remove(full_path)
        return {"success": True, "path": path}
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}