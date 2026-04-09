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


def _find_function_bounds_via_ts_parser(lines, function_name):
    """Try to find function bounds using ts_parser (tree-sitter + regex fallback).

    Returns (start_line, end_line) or (None, None) if not found.
    Uses extract_declarations() to get precise line numbers, then
    _find_brace_block_end() for the end boundary.
    """
    try:
        from common.ts_parser import extract_declarations
    except ImportError:
        return None, None

    content = "\n".join(lines)

    # Detect file type from content heuristics (we don't have the path here)
    # Try multiple extensions to maximize coverage
    for fake_path in ("file.ts", "file.js", "file.py", "file.go", "file.rs"):
        decls = extract_declarations(content, fake_path)
        if not decls:
            continue

        # Search in functions
        for func in decls.get("functions", []):
            if func["name"] == function_name:
                start = func["line"] - 1  # convert 1-indexed to 0-indexed
                if start < 0 or start >= len(lines):
                    continue

                # Determine end: brace-based or indent-based
                stripped = lines[start].lstrip()
                uses_braces = "{" in stripped or (
                    start + 1 < len(lines) and "{" in lines[start + 1].lstrip()[:3]
                )

                if uses_braces:
                    end = _find_brace_block_end(lines, start)
                else:
                    func_indent = len(lines[start]) - len(stripped)
                    end = start + 1
                    while end < len(lines):
                        line = lines[end]
                        if line.strip() == "":
                            end += 1
                            continue
                        if (len(line) - len(line.lstrip())) <= func_indent:
                            break
                        end += 1

                return start, end

        # Also search in classes (for interface/type/enum replacement)
        for cls in decls.get("classes", []):
            if cls["name"] == function_name:
                start = cls["line"] - 1
                if start < 0 or start >= len(lines):
                    continue
                end = _find_brace_block_end(lines, start)
                return start, end

        # If we found declarations (parser worked) but not our function, stop trying other extensions
        if decls.get("functions") or decls.get("classes"):
            break

    return None, None


def _find_function_bounds(lines, function_name):
    """Find start/end of a function by name. Supports Python, JS/TS, Svelte, Go, Rust, Ruby, PHP, Java, C#, Kotlin, Swift, Dart.

    Uses ts_parser (tree-sitter + regex) when available for precise line detection,
    then falls back to regex pattern matching.
    """
    # Try ts_parser first for JS/TS/Svelte/Vue files — it handles all edge cases
    ts_result = _find_function_bounds_via_ts_parser(lines, function_name)
    if ts_result[0] is not None:
        return ts_result

    func_start = None
    func_indent = None
    uses_braces = False

    # Patterns that indicate a function definition for `function_name`
    # Order matters — more specific patterns first
    patterns = [
        # Python: def name(
        rf"def\s+{re.escape(function_name)}\s*\(",
        # JS/TS: function name(, async function name(, export function name(
        rf"(?:export\s+)?(?:async\s+)?function\s+{re.escape(function_name)}\s*\(",
        # JS/TS arrow: [export] const/let/var name = (async)? (
        rf"(?:export\s+)?(?:const|let|var)\s+{re.escape(function_name)}\s*=\s*(?:async\s*)?\(",
        # JS/TS arrow: [export] const/let/var name = async? () =>
        rf"(?:export\s+)?(?:const|let|var)\s+{re.escape(function_name)}\s*=\s*(?:async\s*)?\([^)]*\)\s*=>",
        # Class method: name( or async name(
        rf"(?:async\s+)?{re.escape(function_name)}\s*\(",
        # Go: func name(, func (receiver) name(
        rf"func\s+(?:\([^)]*\)\s+)?{re.escape(function_name)}\s*\(",
        # Rust: fn name(, pub fn name(, async fn name(
        rf"(?:pub\s+)?(?:async\s+)?fn\s+{re.escape(function_name)}\s*\(",
        # Ruby: def name
        rf"def\s+{re.escape(function_name)}\b",
        # PHP: function name(, public function name(
        rf"(?:public|private|protected|static|\s)*function\s+{re.escape(function_name)}\s*\(",
        # Java/C#/Kotlin: visibility type name(
        rf"(?:public|private|protected|internal|static|override|suspend|open|\s)*\w+\s+{re.escape(function_name)}\s*\(",
        # Swift: func name(
        rf"(?:@\w+\s+)*(?:public|private|internal|open|static|override|\s)*func\s+{re.escape(function_name)}\s*\(",
        # Dart: type name(, Future<type> name(
        rf"(?:static\s+)?(?:\w+(?:<[^>]+>)?\s+)?{re.escape(function_name)}\s*\(",
        # TS: interface/type/enum Name { (treat as replaceable block)
        rf"(?:export\s+)?(?:interface|type|enum)\s+{re.escape(function_name)}\b",
    ]

    # Exclusion prefixes — lines that look like function calls, not definitions
    call_prefixes = ("if", "for", "while", "switch", "catch", "return", "//", "/*", "*", "else", "elif", "print", "console", "await", "yield")

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped or stripped.startswith(call_prefixes):
            continue

        for pattern in patterns:
            if re.match(pattern, stripped):
                func_start = i
                func_indent = len(line) - len(stripped)
                # Detect if this language uses braces
                if "{" in stripped or (i + 1 < len(lines) and "{" in lines[i + 1].lstrip()[:3]):
                    uses_braces = True
                break
        if func_start is not None:
            break

    if func_start is None:
        return None, None

    # Find function end
    if uses_braces:
        func_end = _find_brace_block_end(lines, func_start)
    else:
        # Indentation-based (Python, Ruby, YAML, etc.)
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


def _find_brace_block_end(lines, start_line):
    """Find the end of a brace-delimited block starting at or after start_line.

    For arrow functions like `export const GET = async ({ url }) => {`,
    starts counting braces from the `=>` position to skip destructuring params.
    """
    brace_count = 0
    found_open = False

    for i in range(start_line, len(lines)):
        line = lines[i]

        # For the first line, skip past `=>` if it's an arrow function
        # This avoids counting destructuring params like ({ url }) as the block
        scan_start = 0
        if i == start_line and "=>" in line:
            arrow_pos = line.index("=>")
            scan_start = arrow_pos + 2

        in_string = False
        for j in range(scan_start, len(line)):
            ch = line[j]
            if ch in ('"', "'", '`') and (j == 0 or line[j-1] != '\\'):
                in_string = not in_string
            if in_string:
                continue
            if ch == '{':
                brace_count += 1
                found_open = True
            elif ch == '}':
                brace_count -= 1
                if found_open and brace_count == 0:
                    return i + 1

    # Fallback: couldn't find matching brace, return reasonable estimate
    return min(start_line + 50, len(lines))


def _find_class_end(lines, class_name):
    """Find insertion point at end of a class. Supports Python, JS/TS, Java, C#, Kotlin, Swift, Dart, PHP, Ruby."""
    class_start = None
    class_indent = None
    uses_braces = False

    patterns = [
        # Python: class Name( or class Name:
        rf"class\s+{re.escape(class_name)}\s*[\(:]",
        # JS/TS/Java/C#/Kotlin/Dart/PHP: class Name {, class Name extends X {
        rf"(?:export\s+)?(?:abstract\s+)?(?:public\s+)?class\s+{re.escape(class_name)}\b",
        # Ruby: class Name
        rf"class\s+{re.escape(class_name)}\b",
        # Rust: struct Name {, impl Name {
        rf"(?:pub\s+)?(?:struct|impl|enum)\s+{re.escape(class_name)}\b",
        # Swift: class/struct Name {
        rf"(?:public\s+|private\s+|internal\s+|open\s+)?(?:class|struct)\s+{re.escape(class_name)}\b",
        # Go: type Name struct {
        rf"type\s+{re.escape(class_name)}\s+struct\b",
    ]

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        for pattern in patterns:
            if re.match(pattern, stripped):
                class_start = i
                class_indent = len(line) - len(stripped)
                if "{" in stripped or (i + 1 < len(lines) and "{" in lines[i + 1].lstrip()[:3]):
                    uses_braces = True
                break
        if class_start is not None:
            break

    if class_start is None:
        return None

    if uses_braces:
        return _find_brace_block_end(lines, class_start)
    else:
        # Indentation-based (Python, Ruby)
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
    """Extract imports, classes, functions, exports from a file. Universal multi-language support."""
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

    ext = os.path.splitext(path)[1].lower()

    # Language detection by extension
    lang = _detect_language(ext)

    # For component files (Svelte, Vue), extract script content
    script_content = content
    if lang == "svelte":
        match = re.search(r'<script[^>]*>(.*?)</script>', content, re.DOTALL)
        if match:
            script_content = match.group(1)
            lines = script_content.split("\n")
        lang = "js"  # Parse script block as JS
    elif lang == "vue":
        match = re.search(r'<script[^>]*>(.*?)</script>', content, re.DOTALL)
        if match:
            script_content = match.group(1)
            lines = script_content.split("\n")
        lang = "js"

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "#", "/*", "*", "<!--")):
            continue

        # ── IMPORTS ──
        if lang == "python" and (stripped.startswith("import ") or stripped.startswith("from ")):
            imports.append(stripped)
        elif lang == "js" and ("require(" in stripped or stripped.startswith("import ")):
            imports.append(stripped)
        elif lang == "go" and stripped.startswith("import"):
            imports.append(stripped)
        elif lang == "rust" and (stripped.startswith("use ") or stripped.startswith("extern crate")):
            imports.append(stripped)
        elif lang == "java" and stripped.startswith("import "):
            imports.append(stripped)
        elif lang == "ruby" and (stripped.startswith("require") or stripped.startswith("include")):
            imports.append(stripped)
        elif lang == "php" and (stripped.startswith("use ") or stripped.startswith("require") or stripped.startswith("include")):
            imports.append(stripped)
        elif lang == "swift" and stripped.startswith("import "):
            imports.append(stripped)
        elif lang == "dart" and stripped.startswith("import "):
            imports.append(stripped)
        elif lang == "kotlin" and stripped.startswith("import "):
            imports.append(stripped)

        # ── CLASSES ──
        # Go special case: type Name struct
        go_struct = re.match(r'type\s+(\w+)\s+(?:struct|interface)\b', stripped)
        if go_struct:
            classes.append(go_struct.group(1))
        else:
            class_match = re.match(
                r'(?:export\s+)?(?:abstract\s+)?(?:public\s+)?(?:open\s+)?(?:data\s+)?'
                r'(?:class|struct|interface|enum|impl|trait|protocol|extension)\s+(\w+)',
                stripped
            )
            if class_match and not stripped.startswith(("//", "#", "/*")):
                classes.append(class_match.group(1))

        # ── FUNCTIONS ──
        func_info = _parse_function_line(stripped, line, i, lang, classes)
        if func_info:
            if func_info.get("is_constructor"):
                constructors.append({"class": func_info.get("class"), "params": func_info["params"]})
            else:
                functions.append(func_info)

        # ── EXPORTS ──
        if lang == "js":
            if "module.exports" in stripped:
                exports.append(stripped)
            elif stripped.startswith("export "):
                exports.append(stripped)
        elif lang == "python" and stripped.startswith("__all__"):
            exports.append(stripped)
        elif lang == "go" and re.match(r'func\s+[A-Z]', stripped):
            exports.append(stripped.split("{")[0].strip())  # Go: exported = capitalized
        elif lang == "rust" and stripped.startswith("pub "):
            exports.append(stripped.split("{")[0].strip())

    return {
        "path": path,
        "lines": len(content.split("\n")),
        "imports": imports,
        "classes": classes,
        "functions": functions,
        "constructors": constructors,
        "exports": exports
    }


def _detect_language(ext):
    """Map file extension to language identifier."""
    mapping = {
        ".py": "python",
        ".js": "js", ".ts": "js", ".tsx": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
        ".svelte": "svelte", ".vue": "vue",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".cs": "java",  # C# has similar syntax to Java for our purposes
        ".kt": "kotlin", ".kts": "kotlin",
        ".swift": "swift",
        ".dart": "dart",
        ".rb": "ruby",
        ".php": "php",
        ".r": "python",  # R has similar import/function patterns
        ".lua": "lua",
        ".ex": "elixir", ".exs": "elixir",
    }
    return mapping.get(ext, "unknown")


def _parse_function_line(stripped, raw_line, line_num, lang, current_classes):
    """Try to parse a function/method definition from a single line. Returns dict or None."""
    indent = len(raw_line) - len(raw_line.lstrip())
    parent = current_classes[-1] if indent > 0 and current_classes else None

    # Skip obvious non-definitions
    if stripped.startswith(("if", "for", "while", "switch", "catch", "return", "//", "/*", 
                            "*", "else", "elif", "print", "console", "await", "yield",
                            "throw", "raise", "break", "continue", "pass")):
        return None

    result = None

    if lang == "python":
        match = re.match(r'(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)', stripped)
        if match:
            result = {"name": match.group(1), "params": match.group(2), "class": parent, "line": line_num}
            if match.group(1) == "__init__" and parent:
                result["is_constructor"] = True

    elif lang in ("js", "svelte", "vue"):
        # constructor(params)
        con_match = re.match(r'constructor\s*\(([^)]*)\)', stripped)
        if con_match and current_classes:
            return {"name": "constructor", "params": con_match.group(1), "class": parent,
                    "line": line_num, "is_constructor": True}

        # function name( or async function name(
        func_match = re.match(r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)', stripped)
        if func_match:
            return {"name": func_match.group(1), "params": func_match.group(2), "class": None, "line": line_num}

        # const/let/var name = (async?) (...) => or (async?) function
        arrow_match = re.match(r'(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*(?:=>|{)', stripped)
        if arrow_match:
            return {"name": arrow_match.group(1), "params": arrow_match.group(2), "class": None, "line": line_num}

        # Class method: name(params) { or async name(params) {
        method_match = re.match(r'(async\s+)?(\w+)\s*\(([^)]*)\)\s*(?::\s*\w+(?:<[^>]+>)?\s*)?\{?', stripped)
        if method_match:
            fname = method_match.group(2)
            if fname not in ("if", "for", "while", "switch", "catch", "else", "return", "new", "typeof", "instanceof"):
                result = {"name": fname, "params": method_match.group(3), "class": parent, "line": line_num}
                if method_match.group(1):
                    result["async"] = True

    elif lang == "go":
        match = re.match(r'func\s+(?:\((\w+)\s+\*?(\w+)\)\s+)?(\w+)\s*\(([^)]*)\)', stripped)
        if match:
            receiver_type = match.group(2)
            fname = match.group(3)
            params = match.group(4)
            result = {"name": fname, "params": params, "class": receiver_type, "line": line_num}

    elif lang == "rust":
        match = re.match(r'(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*\(([^)]*)\)', stripped)
        if match:
            result = {"name": match.group(1), "params": match.group(2), "class": parent, "line": line_num}
            if match.group(1) == "new" and parent:
                result["is_constructor"] = True

    elif lang == "java":  # Also covers C#, Kotlin-ish
        match = re.match(
            r'(?:@\w+\s+)*(?:public|private|protected|internal|static|override|virtual|abstract|suspend|open|\s)*'
            r'(?:\w+(?:<[^>]+>)?\s+)?(\w+)\s*\(([^)]*)\)',
            stripped
        )
        if match:
            fname = match.group(1)
            if fname not in ("if", "for", "while", "switch", "catch", "return", "new", "else"):
                result = {"name": fname, "params": match.group(2), "class": parent, "line": line_num}
                if fname == parent:  # Java constructor = same name as class
                    result["is_constructor"] = True

    elif lang == "ruby":
        match = re.match(r'def\s+(?:self\.)?(\w+[?!]?)\s*(?:\(([^)]*)\))?', stripped)
        if match:
            result = {"name": match.group(1), "params": match.group(2) or "", "class": parent, "line": line_num}
            if match.group(1) == "initialize" and parent:
                result["is_constructor"] = True

    elif lang == "php":
        match = re.match(
            r'(?:public|private|protected|static|\s)*function\s+(\w+)\s*\(([^)]*)\)',
            stripped
        )
        if match:
            result = {"name": match.group(1), "params": match.group(2), "class": parent, "line": line_num}
            if match.group(1) == "__construct" and parent:
                result["is_constructor"] = True

    elif lang == "swift":
        match = re.match(
            r'(?:@\w+\s+)*(?:public|private|internal|open|static|override|\s)*func\s+(\w+)\s*\(([^)]*)\)',
            stripped
        )
        if match:
            result = {"name": match.group(1), "params": match.group(2), "class": parent, "line": line_num}
        init_match = re.match(r'(?:public|private|internal|convenience|\s)*init\s*\(([^)]*)\)', stripped)
        if init_match and parent:
            return {"name": "init", "params": init_match.group(1), "class": parent,
                    "line": line_num, "is_constructor": True}

    elif lang == "dart":
        match = re.match(
            r'(?:static\s+)?(?:\w+(?:<[^>]+>)?\s+)?(\w+)\s*\(([^)]*)\)\s*(?:async\s*)?\{?',
            stripped
        )
        if match:
            fname = match.group(1)
            if fname not in ("if", "for", "while", "switch", "catch", "return"):
                result = {"name": fname, "params": match.group(2), "class": parent, "line": line_num}
                if fname == parent:
                    result["is_constructor"] = True

    elif lang == "kotlin":
        match = re.match(
            r'(?:public|private|internal|protected|override|suspend|open|\s)*fun\s+(\w+)\s*\(([^)]*)\)',
            stripped
        )
        if match:
            result = {"name": match.group(1), "params": match.group(2), "class": parent, "line": line_num}

    return result


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
            # Idempotent: if the function already exists, replace it instead of duplicating
            func_name = target  # target may contain the function name
            if not func_name:
                # Try to extract function name from content
                import re as _re
                name_match = _re.search(
                    r'(?:export\s+)?(?:async\s+)?(?:function\s+|(?:const|let|var)\s+)(\w+)',
                    content.strip()
                )
                if name_match:
                    func_name = name_match.group(1)

            if func_name:
                existing_start, existing_end = _find_function_bounds(lines, func_name)
                if existing_start is not None:
                    # Function already exists — replace instead of duplicate
                    lines[existing_start:existing_end] = [content + "\n"]
                    _write_lines(full_path, lines)
                    return {"success": True, "path": path, "action": "replaced_existing", "function": func_name}

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