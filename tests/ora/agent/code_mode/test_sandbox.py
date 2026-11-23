"""The code-mode sandbox (``ora/agent/code_mode/sandbox.py``, ``shell.py``) on a host that can run it.

With ``library: extended`` a command's programs import ``ora_lib`` and read the
current published observation, mounted read-only and brought up to date after each
call; without it neither is there.

Skipped where bubblewrap, user namespaces or the code-mode interpreter
(``ORA_CODE_MODE_PYTHON``) are missing: ``sandbox_status`` reports why, and preflight
refuses a ``policy.code_mode`` plan there.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from ora.agent.code_mode.sandbox import sandbox_status
from ora.agent.code_mode.shell import run_command

STATUS = sandbox_status()
pytestmark = pytest.mark.skipif(not STATUS["ok"], reason=f"code-mode sandbox unavailable: {STATUS.get('reason')}")
LIMITS = {"wall_s": 60, "cpu_s": 30, "memory_mib": 2048}


def answer(name: str, arguments: dict) -> dict:
    if name == "robot_submit_action" and arguments.get("x", 0) > 1:
        return {"ok": False, "error": {"code": "script_action_refused", "message": "out of reach"}}
    return {"ok": True, "tool": name, "result": {"echo": arguments}}


def program(tmp_path, code: str, **options):
    """``code`` as a ``bash`` command's program: ``python program.py`` in the workspace ``tmp_path / "work"``."""
    workspace = tmp_path / "work"
    workspace.mkdir(exist_ok=True)
    (workspace / "program.py").write_text(code)
    return run_command("python program.py", workspace=workspace, **{"handle_call": answer, **LIMITS, **options})


def test_the_host_is_out_of_reach(tmp_path) -> None:
    here = str(Path(__file__).resolve())
    code = (f"import json, os, socket\nvisible = os.path.exists({here!r})\n"
            "try:\n    socket.create_connection(('1.1.1.1', 80), timeout=2)\n    net = True\n"
            "except OSError:\n    net = False\n"
            "try:\n    open('/usr/x', 'w')\n    writable = True\nexcept OSError:\n    writable = False\n"
            "print(json.dumps([visible, net, writable]))")
    outcome = program(tmp_path, code)
    assert outcome.status == "completed", outcome.output
    visible, net, writable = json.loads(outcome.output)
    assert visible is False
    # PRoot (ORA_CODE_MODE_SANDBOX=proot) hides the host's files but has no network namespace, and its binds are
    # not read-only: only bubblewrap is held to the other two.
    if STATUS.get("sandbox") != "proot":
        assert [net, writable] == [False, False]


def test_wall_time_and_memory_are_bounded(tmp_path) -> None:
    assert program(tmp_path, "while True:\n    pass", wall_s=3).status == "timeout"
    big = program(tmp_path, "import numpy as np\nnp.ones((40000, 40000))")
    assert big.status == "failed" and "MemoryError" in big.output


@pytest.mark.parametrize("excluded", [False, True])
def test_time_the_host_excludes_from_a_call_does_not_count_against_the_wall_time(tmp_path, excluded) -> None:
    """A committed grasp's stage loop runs for minutes inside one call; the wall time is checked only between
    calls, so without the exclusion the command is stopped right after it and its result is lost."""
    import time

    spent = {"s": 0.0}

    def slow(name: str, arguments: dict) -> dict:
        time.sleep(6)
        spent["s"] += 6 if excluded else 0
        return answer(name, arguments)

    workspace = tmp_path / "work"
    workspace.mkdir()
    outcome = run_command("python -c 'from robot import tools\ntools.robot_submit_action(x=0.5)'; echo after",
                          workspace=workspace, handle_call=slow, wall_s=5, cpu_s=30, memory_mib=2048,
                          uncounted_s=lambda: spent["s"])
    assert outcome.status == ("completed" if excluded else "timeout"), outcome.output
    assert ("after" in outcome.output) is excluded
    assert outcome.calls == 1


def _slow(seconds: float, ran: list) -> Any:
    def handle(name: str, arguments: dict) -> dict:
        import time

        time.sleep(seconds)
        ran.append(name)
        return answer(name, arguments)
    return handle


def test_a_program_its_own_timeout_ends_during_a_call_leaves_a_failed_command(tmp_path) -> None:
    """Two RoboDojo programs (2026-11-03) ran under their own ``timeout`` and were ended while a long call was being
    answered; the answer then had nowhere to go and ``BrokenPipeError`` ended the run. The call ran and is recorded:
    the command now ends ``failed`` with the pipeline's exit status, what it printed and why."""
    ran: list = []
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "step.py").write_text("from robot import tools\nprint('before', flush=True)\n"
                                       "tools.robot_submit_action(x=0.5)\nprint('never')\n")
    outcome = run_command("timeout 3 python step.py 2>&1 | tail -3", workspace=workspace, handle_call=_slow(6, ran),
                          wall_s=60, cpu_s=30, memory_mib=2048)
    assert ran == ["robot_submit_action"] and outcome.calls == 1
    assert outcome.status == "failed" and outcome.exit_code == 0, (outcome.status, outcome.error)  # tail's status
    assert outcome.error["code"] == "ended_during_call" and "signal" not in outcome.error
    assert "call 1 (robot_submit_action) was being returned to it: call 1 ran to its end" in outcome.error["message"]
    assert "before" in outcome.output and "never" not in outcome.output


def test_a_program_ended_by_its_cpu_limit_between_a_call_and_its_answer_names_the_limit(tmp_path) -> None:
    ran: list = []
    code = ("import json, os, socket\nsock = socket.socket(fileno=os.dup(int(os.environ['ORA_CODE_MODE_FD'])))\n"
            "sock.sendall(json.dumps({'op': 'call', 'name': 'robot_submit_action', 'arguments': {'x': 0.5}}).encode()"
            " + b'\\n')\nprint('sent', flush=True)\nwhile True:\n    pass\n")
    outcome = program(tmp_path, code, handle_call=_slow(5, ran), cpu_s=1)
    assert ran == ["robot_submit_action"] and outcome.status == "failed", (outcome.status, outcome.error)
    assert outcome.error["signal"] == "SIGXCPU" and outcome.error["limit"] == "the CPU time limit of 1 s was reached"
    assert outcome.exit_code in (-24, 128 + 24) and "sent" in outcome.output


def test_handoff_stops_the_command_and_survives_a_broad_except(tmp_path) -> None:
    code = ("from robot import tools, handoff\ntools.robot_submit_action(x=0.5)\n"
            "try:\n    handoff('bowl not where planned', dz=0.04)\n"
            "except Exception:\n    print('swallowed')\nprint('never')")
    outcome = program(tmp_path, code)
    assert outcome.status == "handed_back" and outcome.error is None
    assert outcome.handed_back == {"reason": "bowl not where planned", "data": {"dz": 0.04}}
    assert "swallowed" not in outcome.output and "never" not in outcome.output and outcome.calls == 1


def test_a_handoff_keeps_what_a_pipeline_prints_and_acts_no_further(tmp_path) -> None:
    """``tail`` prints once ``python``'s output closes, and the command stops there: what follows in the line never runs."""
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "step.py").write_text("from robot import handoff\nprint('measured 0.04')\nhandoff('checked')\n")
    outcome = run_command("python step.py 2>&1 | tail -3; echo after", workspace=workspace, handle_call=answer,
                          wall_s=30, cpu_s=30, memory_mib=2048)
    assert outcome.status == "handed_back" and outcome.handed_back["reason"] == "checked"
    assert "measured 0.04" in outcome.output and "after" not in outcome.output


def _planner(seen: list) -> Any:
    def handle(name: str, arguments: dict) -> dict:
        seen.append(dict(arguments))
        if arguments.get("dry_run"):
            return {"ok": True, "tool": name, "dry_run": True,
                    "result": {"dry_run": True, "status": "planned", "feasible": True, "summary": "left: 12 native actions"}}
        return {"ok": True, "tool": name, "result": {"status": "reached"}}
    return handle


def test_a_program_that_reads_a_dry_run_as_a_run_is_stopped_there(tmp_path) -> None:
    """push_T-l2 (6d9621b76ddc, calls 11-13): ``set_gripper(close)`` planned and never ran; ``if g.ok`` read the plan
    as a run. A dry run's plan prints as one and its run's fields raise ``PlanNotRun``, naming the call that runs it."""
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "step.py").write_text(
        "from robot import tools\ng = tools.set_gripper(targets={'left': 'close'}, dry_run=True)\n"
        "print('repr', repr(g)[:5])\nif g.ok:\n    print('pushed with open jaws')\n")
    seen: list = []
    outcome = run_command("python step.py", workspace=workspace, handle_call=_planner(seen), wall_s=30, cpu_s=30,
                          memory_mib=2048, extended=True)
    assert outcome.status == "failed" and "repr Plan(" in outcome.output, outcome.output
    assert ("PlanNotRun: set_gripper returned a plan and nothing has run: .ok is a run's; tools.set_gripper(...) "
            "without dry_run=True runs it") in outcome.output and "pushed with open jaws" not in outcome.output
    assert [call.get("dry_run") for call in seen] == [True], "nothing ran"


def test_a_moving_call_acts_as_it_always_has(tmp_path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "step.py").write_text("from robot import tools\nprint(tools.move_to(targets={}).status)\n")
    seen: list = []
    outcome = run_command("python step.py", workspace=workspace, handle_call=_planner(seen), wall_s=30, cpu_s=30,
                          memory_mib=2048, extended=True)
    assert outcome.status == "completed" and "reached" in outcome.output and seen == [{"targets": {}}]


def test_a_workspace_persists_between_commands_and_imports(tmp_path) -> None:
    first = program(tmp_path, "open('policy.py', 'w').write('def gain():\\n    return 3\\n')\nimport os\nprint(os.getcwd())")
    assert first.status == "completed", first.output
    assert first.output.strip() == "/work" and (tmp_path / "work" / "policy.py").is_file()
    second = program(tmp_path, "import policy\nprint(policy.gain() * 2)")
    assert second.status == "completed" and second.output.strip() == "6", second.output


def test_a_bash_command_runs_programs_that_use_the_robot_library(tmp_path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "step.py").write_text(
        "import numpy as np\nfrom robot import tools, handoff\n"
        "r = tools.robot_submit_action(x=0.5)\nprint('echo', r['result'])\n"
        "if np.isclose(r['result']['echo']['x'], 0.5):\n    handoff('checked', x=0.5)\nprint('never')\n")
    outcome = run_command("echo start; python step.py; echo after", handle_call=answer, workspace=workspace,
                          wall_s=60, cpu_s=30, memory_mib=2048)
    assert outcome.status == "handed_back", (outcome.error, outcome.output)
    assert outcome.handed_back == {"reason": "checked", "data": {"x": 0.5}} and outcome.calls == 1
    assert "start" in outcome.output and "echo {'echo': {'x': 0.5}}" in outcome.output
    assert "never" not in outcome.output and "after" not in outcome.output
    plain = run_command("ls; exit 3", handle_call=answer, workspace=workspace, wall_s=60, cpu_s=30, memory_mib=2048)
    assert plain.status == "failed" and plain.exit_code == 3 and "step.py" in plain.output
    refused = run_command("python -c 'from robot import tools\ntools.robot_submit_action(x=5)'",
                          handle_call=answer, workspace=workspace, wall_s=60, cpu_s=30, memory_mib=2048)
    assert refused.status == "failed" and "ToolRefused" in refused.output


def moved(name: str, arguments: dict) -> dict:
    """A legacy action's answer: the public motion effect as the script's ``receipt``."""
    if name == "move_pose" and "position_mode" not in (arguments.get("targets") or {}).get("right", {}):
        return {"ok": False, "error": {"code": "invalid_arguments", "message": "not filled in"}}
    return {"ok": True, "tool": name, "result": {"action": name, "status": "submitted", "echo": arguments},
            "action": 1, "receipt": {"status": "reached", "failure_code": None, "failure_stage": None,
                                     "physical_cause": "unknown", "detail": {"target_arms": ["right"], "arms": {
                                         "right": {"pose_error": {"translation_m": 0.002, "orientation_deg": 1.0}}}}}}


def documentation(message: dict) -> dict:
    """A host that serves ``docs`` (``library: extended``): one function, its parts, and the index."""

    if message.get("name") is None:
        return {"ok": True, "text": "- tools.observe(views?): Look."}
    if message["name"] != "observe":
        return {"ok": False, "error": {"code": "unknown_function", "message": f"no function {message['name']!r}"}}
    return {"ok": True, "text": f"OBSERVE DOCS part={message.get('part')}"}


def explanation(message: dict) -> dict:
    """A host that serves ``explain`` and ``docs`` (``library: extended``): one call it knows."""

    if message.get("op") != "explain":
        return documentation(message)
    if message.get("call_id") != "g0#001":
        return {"ok": False, "error": {"code": "unknown_call", "message": f"no script call {message.get('call_id')!r}"}}
    return {"ok": True, "call_id": "g0#001", "text": "EXPLAIN g0#001\n"}


@pytest.mark.parametrize("extended", [False, True])
def test_a_bash_program_gets_typed_results_only_with_the_extended_library(tmp_path, extended) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "step.py").write_text(
        "from robot import tools, ToolRefused\n"
        "try:\n    r = tools.move_pose(targets={'right': {'frame': 'tcp:right', 'xyz': [0, 0, -0.05]}})\n"
        "    print('typed', type(r).__name__, r.ok, r.status, r.target_error_m)\n"
        "except ToolRefused as e:\n    print('refused', e.code, e.result)\n")
    outcome = run_command("python step.py", handle_call=moved, workspace=workspace, extended=extended,
                          wall_s=60, cpu_s=30, memory_mib=2048)
    assert outcome.status == "completed", outcome.output
    assert outcome.output.strip() == ("typed Result True reached 0.002" if extended
                                      else "refused invalid_arguments None")


def test_a_program_looks_documentation_up_over_the_rpc_without_a_call(tmp_path) -> None:
    """``robot.docs`` and ``help(tools.<name>)`` ask the host (op ``docs``); no tool call is made or counted. A host
    that serves no documentation (``library: base``) refuses the op, and ``help`` still works."""
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "look.py").write_text(
        "import robot\nfrom robot import tools, ToolRefused\n"
        "print(robot.docs('observe'))\nprint(robot.docs('observe', 'arguments'))\nprint(robot.docs())\n"
        "help(tools.observe)\n"
        "try:\n    robot.docs('nope')\nexcept ToolRefused as error:\n    print('refused', error.code)\n")
    outcome = run_command("python look.py", handle_call=answer, workspace=workspace, handle_op=documentation,
                          wall_s=60, cpu_s=30, memory_mib=2048)
    assert outcome.status == "completed", outcome.output
    lines = outcome.output.splitlines()
    assert lines[:3] == ["OBSERVE DOCS part=None", "OBSERVE DOCS part=arguments", "- tools.observe(views?): Look."]
    assert "    OBSERVE DOCS part=None" in lines, "help shows the same text"
    assert lines[-1] == "refused unknown_function" and outcome.calls == 0
    base = run_command("python -c 'import robot\nhelp(robot.tools.observe)\nrobot.docs(\"observe\")'",
                       handle_call=answer, workspace=workspace, wall_s=60, cpu_s=30, memory_mib=2048)
    assert base.status == "failed" and "Help on ToolFunction in module robot" in base.output
    assert "robot.ToolRefused: docs: docs is not served in this code mode" in base.output


def test_a_program_asks_what_a_call_did_over_the_rpc_without_a_call(tmp_path) -> None:
    """``robot.explain`` (op ``explain``) takes a call's result or id and asks the host; no tool call is made or
    counted; a call the host does not know is refused with its code. A host that serves nothing refuses the op."""
    seen = []

    def host(name: str, arguments: dict) -> dict:
        seen.append(name)
        return {"ok": True, "tool": name, "result": {}, "call_id": "g0#001"}

    code = ("import robot\nfrom robot import tools, ToolRefused\n"
            "r = tools.observe()\nprint(robot.explain(r), end='')\nprint(robot.explain('g0#001'), end='')\n"
            "try:\n    robot.explain('g0#002')\nexcept ToolRefused as error:\n    print('refused', error.code)\n")
    outcome = program(tmp_path, code, handle_call=host, handle_op=explanation, extended=True)
    assert outcome.status == "completed", outcome.output
    assert outcome.output.splitlines() == ["EXPLAIN g0#001", "EXPLAIN g0#001", "refused unknown_call"]
    assert outcome.calls == 1 and seen == ["observe"]
    base = program(tmp_path, "import robot\nrobot.explain('g0#001')", handle_call=host, extended=True)
    assert base.status == "failed" and "explain: explain is not served in this code mode" in base.output


def test_an_extended_program_imports_ora_lib_and_converts_a_point(published, tmp_path) -> None:
    """``library: extended``: ``ora_lib`` imports in a command's program, the current observation is mounted
    read-only, and a point converts with its calibration, as on the host."""
    from ora.agent.code_mode.library import observation_mount
    from ora.agent.code_mode.ora_lib import Point
    from ora.agent.code_mode.ora_lib import observation as library_observation

    code = ("import json\nfrom ora_lib import Point, pixels_to_world, current\n"
            "p = Point([0.1, -0.2, 0.9], frame='world').to('camera:head')\n"
            "lifted = pixels_to_world('head', [[u, 240] for u in range(0, 640, 20)])\n"
            "try:\n    open('/ora/observation/current.json', 'w')\n    writable = True\n"
            "except OSError:\n    writable = False\n"
            "print(json.dumps({'p': p.to_json(), 'observation': current().observation_id, 'lifted': len(lifted),\n"
            "                  'writable': writable}))\n")
    with observation_mount({"library": "extended"}, published.control_root) as mount:
        expected = Point([0.1, -0.2, 0.9], "world").to("camera:head",
                                                        observation=library_observation.load(str(mount.root)))
        outcome = program(tmp_path, code, extended=True, observation=mount)
    assert outcome.status == "completed", outcome.output
    value = json.loads(outcome.output)
    assert value["p"]["frame"] == "camera:head"
    np.testing.assert_allclose(value["p"]["xyz"], expected.xyz, rtol=0, atol=1e-12)
    assert value["observation"] == "observation-0001" and value["lifted"] == 32 and value["writable"] is False


SHAPE = """
from robot import tools
from ora_lib import Direction, Point, Pose, current
import numpy as np
print(tools.wait_ticks(15)["result"]["echo"])
print(np.round(Direction([2.4, 0, 1.4], frame="world")).tolist(), np.array(Point([1, 2, 3], frame="world")).tolist())
print(Direction([0, 1, 0], frame="world").xyz.tolist())
print(current().robot_facts["end_effectors"]["left"]["gripper"]["command_target"])
print(current().end_effectors["left"]["gripper"]["measured_opening_normalized"], current().robot_facts["mobile_base"]["x_m"])
for attempt in (lambda: tools.wait_ticks(1, 2), lambda: tools.wait_ticks(1, ticks=2),
                lambda: Pose([0, 0, 1], [0, 0, 0, 1], frame="world").quaternion, lambda: current().mobile_base,
                lambda: tools.wait_ticks(ticks=3)["candidates"], lambda: tools.wait_ticks(ticks=3)["pose"],
                lambda: np.add(Point([1, 2, 3], frame="world"), Point([1, 2, 3], frame="tcp:left"))):
    try:
        attempt()
    except Exception as error:
        print(type(error).__name__, error)
print(tools.wait_ticks.__doc__.splitlines()[0])
help(tools.wait_ticks)
"""


def signatures(message: dict) -> dict:
    return {"ok": True, "text": f"tools.{message['name']}(ticks)\n\nHold."}


def shaped(name: str, arguments: dict) -> dict:
    return {"ok": True, "tool": name, "result": {"echo": arguments, "candidates": []}}


def test_an_extended_program_gets_the_shape_real_scripts_reached_for(published, tmp_path) -> None:
    """In the sandbox's own interpreter: positional arguments, ``Direction.xyz``, NumPy on values, the facts the
    observation message shows and errors that say what is there; a ufunc still refuses."""
    from ora.agent.code_mode.library import observation_mount

    context = published.control_root.parent / "sandbox/workspace/inputs/current-context.json"
    context.parent.mkdir(parents=True)
    gripper = {"command_target": "open", "measured_opening_normalized": 0.25}
    context.write_text(json.dumps({"public_robot_facts": {
        "observation_id": "observation-0001", "end_effectors": {"left": {"gripper": gripper}},
        "mobile_base": {"x_m": 0.75}}}))
    with observation_mount({"library": "extended"}, published.control_root) as mount:
        outcome = program(tmp_path, SHAPE, handle_call=shaped, handle_op=signatures, extended=True, observation=mount)
    assert outcome.status == "completed", outcome.output
    lines = outcome.output.splitlines()
    assert lines[:5] == ["{'ticks': 15}", "[2.0, 0.0, 1.0] [1.0, 2.0, 3.0]", "[0.0, 1.0, 0.0]", "open", "0.25 0.75"]
    assert lines[5] == "TypeError tools.wait_ticks(ticks) takes 1 argument(s), not 2"
    assert lines[6] == "TypeError tools.wait_ticks(ticks) got ticks twice, by position and by name"
    assert lines[7].startswith("AttributeError 'Pose' object has no attribute 'quaternion'; it has: axis, frame, matrix")
    assert lines[8].startswith("AttributeError 'Observation' object has no attribute 'mobile_base'; it has: ")
    assert "robot_facts" in lines[8]
    assert lines[9] == """KeyError 'candidates is under result: r["result"]["candidates"]'"""
    assert lines[10] == "KeyError 'no pose in this answer; its keys are ok, tool, result'"
    assert lines[11].startswith("TypeError operand 'Point' does not support ufuncs")
    assert lines[12] == "tools.wait_ticks(ticks)" and "    tools.wait_ticks(ticks)" in lines[13:], "help as before"


def test_a_base_program_stays_as_it_was(tmp_path) -> None:
    outcome = program(tmp_path, "from robot import tools\ntools.wait_ticks(15)")
    assert outcome.status == "failed" and outcome.calls == 0
    assert "TypeError: ToolFunction.__call__() takes 1 positional argument but 2 were given" in outcome.output
    keyed = program(tmp_path, "from robot import tools\nr = tools.wait_ticks(ticks=1)\nr['candidates']")
    assert keyed.status == "failed" and keyed.output.splitlines()[-1] == "KeyError: 'candidates'"


# --- a workspace file and the sandbox's own imports ----------------------------------------------------------------

#: A workspace file that says so if anything imports it. ``inspect`` is what broke NumPy and ``ora_lib`` in the
#: comparison; ``gc`` is a built-in module in this interpreter, so its file never did.
TRAP = "raise RuntimeError('the workspace file was imported')\n"
SHADOWS = ("inspect", "typing", "random", "gc", "numpy", "scipy", "robot", "ora_lib")
IMPORTS = "import numpy, scipy.ndimage, robot, ora_lib; print(numpy.__file__, robot.__file__, ora_lib.__file__)"


def _workspace(tmp_path: Path, files: dict[str, str]) -> Path:
    workspace = tmp_path / "work"
    for name, text in files.items():
        (workspace / name).parent.mkdir(parents=True, exist_ok=True)
        (workspace / name).write_text(text)
    workspace.mkdir(exist_ok=True)
    return workspace


def _command(command: str, workspace: Path, *, extended: bool = True):
    return run_command(command, handle_call=answer, workspace=workspace, extended=extended, **LIMITS)


@pytest.mark.parametrize("name", SHADOWS)
@pytest.mark.parametrize("command", [f'python -c "{IMPORTS}"', "python step.py", "cd /tmp && python /work/step.py"],
                         ids=["c", "file", "elsewhere"])
def test_a_workspace_file_named_after_a_module_breaks_no_import_of_a_program(tmp_path, name, command) -> None:
    workspace = _workspace(tmp_path, {f"{name}.py": TRAP, "step.py": IMPORTS + "\n"})
    outcome = _command(command, workspace)
    assert outcome.status == "completed", outcome.output
    numpy_file, robot_file, library_file = outcome.output.split()
    assert "site-packages" in numpy_file and robot_file == "/ora/lib/robot.py"
    assert library_file == "/ora/lib/ora_lib/__init__.py"


@pytest.mark.parametrize("command", [
    pytest.param("python step.py", id="a-file-in-work"),
    pytest.param("python /work/step.py", id="by-its-whole-path"),
    pytest.param("python -m step", id="dash-m"),
    pytest.param("python -c 'import policy; print(policy.GAIN)'", id="dash-c"),
    pytest.param("python - <<'EOF'\nimport policy\nprint(policy.GAIN)\nEOF", id="stdin-dash"),
    pytest.param("python <<'EOF'\nimport policy\nprint(policy.GAIN)\nEOF", id="stdin"),
    pytest.param("python sub/beside.py", id="beside-a-script-in-a-subdirectory"),
    pytest.param("python /work/sub/beside.py", id="beside-it-by-its-whole-path"),
    pytest.param("cd sub && python beside.py", id="from-the-subdirectory"),
    pytest.param("cd sub && python -c 'import helper; print(helper.GAIN)'", id="dash-c-in-the-subdirectory"),
    pytest.param("cd sub && python -m beside", id="dash-m-in-the-subdirectory"),
    pytest.param("cd sub && python - <<'EOF'\nimport helper\nprint(helper.GAIN)\nEOF", id="stdin-dash-in-the-subdirectory"),
    pytest.param("cd sub && python <<'EOF'\nimport helper\nprint(helper.GAIN)\nEOF", id="stdin-in-the-subdirectory"),
    pytest.param("python -m sub.beside", id="dash-m-of-a-package-path"),
])
def test_a_program_s_own_modules_still_import(tmp_path, command) -> None:
    """From ``/work``, beside a script in a subdirectory, with ``python -m`` and ``python -c`` and on standard input,
    the workspace holding an ``inspect.py`` all the while."""
    workspace = _workspace(tmp_path, {
        "inspect.py": TRAP, "policy.py": "GAIN = 7\n", "step.py": "import numpy, policy\nprint(policy.GAIN)\n",
        "sub/helper.py": "GAIN = 7\n", "sub/beside.py": "import numpy, helper\nprint(helper.GAIN)\n"})
    if "sub.beside" in command:
        (workspace / "sub/beside.py").write_text("from sub import helper\nprint(helper.GAIN)\n")
    outcome = _command(command, workspace)
    assert outcome.status == "completed" and outcome.output.strip() == "7", outcome.output


def test_running_a_workspace_file_named_after_a_module_runs_that_file(tmp_path) -> None:
    """It is the program, and what it imports is still the real thing."""
    text = ("import numpy, inspect\nfrom ora_lib import offset\n"
            "print('mine', __file__, inspect.__file__.endswith('python3.11/inspect.py'))\n")
    workspace = _workspace(tmp_path, {"inspect.py": text})
    for command in ("python /work/inspect.py", "python inspect.py"):
        outcome = _command(command, workspace)
        assert outcome.status == "completed", outcome.output
        assert outcome.output.split()[0] == "mine" and outcome.output.split()[-1] == "True"


def test_a_base_contract_keeps_python_s_own_path_and_so_the_shadowing(tmp_path) -> None:
    """The control arm as it was: the workspace first."""
    workspace = _workspace(tmp_path, {"inspect.py": TRAP})
    outcome = _command("python -c 'import sys; print(repr(sys.path[:2]), sys.flags.safe_path)'", workspace,
                       extended=False)
    assert outcome.status == "completed" and outcome.output.strip() == "['', '/ora'] False", outcome.output
    assert _command("env | grep -c PYTHONSAFEPATH", workspace, extended=False).output.strip() == "0"
    assert "workspace file was imported" in _command("python -c 'import numpy'", workspace, extended=False).output


def test_a_program_reads_the_observation_a_call_published(published, tmp_path) -> None:
    """After a call publishes the next observation (an action), the program's next read is that observation."""
    import hashlib

    from ora.agent.code_mode.library import observation_mount

    pointer_path = published.control_root / "current-observation.json"
    first = "robodojo:general_pickup:layout-0:observation-0000"
    manifest = published.control_root.parent / "policy-observations" / first / "observation.json"

    def act(name: str, arguments: dict) -> dict:
        pointer = json.loads(pointer_path.read_text())
        pointer.update(observation_id=first, policy_state={"step": 0},
                       observation_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
        pointer_path.write_text(json.dumps(pointer))
        return {"ok": True, "tool": name, "result": {}}

    code = ("import json\nfrom robot import tools\nfrom ora_lib import current\n"
            "before = current().observation_id\ntools.move_pose(x=1)\n"
            "print(json.dumps([before, current().observation_id, current().step]))")
    with observation_mount({"library": "extended"}, published.control_root) as mount:
        outcome = program(tmp_path, code, handle_call=act, extended=True, observation=mount)
    assert outcome.status == "completed", outcome.output
    assert json.loads(outcome.output) == ["observation-0001", first, 0]


def test_an_action_s_result_carries_the_pose_in_the_observation_it_published(published, tmp_path) -> None:
    """``Result.pose_after`` is read in the sandbox from the mount the host brought up to date before the answer
    returned: the measured pose of the targeted arm in the observation the action published."""
    import hashlib

    from ora.agent.code_mode.library import observation_mount

    pointer_path = published.control_root / "current-observation.json"
    first = "robodojo:general_pickup:layout-0:observation-0000"
    root = published.control_root.parent / "policy-observations" / first

    def act(name: str, arguments: dict) -> dict:
        pointer = json.loads(pointer_path.read_text())
        pointer.update(observation_id=first, policy_state={"step": 0},
                       observation_sha256=hashlib.sha256((root / "observation.json").read_bytes()).hexdigest())
        pointer_path.write_text(json.dumps(pointer))
        return {"ok": True, "tool": name, "action": 1, "result": {"status": "submitted"},
                "receipt": {"status": "reached", "detail": {"target_arms": ["left"]}}}

    code = ("import json\nfrom robot import tools\nr = tools.move_pose(targets={})\n"
            "print(json.dumps([r.pose_after, r.arms['right'].pose_after, r.ok, r['action']]))")
    measured = json.loads((root / "proprioception.json").read_text())["end_effectors"]
    with observation_mount({"library": "extended"}, published.control_root) as mount:
        outcome = program(tmp_path, code, handle_call=act, extended=True, observation=mount)
    assert outcome.status == "completed", outcome.output
    assert json.loads(outcome.output) == [measured["left"]["pose_xyz_xyzw"], measured["right"]["pose_xyz_xyzw"], True, 1]


def test_a_program_measures_a_target_a_call_recorded_with_no_opencv(published, tmp_path) -> None:
    """``object_geometry`` runs in the sandbox interpreter (NumPy and SciPy, no OpenCV): the mask of the target a
    ``vision_ground`` call recorded is mounted when that call returns, and its measurement is the tool's."""
    from ora.agent.code_mode.library import observation_mount
    from ora.agent.toolkit.tools.vision_ground.tool import run as ground
    from tests.support.agent.toolkit_tools import api_for

    expected: dict = {}

    def look(name: str, arguments: dict) -> dict:
        result = ground(api_for(published), dict(arguments))
        expected.update(result["candidates"][0]["localization"])
        return {"ok": True, "tool": name, "result": {key: value for key, value in result.items()
                                                      if key != "images_base64"}}

    code = ("import json, robot, sys\nfrom robot import tools\nfrom ora_lib import object_geometry\n"
            "candidate = tools.vision_ground(camera='head', text='object', max_candidates=1)['result']['candidates'][0]\n"
            "g = object_geometry(candidate)\n"
            "print(json.dumps(robot._json_safe({'g': dict(g), 'center': g.center, 'axis': g.largest_axis,\n"
            "                                   'cv2': 'cv2' in sys.modules})))")
    with observation_mount({"library": "extended"}, published.control_root) as mount:
        outcome = program(tmp_path, code, handle_call=look, extended=True, observation=mount)
    assert outcome.status == "completed", outcome.output
    found = json.loads(outcome.output)
    assert found["cv2"] is False and found["g"]["status"] == "localized" and found["center"]["frame"] == "world"
    for name in ("center_xyz_m", "extent_xyz_m", "principal_axes_world", "support_z_m", "height_above_support_m",
                 "mid_height_z_m", "largest_extent_axis_world"):
        np.testing.assert_allclose(found["g"][name], expected[name], rtol=0, atol=1e-9, err_msg=name)
    np.testing.assert_allclose(found["center"]["xyz"], expected["center_xyz_m"], rtol=0, atol=1e-9)
    np.testing.assert_allclose(found["axis"]["v"], expected["largest_extent_axis_world"], rtol=0, atol=1e-9)


def test_a_base_program_has_neither_ora_lib_nor_the_observation(tmp_path) -> None:
    code = ("import json, os\ntry:\n    import ora_lib\n    found = True\nexcept ImportError:\n    found = False\n"
            "print(json.dumps([found, os.path.exists('/ora/lib'), os.path.exists('/ora/observation')]))")
    outcome = program(tmp_path, code)
    assert outcome.status == "completed" and json.loads(outcome.output) == [False, False, False], outcome.output
    command = run_command("python -c 'import ora_lib'; ls /ora", handle_call=answer, workspace=tmp_path / "work",
                          wall_s=60, cpu_s=30, memory_mib=2048)
    assert "ModuleNotFoundError" in command.output and command.output.split()[-1:] == ["robot.py"]
