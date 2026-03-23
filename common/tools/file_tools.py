import os
import shutil

WORKSPACE_PATH = "./workspace"


def _resolve(path: str, workspace_path: str = WORKSPACE_PATH) -> str:
    workspace = os.path.realpath(workspace_path)
    resolved = os.path.realpath(os.path.join(workspace, path))
    if not resolved.startswith(workspace):
        raise PermissionError(f"Access outside workspace is not allowed: {path}")
    return resolved


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


def write_file(path: str, content: str, workspace_path: str = WORKSPACE_PATH) -> dict:
    try:
        full_path = _resolve(path, workspace_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "path": path}
    except PermissionError as e:
        return {"success": False, "error": str(e)}
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


def delete_file(path: str, workspace_path: str = WORKSPACE_PATH) -> dict:
    try:
        full_path = _resolve(path, workspace_path)
        if not os.path.exists(full_path):
            return {"success": False, "error": f"Path not found: {path}"}
        if os.path.isdir(full_path):
            shutil.rmtree(full_path)
        else:
            os.remove(full_path)
        return {"success": True}
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except OSError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}
