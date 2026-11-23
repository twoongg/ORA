"""The code-mode bubblewrap sandbox: what ``bash`` (``shell.py``) runs a command in, and whether this host can.

The sandbox holds the dedicated code-mode interpreter (``ORA_CODE_MODE_PYTHON``: a
Python built from the lock ``sandbox-requirements.txt``, with NumPy, SciPy, Pillow,
scikit-image and Shapely and nothing of ORA), the system libraries and a
private ``/tmp``, all read-only except ``/tmp`` and the workspace; every namespace is
unshared, so there is no network, and nothing else on the host is visible: not the run,
the checkout, the simulator or any credential. A command's programs talk to the host over
one inherited socket. A contract whose ``library`` is ``extended`` also gets ``ora_lib``
at ``/ora/lib/ora_lib`` and a read-only copy of the current published observation at
``/ora/observation`` (``library.py``); one with ``source``, the checkout's files
``library.source_files`` names, read-only under ``/ora/src``.

Resource limits (CPU seconds, address space, file size, no core files) are set
in the child before ``bwrap`` starts and inherited by the command; the wall
time is enforced by ``shell.py`` and kills the sandbox's whole process group. It is
checked between calls, and the host may exclude time it spent answering a
call (``uncounted_s``: a script's action, the Supervisor's stage loop for a committed skill).

Besides its tool calls a program may send a read-only op of ``READ_OPS``
(``docs``: ``robot.docs`` and ``help(tools.<name>)``; ``explain``: ``robot.explain``; ``query``: ``robot.query``):
``handle_op`` answers it, and without one it is refused. It is no tool call and counts against no call limit (a
``query`` counts against the script's query allowance, which the host keeps).

A host where bubblewrap cannot create namespaces (a Kubernetes pod under the default seccomp profile) may name PRoot
instead: ``ORA_CODE_MODE_SANDBOX=proot`` (``ORA_CODE_MODE_PROOT`` names the binary). ``proot_command`` translates the
bubblewrap command, so a program sees the same paths, the same working directory and the same environment, over an
empty root that keeps the rest of the host out of sight; ORA's files are copied into that root read-only, as PRoot
binds cannot be. PRoot traces with ptrace and creates no namespace: a program there still reaches the network and the
host's ``/proc``, and root may write the system paths. The resource limits and the wall time are the same.

A host without ``bwrap`` or user namespaces cannot run code mode under bubblewrap, nor can an
interpreter whose packages differ from its lock: ``sandbox_status`` says why and what to install or set, and
preflight refuses a plan that needs one. Both are host prerequisites; ``ora setup --env robodojo`` builds the
interpreter at ``default_python()``, where ``python_path`` finds it when ``ORA_CODE_MODE_PYTHON`` names none, and
``ora doctor`` reports each of the three (``prerequisite_checks``). Nothing here decides whether a plan is in code
mode: that is the plan's, resolved from the RunSpec and its baseline alone.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import resource
import shutil
import signal
import socket
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ora.version import ora_command

from .contract import WORKSPACE_MOUNT
from .library import OBSERVATION_MOUNT, library_binds, source_binds

PYTHON_ENV = "ORA_CODE_MODE_PYTHON"
BWRAP_ENV = "ORA_CODE_MODE_BWRAP"
FD_ENV = "ORA_CODE_MODE_FD"
#: Which sandbox runs a command: ``bwrap`` (the default) or ``proot`` (``proot_command``).
SANDBOX_ENV = "ORA_CODE_MODE_SANDBOX"
SANDBOX_KINDS = ("bwrap", "proot")
PROOT_ENV = "ORA_CODE_MODE_PROOT"
#: bubblewrap options that have no PRoot counterpart: PRoot creates no namespace and the environment is built anew.
PROOT_DROPPED = frozenset({"--unshare-all", "--die-with-parent", "--new-session", "--clearenv"})
#: System paths the interpreter and its libraries load from, bound read-only when present.
SYSTEM_PATHS = ("/usr", "/lib", "/lib64", "/bin", "/etc/ld.so.cache", "/etc/alternatives", "/etc/localtime")
MAX_LINE_BYTES = 8 * 1024 * 1024
FILE_SIZE_LIMIT = 64 * 1024 * 1024
#: Read-only ops a script may send besides ``call`` (``ora.code-mode-rpc.v1``), answered by ``handle_op``.
READ_OPS = frozenset({"docs", "explain", "query"})
#: The interpreter's lock: every package ``ORA_CODE_MODE_PYTHON`` holds, at an exact version with its hashes
#: (``uv pip compile`` of ``sandbox-requirements.in``; the lock's header is the command).
LOCK = Path(__file__).with_name("sandbox-requirements.txt")
#: How to build an interpreter that matches the lock, named in a refusal. The sandbox cannot write bytecode, so it is
#: compiled here; without it every program compiles what it imports again.
LOCK_BUILD = ("uv venv --python {python} DIR && uv pip install --python DIR/bin/python --require-hashes --no-deps "
              "--only-binary :all: --compile-bytecode -r {lock}, then set ORA_CODE_MODE_PYTHON=DIR/bin/python")
#: Where ``ora setup --env robodojo`` builds the interpreter (component ``code-mode``, ``ora/app/gpu_recipes.py``),
#: under its prefix: ``ORA_GPU_PREFIX``, else the user data directory's ``ora/gpu`` (``gpu_setup.default_prefix``).
DEFAULT_PYTHON = "envs/code-mode/bin/python"
PREFIX_ENV = "ORA_GPU_PREFIX"
#: What to do when ``bwrap`` is missing, or cannot create the sandbox's namespaces (a host prerequisite each).
BWRAP_REMEDY = ("install bubblewrap (Debian/Ubuntu: apt-get install bubblewrap; without sudo, unpack the package "
                "with apt-get download bubblewrap and dpkg -x, and put its usr/bin/bwrap at ~/.local/bin/bwrap), "
                f"or set {BWRAP_ENV} to a bwrap binary")
USER_NAMESPACE_REMEDY = ("enable unprivileged user namespaces: sysctl user.max_user_namespaces must be above 0 and, "
                         "on Debian and Ubuntu, kernel.unprivileged_userns_clone=1; Ubuntu 23.10 and later also "
                         "restrict them through AppArmor (kernel.apparmor_restrict_unprivileged_userns=0, or an "
                         "AppArmor profile that lets bwrap create them); in a container, start it with user "
                         "namespaces allowed (Docker's default seccomp profile refuses them), or run code mode under "
                         f"PRoot: {SANDBOX_ENV}=proot")
PROOT_REMEDY = ("install PRoot 5.2 or later (Debian/Ubuntu: apt-get install proot; without sudo, the build "
                "https://proot.gitlab.io/proot/bin/proot on PATH or at ~/.local/bin/proot), "
                f"or set {PROOT_ENV} to a proot binary")
PTRACE_REMEDY = ("PRoot traces programs with ptrace: a container's seccomp profile must allow ptrace (Docker's "
                 "default does on Linux 4.8 and later) and kernel.yama.ptrace_scope must be 0 or 1; a PRoot "
                 "older than 5.2 lacks --kill-on-exit, so use a current build; where PRoot cannot use the kernel's "
                 "seccomp filter, try PROOT_NO_SECCOMP=1")
#: Run in the sandbox by ``sandbox_status`` after ``LOCKED = [names]``: prints each locked package's version, with its
#: top-level modules imported.
LOCK_PROBE = """\
import importlib, importlib.metadata as metadata, json, re, sys
def key(name):
    return re.sub(r'[-_.]+', '-', name).lower()
modules = {}
for module, names in metadata.packages_distributions().items():
    for name in names:
        modules.setdefault(key(name), []).append(module)
found = {}
for name in LOCKED:
    version = error = None
    try:
        version = metadata.version(name)
        for module in sorted(modules.get(key(name), ())):
            importlib.import_module(module)
    except metadata.PackageNotFoundError:
        pass
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    found[name] = {'version': version, 'error': error}
print(json.dumps({'executable': sys.executable, 'python': sys.version.split()[0], 'packages': found}))
"""


class SandboxError(RuntimeError):
    """The sandbox cannot run here (no bwrap, no interpreter, no user namespaces)."""


def bwrap_path() -> str:
    configured = os.environ.get(BWRAP_ENV)
    candidates = [configured] if configured else [shutil.which("bwrap"), str(Path.home() / ".local/bin/bwrap")]
    for candidate in candidates:
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    if configured:
        raise SandboxError(f"{BWRAP_ENV}={configured} is not an executable bwrap; {BWRAP_REMEDY}")
    raise SandboxError(f"bubblewrap (bwrap) is not installed (not on PATH, not at ~/.local/bin/bwrap): {BWRAP_REMEDY}")


def sandbox_kind() -> str:
    """``bwrap``, unless the host names ``proot`` in ``ORA_CODE_MODE_SANDBOX``."""

    value = os.environ.get(SANDBOX_ENV) or "bwrap"
    if value not in SANDBOX_KINDS:
        raise SandboxError(f"{SANDBOX_ENV}={value}: the sandbox is {' or '.join(SANDBOX_KINDS)}")
    return value


def proot_path() -> str:
    configured = os.environ.get(PROOT_ENV)
    candidates = [configured] if configured else [shutil.which("proot"), str(Path.home() / ".local/bin/proot")]
    for candidate in candidates:
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    if configured:
        raise SandboxError(f"{PROOT_ENV}={configured} is not an executable proot; {PROOT_REMEDY}")
    raise SandboxError(f"PRoot (proot) is not installed (not on PATH, not at ~/.local/bin/proot): {PROOT_REMEDY}")


def runner() -> tuple[str, str]:
    """The sandbox this host runs a command in, and its binary: ``("bwrap", path)`` or ``("proot", path)``."""

    kind = sandbox_kind()
    return kind, bwrap_path() if kind == "bwrap" else proot_path()


def ptrace_refusal(proot: str) -> str | None:
    """Why PRoot cannot trace a program on this host (its own words), or None when it runs ``true``."""

    try:
        # The options every command uses: a PRoot without --kill-on-exit (before 5.2) is refused here too.
        probe = subprocess.run([proot, "--kill-on-exit", "-v", "-1", shutil.which("true") or "/bin/true"],
                               capture_output=True,
                               text=True, timeout=30,
                               check=False, env={name: value for name, value in os.environ.items()
                                                 if name.startswith("PROOT_")})
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{type(exc).__name__}: {exc}"
    if probe.returncode == 0:
        return None
    return (probe.stderr.strip() or probe.stdout.strip() or f"exit status {probe.returncode}")[-500:]


def ptrace_reason(refusal: str) -> str:
    return f"PRoot cannot trace programs on this host ({refusal}): {PTRACE_REMEDY}"


def default_python(environ: Mapping[str, str] | None = None) -> Path:
    """Where ``ora setup --env robodojo`` builds the code-mode interpreter: ``DEFAULT_PYTHON`` under ``ORA_GPU_PREFIX``,
    else under the user data directory's ``ora/gpu`` (``$XDG_DATA_HOME``, else ``~/.local/share``), as
    ``ora.app.gpu_setup.default_prefix`` chooses the prefix."""

    environ = os.environ if environ is None else environ
    prefix = environ.get(PREFIX_ENV)
    if prefix:
        return Path(prefix).expanduser().absolute() / DEFAULT_PYTHON
    base = Path(environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share"))
    return base.expanduser().absolute() / "ora" / "gpu" / DEFAULT_PYTHON


def interpreter_remedy() -> str:
    """How to get an interpreter that matches the lock: setup, or the explicit build."""

    python, _ = locked_packages(LOCK)
    return (f"run {ora_command()} setup --env robodojo, which builds it at {default_python()}, or build one: "
            + LOCK_BUILD.format(python=python, lock=LOCK))


def python_path() -> str:
    """The code-mode interpreter: ``ORA_CODE_MODE_PYTHON`` when it is set (it wins, and must be executable), else the
    one setup built (``default_python``)."""

    configured = os.environ.get(PYTHON_ENV)
    if configured:
        if not os.access(configured, os.X_OK):
            raise SandboxError(f"{PYTHON_ENV}={configured} is not an executable interpreter; {interpreter_remedy()}")
        return configured
    fallback = default_python()
    if os.access(fallback, os.X_OK):
        return str(fallback)
    raise SandboxError(f"no code-mode interpreter: {PYTHON_ENV} is unset and {fallback} does not exist; "
                       f"{interpreter_remedy()}")


def namespace_refusal(bwrap: str) -> str | None:
    """Why ``bwrap`` cannot create the sandbox's namespaces on this host (its own words), or None when it can: an
    empty sandbox with every namespace unshared, as ``bwrap_command``'s, runs ``true``."""

    try:
        probe = subprocess.run([bwrap, "--unshare-all", "--die-with-parent", "--ro-bind", "/", "/", "true"],
                               capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{type(exc).__name__}: {exc}"
    if probe.returncode == 0:
        return None
    return (probe.stderr.strip() or probe.stdout.strip() or f"exit status {probe.returncode}")[-500:]


def user_namespace_reason(refusal: str) -> str:
    return f"bubblewrap cannot create the sandbox's namespaces on this host ({refusal}): {USER_NAMESPACE_REMEDY}"


@lru_cache(maxsize=8)
def _interpreter_roots(python: str) -> tuple[str, ...]:
    """The directories the interpreter loads from: its prefixes and the binary's real location."""

    probe = subprocess.run([python, "-I", "-c", "import sys, json; print(json.dumps([sys.prefix, "
                            "sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix]))"],
                           capture_output=True, text=True, timeout=60, check=False)
    if probe.returncode:
        raise SandboxError(f"the code-mode interpreter does not start: {probe.stderr.strip()[-500:]}")
    prefixes = json.loads(probe.stdout)
    # Both spellings of each prefix: the venv's ``python`` link names the interpreter through the unresolved one
    # (uv's managed ``cpython-3.11-...`` is a link to ``cpython-3.11.18-...``), which must exist inside too.
    roots = {str(Path(item).resolve()) for item in prefixes} | {str(Path(item).absolute()) for item in prefixes}
    roots.add(str(Path(python).resolve().parent.parent))
    roots.add(str(Path(python).parent.parent))
    return tuple(sorted(roots))


def bwrap_command(python: str, bwrap: str, fd: int, workspace: Path, *, extended: bool = False,
                  observation: Path | None = None, source: bool = False) -> list[str]:
    """The bubblewrap command, up to what it runs; ``workspace`` is bound read-write at ``/work`` and becomes the
    working directory.

    With ``extended`` (``library: extended``) ``ora_lib`` is bound read-only at ``/ora/lib/ora_lib``; an ``observation``
    directory (``library.ObservationMount``) is bound read-only at ``/ora/observation``; ``source`` binds ORA's code
    (``library.source_files``) read-only under ``/ora/src``."""

    command = [bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
    for path in SYSTEM_PATHS:
        if os.path.exists(path):
            command += ["--ro-bind", path, path]
    # The fresh /tmp first: an interpreter installed under /tmp (a home directory there) is bound on top of it.
    command += ["--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev"]
    for root in _interpreter_roots(python):
        command += ["--ro-bind", root, root]
    if extended:
        command += library_binds()
    if source:
        command += source_binds()
    if observation is not None:
        command += ["--ro-bind", str(observation), OBSERVATION_MOUNT]
    command += ["--bind", str(workspace), WORKSPACE_MOUNT, "--chdir", WORKSPACE_MOUNT]
    for name, value in {
        FD_ENV: str(fd), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
        "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
    }.items():
        command += ["--setenv", name, value]
    return command


def proot_command(bwrap: Sequence[str], proot: str, stage: Path) -> tuple[list[str], dict[str, str]]:
    """The PRoot command and environment for a bubblewrap command (``bwrap_command`` plus what it runs).

    The root is ``stage / "root"``, empty but for what is staged there: every path the bubblewrap command shows is
    bound to the same place, or, for ORA's own files (a read-only bind whose source is elsewhere: the library, the
    ``robot`` module, the source), copied there read-only. The observation and the workspace stay bound, since the host
    brings the observation up to date during a command and the workspace is the program's own. A fresh ``/tmp`` is the
    root's. The environment is what bubblewrap sets, plus the host's ``PROOT_*`` variables, which configure PRoot.
    ``stage`` belongs to the caller, who removes it when the command ends (``remove_stage``)."""

    root = stage / "root"
    root.mkdir(parents=True, exist_ok=True)
    binds: list[tuple[str, str]] = []
    env = {name: value for name, value in os.environ.items() if name.startswith("PROOT_")}
    cwd = "/"
    index = 1  # bwrap[0] is bubblewrap itself
    while index < len(bwrap):
        option = bwrap[index]
        if option in PROOT_DROPPED:
            index += 1
        elif option in {"--ro-bind", "--bind"}:
            source, target = bwrap[index + 1], bwrap[index + 2]
            index += 3
            if option == "--bind" or source == target or target == OBSERVATION_MOUNT:
                binds.append((source, target))
            else:
                _stage_copy(Path(source), root / target.lstrip("/"))
        elif option == "--tmpfs":
            (root / bwrap[index + 1].lstrip("/")).mkdir(parents=True, exist_ok=True)
            index += 2
        elif option in {"--proc", "--dev"}:
            binds.append((bwrap[index + 1], bwrap[index + 1]))
            index += 2
        elif option == "--chdir":
            cwd = bwrap[index + 1]
            index += 2
        elif option == "--setenv":
            env[bwrap[index + 1]] = bwrap[index + 2]
            index += 3
        elif option.startswith("--"):
            raise SandboxError(f"the bubblewrap option {option} has no PRoot translation")
        else:
            break
    command = [proot, "--kill-on-exit", "-v", "-1", "-r", str(root)]
    for source, target in binds:
        command += ["-b", f"{source}:{target}"]
    return [*command, "-w", cwd, *bwrap[index:]], env


def _stage_copy(source: Path, target: Path) -> None:
    """``source`` copied to ``target``, read-only, as a bubblewrap read-only bind shows it."""

    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
        for path in target.rglob("*"):
            if path.is_file():
                path.chmod(0o444)
    else:
        shutil.copyfile(source, target)
        target.chmod(0o444)


def remove_stage(stage: Path) -> None:
    """Remove a PRoot command's stage, whatever its program left there read-only."""

    def writable_again(function: Callable[..., Any], path: str, _: Any) -> None:
        try:
            os.chmod(path, 0o700)
            os.chmod(os.path.dirname(path), 0o700)
            function(path)
        except OSError:
            pass

    shutil.rmtree(stage, onerror=writable_again)


def _limits(cpu_s: int, memory_mib: int) -> Callable[[], None]:
    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 1))
        memory = memory_mib * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_SIZE_LIMIT, FILE_SIZE_LIMIT))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    return apply


def _send(sock: socket.socket, message: Mapping[str, Any]) -> None:
    sock.sendall((json.dumps(message, ensure_ascii=False, default=repr) + "\n").encode("utf-8"))


def read_op(message: Mapping[str, Any],
            handle_op: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None) -> Mapping[str, Any]:
    """The answer to a read-only op: ``handle_op``'s, or a refusal where the host serves none."""

    if handle_op is None:
        return {"ok": False, "error": {"code": "op_unavailable",
                                       "message": f"{message.get('op')} is not served in this code mode"}}
    return handle_op(message)


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def locked_packages(lock: Path) -> tuple[str, dict[str, str]]:
    """The lock's Python version (``--python-version`` in its header) and its packages, ``{name: version}``."""

    text = lock.read_text(encoding="utf-8")
    python = re.search(r"--python-version (\S+)", text)
    return python.group(1) if python else "", dict(re.findall(r"^([A-Za-z0-9][\w.-]*)==([^\s;\\]+)", text, re.M))


def lock_refusal(found: Mapping[str, Any], lock: Path) -> str | None:
    """Why the interpreter a probe ``found`` (``LOCK_PROBE``'s value) does not match ``lock``: the lock, each package
    that differs with both versions, and the command that builds one that does; None when it matches."""

    python, packages = locked_packages(lock)
    problems = [] if str(found.get("python")).startswith(f"{python}.") else [
        f"Python: the lock says {python}, the interpreter has {found.get('python')}"]
    for name, version in packages.items():
        seen = (found.get("packages") or {}).get(name) or {}
        if seen.get("version") != version:
            problems.append(f"{name}: the lock says {version}, the interpreter has {seen.get('version') or 'none'}")
        elif seen.get("error"):
            problems.append(f"{name} {version} does not import: {seen['error']}")
    if not problems:
        return None
    return (f"the code-mode interpreter {found.get('executable')} does not match its lock {lock}: "
            f"{'; '.join(problems)}. Build one that does: {LOCK_BUILD.format(python=python, lock=lock)}")


def sandbox_status() -> dict[str, Any]:
    """Whether this host can run code mode: every package of the interpreter's lock (``LOCK``) imported in the real
    sandbox, at its locked version, by the program a ``bash`` command would run. ``interpreter`` records what ran
    (executable, Python version, the locked packages' versions, the lock's sha256); preflight keeps it in the run's
    ``preflight.json``."""

    from .shell import run_command  # it runs in this module's sandbox

    # The host prerequisites first, each refused with what to install or set. A PRoot host says so in every answer.
    try:
        kind = sandbox_kind()
    except SandboxError as exc:
        return {"ok": False, "reason": str(exc)}
    named = {"sandbox": kind} if kind != "bwrap" else {}
    try:
        if kind == "proot":
            refusal = ptrace_refusal(proot_path())
            if refusal is not None:
                return {"ok": False, "reason": ptrace_reason(refusal), **named}
        else:
            refusal = namespace_refusal(bwrap_path())
            if refusal is not None:
                return {"ok": False, "reason": user_namespace_reason(refusal)}
        python_path()
    except SandboxError as exc:
        return {"ok": False, "reason": str(exc), **named}
    _, packages = locked_packages(LOCK)
    with tempfile.TemporaryDirectory(prefix="ora-code-mode-status-") as workspace:
        (Path(workspace) / "probe.py").write_text(f"LOCKED = {sorted(packages)!r}\n" + LOCK_PROBE, encoding="utf-8")
        try:
            outcome = run_command("python probe.py", handle_call=lambda *_: {"ok": False}, wall_s=60, cpu_s=30,
                                  memory_mib=2048, workspace=Path(workspace))
        except SandboxError as exc:
            return {"ok": False, "reason": str(exc), **named}
    try:
        found = json.loads(outcome.output.strip().splitlines()[-1])
    except (IndexError, ValueError):
        found = None
    if outcome.status != "completed" or not isinstance(found, dict):
        return {"ok": False, "reason": f"the sandbox did not run a program: {outcome.error or outcome.output[-2000:]}",
                **named}
    interpreter = {"executable": found.get("executable"), "python": found.get("python"), "lock": str(LOCK),
                   "lock_sha256": hashlib.sha256(LOCK.read_bytes()).hexdigest(),
                   "packages": {name: (found["packages"].get(name) or {}).get("version") for name in packages}}
    refusal = lock_refusal(found, LOCK)
    if refusal:
        return {"ok": False, "reason": refusal, "interpreter": interpreter, **named}
    return {"ok": True, "wall_s": outcome.wall_s, "interpreter": interpreter, **named}


def prerequisite_checks() -> list[dict[str, Any]]:
    """``ora doctor``'s checks of the sandbox, read-only: ``bwrap``, the user namespaces it needs (an empty sandbox
    runs ``true``) and the interpreter held to its lock (``LOCK_PROBE`` run by the interpreter itself, outside the
    sandbox, so it is checked on a host without either). Launch preflight runs the whole sandbox (``sandbox_status``)."""

    checks: list[dict[str, Any]] = []
    try:
        kind = sandbox_kind()
    except SandboxError as exc:
        return [{"check": "code_mode_sandbox", "passed": False, "detail": str(exc)},
                {"check": "code_mode_interpreter", **_interpreter_check()}]
    if kind == "proot":
        return [*_proot_checks(), {"check": "code_mode_interpreter", **_interpreter_check()}]
    try:
        bwrap = bwrap_path()
    except SandboxError as exc:
        bwrap = None
        checks.append({"check": "code_mode_bwrap", "passed": False, "detail": str(exc)})
    else:
        checks.append({"check": "code_mode_bwrap", "passed": True, "detail": bwrap})
    refusal = namespace_refusal(bwrap) if bwrap is not None else "not checked without bwrap"
    checks.append({"check": "code_mode_user_namespaces", "passed": refusal is None,
                   "detail": "bwrap creates the sandbox's namespaces" if refusal is None
                   else user_namespace_reason(refusal)})
    checks.append({"check": "code_mode_interpreter", **_interpreter_check()})
    return checks


def _proot_checks() -> list[dict[str, Any]]:
    """``ora doctor``'s checks of a PRoot host (``ORA_CODE_MODE_SANDBOX=proot``): the binary and ptrace."""

    try:
        proot = proot_path()
    except SandboxError as exc:
        return [{"check": "code_mode_proot", "passed": False, "detail": str(exc)},
                {"check": "code_mode_ptrace", "passed": False, "detail": "not checked without proot"}]
    refusal = ptrace_refusal(proot)
    return [{"check": "code_mode_proot", "passed": True, "detail": proot},
            {"check": "code_mode_ptrace", "passed": refusal is None,
             "detail": "PRoot traces a program" if refusal is None else ptrace_reason(refusal)}]


def _interpreter_check() -> dict[str, Any]:
    try:
        python = python_path()
    except SandboxError as exc:
        return {"passed": False, "detail": str(exc)}
    _, packages = locked_packages(LOCK)
    try:
        probe = subprocess.run([python, "-I", "-c", f"LOCKED = {sorted(packages)!r}\n" + LOCK_PROBE],
                               capture_output=True, text=True, timeout=120, check=False)
        found = json.loads(probe.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, IndexError, ValueError) as exc:
        return {"passed": False, "detail": f"the code-mode interpreter {python} does not run the lock check "
                                           f"({type(exc).__name__}); {interpreter_remedy()}"}
    refusal = lock_refusal(found, LOCK)
    return {"passed": refusal is None, "detail": refusal or f"{python} matches {LOCK.name}"}


__all__ = ["DEFAULT_PYTHON", "LOCK", "PROOT_REMEDY", "READ_OPS", "SandboxError", "bwrap_command", "default_python",
           "lock_refusal", "locked_packages", "namespace_refusal", "prerequisite_checks", "proot_command",
           "proot_path", "ptrace_refusal", "python_path", "read_op", "remove_stage", "runner", "sandbox_kind",
           "sandbox_status"]
