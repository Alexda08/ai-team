import os, re, json
from agents.base_agent import BaseAgent
from common.tools import read_file, list_files, file_summary
from common.utils import Utils

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_PATH = os.path.join(BASE_DIR, "workspace")

FINAL_VALIDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["PASSED", "FAILED"]},
        "fixes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "method": {"type": "string"},
                    "issue": {"type": "string"},
                    "action": {"type": "string", "enum": ["replace_function", "add_function", "add_import", "create"]},
                    "description": {"type": "string"}
                },
                "required": ["file", "issue", "action", "description"]
            }
        }
    },
    "required": ["status", "fixes"]
}


class ValidatorAgent(BaseAgent):

    def __init__(self, name, system_prompt, llm):
        super().__init__(name, system_prompt, llm)

    # ═══════════════════════════════════════════════
    # TASK VALIDATION (per-task, after each coder run)
    # ═══════════════════════════════════════════════

    def validate_task(self, task, coder_result):
        written_files = self._get_written_files(coder_result)
        if not written_files:
            return {"status": "FAILED", "issues": "No files were written by the CODER.", "files_checked": []}

        # Phase 1: Programmatic checks on written files
        programmatic_issues = []
        for file_path in written_files:
            issues = self._check_file_integrity(file_path, task)
            programmatic_issues.extend(issues)

        # Phase 2: Cross-file checks (imports resolve, exports match)
        cross_issues = self._check_cross_file(written_files, task)
        programmatic_issues.extend(cross_issues)

        # If programmatic checks found critical issues, fail fast
        critical = [i for i in programmatic_issues if i.get("severity") == "critical"]
        if critical:
            issues_text = "; ".join(i["message"] for i in critical)
            return {"status": "FAILED", "issues": issues_text, "files_checked": written_files}

        # Phase 3: LLM validation (does the code match the task description?)
        file_contents = {}
        for file_path in written_files:
            result = read_file(file_path, WORKSPACE_PATH)
            if result["success"]:
                file_contents[file_path] = result["content"]
            else:
                file_contents[file_path] = f"[COULD NOT READ: {result['error']}]"

        # Include dependency context for cross-file validation
        dep_context = self._get_dependency_context(written_files, task)

        prompt = self._build_task_validation_prompt(task, file_contents, programmatic_issues, dep_context)
        response = self.llm.generate(system=self.system_prompt, messages=[{"role": "user", "content": prompt}])
        return self._parse_response(response, written_files)

    # ═══════════════════════════════════════════════
    # PROGRAMMATIC CHECKS (no LLM needed)
    # ═══════════════════════════════════════════════

    def _check_file_integrity(self, file_path, task):
        """Check a single file for structural problems."""
        issues = []
        result = read_file(file_path, WORKSPACE_PATH)
        if not result["success"]:
            issues.append({"severity": "critical", "file": file_path,
                           "message": f"{file_path}: file not readable — {result['error']}"})
            return issues

        content = result["content"]
        lines = content.split("\n")

        # Check: file is not empty (besides whitespace)
        if not content.strip():
            issues.append({"severity": "critical", "file": file_path,
                           "message": f"{file_path}: file is empty"})
            return issues

        # Check: no syntax-breaking patterns
        # Python: unclosed strings, mismatched brackets
        if file_path.endswith(".py"):
            issues.extend(self._check_python_syntax(file_path, content))
        # JS/TS: basic checks
        elif file_path.endswith((".js", ".ts", ".tsx", ".jsx", ".svelte", ".vue")):
            issues.extend(self._check_js_syntax(file_path, content))

        # Check: export statement exists (for non-trivial files)
        if file_path.endswith((".js", ".ts")) and len(lines) > 5:
            has_export = any("module.exports" in l or "export " in l for l in lines)
            if not has_export:
                issues.append({"severity": "warning", "file": file_path,
                               "message": f"{file_path}: no export statement found"})

        return issues

    def _check_python_syntax(self, file_path, content):
        """Quick Python syntax checks without executing."""
        issues = []
        try:
            compile(content, file_path, "exec")
        except SyntaxError as e:
            issues.append({"severity": "critical", "file": file_path,
                           "message": f"{file_path}:{e.lineno}: SyntaxError — {e.msg}"})
        return issues

    def _check_js_syntax(self, file_path, content):
        """Quick JS structural checks."""
        issues = []
        # Check bracket balance
        opens = content.count("{") + content.count("[") + content.count("(")
        closes = content.count("}") + content.count("]") + content.count(")")
        if abs(opens - closes) > 2:  # Small tolerance for template literals etc
            issues.append({"severity": "warning", "file": file_path,
                           "message": f"{file_path}: bracket imbalance (open={opens}, close={closes})"})
        return issues

    def _check_cross_file(self, written_files, task):
        """Check that imports in written files resolve to real exports in workspace."""
        issues = []

        for file_path in written_files:
            result = read_file(file_path, WORKSPACE_PATH)
            if not result["success"]:
                continue
            content = result["content"]

            # Extract imports
            imports = self._extract_imports(content, file_path)

            for imp in imports:
                resolved_path = imp.get("resolved_path")
                imported_names = imp.get("names", [])

                if not resolved_path:
                    continue  # External package, skip

                # Check file exists
                full_resolved = os.path.join(WORKSPACE_PATH, resolved_path)
                # Try with common extensions
                found_path = self._resolve_import_path(resolved_path)
                if not found_path:
                    issues.append({"severity": "critical", "file": file_path,
                                   "message": f"{file_path}: imports from '{imp['raw_path']}' but file not found in workspace"})
                    continue

                # Check named imports match exports
                if imported_names:
                    export_names = self._get_export_names(found_path)
                    for name in imported_names:
                        if export_names and name not in export_names:
                            issues.append({"severity": "critical", "file": file_path,
                                           "message": f"{file_path}: imports '{name}' from '{imp['raw_path']}' but '{found_path}' exports {export_names}"})

        return issues

    def _extract_imports(self, content, file_path):
        """Extract import statements and resolve to workspace paths."""
        imports = []

        if file_path.endswith(".py"):
            for match in re.finditer(r'^from\s+([\w.]+)\s+import\s+(.+)$', content, re.MULTILINE):
                module = match.group(1)
                names = [n.strip().split(" as ")[0] for n in match.group(2).split(",")]
                rel_path = module.replace(".", "/") + ".py"
                imports.append({"raw_path": module, "resolved_path": rel_path, "names": names})

            for match in re.finditer(r'^import\s+([\w.]+)', content, re.MULTILINE):
                module = match.group(1)
                rel_path = module.replace(".", "/") + ".py"
                imports.append({"raw_path": module, "resolved_path": rel_path, "names": []})

        elif file_path.endswith((".js", ".ts", ".tsx", ".jsx", ".svelte", ".vue")):
            # require() destructured
            for match in re.finditer(r"(?:const|let|var)\s+\{\s*([^}]+)\}\s*=\s*require\(['\"]([^'\"]+)['\"]\)", content):
                names = [n.strip() for n in match.group(1).split(",")]
                resolved = self._resolve_path_or_alias(match.group(2), file_path)
                if resolved:
                    imports.append({"raw_path": match.group(2), "resolved_path": resolved, "names": names})

            # require() default
            for match in re.finditer(r"(?:const|let|var)\s+(\w+)\s*=\s*require\(['\"]([^'\"]+)['\"]\)", content):
                if "{" not in match.group(0):
                    resolved = self._resolve_path_or_alias(match.group(2), file_path)
                    if resolved:
                        imports.append({"raw_path": match.group(2), "resolved_path": resolved, "names": [match.group(1)]})

            # import { X } from 'path'
            for match in re.finditer(r"import\s+\{\s*([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]", content):
                names = [n.strip() for n in match.group(1).split(",")]
                resolved = self._resolve_path_or_alias(match.group(2), file_path)
                if resolved:
                    imports.append({"raw_path": match.group(2), "resolved_path": resolved, "names": names})

            # import X from 'path'
            for match in re.finditer(r"import\s+(\w+)\s+from\s+['\"]([^'\"]+)['\"]", content):
                resolved = self._resolve_path_or_alias(match.group(2), file_path)
                if resolved:
                    imports.append({"raw_path": match.group(2), "resolved_path": resolved, "names": [match.group(1)]})

            # import type { X } from 'path' — track for cohesion but mark as type
            for match in re.finditer(r"import\s+type\s+\{\s*([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]", content):
                names = [f"type {n.strip()}" for n in match.group(1).split(",")]
                resolved = self._resolve_path_or_alias(match.group(2), file_path)
                if resolved:
                    imports.append({"raw_path": match.group(2), "resolved_path": resolved, "names": names})

        return imports

    def _resolve_path_or_alias(self, raw_path, importing_file):
        """Resolve relative path or framework alias. Returns None for external packages."""
        # SvelteKit auto-generated virtual modules — skip
        if raw_path in ("./$types", "$types") or raw_path.endswith("/$types"):
            return None
        if raw_path.startswith("$app/") or raw_path.startswith("$env/"):
            return None

        if raw_path.startswith("."):
            return self._resolve_relative_path(raw_path, importing_file)

        # Framework aliases
        alias_map = {"$lib/": "src/lib/", "@/": "src/", "~/": "src/"}
        for alias, real in alias_map.items():
            if raw_path.startswith(alias):
                return raw_path.replace(alias, real, 1)

        # External package — not resolvable
        return None

    def _resolve_relative_path(self, raw_path, importing_file):
        """Resolve a relative import path relative to the importing file."""
        dir_of_importer = os.path.dirname(importing_file)
        resolved = os.path.normpath(os.path.join(dir_of_importer, raw_path))
        return resolved.replace("\\", "/")

    def _resolve_import_path(self, path):
        """Try to find the actual file in workspace, trying common extensions."""
        extensions = ["", ".js", ".ts", ".tsx", ".jsx", ".svelte", ".vue", "/index.js", "/index.ts"]
        for ext in extensions:
            candidate = path + ext
            full = os.path.join(WORKSPACE_PATH, candidate)
            if os.path.isfile(full):
                return candidate
        return None

    def _resolve_alias_path(self, raw_path):
        """Resolve framework aliases like $lib/ to workspace paths."""
        alias_map = {
            "$lib/": "src/lib/",
            "$lib\\": "src/lib/",
            "@/": "src/",
            "~/": "src/",
        }
        for alias, real in alias_map.items():
            if raw_path.startswith(alias):
                return raw_path.replace(alias, real, 1)
        return None

    def _get_export_names(self, file_path):
        """Extract exported names from a file."""
        result = read_file(file_path, WORKSPACE_PATH)
        if not result["success"]:
            return None  # Can't check, skip
        content = result["content"]
        names = set()

        # Python: defined at module level
        if file_path.endswith(".py"):
            summ = file_summary(file_path, WORKSPACE_PATH)
            if summ.get("success"):
                for cls in summ["summary"].get("classes", []):
                    names.add(cls["name"])
                for func in summ["summary"].get("functions", []):
                    if not func.get("class"):  # Top-level functions only
                        names.add(func["name"])

        # JS: module.exports = { X, Y }
        elif file_path.endswith((".js", ".ts", ".tsx", ".jsx", ".svelte", ".vue")):
            # Named exports: module.exports = { A, B, C }
            match = re.search(r"module\.exports\s*=\s*\{([^}]+)\}", content)
            if match:
                for name in match.group(1).split(","):
                    clean = name.strip().split(":")[0].strip()
                    if clean:
                        names.add(clean)

            # Default export: module.exports = ClassName
            match = re.search(r"module\.exports\s*=\s*(\w+)\s*;?\s*$", content, re.MULTILINE)
            if match:
                names.add(match.group(1))

            # ES6: export { X, Y }
            for match in re.finditer(r"export\s+\{([^}]+)\}", content):
                for name in match.group(1).split(","):
                    clean = name.strip().split(" as ")[0].strip()
                    if clean:
                        names.add(clean)

            # ES6: export class/function/const
            for match in re.finditer(r"export\s+(?:default\s+)?(?:class|function|const|let|var)\s+(\w+)", content):
                names.add(match.group(1))

        return names if names else None

    def _get_dependency_context(self, written_files, task):
        """Get summaries of files that the written files depend on."""
        dep_summaries = []
        seen = set(written_files)

        for file_path in written_files:
            result = read_file(file_path, WORKSPACE_PATH)
            if not result["success"]:
                continue
            imports = self._extract_imports(result["content"], file_path)

            for imp in imports:
                resolved = imp.get("resolved_path")
                if not resolved:
                    continue
                found = self._resolve_import_path(resolved)
                if found and found not in seen:
                    seen.add(found)
                    summ = file_summary(found, WORKSPACE_PATH)
                    if summ.get("success") and isinstance(summ.get("summary"), dict):
                        s = summ["summary"]
                        export_names = self._get_export_names(found)
                        dep_summaries.append(
                            f"  {found}: exports={export_names or '?'}, "
                            f"classes={[c['name'] for c in s.get('classes', []) if isinstance(c, dict)]}, "
                            f"functions={[f['name'] for f in s.get('functions', []) if isinstance(f, dict)]}"
                        )

        return "\n".join(dep_summaries) if dep_summaries else None

    # ═══════════════════════════════════════════════
    # LLM VALIDATION PROMPTS
    # ═══════════════════════════════════════════════

    def _build_task_validation_prompt(self, task, file_contents, programmatic_issues=None, dep_context=None):
        files_block = "\n".join(f"=== {path} ===\n{content}\n=== END ===" for path, content in file_contents.items())

        issues_block = ""
        if programmatic_issues:
            warnings = [i for i in programmatic_issues if i["severity"] == "warning"]
            if warnings:
                issues_block = "\nAUTOMATED CHECKS FOUND WARNINGS:\n" + "\n".join(f"  - {w['message']}" for w in warnings) + "\nConsider these when evaluating.\n"

        dep_block = ""
        if dep_context:
            dep_block = f"\nDEPENDENCY FILES (what this file imports from):\n{dep_context}\nVerify imports match these actual exports.\n"

        return f"""TASK TO VALIDATE:
            Title: {task["title"]}
            Description: {task["description"]}

            CODE PRODUCED:
            {files_block}
            {dep_block}
            {issues_block}
            Validate:
            1. Does the code fully implement what the task description requires?
            2. Do all function/method signatures match what the description specifies?
            3. If this task creates exports, is there a proper export statement?
            4. If this task imports from other files, do the imports match the actual exports of those files?
        """

    # ═══════════════════════════════════════════════
    # PROJECT VALIDATION (final, all files)
    # ═══════════════════════════════════════════════

    def validate_project(self, tasks, completed_tasks):
        all_files = list_files(".", WORKSPACE_PATH)
        if not all_files["success"]:
            return {"status": "FAILED", "fixes": []}

        code_files = [f for f in all_files.get("files", [])
                      if f.endswith(('.py', '.js', '.ts', '.tsx', '.jsx', '.svelte', '.vue', '.json', '.yaml', '.yml', '.sql'))]

        # Phase 1: Programmatic cross-file validation (ALL files)
        prog_fixes = self._programmatic_project_validation(code_files)

        if prog_fixes:
            print(f"  [VALIDATOR] Programmatic checks found {len(prog_fixes)} issues")
            for fix in prog_fixes[:5]:
                print(f"    - {fix['issue'][:120]}")
            return {"status": "FAILED", "fixes": prog_fixes}

        # Phase 2: LLM validation (only if programmatic passes)
        file_contents = {}
        for file_path in code_files:
            if not file_path.endswith(('.json', '.yaml', '.yml', '.sql')):
                result = read_file(file_path, WORKSPACE_PATH)
                if result["success"]:
                    file_contents[file_path] = result["content"]

        file_list = "\n".join(f"  - {p}" for p in file_contents.keys())
        task_summary = "\n".join(
            f"- Task {t['id']}: {t['title']}"
            for t in tasks if t["id"] in completed_tasks
        )

        prompt = f"""
            FINAL PROJECT VALIDATION

            Tasks completed:
            {task_summary}

            EXISTING FILES (use these EXACT paths in fixes):
            {file_list}

            Workspace files:
            {chr(10).join(f'=== {p} ==={chr(10)}{c}{chr(10)}' for p, c in file_contents.items())}

            All automated import/export checks have PASSED. Now check for LOGIC issues only:
            1. Method calls match existing signatures (name, params, return type)
            2. Attribute access matches actual class/dataclass fields
            3. No duplicate or conflicting definitions
            4. Entry point (main) works end-to-end
            5. Constructor calls pass correct arguments in correct order

            If ALL checks pass: status=PASSED, fixes=[]
            If ANY fail: status=FAILED, one fix per issue.
            Only report real bugs, not style issues.
            Return ONLY valid JSON.
        """

        response = self.llm.generate(
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}],
            json_schema=FINAL_VALIDATION_SCHEMA
        )

        try:
            parsed = json.loads(Utils.clean_json(response))
            if "status" in parsed and "fixes" in parsed:
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

        fixes = self._extract_fixes_from_text(response)
        if fixes:
            return {"status": "FAILED", "fixes": fixes}

        if "PASSED" in response.upper() and "FAILED" not in response.upper():
            return {"status": "PASSED", "fixes": []}

        print(f"  [WARN] Could not parse validation response")
        return {"status": "FAILED", "fixes": []}

    def _programmatic_project_validation(self, code_files):
        """Run deterministic cross-file checks on entire project in 6 phases."""
        all_fixes = []

        # Pre-compute: read all files, build maps
        file_contents = {}
        for f in code_files:
            if f.endswith(('.json', '.yaml', '.yml', '.sql')):
                continue
            result = read_file(f, WORKSPACE_PATH)
            if result["success"]:
                file_contents[f] = result["content"]

        export_map = {}
        for f in file_contents:
            names = self._get_export_names(f)
            if names:
                export_map[f] = names

        import_map = {}
        for f, content in file_contents.items():
            imports = self._extract_imports(content, f)
            if imports:
                import_map[f] = imports

        # ── Phase 1: SYNTAX ──────────────────────────────────
        # File-level: empty files, Python compile(), JS bracket balance
        phase1 = self._phase_syntax(file_contents)
        all_fixes.extend(phase1)
        if phase1:
            print(f"  [P1-SYNTAX] {len(phase1)} issues")
            return all_fixes  # Fatal — can't analyze broken files

        # ── Phase 2: STRUCTURE ───────────────────────────────
        # All imports resolve to existing files
        phase2 = self._phase_structure(import_map)
        all_fixes.extend(phase2)
        if phase2:
            print(f"  [P2-STRUCTURE] {len(phase2)} issues")

        # ── Phase 3: COHESION ────────────────────────────────
        # Named imports match actual exports
        phase3 = self._phase_cohesion(import_map, export_map)
        all_fixes.extend(phase3)
        if phase3:
            print(f"  [P3-COHESION] {len(phase3)} issues")

        # ── Phase 4: CONTRACTS ───────────────────────────────
        # fetch() calls match API endpoint response shapes and body signatures
        phase4 = self._phase_contracts(file_contents)
        all_fixes.extend(phase4)
        if phase4:
            print(f"  [P4-CONTRACTS] {len(phase4)} issues")

        # ── Phase 5: TYPES ───────────────────────────────────
        # TS interface fields match store initialization; JSON type usage
        phase5 = self._phase_types(file_contents)
        all_fixes.extend(phase5)
        if phase5:
            print(f"  [P5-TYPES] {len(phase5)} issues")

        # ── Phase 6: CONSISTENCY ─────────────────────────────
        # Export style uniformity, duplicate definitions
        phase6 = self._phase_consistency(file_contents, code_files)
        all_fixes.extend(phase6)
        if phase6:
            print(f"  [P6-CONSISTENCY] {len(phase6)} issues")

        return all_fixes

    # ── Phase 1: SYNTAX ──────────────────────────────────────

    def _phase_syntax(self, file_contents):
        """Check each file compiles / has balanced brackets."""
        fixes = []
        for f, content in file_contents.items():
            if not content.strip():
                fixes.append({"file": f, "issue": f"Empty file: {f}",
                              "action": "create", "description": f"File {f} is empty"})
                continue

            if f.endswith(".py"):
                try:
                    compile(content, f, "exec")
                except SyntaxError as e:
                    fixes.append({"file": f, "issue": f"SyntaxError line {e.lineno}: {e.msg}",
                                  "action": "replace_function",
                                  "description": f"In {f}: fix syntax error at line {e.lineno}"})

            elif f.endswith((".js", ".ts", ".tsx", ".jsx", ".svelte", ".vue")):
                check = content
                if f.endswith((".svelte", ".vue")):
                    m = re.search(r'<script[^>]*>(.*?)</script>', content, re.DOTALL)
                    if m:
                        check = m.group(1)
                opens = check.count("{") + check.count("[") + check.count("(")
                closes = check.count("}") + check.count("]") + check.count(")")
                if abs(opens - closes) > 2:
                    fixes.append({"file": f,
                                  "issue": f"Bracket imbalance in {f} (open={opens}, close={closes})",
                                  "action": "replace_function",
                                  "description": f"In {f}: fix mismatched brackets"})
        return fixes

    # ── Phase 2: STRUCTURE ───────────────────────────────────

    def _phase_structure(self, import_map):
        """All imports resolve to existing workspace files."""
        fixes = []
        for f, imports in import_map.items():
            for imp in imports:
                resolved = imp.get("resolved_path")
                if not resolved:
                    continue
                found = self._resolve_import_path(resolved)
                if not found:
                    fixes.append({
                        "file": f,
                        "issue": f"Broken import: '{imp['raw_path']}' — file not found",
                        "action": "add_import",
                        "description": f"In {f}: import '{imp['raw_path']}' has no matching file"
                    })
        return fixes

    # ── Phase 3: COHESION ────────────────────────────────────

    def _phase_cohesion(self, import_map, export_map):
        """Named imports match actual exports of target files."""
        fixes = []
        for f, imports in import_map.items():
            for imp in imports:
                resolved = imp.get("resolved_path")
                if not resolved:
                    continue
                found = self._resolve_import_path(resolved)
                if not found:
                    continue
                export_names = export_map.get(found)
                if not export_names:
                    continue
                for name in imp.get("names", []):
                    clean = name.strip()
                    if clean.startswith("type "):
                        continue
                    if clean and clean not in export_names:
                        fixes.append({
                            "file": f,
                            "issue": f"Import mismatch: '{clean}' not in '{found}' exports {sorted(export_names)}",
                            "action": "replace_function",
                            "description": f"In {f}: '{clean}' not exported by '{found}'"
                        })
        return fixes

    # ── Phase 4: CONTRACTS ───────────────────────────────────

    def _phase_contracts(self, file_contents):
        """Check fetch() calls match API endpoint signatures."""
        fixes = []
        api_map = self._build_api_map(file_contents)
        if not api_map:
            return fixes

        for f, content in file_contents.items():
            if "/api/" in f and "+server" in f:
                continue

            for match in re.finditer(
                r"""fetch\(\s*[`'"](/api/[^`'"]+)[`'"]\s*(?:,\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\})?\s*\)""",
                content, re.DOTALL
            ):
                url = match.group(1)
                options = match.group(2) or ""

                method_match = re.search(r"method\s*:\s*['\"](\w+)['\"]", options)
                method = method_match.group(1).upper() if method_match else "GET"

                normalized = re.sub(r'/\$\{[^}]+\}', '/[id]', url)
                normalized = re.sub(r'/[a-zA-Z0-9_-]{10,}', '/[id]', normalized)

                endpoint = api_map.get(normalized, {}).get(method)
                if not endpoint:
                    continue

                # POST/PATCH: body fields must include what handler destructures
                if method in ("POST", "PATCH") and endpoint.get("body_destructures"):
                    body_match = re.search(r"body\s*:\s*JSON\.stringify\(\s*\{([^}]+)\}", options, re.DOTALL)
                    if body_match:
                        sent = set()
                        for fld in body_match.group(1).split(","):
                            k = fld.strip().split(":")[0].strip()
                            if k:
                                sent.add(k)
                        required = endpoint["body_destructures"]
                        missing = required - sent
                        if missing:
                            fixes.append({
                                "file": f,
                                "issue": f"{method} {url}: client sends {sorted(sent)} but endpoint needs {sorted(required)} — missing {sorted(missing)}",
                                "action": "replace_function",
                                "description": f"In {f}: {method} {url} missing body fields: {sorted(missing)}"
                            })

                # GET: response shape — API wraps in {key: data} but consumer uses raw
                if method == "GET" and endpoint.get("response_wraps"):
                    wraps_key = endpoint["response_wraps"]
                    after = content[match.end():match.end() + 500]
                    if re.search(r"\.set\(\s*data\s*\)", after):
                        fixes.append({
                            "file": f,
                            "issue": f"GET {url} returns {{'{wraps_key}': [...]}} but consumer uses raw data — need data.{wraps_key}",
                            "action": "replace_function",
                            "description": f"In {f}: GET {url} response wrapped in '{wraps_key}', use data.{wraps_key}"
                        })
        return fixes

    def _build_api_map(self, file_contents):
        """Parse API endpoint files to extract contracts."""
        api_map = {}
        for f, content in file_contents.items():
            if "/api/" not in f or "+server" not in f:
                continue

            route = re.sub(r'^src/routes', '', f)
            route = re.sub(r'/\+server\.\w+$', '', route)
            route = route.replace("\\", "/")

            for handler_match in re.finditer(
                r'export\s+const\s+(GET|POST|PATCH|DELETE)\s*[:\s].*?(?=export\s+const\s+(?:GET|POST|PATCH|DELETE)|$)',
                content, re.DOTALL
            ):
                method = handler_match.group(1)
                body = handler_match.group(0)
                info = {}

                # Body destructuring
                bd = re.search(r'const\s*\{\s*([^}]+)\}\s*=\s*(?:body|await\s+request\.json\(\))', body)
                if bd:
                    fields = set()
                    for fld in bd.group(1).split(","):
                        clean = fld.strip().split(":")[0].strip()
                        if clean:
                            fields.add(clean)
                    info["body_destructures"] = fields

                # Response wrapping (GET only)
                if method == "GET":
                    resp_matches = list(re.finditer(r'return\s+json\(\s*\{\s*(\w+)\s*[,:\}]', body))
                    # Take first non-error response (error responses are fallbacks)
                    for rm in resp_matches:
                        key = rm.group(1)
                        if key != "error":
                            info["response_wraps"] = key
                            break

                if info:
                    if route not in api_map:
                        api_map[route] = {}
                    api_map[route][method] = info
        return api_map

    # ── Phase 5: TYPES ───────────────────────────────────────

    def _phase_types(self, file_contents):
        """TS interface fields vs store init; JSON type misuse."""
        fixes = []

        # Extract interfaces
        interfaces = {}
        for f, content in file_contents.items():
            if not f.endswith((".ts", ".tsx")):
                continue
            for m in re.finditer(r'(?:export\s+)?interface\s+(\w+)\s*\{([^}]+)\}', content, re.DOTALL):
                name = m.group(1)
                fields = set()
                optional_fields = set()
                for line in m.group(2).split("\n"):
                    line = line.strip()
                    if not line or line.startswith("//"):
                        continue
                    fm = re.match(r'(\w+)\s*(\?)\s*:', line)
                    if fm:
                        fields.add(fm.group(1))
                        optional_fields.add(fm.group(1))
                        continue
                    fm = re.match(r'(\w+)\s*:', line)
                    if fm:
                        fields.add(fm.group(1))
                if fields:
                    interfaces[name] = {"all": fields, "optional": optional_fields,
                                         "required": fields - optional_fields}

        # Check store init vs interface
        for f, content in file_contents.items():
            for m in re.finditer(
                r'Writable\s*<\s*(\w+)\s*>\s*=\s*writable\s*\(\s*\{([^}]+)\}',
                content, re.DOTALL
            ):
                type_name = m.group(1)
                if type_name not in interfaces:
                    continue
                init_fields = set()
                for line in m.group(2).split(","):
                    fm = re.match(r'\s*(\w+)\s*:', line.strip())
                    if fm:
                        init_fields.add(fm.group(1))
                if not init_fields:
                    continue

                iface = interfaces[type_name]
                extra = init_fields - iface["all"]
                if extra:
                    fixes.append({
                        "file": f,
                        "issue": f"Type mismatch: writable<{type_name}> init has {sorted(extra)} not in {type_name} ({sorted(iface['all'])})",
                        "action": "replace_function",
                        "description": f"In {f}: store fields {sorted(extra)} not in interface {type_name}"
                    })
                missing_req = iface["required"] - init_fields
                if missing_req:
                    fixes.append({
                        "file": f,
                        "issue": f"Type mismatch: writable<{type_name}> missing required fields {sorted(missing_req)}",
                        "action": "replace_function",
                        "description": f"In {f}: store missing required {type_name} fields: {sorted(missing_req)}"
                    })

        # Detect .toISOString() on JSON string fields
        for f, content in file_contents.items():
            if "/api/" in f and "+server" in f:
                continue
            for m in re.finditer(r'(\w+)\.(startedAt|endedAt|createdAt|updatedAt)\.toISOString\(\)', content):
                fixes.append({
                    "file": f,
                    "issue": f"Type error: {m.group(1)}.{m.group(2)}.toISOString() — JSON dates are strings, not Date",
                    "action": "replace_function",
                    "description": f"In {f}: {m.group(2)} from JSON is string, remove .toISOString() or wrap in new Date()"
                })

        return fixes

    # ── Phase 6: CONSISTENCY ─────────────────────────────────

    def _phase_consistency(self, file_contents, code_files):
        """Export style uniformity, duplicate definitions."""
        fixes = []

        js_files = [f for f in code_files if f.endswith((".js", ".ts", ".tsx", ".jsx"))]
        if len(js_files) > 5:
            named_cjs = []
            default_cjs = []
            for f in js_files:
                c = file_contents.get(f, "")
                if re.search(r"module\.exports\s*=\s*\{", c):
                    named_cjs.append(f)
                elif re.search(r"module\.exports\s*=\s*\w+", c):
                    default_cjs.append(f)
            if named_cjs and default_cjs:
                minority = default_cjs if len(default_cjs) < len(named_cjs) else named_cjs
                majority = "named" if len(named_cjs) >= len(default_cjs) else "default"
                for f in minority[:5]:
                    fixes.append({
                        "file": f,
                        "issue": f"Inconsistent export style — project uses {majority}",
                        "action": "replace_function",
                        "description": f"In {f}: change export to {majority} convention"
                    })

        # Duplicate function definitions across non-API files
        func_locs = {}
        for f, content in file_contents.items():
            for m in re.finditer(r'(?:export\s+)?(?:async\s+)?function\s+(\w+)', content):
                func_locs.setdefault(m.group(1), []).append(f)
        for name, files in func_locs.items():
            non_api = [f for f in files if "/api/" not in f]
            if len(non_api) > 1:
                fixes.append({
                    "file": non_api[0],
                    "issue": f"Duplicate function '{name}' in: {', '.join(non_api)}",
                    "action": "replace_function",
                    "description": f"Function '{name}' duplicated across: {', '.join(non_api)}"
                })

        return fixes


    # ═══════════════════════════════════════════════
    # FEEDBACK BUILDER
    # ═══════════════════════════════════════════════

    def build_dynamic_feedback(self, task, res_coder, validation_result):
        actions = [r.get("tool") for r in res_coder.get("results", [])]
        status = validation_result.get("status")
        issues = validation_result.get("issues", "")

        # Check if any write operation succeeded
        has_successful_write = any(
            r.get("tool") in ("smart_edit", "create_file") and r.get("result", {}).get("success")
            for r in res_coder.get("results", [])
        )

        if not has_successful_write:
            if "read_context" in actions:
                return (
                    f"You used read_context but produced no output. "
                    f"Use create_file for new files, or smart_edit to modify existing files. "
                    f"Task: {task['description']}"
                )
            return (
                f"No output produced. "
                f"Use create_file for new files, or smart_edit for existing files. "
                f"Task: {task['description']}"
            )

        # Check if any write operation failed
        write_failed = any(
            r.get("tool") in ("smart_edit", "create_file") and not r.get("result", {}).get("success")
            for r in res_coder.get("results", [])
        )

        if write_failed:
            failed_errors = [
                r["result"].get("error", "")
                for r in res_coder.get("results", [])
                if r.get("tool") in ("smart_edit", "create_file") and not r.get("result", {}).get("success")
            ]
            error_text = "; ".join(failed_errors)

            if "not found" in error_text.lower():
                target_file = ""
                for r in res_coder.get("results", []):
                    if r.get("tool") in ("smart_edit", "create_file") and not r.get("result", {}).get("success"):
                        err = r["result"].get("error", "")
                        path_match = re.search(r"in (.+?)(?:\.|$)", err)
                        if path_match:
                            target_file = path_match.group(1)
                            break

                existing = ""
                if target_file:
                    summ = file_summary(target_file, WORKSPACE_PATH)
                    if summ.get("success"):
                        s = summ["summary"]
                        classes = s.get("classes", [])
                        funcs = s.get("functions", [])
                        class_names = [c if isinstance(c, str) else c.get("name", c) for c in classes]
                        func_names = [f if isinstance(f, str) else f.get("name", f) for f in funcs]
                        existing = f" Existing classes: {class_names}. Existing functions: {func_names}."

                return (
                    f"smart_edit failed: {error_text}.{existing} "
                    f"If the function/class does not exist, use action='add_function'. "
                    f"If the file does not exist, use create_file instead. "
                    f"Task: {task['description']}"
                )

            return (
                f"Write failed: {error_text}. "
                f"For new files: use create_file. "
                f"For existing files: smart_edit with add_function, replace_function, add_import, or append. "
                f"Task: {task['description']}"
            )

        if status == "FAILED" and issues:
            prompt = f"""The CODER attempted this task but was rejected.
                TASK: {task['title']}
                DESCRIPTION: {task['description']}
                ISSUES: {issues}
                TOOLS USED: {actions}

                Suggest what tool and action to use. Be specific.
                Available: create_file (new files), smart_edit (add_function, replace_function, add_import, append).
            """
            response = self.llm.generate(
                system=self.system_prompt,
                messages=[{"role": "user", "content": prompt}]
            )
            return response

        return f"Previous attempt failed. Fix: {issues}. Task: {task['description']}"

    # ═══════════════════════════════════════════════
    # PRIVATE HELPERS
    # ═══════════════════════════════════════════════

    def _extract_fixes_from_text(self, response):
        fixes = []
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        if json_match:
            try:
                items = json.loads(json_match.group())
                for item in items:
                    if isinstance(item, dict):
                        fixes.append({
                            "file": item.get("file", "unknown"),
                            "issue": item.get("fix", item.get("issue", str(item))),
                            "action": self._infer_action(item.get("fix", item.get("issue", ""))),
                            "description": item.get("description", item.get("fix", str(item)))
                        })
                if fixes:
                    return fixes
            except json.JSONDecodeError:
                pass

        for match in re.finditer(r'\d+\.\s*\*?\*?(.+?)\*?\*?.*?:\s*(.+?)(?=\n\d+\.|\Z)', response, re.DOTALL):
            file_match = re.search(r'[`"]?([\w/]+\.\w+)[`"]?', match.group(0))
            fixes.append({
                "file": file_match.group(1) if file_match else "unknown",
                "issue": match.group(1).strip()[:200],
                "action": "replace_function",
                "description": f"In {file_match.group(1) if file_match else 'unknown'}: MODIFY {match.group(2).strip()[:200]}"
            })
        return fixes

    def _infer_action(self, text):
        if not isinstance(text, str):
            text = str(text)
        t = text.lower()
        if "add import" in t or "missing import" in t:
            return "add_import"
        if "add" in t and ("method" in t or "function" in t):
            return "add_function"
        if "create" in t and "file" in t:
            return "create"
        return "replace_function"

    def _get_written_files(self, coder_result):
        written = []
        for r in coder_result.get("results", []):
            if r.get("tool") in ("smart_edit", "create_file") and r.get("result", {}).get("success"):
                path = r["result"].get("path")
                if path:
                    written.append(path)
        return written

    def _parse_response(self, response, files_checked):
        status_match = re.search(r"STATUS\s*:\s*(PASSED|FAILED)", response, re.IGNORECASE)
        if not status_match:
            return {"status": "FAILED", "issues": f"Unparseable: {response[:200]}", "files_checked": files_checked}

        status = status_match.group(1).upper()
        if status == "PASSED":
            reason_match = re.search(r"REASON\s*:\s*(.+)", response, re.IGNORECASE)
            return {"status": "PASSED", "reason": reason_match.group(1).strip() if reason_match else "Passed", "files_checked": files_checked}
        else:
            issues_match = re.search(r"ISSUES\s*:\s*(.+)", response, re.IGNORECASE | re.DOTALL)
            return {"status": "FAILED", "issues": issues_match.group(1).strip() if issues_match else "Unknown issues", "files_checked": files_checked}