import os, shutil, json

WORKSPACE_PATH = "./workspace"


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
        if stripped.startswith(f"def {function_name}(") or stripped.startswith(f"def {function_name} ("):
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
        if stripped.startswith(f"class {class_name}(") or stripped.startswith(f"class {class_name}:"):
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


# === READ TOOLS ===

def read_file(path: str, workspace_path: str = WORKSPACE_PATH) -> dict:
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


def read_file_lines(path: str, start: int, end: int, workspace_path: str = WORKSPACE_PATH) -> dict:
    try:
        full_path = _resolve(path, workspace_path)
        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        total = len(lines)
        start = max(1, start)
        end = min(total, end)

        if start > total:
            return {"success": False, "error": f"Start line {start} exceeds file length ({total} lines)"}

        return {
            "success": True,
            "content": "".join(lines[start - 1:end]),
            "total_lines": total,
            "range": f"{start}-{end}"
        }
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except FileNotFoundError:
        return {"success": False, "error": f"File not found: {path}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def file_summary(path: str, workspace_path: str = WORKSPACE_PATH) -> dict:
    try:
        full_path = _resolve(path, workspace_path)
        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        total_lines = len(lines)
        imports = []
        classes = []
        functions = []

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                imports.append({"line": i, "text": stripped})
            elif stripped.startswith("class "):
                name = stripped.split("(")[0].split(":")[0].replace("class ", "").strip()
                classes.append({"line": i, "name": name})
            elif stripped.startswith("def "):
                name = stripped.split("(")[0].replace("def ", "").strip()
                indent = len(line) - len(line.lstrip())
                parent = None
                if indent > 0 and classes:
                    parent = classes[-1]["name"]
                functions.append({"line": i, "name": name, "class": parent})

        summary = {
            "total_lines": total_lines,
            "imports": imports,
            "classes": classes,
            "functions": functions
        }

        return {"success": True, "summary": summary, "content": json.dumps(summary, indent=2)}
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except FileNotFoundError:
        return {"success": False, "error": f"File not found: {path}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def list_files(path: str = ".", workspace_path: str = WORKSPACE_PATH) -> dict:
    try:
        full_path = _resolve(path, workspace_path)
        if not os.path.exists(full_path):
            return {"success": False, "error": f"Path not found: {path}"}
        workspace = os.path.realpath(workspace_path)
        files = []
        for root, dirs, filenames in os.walk(full_path):
            for name in dirs:
                abs_path = os.path.join(root, name)
                files.append(os.path.relpath(abs_path, workspace))
            for name in filenames:
                abs_path = os.path.join(root, name)
                files.append(os.path.relpath(abs_path, workspace))
        return {"success": True, "files": files}
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


# === WRITE TOOLS ===

def smart_edit(path: str, action: str, content: str, target: str = None, class_name: str = None, workspace_path: str = WORKSPACE_PATH) -> dict:
    """
    Primary tool for all file modifications.
    
    Actions:
        create          — create new file with content
        add_function    — add function to end of file, or inside class if class_name provided
        replace_function — replace function by name (target = function name)
        add_import      — add import at top (skips duplicates)
        add_to_class    — add code block inside a class (class_name required)
        insert_at       — insert content at a specific line (target = line number as string)
        append          — add content to end of file
    """
    try:
        full_path = _resolve(path, workspace_path)

        if content and not content.endswith("\n"):
            content += "\n"

        # CREATE
        if action == "create":
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"success": True, "path": path, "action": "created"}

        # All other actions need existing file
        if not os.path.exists(full_path):
            return {"success": False, "error": f"File not found: {path}"}

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
                if stripped.startswith("import ") or stripped.startswith("from "):
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
                return {"success": False, "error": f"Function '{target}' not found in {path}"}

            lines[start:end] = [content]
            _write_lines(full_path, lines)
            return {"success": True, "path": path, "action": "replaced", "function": target}

        # ADD_FUNCTION
        if action == "add_function":
            if class_name:
                insert_at = _find_class_end(lines, class_name)
                if insert_at is None:
                    return {"success": False, "error": f"Class '{class_name}' not found in {path}"}
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
                return {"success": False, "error": f"Class '{class_name}' not found in {path}"}

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


def delete_file(path: str, workspace_path: str = WORKSPACE_PATH) -> dict:
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


def run_command(command: str, workspace_path: str = WORKSPACE_PATH) -> dict:
    import subprocess
    answer = input(f"Command: {command} — Allow execution? (y/n): ").strip().lower()
    if answer != "y":
        return {"success": False, "error": "Command rejected by user"}

    workspace = os.path.realpath(workspace_path)
    os.makedirs(workspace, exist_ok=True)

    try:
        result = subprocess.run(command, shell=True, cwd=workspace, capture_output=True, text=True, timeout=60)
        return {"success": True, "stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Command timed out after 60 seconds"}
    except Exception as e:
        return {"success": False, "error": str(e)}