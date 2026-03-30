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
        elif file_path.endswith((".js", ".ts", ".tsx", ".jsx")):
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
            # Python: from X import Y, Z
            for match in re.finditer(r'^from\s+([\w.]+)\s+import\s+(.+)$', content, re.MULTILINE):
                module = match.group(1)
                names = [n.strip().split(" as ")[0] for n in match.group(2).split(",")]
                rel_path = module.replace(".", "/") + ".py"
                imports.append({"raw_path": module, "resolved_path": rel_path, "names": names})

            # Python: import X
            for match in re.finditer(r'^import\s+([\w.]+)', content, re.MULTILINE):
                module = match.group(1)
                rel_path = module.replace(".", "/") + ".py"
                imports.append({"raw_path": module, "resolved_path": rel_path, "names": []})

        elif file_path.endswith((".js", ".ts", ".tsx", ".jsx")):
            # JS/TS: const { X, Y } = require('./path')
            for match in re.finditer(r"(?:const|let|var)\s+\{\s*([^}]+)\}\s*=\s*require\(['\"]([^'\"]+)['\"]\)", content):
                names = [n.strip() for n in match.group(1).split(",")]
                raw_path = match.group(2)
                if raw_path.startswith("."):
                    resolved = self._resolve_relative_path(raw_path, file_path)
                    imports.append({"raw_path": raw_path, "resolved_path": resolved, "names": names})

            # JS/TS: const X = require('./path')
            for match in re.finditer(r"(?:const|let|var)\s+(\w+)\s*=\s*require\(['\"]([^'\"]+)['\"]\)", content):
                if "{" not in match.group(0):  # Skip destructured (already caught above)
                    name = match.group(1)
                    raw_path = match.group(2)
                    if raw_path.startswith("."):
                        resolved = self._resolve_relative_path(raw_path, file_path)
                        imports.append({"raw_path": raw_path, "resolved_path": resolved, "names": [name]})

            # JS/TS: import { X, Y } from './path'
            for match in re.finditer(r"import\s+\{\s*([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]", content):
                names = [n.strip() for n in match.group(1).split(",")]
                raw_path = match.group(2)
                if raw_path.startswith("."):
                    resolved = self._resolve_relative_path(raw_path, file_path)
                    imports.append({"raw_path": raw_path, "resolved_path": resolved, "names": names})

            # JS/TS: import X from './path'
            for match in re.finditer(r"import\s+(\w+)\s+from\s+['\"]([^'\"]+)['\"]", content):
                name = match.group(1)
                raw_path = match.group(2)
                if raw_path.startswith("."):
                    resolved = self._resolve_relative_path(raw_path, file_path)
                    imports.append({"raw_path": raw_path, "resolved_path": resolved, "names": [name]})

        return imports

    def _resolve_relative_path(self, raw_path, importing_file):
        """Resolve a relative import path relative to the importing file."""
        dir_of_importer = os.path.dirname(importing_file)
        resolved = os.path.normpath(os.path.join(dir_of_importer, raw_path))
        return resolved.replace("\\", "/")

    def _resolve_import_path(self, path):
        """Try to find the actual file in workspace, trying common extensions."""
        extensions = ["", ".js", ".ts", ".tsx", ".jsx", "/index.js", "/index.ts"]
        for ext in extensions:
            candidate = path + ext
            full = os.path.join(WORKSPACE_PATH, candidate)
            if os.path.isfile(full):
                return candidate
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
        elif file_path.endswith((".js", ".ts", ".tsx", ".jsx")):
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
                    if summ.get("success"):
                        export_names = self._get_export_names(found)
                        dep_summaries.append(
                            f"  {found}: exports={export_names or '?'}, "
                            f"classes={[c['name'] for c in summ['summary'].get('classes', [])]}, "
                            f"functions={[f['name'] for f in summ['summary'].get('functions', [])]}"
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
                      if f.endswith(('.py', '.js', '.ts', '.tsx', '.jsx', '.json', '.yaml', '.yml', '.sql'))]

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
        """Run deterministic cross-file checks on entire project."""
        fixes = []

        # Build export map: file -> set of exported names
        export_map = {}
        for f in code_files:
            if f.endswith(('.json', '.yaml', '.yml', '.sql')):
                continue
            names = self._get_export_names(f)
            if names:
                export_map[f] = names

        # Build import map: file -> list of {raw_path, resolved_path, names}
        import_map = {}
        for f in code_files:
            if f.endswith(('.json', '.yaml', '.yml', '.sql')):
                continue
            result = read_file(f, WORKSPACE_PATH)
            if not result["success"]:
                continue
            imports = self._extract_imports(result["content"], f)
            if imports:
                import_map[f] = imports

        # Check 1: All imports resolve to existing files
        for f, imports in import_map.items():
            for imp in imports:
                resolved = imp.get("resolved_path")
                if not resolved:
                    continue
                found = self._resolve_import_path(resolved)
                if not found:
                    fixes.append({
                        "file": f,
                        "issue": f"Broken import: '{imp['raw_path']}' — file not found in workspace",
                        "action": "add_import",
                        "description": f"In {f}: fix import from '{imp['raw_path']}' — target file does not exist"
                    })

        # Check 2: Named imports match actual exports
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
                    if name not in export_names:
                        fixes.append({
                            "file": f,
                            "issue": f"Import mismatch: '{name}' from '{imp['raw_path']}' — "
                                     f"'{found}' exports {sorted(export_names)}",
                            "action": "replace_function",
                            "description": f"In {f}: '{name}' is not exported by '{found}'. "
                                           f"Available exports: {sorted(export_names)}"
                        })

        # Check 3: Export style consistency (for JS/TS projects)
        js_files = [f for f in code_files if f.endswith((".js", ".ts", ".tsx", ".jsx"))]
        if js_files:
            named_exports = []  # module.exports = { X }
            default_exports = []  # module.exports = X

            for f in js_files:
                result = read_file(f, WORKSPACE_PATH)
                if not result["success"]:
                    continue
                content = result["content"]
                if re.search(r"module\.exports\s*=\s*\{", content):
                    named_exports.append(f)
                elif re.search(r"module\.exports\s*=\s*\w+", content):
                    default_exports.append(f)

            # If mixed styles and more than a few files, warn about inconsistency
            if named_exports and default_exports and len(js_files) > 5:
                minority = default_exports if len(default_exports) < len(named_exports) else named_exports
                majority_style = "named (module.exports = {{ X }})" if len(named_exports) >= len(default_exports) else "default (module.exports = X)"
                for f in minority[:5]:  # Cap to avoid too many fixes
                    fixes.append({
                        "file": f,
                        "issue": f"Inconsistent export style — project majority uses {majority_style}",
                        "action": "replace_function",
                        "description": f"In {f}: change export to match project convention ({majority_style})"
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