import os
import subprocess

WORKSPACE_PATH = "./workspace"

def run_command(command: str, workspace_path: str = WORKSPACE_PATH) -> dict:
    answer = input(f"Command: {command} — Allow execution? (y/n): ").strip().lower()
    if answer != "y":
        return {"success": False, "error": "Command rejected by user"}

    workspace = os.path.realpath(workspace_path)
    os.makedirs(workspace, exist_ok=True)

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return {
            "success": True,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Command timed out after 60 seconds", "returncode": -1}
    except Exception as e:
        return {"success": False, "error": str(e), "returncode": -1}
