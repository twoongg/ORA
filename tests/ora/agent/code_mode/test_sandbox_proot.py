"""The code-mode sandbox under PRoot (``ORA_CODE_MODE_SANDBOX=proot``), for a host where bubblewrap cannot create
namespaces (a Kubernetes pod under the default seccomp profile).

The PRoot command is the bubblewrap command translated (``sandbox.proot_command``): every path a program sees is the
one bubblewrap shows it (``/work``, ``/ora/lib``, ``/ora/observation``, ``/ora/src``, the system paths and the
interpreter), over an empty root, so the rest of the host stays out of sight. ORA's own files are copied into that
root read-only, since PRoot binds cannot be; the observation and the workspace stay bound. The environment is the
one bubblewrap sets and nothing of the host's. A fake ``proot`` that maps the paths a command starts with stands for
the host here; nothing needs ptrace.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

import pytest

from ora.agent.code_mode import sandbox
from ora.agent.code_mode.library import LIBRARY_MOUNT, LIBRARY_ROOT, OBSERVATION_MOUNT, SOURCE_MOUNT, SOURCE_ROOT
from ora.agent.code_mode.sandbox import SandboxError
from ora.agent.code_mode.shell import PROGRAM_PATH, ROBOT_LIBRARY, command_bwrap, run_command

#: Stands for PRoot: binds (``-b HOST:GUEST``) and the root (``-r``) map the start directory, ``PATH`` and
#: ``PYTHONPATH``; programs then run on the host as they are. It cannot hide anything; it carries the command.
FAKE_PROOT = """\
import os, sys
args = sys.argv[1:]
root, binds, cwd = "/", {}, "/"
while args and args[0].startswith("-"):
    option = args.pop(0)
    if option == "--kill-on-exit":
        continue
    value = args.pop(0)
    if option == "-r":
        root = value
    elif option == "-b":
        source, _, target = value.partition(":")
        binds[target or source] = source
    elif option == "-w":
        cwd = value
    elif option != "-v":
        sys.exit(f"fake proot: unknown option {option}")
def host(path):
    for target in sorted(binds, key=len, reverse=True):
        if path == target or path.startswith(target.rstrip("/") + "/"):
            return binds[target] + path[len(target):]
    return root.rstrip("/") + path
env = dict(os.environ)
for name in ("PATH", "PYTHONPATH"):
    if name in env:
        env[name] = ":".join(host(item) for item in env[name].split(":"))
os.chdir(host(cwd))
os.execve(host(args[0]), args, env)
"""


def _executable(path: Path, body: str, interpreter: str = "/bin/sh") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{interpreter}\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A host that selects PRoot, with nothing else configured."""
    monkeypatch.setenv("ORA_GPU_PREFIX", str(tmp_path / "prefix"))
    monkeypatch.setenv("ORA_CODE_MODE_SANDBOX", "proot")
    for name in ("ORA_CODE_MODE_PYTHON", "ORA_CODE_MODE_BWRAP", "ORA_CODE_MODE_PROOT"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _binds(argv: list[str]) -> dict[str, str]:
    """``{guest: host}`` of a PRoot command's ``-b HOST:GUEST`` options."""
    found = {}
    for index, item in enumerate(argv):
        if item == "-b":
            source, _, target = argv[index + 1].partition(":")
            found[target or source] = source
    return found


def _targets(argv: list[str]) -> set[str]:
    """Every path a bubblewrap command shows its programs."""
    found = set()
    for index, item in enumerate(argv):
        if item in {"--ro-bind", "--bind"}:
            found.add(argv[index + 2])
        elif item in {"--tmpfs", "--proc", "--dev"}:
            found.add(argv[index + 1])
    return found


def test_the_sandbox_is_bubblewrap_unless_the_host_names_proot(monkeypatch):
    monkeypatch.delenv("ORA_CODE_MODE_SANDBOX", raising=False)
    assert sandbox.sandbox_kind() == "bwrap"
    monkeypatch.setenv("ORA_CODE_MODE_SANDBOX", "proot")
    assert sandbox.sandbox_kind() == "proot"
    monkeypatch.setenv("ORA_CODE_MODE_SANDBOX", "nsjail")
    with pytest.raises(SandboxError, match="ORA_CODE_MODE_SANDBOX=nsjail: the sandbox is bwrap or proot"):
        sandbox.sandbox_kind()


def test_the_proot_command_shows_every_path_bubblewrap_shows_and_nothing_of_the_host(tmp_path):
    workspace, observation, stage = tmp_path / "work", tmp_path / "observation", tmp_path / "stage"
    for directory in (workspace, observation, stage):
        directory.mkdir()
    bwrap = command_bwrap(sys.executable, "bwrap", 3, workspace, "python step.py", extended=True,
                          observation=observation, source=True)
    argv, env = sandbox.proot_command(bwrap, "/opt/proot", stage)
    root = stage / "root"
    assert argv[:6] == ["/opt/proot", "--kill-on-exit", "-v", "-1", "-r", str(root)]
    assert argv[-3:] == ["/bin/bash", "-c", "python step.py"]
    assert argv[argv.index("-w") + 1] == "/work"
    assert not {"--unshare-all", "--ro-bind", "--bind", "--setenv", "--clearenv", "--chdir"} & set(argv)
    binds = _binds(argv)
    assert binds["/work"] == str(workspace) and binds[OBSERVATION_MOUNT] == str(observation)
    assert binds["/proc"] == "/proc" and binds["/dev"] == "/dev"
    for path in sandbox.SYSTEM_PATHS:
        assert (path in binds) == os.path.exists(path)
    for path in sandbox._interpreter_roots(sys.executable):
        assert binds[path] == path
    # Each path bubblewrap shows is bound, or copied into the root; a fresh /tmp is the root's own.
    for target in _targets(bwrap):
        assert target in binds or (root / target.lstrip("/")).exists(), target
    assert (root / "tmp").is_dir() and not any((root / "tmp").iterdir())
    staged = {
        f"{LIBRARY_MOUNT}/robot.py": ROBOT_LIBRARY, f"{LIBRARY_MOUNT}/sitecustomize.py": PROGRAM_PATH,
        f"{LIBRARY_MOUNT}/ora_lib/observation.py": LIBRARY_ROOT / "observation.py",
        f"{SOURCE_MOUNT}/ora/agent/toolkit/tools/move_pose/tool.py":
            SOURCE_ROOT / "ora/agent/toolkit/tools/move_pose/tool.py",
    }
    for guest, original in staged.items():
        copy = root / guest.lstrip("/")
        assert copy.read_bytes() == original.read_bytes() and not copy.is_symlink(), guest
        assert not stat.S_IMODE(copy.stat().st_mode) & 0o222, f"{guest} is read-only"
    # The environment is bubblewrap's --setenv, and nothing of the host's.
    assert env == {"ORA_CODE_MODE_FD": "3", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                   "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
                   "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin", "PYTHONPATH": LIBRARY_MOUNT,
                   "PYTHONSAFEPATH": "1", "ORA_CODE_MODE_LIBRARY": "extended", "HOME": "/work"}


def test_a_base_command_gets_the_robot_library_alone(tmp_path):
    workspace, stage = tmp_path / "work", tmp_path / "stage"
    workspace.mkdir()
    stage.mkdir()
    argv, env = sandbox.proot_command(command_bwrap(sys.executable, "bwrap", 3, workspace, "true"), "proot", stage)
    root = stage / "root"
    assert (root / "ora/robot.py").read_bytes() == ROBOT_LIBRARY.read_bytes()
    assert not (root / "ora/lib").exists() and not (root / "ora/src").exists()
    assert OBSERVATION_MOUNT not in _binds(argv) and env["PYTHONPATH"] == "/ora"


def test_proot_variables_of_the_host_reach_proot(tmp_path, monkeypatch):
    """``PROOT_NO_SECCOMP`` and the like configure PRoot itself on a host that needs them."""
    monkeypatch.setenv("PROOT_NO_SECCOMP", "1")
    monkeypatch.setenv("SECRET_API_KEY", "sk-host")
    workspace, stage = tmp_path / "work", tmp_path / "stage"
    workspace.mkdir()
    stage.mkdir()
    _, env = sandbox.proot_command(command_bwrap(sys.executable, "bwrap", 3, workspace, "true"), "proot", stage)
    assert env["PROOT_NO_SECCOMP"] == "1" and "SECRET_API_KEY" not in env


def test_a_command_runs_under_proot_and_its_stage_is_removed(host, monkeypatch):
    from ora.agent.code_mode import shell

    if sys.platform == "darwin":  # macOS refuses RLIMIT_AS; the limits are the sandbox's own, tested in test_sandbox
        monkeypatch.setattr(shell, "_limits", lambda cpu_s, memory_mib: None)
    proot = _executable(host / "bin/proot", FAKE_PROOT, interpreter=sys.executable)
    monkeypatch.setenv("ORA_CODE_MODE_PROOT", str(proot))
    monkeypatch.setenv("ORA_CODE_MODE_PYTHON", sys.executable)
    stages = host / "stages"
    stages.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(stages))
    workspace = host / "work"
    workspace.mkdir()
    (workspace / "step.py").write_text(
        "import os\nfrom robot import tools, handoff\n"
        "r = tools.robot_submit_action(x=0.5)\nprint('echo', r['result'], os.environ['HOME'])\n"
        "handoff('checked', x=0.5)\n")
    seen = []

    def answer(name, arguments):
        seen.append((name, arguments))
        return {"ok": True, "tool": name, "result": {"echo": arguments}}

    outcome = run_command("echo start; python step.py; echo never", handle_call=answer, workspace=workspace,
                          wall_s=60, cpu_s=30, memory_mib=2048)
    assert outcome.status == "handed_back", (outcome.error, outcome.output)
    assert outcome.handed_back == {"reason": "checked", "data": {"x": 0.5}} and outcome.calls == 1
    assert seen == [("robot_submit_action", {"x": 0.5})]
    assert "start" in outcome.output and "echo {'echo': {'x': 0.5}} /work" in outcome.output
    assert "never" not in outcome.output
    assert list(stages.iterdir()) == [], "the stage goes with the command"


def test_preflight_says_to_install_proot(host, monkeypatch):
    from ora.orchestrator.preflight.gates import _code_mode_sandbox

    monkeypatch.setenv("ORA_CODE_MODE_PROOT", str(host / "no-proot"))
    check = _code_mode_sandbox({"policy": {"code_mode": {}}})
    assert check["check"] == "code_mode_sandbox" and check["passed"] is False
    assert "is not an executable proot; install PRoot" in check["detail"]["reason"]
    assert "apt-get install proot" in check["detail"]["reason"]


def test_preflight_says_why_proot_cannot_trace_programs(host, monkeypatch):
    from ora.orchestrator.preflight.gates import _code_mode_sandbox

    message = "proot error: ptrace(TRACEME): Operation not permitted"
    monkeypatch.setenv("ORA_CODE_MODE_PROOT", str(_executable(host / "bin/proot", f"echo '{message}' >&2; exit 1")))
    reason = _code_mode_sandbox({"policy": {"code_mode": {}}})["detail"]["reason"]
    assert reason.startswith(f"PRoot cannot trace programs on this host ({message})")
    assert "ptrace" in reason and "PROOT_NO_SECCOMP=1" in reason


def test_the_lock_probe_runs_under_proot(host, monkeypatch):
    """With PRoot traced programs, status runs the lock probe in the sandbox: an interpreter off the lock is named."""
    from ora.agent.code_mode import shell

    if sys.platform == "darwin":  # macOS refuses RLIMIT_AS
        monkeypatch.setattr(shell, "_limits", lambda cpu_s, memory_mib: None)
    monkeypatch.setenv("ORA_CODE_MODE_PROOT", str(_executable(host / "bin/proot", FAKE_PROOT,
                                                              interpreter=sys.executable)))
    python, packages = sandbox.locked_packages(sandbox.LOCK)
    found = {"executable": "/fake/python", "python": f"{python}.9",
             "packages": {name: {"version": "0", "error": None} for name in packages}}
    fake = _executable(host / "venv/bin/python", f"echo '{json.dumps(found)}'")
    monkeypatch.setenv("ORA_CODE_MODE_PYTHON", str(fake))
    monkeypatch.setattr(sandbox, "_interpreter_roots", lambda python: (str(host / "venv"),))
    status = sandbox.sandbox_status()
    assert status["ok"] is False and status["sandbox"] == "proot"
    assert "does not match its lock" in status["reason"] and status["interpreter"]["executable"] == "/fake/python"


def test_doctor_checks_proot_and_ptrace_in_place_of_bwrap(host, monkeypatch):
    monkeypatch.setenv("ORA_CODE_MODE_PROOT", str(_executable(host / "bin/proot", "exit 0")))
    checks = {item["check"]: item for item in sandbox.prerequisite_checks()}
    assert list(checks) == ["code_mode_proot", "code_mode_ptrace", "code_mode_interpreter"]
    assert checks["code_mode_proot"] == {"check": "code_mode_proot", "passed": True,
                                         "detail": str(host / "bin/proot")}
    assert checks["code_mode_ptrace"]["passed"] is True


def test_setup_installs_no_bubblewrap_for_proot_and_warns_when_proot_is_missing(host, monkeypatch):
    from ora.app import gpu_recipes

    monkeypatch.setenv("PATH", str(host / "empty-path"))
    monkeypatch.setattr(Path, "home", lambda: host / "home")
    assert gpu_recipes._bwrap_found(host / "prefix") is True, "bubblewrap is not needed under PRoot"
    warning = gpu_recipes.sandbox_warning(["code-mode"], host / "prefix")
    assert warning is not None and "install PRoot" in warning
    monkeypatch.setenv("ORA_CODE_MODE_PROOT", str(_executable(host / "bin/proot", "exit 0")))
    assert gpu_recipes.sandbox_warning(["code-mode"], host / "prefix") is None
