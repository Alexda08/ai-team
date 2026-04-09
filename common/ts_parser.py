"""
Universal code parser using tree-sitter with regex fallback.

Usage:
    from common.ts_parser import extract_exports, extract_imports, extract_declarations

Each function takes (content: str, file_path: str) and returns structured data.
If tree-sitter grammars are installed, uses AST parsing. Otherwise falls back to regex.

Install grammars (one-time):
    pip install tree-sitter tree-sitter-python tree-sitter-javascript tree-sitter-typescript
    pip install tree-sitter-go tree-sitter-rust tree-sitter-java tree-sitter-c-sharp
"""

import os, re

# ═══════════════════════════════════════════════
# TREE-SITTER SETUP
# ═══════════════════════════════════════════════

_TS_AVAILABLE = False
_TS_PARSERS = {}

try:
    import tree_sitter
    # Try loading common language grammars
    _GRAMMAR_MAP = {}
    
    _LANG_PACKAGES = {
        "python": "tree_sitter_python",
        "javascript": "tree_sitter_javascript",
        "typescript": "tree_sitter_typescript",
        "go": "tree_sitter_go",
        "rust": "tree_sitter_rust",
        "java": "tree_sitter_java",
        "c_sharp": "tree_sitter_c_sharp",
        "ruby": "tree_sitter_ruby",
        "php": "tree_sitter_php",
    }

    for lang_name, pkg_name in _LANG_PACKAGES.items():
        try:
            mod = __import__(pkg_name)
            if lang_name == "typescript":
                # typescript package exposes .typescript() and .tsx()
                lang_fn = getattr(mod, "language_typescript", None) or getattr(mod, "typescript", None)
                if callable(lang_fn):
                    _GRAMMAR_MAP["typescript"] = lang_fn()
                tsx_fn = getattr(mod, "language_tsx", None) or getattr(mod, "tsx", None)
                if callable(tsx_fn):
                    _GRAMMAR_MAP["tsx"] = tsx_fn()
            else:
                lang_fn = getattr(mod, f"language_{lang_name}", None) or getattr(mod, "language", None)
                if callable(lang_fn):
                    _GRAMMAR_MAP[lang_name] = lang_fn()
        except (ImportError, AttributeError, Exception):
            continue

    if _GRAMMAR_MAP:
        for lang_name, language in _GRAMMAR_MAP.items():
            try:
                parser = tree_sitter.Parser()
                parser.language = language
                _TS_PARSERS[lang_name] = parser
            except (TypeError, Exception):
                continue
        _TS_AVAILABLE = len(_TS_PARSERS) > 0
        
except ImportError:
    pass


# ═══════════════════════════════════════════════
# LANGUAGE DETECTION
# ═══════════════════════════════════════════════

_EXT_TO_LANG = {
    ".py": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".jsx": "javascript",
    ".svelte": "svelte", ".vue": "vue",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cs": "c_sharp",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin", ".kts": "kotlin",
    ".swift": "swift",
    ".dart": "dart",
}

def _detect_lang(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    return _EXT_TO_LANG.get(ext)


def _get_script_content(content, lang):
    """Extract <script> block for component files."""
    if lang in ("svelte", "vue"):
        m = re.search(r'<script[^>]*>(.*?)</script>', content, re.DOTALL)
        if m:
            return m.group(1), "typescript"  # treat script block as TS
    return content, lang


# ═══════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════

def extract_exports(content, file_path):
    """
    Extract exported names from a file.
    Returns: set of exported name strings, or None if can't parse.
    """
    lang = _detect_lang(file_path)
    if not lang:
        return None

    script, parse_lang = _get_script_content(content, lang)

    # Try tree-sitter first
    if _TS_AVAILABLE and parse_lang in _TS_PARSERS:
        result = _ts_extract_exports(script, parse_lang)
        if result is not None:
            return result

    # Regex fallback
    return _regex_extract_exports(script, parse_lang)


def extract_imports(content, file_path):
    """
    Extract import statements from a file.
    Returns: list of {"raw_path": str, "names": [str], "is_type": bool}
    """
    lang = _detect_lang(file_path)
    if not lang:
        return []

    script, parse_lang = _get_script_content(content, lang)

    if _TS_AVAILABLE and parse_lang in _TS_PARSERS:
        result = _ts_extract_imports(script, parse_lang)
        if result is not None:
            return result

    return _regex_extract_imports(script, parse_lang)


def extract_declarations(content, file_path):
    """
    Extract classes, functions, interfaces, type aliases.
    Returns: {
        "classes": [{"name": str, "line": int}],
        "functions": [{"name": str, "params": str, "line": int, "is_constructor": bool}],
        "interfaces": [{"name": str, "fields": {"all": set, "required": set, "optional": set}}],
    }
    """
    lang = _detect_lang(file_path)
    if not lang:
        return {"classes": [], "functions": [], "interfaces": []}

    script, parse_lang = _get_script_content(content, lang)

    if _TS_AVAILABLE and parse_lang in _TS_PARSERS:
        result = _ts_extract_declarations(script, parse_lang)
        if result is not None:
            return result

    return _regex_extract_declarations(script, parse_lang)


def is_available():
    """Check if tree-sitter is available."""
    return _TS_AVAILABLE


def supported_languages():
    """Return list of languages with tree-sitter parsers loaded."""
    return list(_TS_PARSERS.keys())


# ═══════════════════════════════════════════════
# TREE-SITTER IMPLEMENTATIONS
# ═══════════════════════════════════════════════

def _ts_parse(content, lang):
    """Parse content and return tree-sitter tree."""
    parser = _TS_PARSERS.get(lang)
    if not parser:
        return None
    try:
        tree = parser.parse(content.encode("utf-8"))
        return tree
    except Exception:
        return None


def _ts_extract_exports(content, lang):
    """Extract exports using tree-sitter AST."""
    tree = _ts_parse(content, lang)
    if not tree:
        return None
    
    names = set()
    root = tree.root_node

    if lang == "python":
        # Python: all top-level def/class names are exports
        for child in root.children:
            if child.type in ("function_definition", "class_definition"):
                name_node = child.child_by_field_name("name")
                if name_node:
                    names.add(name_node.text.decode())
            elif child.type == "decorated_definition":
                inner = child.child_by_field_name("definition")
                if inner:
                    name_node = inner.child_by_field_name("name")
                    if name_node:
                        names.add(name_node.text.decode())

    elif lang in ("javascript", "typescript", "tsx"):
        _ts_walk_js_exports(root, names, content)

    elif lang == "go":
        # Go: capitalized top-level functions/types
        for child in root.children:
            if child.type == "function_declaration":
                name_node = child.child_by_field_name("name")
                if name_node:
                    name = name_node.text.decode()
                    if name[0].isupper():
                        names.add(name)
            elif child.type == "type_declaration":
                for spec in child.children:
                    if spec.type == "type_spec":
                        name_node = spec.child_by_field_name("name")
                        if name_node:
                            name = name_node.text.decode()
                            if name[0].isupper():
                                names.add(name)

    elif lang == "rust":
        for child in root.children:
            if child.type in ("function_item", "struct_item", "enum_item", "trait_item", "impl_item", "type_item"):
                text = child.text.decode()
                if text.startswith("pub"):
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        names.add(name_node.text.decode())

    return names if names else None


def _ts_walk_js_exports(node, names, content):
    """Walk JS/TS AST to find all export declarations."""
    for child in node.children:
        if child.type == "export_statement":
            # export function X, export class X, export const X, export async function X
            decl = child.child_by_field_name("declaration")
            if decl:
                name_node = decl.child_by_field_name("name")
                if name_node:
                    names.add(name_node.text.decode())
                # export const x = ..., export let x = ...
                if decl.type == "lexical_declaration":
                    for declarator in decl.children:
                        if declarator.type == "variable_declarator":
                            name_n = declarator.child_by_field_name("name")
                            if name_n:
                                names.add(name_n.text.decode())
            
            # export { X, Y, Z }
            for spec_list in child.children:
                if spec_list.type == "export_clause":
                    for spec in spec_list.children:
                        if spec.type == "export_specifier":
                            name_node = spec.child_by_field_name("name")
                            if name_node:
                                names.add(name_node.text.decode())
            
            # export default X
            if child.child_by_field_name("value"):
                val = child.child_by_field_name("value")
                if val and val.type == "identifier":
                    names.add(val.text.decode())

        # module.exports = { ... } or module.exports = X
        elif child.type == "expression_statement":
            text = child.text.decode().strip()
            if "module.exports" in text:
                # module.exports = { A, B, C }
                m = re.search(r'module\.exports\s*=\s*\{([^}]+)\}', text)
                if m:
                    for name in m.group(1).split(","):
                        clean = name.strip().split(":")[0].strip()
                        if clean:
                            names.add(clean)
                else:
                    # module.exports = ClassName
                    m = re.search(r'module\.exports\s*=\s*(\w+)', text)
                    if m:
                        names.add(m.group(1))


def _ts_extract_imports(content, lang):
    """Extract imports using tree-sitter AST."""
    tree = _ts_parse(content, lang)
    if not tree:
        return None
    
    imports = []
    root = tree.root_node

    if lang == "python":
        for child in root.children:
            if child.type == "import_from_statement":
                module_node = child.child_by_field_name("module_name")
                raw_path = module_node.text.decode() if module_node else ""
                imported_names = []
                for n in child.children:
                    if n.type == "dotted_name" and n != module_node:
                        imported_names.append(n.text.decode())
                    elif n.type == "aliased_import":
                        name_n = n.child_by_field_name("name")
                        if name_n:
                            imported_names.append(name_n.text.decode())
                imports.append({"raw_path": raw_path, "names": imported_names, "is_type": False})

            elif child.type == "import_statement":
                for n in child.children:
                    if n.type == "dotted_name":
                        imports.append({"raw_path": n.text.decode(), "names": [], "is_type": False})

    elif lang in ("javascript", "typescript", "tsx"):
        for child in root.children:
            if child.type == "import_statement":
                is_type = False
                source = child.child_by_field_name("source")
                raw_path = source.text.decode().strip("'\"") if source else ""
                
                # Check for import type { ... }
                text = child.text.decode()
                if text.strip().startswith("import type"):
                    is_type = True

                imported_names = []
                for n in child.children:
                    if n.type == "import_clause":
                        for spec_list in n.children:
                            if spec_list.type == "named_imports":
                                for spec in spec_list.children:
                                    if spec.type == "import_specifier":
                                        name_n = spec.child_by_field_name("name")
                                        if name_n:
                                            imported_names.append(name_n.text.decode())
                            elif spec_list.type == "identifier":
                                imported_names.append(spec_list.text.decode())

                imports.append({"raw_path": raw_path, "names": imported_names, "is_type": is_type})

    return imports


def _ts_extract_declarations(content, lang):
    """Extract declarations using tree-sitter AST."""
    tree = _ts_parse(content, lang)
    if not tree:
        return None

    classes = []
    functions = []
    interfaces = []
    root = tree.root_node

    if lang in ("javascript", "typescript", "tsx"):
        _ts_walk_js_declarations(root, classes, functions, interfaces, content)
    elif lang == "python":
        _ts_walk_py_declarations(root, classes, functions)

    return {"classes": classes, "functions": functions, "interfaces": interfaces}


def _ts_walk_js_declarations(node, classes, functions, interfaces, content):
    """Walk JS/TS AST for declarations."""
    for child in node.children:
        actual = child
        # Unwrap export_statement
        if child.type == "export_statement":
            decl = child.child_by_field_name("declaration")
            if decl:
                actual = decl

        if actual.type in ("class_declaration", "abstract_class_declaration"):
            name_node = actual.child_by_field_name("name")
            if name_node:
                classes.append({"name": name_node.text.decode(), "line": actual.start_point[0] + 1})

        elif actual.type in ("function_declaration",):
            name_node = actual.child_by_field_name("name")
            params_node = actual.child_by_field_name("parameters")
            if name_node:
                functions.append({
                    "name": name_node.text.decode(),
                    "params": params_node.text.decode() if params_node else "()",
                    "line": actual.start_point[0] + 1,
                    "is_constructor": False
                })

        elif actual.type == "lexical_declaration":
            for declarator in actual.children:
                if declarator.type == "variable_declarator":
                    name_n = declarator.child_by_field_name("name")
                    value = declarator.child_by_field_name("value")
                    if name_n and value and value.type in ("arrow_function", "function"):
                        params = value.child_by_field_name("parameters")
                        functions.append({
                            "name": name_n.text.decode(),
                            "params": params.text.decode() if params else "()",
                            "line": actual.start_point[0] + 1,
                            "is_constructor": False
                        })

        elif actual.type == "interface_declaration":
            name_node = actual.child_by_field_name("name")
            if name_node:
                iface_name = name_node.text.decode()
                fields_all = set()
                fields_optional = set()
                body = actual.child_by_field_name("body")
                if body:
                    for prop in body.children:
                        if prop.type == "property_signature":
                            prop_name = prop.child_by_field_name("name")
                            if prop_name:
                                fname = prop_name.text.decode()
                                fields_all.add(fname)
                                # Check for ? after name
                                ptext = prop.text.decode()
                                if re.match(rf'{re.escape(fname)}\s*\?', ptext):
                                    fields_optional.add(fname)
                interfaces.append({
                    "name": iface_name,
                    "fields": {
                        "all": fields_all,
                        "required": fields_all - fields_optional,
                        "optional": fields_optional
                    }
                })

        elif actual.type == "type_alias_declaration":
            name_node = actual.child_by_field_name("name")
            if name_node:
                classes.append({"name": name_node.text.decode(), "line": actual.start_point[0] + 1})

        # Recurse into module blocks (for namespace/module patterns)
        if actual.type in ("program", "statement_block"):
            _ts_walk_js_declarations(actual, classes, functions, interfaces, content)


def _ts_walk_py_declarations(node, classes, functions):
    """Walk Python AST for declarations."""
    for child in node.children:
        actual = child
        if child.type == "decorated_definition":
            actual = child.child_by_field_name("definition") or child

        if actual.type == "class_definition":
            name_node = actual.child_by_field_name("name")
            if name_node:
                classes.append({"name": name_node.text.decode(), "line": actual.start_point[0] + 1})
            # Recurse into class body for methods
            body = actual.child_by_field_name("body")
            if body:
                for method in body.children:
                    m = method
                    if m.type == "decorated_definition":
                        m = m.child_by_field_name("definition") or m
                    if m.type == "function_definition":
                        mn = m.child_by_field_name("name")
                        mp = m.child_by_field_name("parameters")
                        if mn:
                            fname = mn.text.decode()
                            functions.append({
                                "name": fname,
                                "params": mp.text.decode() if mp else "()",
                                "line": m.start_point[0] + 1,
                                "is_constructor": fname in ("__init__", "__new__"),
                                "class": name_node.text.decode()
                            })

        elif actual.type == "function_definition":
            name_node = actual.child_by_field_name("name")
            params_node = actual.child_by_field_name("parameters")
            if name_node:
                functions.append({
                    "name": name_node.text.decode(),
                    "params": params_node.text.decode() if params_node else "()",
                    "line": actual.start_point[0] + 1,
                    "is_constructor": False
                })


# ═══════════════════════════════════════════════
# REGEX FALLBACK IMPLEMENTATIONS
# ═══════════════════════════════════════════════

def _regex_extract_exports(content, lang):
    """Regex fallback for export extraction."""
    names = set()

    if lang == "python":
        # All top-level def/class
        for m in re.finditer(r'^(?:async\s+)?def\s+(\w+)', content, re.MULTILINE):
            names.add(m.group(1))
        for m in re.finditer(r'^class\s+(\w+)', content, re.MULTILINE):
            names.add(m.group(1))

    elif lang in ("javascript", "typescript", "tsx", "svelte", "vue"):
        # module.exports = { A, B }
        m = re.search(r'module\.exports\s*=\s*\{([^}]+)\}', content)
        if m:
            for name in m.group(1).split(","):
                clean = name.strip().split(":")[0].strip()
                if clean:
                    names.add(clean)

        # module.exports = X
        m = re.search(r'module\.exports\s*=\s*(\w+)\s*;?\s*$', content, re.MULTILINE)
        if m:
            names.add(m.group(1))

        # export { X, Y }
        for m in re.finditer(r'export\s+\{([^}]+)\}', content):
            for name in m.group(1).split(","):
                clean = name.strip().split(" as ")[0].strip()
                if clean:
                    names.add(clean)

        # export [async] [default] [class|function|const|let|var|interface|type|enum] Name
        for m in re.finditer(
            r'export\s+(?:default\s+)?(?:async\s+)?(?:abstract\s+)?'
            r'(?:class|function|const|let|var|interface|type|enum)\s+(\w+)',
            content
        ):
            names.add(m.group(1))

    elif lang == "go":
        for m in re.finditer(r'^func\s+(?:\([^)]*\)\s+)?([A-Z]\w*)', content, re.MULTILINE):
            names.add(m.group(1))
        for m in re.finditer(r'^type\s+([A-Z]\w*)', content, re.MULTILINE):
            names.add(m.group(1))

    elif lang == "rust":
        for m in re.finditer(r'^pub\s+(?:async\s+)?(?:fn|struct|enum|trait|type|const|static)\s+(\w+)', content, re.MULTILINE):
            names.add(m.group(1))

    elif lang == "java":
        for m in re.finditer(r'public\s+(?:static\s+)?(?:abstract\s+)?(?:class|interface|enum)\s+(\w+)', content):
            names.add(m.group(1))

    elif lang == "ruby":
        for m in re.finditer(r'^(?:class|module)\s+(\w+)', content, re.MULTILINE):
            names.add(m.group(1))

    return names if names else None


def _regex_extract_imports(content, lang):
    """Regex fallback for import extraction."""
    imports = []

    if lang == "python":
        for m in re.finditer(r'^from\s+([\w.]+)\s+import\s+(.+)$', content, re.MULTILINE):
            names = [n.strip().split(" as ")[0] for n in m.group(2).split(",")]
            imports.append({"raw_path": m.group(1), "names": names, "is_type": False})
        for m in re.finditer(r'^import\s+([\w.]+)', content, re.MULTILINE):
            imports.append({"raw_path": m.group(1), "names": [], "is_type": False})

    elif lang in ("javascript", "typescript", "tsx"):
        # import type { X } from 'path'
        for m in re.finditer(r"import\s+type\s+\{\s*([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]", content):
            names = [n.strip() for n in m.group(1).split(",")]
            imports.append({"raw_path": m.group(2), "names": names, "is_type": True})

        # import { X, Y } from 'path'
        for m in re.finditer(r"import\s+\{\s*([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]", content):
            # Skip if already caught as type import
            if f"import type" in content[max(0, m.start()-20):m.start()]:
                continue
            names = [n.strip() for n in m.group(1).split(",")]
            imports.append({"raw_path": m.group(2), "names": names, "is_type": False})

        # import X from 'path'
        for m in re.finditer(r"import\s+(\w+)\s+from\s+['\"]([^'\"]+)['\"]", content):
            imports.append({"raw_path": m.group(2), "names": [m.group(1)], "is_type": False})

        # require()
        for m in re.finditer(r"(?:const|let|var)\s+\{\s*([^}]+)\}\s*=\s*require\(['\"]([^'\"]+)['\"]\)", content):
            names = [n.strip() for n in m.group(1).split(",")]
            imports.append({"raw_path": m.group(2), "names": names, "is_type": False})

        for m in re.finditer(r"(?:const|let|var)\s+(\w+)\s*=\s*require\(['\"]([^'\"]+)['\"]\)", content):
            if "{" not in m.group(0):
                imports.append({"raw_path": m.group(2), "names": [m.group(1)], "is_type": False})

    elif lang == "go":
        for m in re.finditer(r'"([^"]+)"', content):
            path = m.group(1)
            if "/" in path:
                imports.append({"raw_path": path, "names": [], "is_type": False})

    elif lang == "rust":
        for m in re.finditer(r'use\s+([\w:]+)(?:::\{([^}]+)\})?', content):
            base = m.group(1)
            if m.group(2):
                names = [n.strip() for n in m.group(2).split(",")]
            else:
                names = [base.split("::")[-1]]
            imports.append({"raw_path": base, "names": names, "is_type": False})

    return imports


def _regex_extract_declarations(content, lang):
    """Regex fallback for declaration extraction."""
    classes = []
    functions = []
    interfaces = []

    lines = content.split("\n")

    if lang == "python":
        for i, line in enumerate(lines, 1):
            s = line.strip()
            m = re.match(r'class\s+(\w+)', s)
            if m:
                classes.append({"name": m.group(1), "line": i})
            m = re.match(r'(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)', s)
            if m:
                functions.append({"name": m.group(1), "params": m.group(2), "line": i,
                                  "is_constructor": m.group(1) in ("__init__", "__new__")})

    elif lang in ("javascript", "typescript", "tsx"):
        for i, line in enumerate(lines, 1):
            s = line.strip()

            # class/interface/type/enum
            m = re.match(
                r'(?:export\s+)?(?:default\s+)?(?:abstract\s+)?'
                r'(class|interface|type|enum)\s+(\w+)', s)
            if m:
                kind = m.group(1)
                name = m.group(2)
                if kind == "interface":
                    # Parse interface fields
                    iface_fields = _regex_parse_interface_fields(content, name)
                    interfaces.append({"name": name, "fields": iface_fields})
                else:
                    classes.append({"name": name, "line": i})
                continue

            # function declarations + arrow functions
            m = re.match(
                r'(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)', s)
            if m:
                functions.append({"name": m.group(1), "params": m.group(2), "line": i,
                                  "is_constructor": m.group(1) == "constructor"})
                continue

            m = re.match(
                r'(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*(?:=>|{)', s)
            if m:
                functions.append({"name": m.group(1), "params": m.group(2), "line": i,
                                  "is_constructor": False})
                continue

            # class method
            m = re.match(r'(?:async\s+)?(\w+)\s*\(([^)]*)\)\s*(?::\s*\S+\s*)?\{', s)
            if m and not s.startswith(("if", "for", "while", "switch", "catch")):
                functions.append({"name": m.group(1), "params": m.group(2), "line": i,
                                  "is_constructor": m.group(1) == "constructor"})

    return {"classes": classes, "functions": functions, "interfaces": interfaces}


def _regex_parse_interface_fields(content, interface_name):
    """Parse TypeScript interface fields using regex."""
    fields_all = set()
    fields_optional = set()

    m = re.search(rf'interface\s+{re.escape(interface_name)}\s*\{{([^}}]+)\}}', content, re.DOTALL)
    if m:
        for line in m.group(1).split("\n"):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            fm = re.match(r'(\w+)\s*\?\s*:', line)
            if fm:
                fields_all.add(fm.group(1))
                fields_optional.add(fm.group(1))
                continue
            fm = re.match(r'(\w+)\s*:', line)
            if fm:
                fields_all.add(fm.group(1))

    return {"all": fields_all, "required": fields_all - fields_optional, "optional": fields_optional}