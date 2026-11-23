"""``bash`` in the code-mode sandbox (plan ``policy.code_mode``): one command, served.

The command runs as ``/bin/bash -c`` in the bubblewrap sandbox
(``sandbox.py``; or its PRoot translation where the host names ``ORA_CODE_MODE_SANDBOX=proot``, staged per command
and removed after it), in the workspace ``/work``, with the code-mode
interpreter first on ``PATH`` and the ``robot`` library (``robot.py``) on
``PYTHONPATH`` (with ``library: extended``, ``ora_lib`` too, and the current
observation at ``/ora/observation``: ``library.py``; and the program's own directories
after everything it imports: ``sitecustomize.py``). Its programs reach the
host over the inherited socket; this module answers their calls in order,
collects the combined output, and ends the command on its exit, on a
``handoff`` (the whole process group is stopped), or at its wall time, less
the time the host excludes (``uncounted_s``). A read-only op
(``sandbox.READ_OPS``: ``robot.docs``, ``robot.explain``, ``robot.query``) is answered by
``handle_op``. A program that ends while the host answers one of its calls (its
own ``timeout``, a signal, a resource limit) is no fault of the host: the call
ran and is recorded, the answer has nowhere to go, and the command ends
``failed`` with its exit status and a message that says so (``ended_during_call``).
"""

from __future__ import annotations

import json
import selectors
import signal
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .library import LIBRARY_MOUNT, ObservationMount
from .sandbox import (
    FILE_SIZE_LIMIT, MAX_LINE_BYTES, READ_OPS, SandboxError, _limits, _send, _stop, bwrap_command, proot_command,
    python_path, read_op, remove_stage, runner,
)

ROBOT_LIBRARY = Path(__file__).with_name("robot.py")
#: With ``library: extended``, bound as ``sitecustomize`` under ``PYTHONSAFEPATH``: a workspace file cannot shadow a module.
PROGRAM_PATH = Path(__file__).with_name("sitecustomize.py")
#: Set to ``extended`` with ``library: extended``: the ``robot`` library then returns typed results.
LIBRARY_ENV = "ORA_CODE_MODE_LIBRARY"
MAX_OUTPUT_BYTES = 1 << 20
#: How often a running command asks ``should_stop`` (free play), in seconds.
STOP_CHECK_S = 0.5


@dataclass
class CommandOutcome:
    """How a command ended: ``completed`` (exit 0), ``failed``, ``handed_back``, ``timeout``, ``stopped`` (the
    operator stopped it, free play) or ``crashed``."""

    status: str
    exit_code: int | None = None
    output: str = ""
    handed_back: dict[str, Any] | None = None
    calls: int = 0
    wall_s: float = 0.0
    error: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def command_bwrap(python: str, bwrap: str, fd: int, workspace: Path, command: str, *, extended: bool = False,
                  observation: Path | None = None, source: bool = False) -> list[str]:
    return bwrap_command(python, bwrap, fd, workspace, extended=extended, observation=observation, source=source) + [
        # every command's programs reach the host through the library (``from robot import tools``). An extended
        # program's path is ``LIBRARY_MOUNT`` alone, ahead of the workspace (``sitecustomize.py``), so the library sits
        # there
        *(["--ro-bind", str(ROBOT_LIBRARY), f"{LIBRARY_MOUNT}/robot.py",
           "--ro-bind", str(PROGRAM_PATH), f"{LIBRARY_MOUNT}/sitecustomize.py"] if extended
          else ["--ro-bind", str(ROBOT_LIBRARY), "/ora/robot.py"]),
        "--setenv", "PATH", f"{Path(python).parent}:/usr/bin:/bin",
        "--setenv", "PYTHONPATH", LIBRARY_MOUNT if extended else "/ora",
        *(["--setenv", "PYTHONSAFEPATH", "1", "--setenv", LIBRARY_ENV, "extended"] if extended else []),
        "--setenv", "HOME", "/work", "/bin/bash", "-c", command,
    ]


def run_command(
    command: str, *, handle_call: Callable[[str, Mapping[str, Any]], Mapping[str, Any]], wall_s: float,
    cpu_s: int, memory_mib: int, workspace: Path, uncounted_s: Callable[[], float] = lambda: 0.0,
    extended: bool = False,
    handle_op: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    observation: ObservationMount | None = None, source: bool = False,
    should_stop: Callable[[], str | None] | None = None,
) -> CommandOutcome:
    """Run ``command`` to its end; ``handle_call(name, arguments)`` answers each robot call, in order.

    ``extended`` (``library: extended``) gives its programs typed results (``robot.py``) and puts ``ora_lib`` on
    ``PYTHONPATH``; ``handle_op(message)`` answers a read-only op (``READ_OPS``); an ``observation`` mount is
    bound and brought up to date after every call; ``source`` binds ORA's code read-only. ``should_stop()``, asked
    between the program's messages about every half second, names why the command must stop now (free play: the
    operator's Esc, reset or quit); the command then ends ``stopped`` and its process group is killed."""

    python = python_path()
    kind, sandbox = runner()
    if observation is not None:
        handle_call = observation.served(handle_call)
    host, child = socket.socketpair()
    started = time.monotonic()
    stage: Path | None = None
    try:
        argv = command_bwrap(python, sandbox if kind == "bwrap" else "bwrap", child.fileno(), workspace, command,
                             extended=extended, observation=observation.root if observation is not None else None,
                             source=source)
        env = None  # bubblewrap clears and sets the environment itself
        if kind == "proot":
            stage = Path(tempfile.mkdtemp(prefix="ora-code-mode-proot-"))
            argv, env = proot_command(argv, sandbox, stage)
        process = subprocess.Popen(
            argv, env=env, pass_fds=(child.fileno(),),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
            preexec_fn=_limits(cpu_s, memory_mib),
        )
    except (OSError, SandboxError) as exc:
        host.close()
        if stage is not None:
            remove_stage(stage)
        if isinstance(exc, SandboxError):
            raise
        raise SandboxError(f"the sandbox does not start: {exc}") from exc
    finally:
        child.close()
    output = bytearray()
    try:
        outcome = _serve(host, process, handle_call, output, deadline=started + wall_s, uncounted_s=uncounted_s,
                         handle_op=handle_op, cpu_s=cpu_s, should_stop=should_stop)
    finally:
        host.close()
        _stop(process)
        if process.stdout:
            process.stdout.close()
        if stage is not None:
            remove_stage(stage)
    outcome.output = output.decode("utf-8", "replace")
    outcome.wall_s = round(time.monotonic() - started, 3)
    if outcome.exit_code is None and outcome.status in {"completed", "failed"}:
        outcome.exit_code = process.returncode
    return outcome


def _serve(host: socket.socket, process: subprocess.Popen, handle_call: Callable[..., Mapping[str, Any]],
           output: bytearray, *, deadline: float, uncounted_s: Callable[[], float] = lambda: 0.0,
           handle_op: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
           cpu_s: int | None = None, should_stop: Callable[[], str | None] | None = None) -> CommandOutcome:
    selector = selectors.DefaultSelector()
    selector.register(host, selectors.EVENT_READ, "socket")
    selector.register(process.stdout, selectors.EVENT_READ, "output")
    buffer, calls, open_streams = b"", 0, 2
    ended: Mapping[str, Any] | None = None  # the message being answered when the program's end closed the socket
    next_stop_check = time.monotonic()
    try:
        while True:
            newline = -1 if ended else buffer.find(b"\n")
            if newline >= 0:
                line, buffer = buffer[:newline], buffer[newline + 1:]
                message = json.loads(line)
                op = message.get("op")
                if op == "call" or op in READ_OPS:
                    if op == "call":
                        calls += 1
                        answer = handle_call(str(message.get("name")), dict(message.get("arguments") or {}))
                    else:
                        answer = read_op(message, handle_op)
                    try:
                        _send(host, answer)
                    except ConnectionError:  # BrokenPipeError, ConnectionResetError: every end of the socket is closed
                        ended, buffer = message, b""
                        selector.unregister(host)
                        open_streams -= 1
                elif op == "handoff":
                    return CommandOutcome(status="handed_back", calls=calls, handed_back={
                        "reason": str(message.get("reason")), "data": message.get("data")})
                else:
                    return CommandOutcome(status="crashed", calls=calls, error={
                        "type": "ProtocolError", "message": f"unknown message {message.get('op')!r}"})
                continue
            if open_streams == 0 or (process.poll() is not None and open_streams < 2):
                code = process.wait()
                if ended:
                    _drain(selector, output)  # what it printed while the host answered is still in the pipe
                    return CommandOutcome(status="failed", exit_code=code, calls=calls,
                                          error=_ended_during_call(ended, calls, code, cpu_s))
                return CommandOutcome(status="completed" if code == 0 else "failed", exit_code=code, calls=calls)
            if should_stop is not None and time.monotonic() >= next_stop_check:
                # Between messages only: a robot call the program made has been answered (or not yet asked).
                next_stop_check = time.monotonic() + STOP_CHECK_S
                reason = should_stop()
                if reason:  # what it printed so far is in output; the process group is killed after this
                    return CommandOutcome(status="stopped", calls=calls, error={
                        "type": "Stopped", "code": str(reason),
                        "message": "the operator stopped the command; its calls up to here ran and are recorded"})
            remaining = deadline + uncounted_s() - time.monotonic()
            if remaining <= 0:
                return CommandOutcome(status="timeout", calls=calls, error={
                    "type": "Timeout", "message": "the command ran out of wall time"})
            for key, _ in selector.select(timeout=min(remaining, STOP_CHECK_S if should_stop is not None else 1.0)):
                stream = key.fileobj
                chunk = stream.recv(65536) if key.data == "socket" else stream.read1(65536)  # type: ignore[union-attr]
                if not chunk:
                    selector.unregister(stream)
                    open_streams -= 1
                elif key.data == "socket":
                    buffer += chunk
                    if len(buffer) > MAX_LINE_BYTES:
                        return CommandOutcome(status="crashed", calls=calls, error={
                            "type": "ProtocolError", "message": "a message from a program is too long"})
                else:
                    output.extend(chunk)
                    del output[:max(0, len(output) - MAX_OUTPUT_BYTES)]
    finally:
        selector.close()


def _drain(selector: selectors.BaseSelector, output: bytearray) -> None:
    """Read the output an ended command left in its pipe, to its end or a second's silence (a process it left behind)."""

    while selector.get_map():
        ready = selector.select(timeout=1.0)
        if not ready:
            return
        for key, _ in ready:
            chunk = key.fileobj.read1(65536)  # type: ignore[union-attr]
            if not chunk:
                selector.unregister(key.fileobj)
            else:
                output.extend(chunk)
                del output[:max(0, len(output) - MAX_OUTPUT_BYTES)]


def _ended_during_call(message: Mapping[str, Any], calls: int, code: int, cpu_s: int | None) -> dict[str, Any]:
    """The error of a command whose program ended while the answer to ``message`` was being returned to it.

    A program blocks on its socket for the host's answer, so it ends there by something outside the call: its own
    ``timeout``, a signal, a resource limit. The call itself ran to its end and is recorded, so what it did is known
    (an action has its receipt, and the current observation shows it). ``code`` is the command's exit status: negative
    for a signal that ended the sandbox itself, 128 + N where bash reports a command that signal N ended."""

    number = -code if code < 0 else code - 128 if code > 128 else None
    try:
        name = None if number is None else signal.Signals(number).name
    except ValueError:  # not a signal this platform names
        name = None
    limit = {signal.SIGXCPU: None if cpu_s is None else f"the CPU time limit of {cpu_s} s was reached",
             signal.SIGXFSZ: f"the file size limit of {FILE_SIZE_LIMIT >> 20} MiB was reached"}.get(number)
    status = (f"the command was ended by {name}" if code < 0 and name else
              f"the command exited with status {code}" + (f", the status bash gives a command {name} ended"
                                                           if name else ""))
    if message.get("op") == "call":
        what = (f"the result of call {calls} ({message.get('name')}) was being returned to it: call {calls} ran to "
                f"its end and is recorded, so what it did is in the current observation")
    else:
        what = f"the answer to its {message.get('op')} request was being returned to it"
    error: dict[str, Any] = {"type": "ProgramEnded", "code": "ended_during_call", "calls": calls,
                             "message": f"the program ended while {what}; {status}" + (f" ({limit})" if limit else "")
                             + "."}
    if name:
        error["signal"] = name
    if limit:
        error["limit"] = limit
    return error


__all__ = ["CommandOutcome", "run_command"]
