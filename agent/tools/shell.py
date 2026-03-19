"""Shell command execution tool."""

import subprocess

from agent.domain.model import ToolDefinition

DEFINITION = ToolDefinition(
    name="run_command",
    description="Execute a shell command and return its output. Use for system tasks, installing packages, running scripts, git operations, etc.",
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute",
            },
            "timeout": {
                "type": "integer",
                "description": "Max seconds to wait (default 30, max 120)",
            },
        },
        "required": ["command"],
    },
)

DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 120


def handle(arguments: dict) -> str:
    command = arguments.get("command", "")
    if not command:
        return "Error: no command provided"
    timeout = min(arguments.get("timeout", DEFAULT_TIMEOUT), MAX_TIMEOUT)
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
