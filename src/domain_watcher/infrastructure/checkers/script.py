"""Script ``ExpirationChecker`` (id ``"script"``).

Spawns a subprocess that prints a JSON line to stdout. Contract from
ADR 0004 §7:

```
{"ok": true,  "expires_at": "2026-12-31T00:00:00Z", "source": "..."}
{"ok": false, "transient": true,  "error": "..."}
{"ok": false, "transient": false, "error": "..."}
```

argv is ``[*command, domain_name]``. Timeout kills the process. Stderr is
captured (truncated to 4 KiB) for diagnostics.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

from domain_watcher.core.checking.value_objects import CheckOutcome, CheckResult

if TYPE_CHECKING:
    from domain_watcher.core.shared.value_objects import DomainName

_log = logging.getLogger(__name__)

_STDERR_LIMIT = 4096


def _parse_iso(value: str) -> datetime:
    raw = value.replace("Z", "+00:00") if value.endswith("Z") else value
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass(slots=True)
class ScriptChecker:
    """Adapter wrapping a user-provided executable."""

    id: ClassVar[str] = "script"

    command: tuple[str, ...]
    timeout: float = 30.0
    env: dict[str, str] | None = field(default=None)

    async def check(self, domain: DomainName) -> CheckResult:
        argv = (*self.command, domain.value)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.env,
            )
        except (OSError, ValueError) as exc:
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.PERMANENT_ERROR,
                expires_at=None,
                source=self.id,
                error=f"spawn failed: {exc}",
            )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except TimeoutError:
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.TRANSIENT_ERROR,
                expires_at=None,
                source=self.id,
                error=f"script timeout after {self.timeout}s",
            )

        stderr_tail = stderr_b[-_STDERR_LIMIT:].decode("utf-8", errors="replace")
        stdout_str = stdout_b.decode("utf-8", errors="replace").strip()

        # Try to parse JSON regardless of exit code — scripts may exit
        # non-zero with a structured error.
        payload: dict[str, object] | None = None
        if stdout_str:
            try:
                parsed = json.loads(stdout_str)
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = None

        if proc.returncode != 0 and payload is None:
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.PERMANENT_ERROR,
                expires_at=None,
                source=self.id,
                error=(f"script exit={proc.returncode}; stderr={stderr_tail.strip()!r}"),
            )

        if payload is None:
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.PERMANENT_ERROR,
                expires_at=None,
                source=self.id,
                error=f"non-JSON stdout: {stdout_str[:200]!r}",
            )

        ok = payload.get("ok")
        if ok is True:
            expires_str = payload.get("expires_at")
            if not isinstance(expires_str, str):
                return CheckResult(
                    domain=domain,
                    outcome=CheckOutcome.PERMANENT_ERROR,
                    expires_at=None,
                    source=self.id,
                    error="script ok=true but no expires_at string",
                )
            try:
                expires_at = _parse_iso(expires_str)
            except ValueError as exc:
                return CheckResult(
                    domain=domain,
                    outcome=CheckOutcome.PERMANENT_ERROR,
                    expires_at=None,
                    source=self.id,
                    error=f"bad expires_at: {exc}",
                )
            return CheckResult(
                domain=domain,
                outcome=CheckOutcome.OK,
                expires_at=expires_at,
                source=self.id,
            )

        transient = bool(payload.get("transient", False))
        error_msg = str(payload.get("error", "script reported failure"))
        return CheckResult(
            domain=domain,
            outcome=(CheckOutcome.TRANSIENT_ERROR if transient else CheckOutcome.PERMANENT_ERROR),
            expires_at=None,
            source=self.id,
            error=error_msg,
        )


__all__ = ["ScriptChecker"]
