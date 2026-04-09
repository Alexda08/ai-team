"""Environment interaction tools.

These tools provide the agents with the ability to interact with the
development environment: run shell commands, manage processes, make HTTP
requests, operate git, and install packages.
"""

import os
import subprocess
import signal
import glob as _glob
import json
import shutil
from typing import Any, Dict

from core.tools import BaseTool, ToolSpec
from core.registry import ToolRegistry


# ---------------------------------------------------------------------------
# ShellTool — execute shell commands with safety guardrails
# ---------------------------------------------------------------------------

@ToolRegistry.register("shell")
class ShellTool(BaseTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="shell",
            description="Execute a shell command in the workspace with safety guardrails, timeout, and output capture",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "workspace_path": {"type": "string", "description": "Working directory (default: ./workspace)"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default: 120)"},
                },
                "required": ["command"],
            },
            category="system",
            cost_estimate="medium",
            timeout=120,
            requires_confirmation=True,
            required_capabilities=["SHELL_ACCESS"],
        )

    def execute(self, **kwargs) -> Dict[str, Any]:
        command = kwargs["command"]
        workspace = os.path.realpath(kwargs.get("workspace_path", "./workspace"))
        timeout = kwargs.get("timeout", 120)

        # Safety: block dangerous patterns
        dangerous = ["rm -rf /", "mkfs", "dd if=", ":(){", "fork bomb"]
        for pattern in dangerous:
            if pattern in command.lower():
                return {"success": False, "error": f"Blocked dangerous command pattern: {pattern}"}

        os.makedirs(workspace, exist_ok=True)

        try:
            result = subprocess.run(
                command, shell=True, cwd=workspace,
                capture_output=True, text=True, timeout=timeout,
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout[-5000:] if len(result.stdout) > 5000 else result.stdout,
                "stderr": result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr,
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Timed out after {timeout}s"}
        except Exception as e:
            return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# FileSystemTool — advanced filesystem operations
# ---------------------------------------------------------------------------

@ToolRegistry.register("filesystem")
class FileSystemTool(BaseTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="filesystem",
            description="Advanced filesystem operations: glob search, directory tree, disk usage, copy, move",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["glob", "tree", "disk_usage", "copy", "move", "mkdir"],
                        "description": "Filesystem action to perform",
                    },
                    "path": {"type": "string", "description": "Target path or glob pattern"},
                    "destination": {"type": "string", "description": "Destination path (for copy/move)"},
                    "workspace_path": {"type": "string", "description": "Workspace root (default: ./workspace)"},
                },
                "required": ["action", "path"],
            },
            category="file",
            required_capabilities=["FILE_READ"],
        )

    def execute(self, **kwargs) -> Dict[str, Any]:
        action = kwargs["action"]
        path = kwargs["path"]
        workspace = os.path.realpath(kwargs.get("workspace_path", "./workspace"))

        if action == "glob":
            full_pattern = os.path.join(workspace, path)
            matches = _glob.glob(full_pattern, recursive=True)
            relative = [os.path.relpath(m, workspace) for m in matches]
            return {"success": True, "matches": relative, "count": len(relative)}

        elif action == "tree":
            full_path = os.path.join(workspace, path)
            if not os.path.isdir(full_path):
                return {"success": False, "error": f"Not a directory: {path}"}
            tree = []
            for root, dirs, files in os.walk(full_path):
                level = os.path.relpath(root, full_path).count(os.sep)
                indent = "  " * level
                tree.append(f"{indent}{os.path.basename(root)}/")
                for f in files:
                    tree.append(f"{indent}  {f}")
                if len(tree) > 500:
                    tree.append("... (truncated)")
                    break
            return {"success": True, "tree": "\n".join(tree)}

        elif action == "disk_usage":
            full_path = os.path.join(workspace, path)
            if not os.path.exists(full_path):
                return {"success": False, "error": f"Path not found: {path}"}
            total = sum(
                os.path.getsize(os.path.join(r, f))
                for r, _, files in os.walk(full_path) for f in files
            )
            return {"success": True, "bytes": total, "human": f"{total / 1024 / 1024:.2f} MB"}

        elif action == "copy":
            src = os.path.join(workspace, path)
            dst = os.path.join(workspace, kwargs.get("destination", ""))
            if not dst:
                return {"success": False, "error": "Destination required for copy"}
            try:
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                return {"success": True, "source": path, "destination": kwargs["destination"]}
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif action == "move":
            src = os.path.join(workspace, path)
            dst = os.path.join(workspace, kwargs.get("destination", ""))
            if not dst:
                return {"success": False, "error": "Destination required for move"}
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
                return {"success": True, "source": path, "destination": kwargs["destination"]}
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif action == "mkdir":
            full_path = os.path.join(workspace, path)
            os.makedirs(full_path, exist_ok=True)
            return {"success": True, "path": path}

        return {"success": False, "error": f"Unknown action: {action}"}


# ---------------------------------------------------------------------------
# ProcessTool — start/stop/monitor background processes
# ---------------------------------------------------------------------------

_PROCESSES: Dict[str, subprocess.Popen] = {}


@ToolRegistry.register("process")
class ProcessTool(BaseTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="process",
            description="Start, stop, or check status of background processes (dev servers, builds)",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["start", "stop", "status", "list", "logs"],
                        "description": "Process action",
                    },
                    "name": {"type": "string", "description": "Process name/identifier"},
                    "command": {"type": "string", "description": "Command to start (for 'start' action)"},
                    "workspace_path": {"type": "string", "description": "Working directory"},
                },
                "required": ["action"],
            },
            category="system",
            requires_confirmation=True,
            required_capabilities=["PROCESS_MANAGE"],
        )

    def execute(self, **kwargs) -> Dict[str, Any]:
        action = kwargs["action"]
        name = kwargs.get("name", "default")
        workspace = os.path.realpath(kwargs.get("workspace_path", "./workspace"))

        if action == "start":
            command = kwargs.get("command")
            if not command:
                return {"success": False, "error": "Command required for start"}
            if name in _PROCESSES and _PROCESSES[name].poll() is None:
                return {"success": False, "error": f"Process '{name}' already running (PID {_PROCESSES[name].pid})"}

            os.makedirs(workspace, exist_ok=True)
            proc = subprocess.Popen(
                command, shell=True, cwd=workspace,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            _PROCESSES[name] = proc
            return {"success": True, "name": name, "pid": proc.pid}

        elif action == "stop":
            proc = _PROCESSES.get(name)
            if not proc or proc.poll() is not None:
                return {"success": False, "error": f"No running process '{name}'"}
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            return {"success": True, "name": name, "returncode": proc.returncode}

        elif action == "status":
            proc = _PROCESSES.get(name)
            if not proc:
                return {"success": True, "name": name, "status": "not_found"}
            running = proc.poll() is None
            return {"success": True, "name": name, "status": "running" if running else "stopped",
                    "pid": proc.pid, "returncode": proc.returncode}

        elif action == "list":
            result = {}
            for pname, proc in _PROCESSES.items():
                running = proc.poll() is None
                result[pname] = {"pid": proc.pid, "running": running}
            return {"success": True, "processes": result}

        elif action == "logs":
            proc = _PROCESSES.get(name)
            if not proc:
                return {"success": False, "error": f"No process '{name}'"}
            stdout = ""
            stderr = ""
            if proc.stdout and proc.stdout.readable():
                try:
                    stdout = proc.stdout.read(8192).decode("utf-8", errors="replace") if proc.poll() is not None else ""
                except Exception:
                    pass
            if proc.stderr and proc.stderr.readable():
                try:
                    stderr = proc.stderr.read(8192).decode("utf-8", errors="replace") if proc.poll() is not None else ""
                except Exception:
                    pass
            return {"success": True, "name": name, "stdout": stdout, "stderr": stderr}

        return {"success": False, "error": f"Unknown action: {action}"}


# ---------------------------------------------------------------------------
# HttpTool — make HTTP requests
# ---------------------------------------------------------------------------

@ToolRegistry.register("http")
class HttpTool(BaseTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="http",
            description="Make HTTP requests (GET, POST, PUT, DELETE) to test APIs or fetch resources",
            parameters={
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"], "description": "HTTP method"},
                    "url": {"type": "string", "description": "Request URL"},
                    "headers": {"type": "object", "description": "Request headers"},
                    "body": {"type": "string", "description": "Request body (JSON string)"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30)"},
                },
                "required": ["method", "url"],
            },
            category="network",
            timeout=30,
            required_capabilities=["NETWORK_FETCH"],
        )

    def execute(self, **kwargs) -> Dict[str, Any]:
        import requests

        method = kwargs["method"]
        url = kwargs["url"]
        headers = kwargs.get("headers", {})
        body = kwargs.get("body")
        timeout = kwargs.get("timeout", 30)

        # Safety: block internal/private network access
        from urllib.parse import urlparse
        parsed = urlparse(url)
        blocked_hosts = ["169.254.169.254", "metadata.google", "localhost", "127.0.0.1", "0.0.0.0"]
        if any(h in parsed.hostname for h in blocked_hosts if parsed.hostname):
            return {"success": False, "error": f"Blocked request to internal host: {parsed.hostname}"}

        try:
            resp = requests.request(
                method=method, url=url, headers=headers,
                data=body, timeout=timeout,
            )
            content_type = resp.headers.get("content-type", "")
            body_text = resp.text[:10000] if len(resp.text) > 10000 else resp.text

            return {
                "success": resp.ok,
                "status_code": resp.status_code,
                "headers": dict(resp.headers),
                "body": body_text,
                "content_type": content_type,
            }
        except requests.Timeout:
            return {"success": False, "error": f"Request timed out after {timeout}s"}
        except Exception as e:
            return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# GitTool — git operations
# ---------------------------------------------------------------------------

@ToolRegistry.register("git")
class GitTool(BaseTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="git",
            description="Git operations: status, diff, commit, branch, log, init",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "diff", "log", "commit", "branch", "checkout", "init", "add"],
                        "description": "Git action to perform",
                    },
                    "args": {"type": "string", "description": "Additional arguments (e.g. commit message, branch name)"},
                    "workspace_path": {"type": "string", "description": "Repository path"},
                },
                "required": ["action"],
            },
            category="vcs",
            required_capabilities=["FILE_WRITE"],
        )

    def execute(self, **kwargs) -> Dict[str, Any]:
        action = kwargs["action"]
        args = kwargs.get("args", "")
        workspace = os.path.realpath(kwargs.get("workspace_path", "./workspace"))

        cmd_map = {
            "status": "git status --short",
            "diff": "git diff",
            "log": "git log --oneline -20",
            "commit": f'git commit -m "{args}"',
            "branch": f"git branch {args}".strip(),
            "checkout": f"git checkout {args}",
            "init": "git init",
            "add": f"git add {args or '.'}",
        }

        command = cmd_map.get(action)
        if not command:
            return {"success": False, "error": f"Unknown git action: {action}"}

        try:
            result = subprocess.run(
                command, shell=True, cwd=workspace,
                capture_output=True, text=True, timeout=30,
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout[-5000:],
                "stderr": result.stderr[-2000:],
                "returncode": result.returncode,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# PackageManagerTool — detect and run package managers
# ---------------------------------------------------------------------------

@ToolRegistry.register("package_manager")
class PackageManagerTool(BaseTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="package_manager",
            description="Detect the project's package manager and install dependencies",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["detect", "install", "add"],
                        "description": "Action: detect manager, install all deps, or add a specific package",
                    },
                    "package": {"type": "string", "description": "Package name (for 'add' action)"},
                    "workspace_path": {"type": "string", "description": "Project root path"},
                },
                "required": ["action"],
            },
            category="system",
            timeout=300,
            requires_confirmation=True,
            required_capabilities=["CODE_EXECUTE"],
        )

    def _detect(self, workspace: str) -> Dict[str, str]:
        """Detect package manager from project files."""
        checks = [
            ("package-lock.json", "npm", "npm install", "npm install"),
            ("yarn.lock", "yarn", "yarn install", "yarn add"),
            ("pnpm-lock.yaml", "pnpm", "pnpm install", "pnpm add"),
            ("bun.lockb", "bun", "bun install", "bun add"),
            ("requirements.txt", "pip", "pip install -r requirements.txt", "pip install"),
            ("pyproject.toml", "pip", "pip install -e .", "pip install"),
            ("Pipfile", "pipenv", "pipenv install", "pipenv install"),
            ("poetry.lock", "poetry", "poetry install", "poetry add"),
            ("Cargo.toml", "cargo", "cargo build", "cargo add"),
            ("go.mod", "go", "go mod download", "go get"),
        ]
        for filename, manager, install_cmd, add_cmd in checks:
            if os.path.isfile(os.path.join(workspace, filename)):
                return {"manager": manager, "install_cmd": install_cmd, "add_cmd": add_cmd, "lockfile": filename}
        return {}

    def execute(self, **kwargs) -> Dict[str, Any]:
        action = kwargs["action"]
        workspace = os.path.realpath(kwargs.get("workspace_path", "./workspace"))

        detected = self._detect(workspace)
        if not detected and action != "detect":
            return {"success": False, "error": "No package manager detected in workspace"}

        if action == "detect":
            if detected:
                return {"success": True, **detected}
            return {"success": True, "manager": None, "message": "No package manager found"}

        elif action == "install":
            cmd = detected["install_cmd"]
            try:
                result = subprocess.run(
                    cmd, shell=True, cwd=workspace,
                    capture_output=True, text=True, timeout=300,
                )
                return {
                    "success": result.returncode == 0,
                    "manager": detected["manager"],
                    "stdout": result.stdout[-5000:],
                    "stderr": result.stderr[-2000:],
                }
            except subprocess.TimeoutExpired:
                return {"success": False, "error": "Install timed out after 5 minutes"}
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif action == "add":
            package = kwargs.get("package")
            if not package:
                return {"success": False, "error": "Package name required for 'add' action"}
            cmd = f"{detected['add_cmd']} {package}"
            try:
                result = subprocess.run(
                    cmd, shell=True, cwd=workspace,
                    capture_output=True, text=True, timeout=300,
                )
                return {
                    "success": result.returncode == 0,
                    "manager": detected["manager"],
                    "package": package,
                    "stdout": result.stdout[-3000:],
                    "stderr": result.stderr[-2000:],
                }
            except Exception as e:
                return {"success": False, "error": str(e)}

        return {"success": False, "error": f"Unknown action: {action}"}
