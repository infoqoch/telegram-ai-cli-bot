"""Best-effort script snapshots for command schedule execution context."""

from __future__ import annotations

import hashlib
import shlex
from pathlib import Path
from typing import Any


MAX_SCRIPT_SNAPSHOT_BYTES = 100_000


def build_command_execution_snapshot(
    command: str,
    workspace_path: str | Path | None,
    *,
    default_workspace: Path,
) -> dict[str, Any]:
    """Return execution context without letting snapshot failures block the command."""
    raw_workspace = Path(workspace_path).expanduser() if workspace_path else default_workspace
    payload: dict[str, Any] = {
        "kind": "command_schedule",
        "command": command,
        "workspace_path": str(raw_workspace),
    }

    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        payload["script_error"] = f"command parse failed: {exc}"
        return payload

    script_token = next((token for token in tokens if token.lower().endswith(".py")), None)
    if not script_token:
        payload["script_error"] = "no Python script path found in command"
        return payload

    payload["script_path"] = script_token
    try:
        workspace = raw_workspace.resolve()
        payload["workspace_path"] = str(workspace)
        script_path = (workspace / script_token).resolve()
        if workspace != script_path and workspace not in script_path.parents:
            payload["script_error"] = "script path escapes the schedule workspace"
            return payload
        if not script_path.is_file():
            payload["script_error"] = "script file not found"
            return payload

        script_bytes = script_path.read_bytes()
        payload.update(
            {
                "script_path": str(script_path.relative_to(workspace)),
                "script_sha256": hashlib.sha256(script_bytes).hexdigest(),
                "script_content": script_bytes[:MAX_SCRIPT_SNAPSHOT_BYTES].decode(
                    "utf-8", errors="replace"
                ),
                "script_truncated": len(script_bytes) > MAX_SCRIPT_SNAPSHOT_BYTES,
            }
        )
    except (OSError, RuntimeError, ValueError) as exc:
        payload["script_error"] = f"script snapshot failed: {exc}"

    return payload
