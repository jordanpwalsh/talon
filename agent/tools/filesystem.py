"""Filesystem tools: read, write, and list."""

import os

from agent.domain.model import ToolDefinition

READ_DEFINITION = ToolDefinition(
    name="read_file",
    description="Read the contents of a file at the given path.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or relative file path to read",
            },
        },
        "required": ["path"],
    },
)

WRITE_DEFINITION = ToolDefinition(
    name="write_file",
    description="Write content to a file, creating it if it doesn't exist.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or relative file path to write",
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file",
            },
        },
        "required": ["path", "content"],
    },
)

LIST_DEFINITION = ToolDefinition(
    name="list_directory",
    description="List files and directories at the given path.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path to list. Defaults to current directory.",
                "default": ".",
            },
        },
    },
)

MAX_READ_BYTES = 512_000


def handle_read(arguments: dict) -> str:
    path = arguments.get("path", "")
    if not path:
        return "Error: no path provided"
    try:
        with open(path, "r") as f:
            content = f.read(MAX_READ_BYTES)
        if len(content) == MAX_READ_BYTES:
            content += "\n... (truncated)"
        return content or "(empty file)"
    except Exception as e:
        return f"Error: {e}"


def handle_write(arguments: dict) -> str:
    path = arguments.get("path", "")
    content = arguments.get("content", "")
    if not path:
        return "Error: no path provided"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def handle_list(arguments: dict) -> str:
    path = arguments.get("path", ".")
    try:
        entries = sorted(os.listdir(path))
        if not entries:
            return "(empty directory)"
        lines = []
        for name in entries:
            full = os.path.join(path, name)
            suffix = "/" if os.path.isdir(full) else ""
            lines.append(f"{name}{suffix}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"
