"""Class-based wrappers around existing tool functions.

These wrap the plain functions in file_tools.py and command_tools.py
so they can be used via the ToolExecutor / ToolRegistry system.
The original functions remain importable and work as before.
"""

from core.tools import BaseTool, ToolSpec
from core.registry import ToolRegistry
from common.tools.file_tools import (
    create_file as _create_file,
    read_context as _read_context,
    smart_edit as _smart_edit,
    read_file as _read_file,
    file_summary as _file_summary,
    list_files as _list_files,
    delete_file as _delete_file,
)
from common.tools.command_tools import run_command as _run_command


@ToolRegistry.register("create_file")
class CreateFileTool(BaseTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="create_file",
            description="Create a new file with the given content at the specified path",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path within workspace"},
                    "content": {"type": "string", "description": "File content to write"},
                    "workspace_path": {"type": "string", "description": "Workspace root path"},
                },
                "required": ["path", "content"],
            },
            category="file",
            required_capabilities=["FILE_WRITE"],
        )

    def execute(self, **kwargs):
        return _create_file(kwargs["path"], kwargs["content"], kwargs.get("workspace_path", "./workspace"))


@ToolRegistry.register("read_context")
class ReadContextTool(BaseTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="read_context",
            description="Read a file and its dependency context (imports, structure, workspace summary)",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path to read"},
                    "workspace_path": {"type": "string", "description": "Workspace root path"},
                },
                "required": ["path"],
            },
            category="file",
            required_capabilities=["FILE_READ"],
        )

    def execute(self, **kwargs):
        return _read_context(kwargs["path"], kwargs.get("workspace_path", "./workspace"))


@ToolRegistry.register("smart_edit")
class SmartEditTool(BaseTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="smart_edit",
            description="Apply a targeted edit to an existing file (add/replace functions, imports, etc.)",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path"},
                    "action": {"type": "string", "description": "Edit action: add_import, add_function, replace_function, add_to_class, append, insert_at"},
                    "content": {"type": "string", "description": "Content to add/replace"},
                    "target": {"type": "string", "description": "Target identifier (function name, line number, etc.)"},
                    "class_name": {"type": "string", "description": "Class name for add_to_class action"},
                    "workspace_path": {"type": "string", "description": "Workspace root path"},
                },
                "required": ["path", "action", "content"],
            },
            category="file",
            required_capabilities=["FILE_WRITE"],
        )

    def execute(self, **kwargs):
        return _smart_edit(
            kwargs["path"],
            kwargs["action"],
            kwargs["content"],
            target=kwargs.get("target"),
            class_name=kwargs.get("class_name"),
            workspace_path=kwargs.get("workspace_path", "./workspace"),
        )


@ToolRegistry.register("read_file")
class ReadFileTool(BaseTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="read_file",
            description="Read the full content of a file",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path"},
                    "workspace_path": {"type": "string", "description": "Workspace root path"},
                },
                "required": ["path"],
            },
            category="file",
            required_capabilities=["FILE_READ"],
        )

    def execute(self, **kwargs):
        return _read_file(kwargs["path"], kwargs.get("workspace_path", "./workspace"))


@ToolRegistry.register("list_files")
class ListFilesTool(BaseTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="list_files",
            description="List all files in the workspace directory tree",
            parameters={
                "type": "object",
                "properties": {
                    "workspace_path": {"type": "string", "description": "Workspace root path"},
                },
            },
            category="file",
            required_capabilities=["FILE_READ"],
        )

    def execute(self, **kwargs):
        return _list_files(kwargs.get("workspace_path", "./workspace"))


@ToolRegistry.register("run_command")
class RunCommandTool(BaseTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="run_command",
            description="Execute a shell command in the workspace directory",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "workspace_path": {"type": "string", "description": "Workspace root path"},
                },
                "required": ["command"],
            },
            category="system",
            requires_confirmation=True,
            required_capabilities=["CODE_EXECUTE"],
        )

    def execute(self, **kwargs):
        return _run_command(kwargs["command"], kwargs.get("workspace_path", "./workspace"))
