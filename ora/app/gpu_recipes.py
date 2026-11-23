"""The single-GPU installation recipes behind ``ora setup --env``.

Every command here is a step of ``docs/getting-started/single-gpu.md``
§9 as verified on 2026-10-14: cu128 Torch in every simulator environment,
RoboTwin's requirements without its torch/torchvision pins, CuRobo built for
this GPU's compute capability, SAM3 from ModelScope with the pinned SHA-256
and the ``setup-robodojo`` stages including ``graphics-compat``. Pins are
duplicated from their canonical modules so this file imports nothing heavy;
``tests/ora/app/test_gpu_setup.py`` keeps them equal.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from ora.orchestrator.episode import agent_core
from ora.orchestrator.backends.registry import backend
from ora.version import ora_command
from .gpu_setup import Context, GpuSetupError, Step

TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
LIBERO_CODE = ("https://github.com/Zxy-MLlab/LIBERO-PRO.git", "2b910b5b5f53016bef9907632f6f840f1ce2229c")
LIBERO_DATA = ("https://huggingface.co/datasets/zhouxueyang/LIBERO-Pro", "c86fc3b8293185a6f373677018ff3e37f8391602")
ROBOTWIN = ("https://github.com/RoboTwin-Platform/RoboTwin.git", "c3ddfa8b97d5519efa828b075999bd0006778e5e")
#: The vendored install's deterministic commit on top of ``ROBOTWIN``.
ROBOTWIN_INSTALL_REVISION = "cf4872cc90538fd4bca761c8db912d8f65e49f2b"
CUROBO = ("https://github.com/NVlabs/curobo.git", "d64c4b005459db10c5dd867d8b30a87d5bda9bdb")
CAPX = ("https://github.com/capgym/cap-x.git", "53e9966d7a8e2fa7494676772bccc35280f5c0ed")
SAM3_SOURCE_COMMIT = "6fe87d64a5beb9084923d7a9e002741178635b09"
CONTACT_SOURCE_COMMIT = "da3dcfb2f53e43b186083ee4a9d1e232f73efc98"
SAM3_URL = "https://modelscope.cn/models/facebook/sam3/resolve/master/sam3.pt"
SAM3_SHA256 = "9999e2341ceef5e136daa386eecb55cb414446a00ac2b55eb2dfd2f7c3cf8c9e"
CONTACT_CHECKPOINT_SHA256 = "39fc3439d5814043ba64e0715c127c2e8aca6ea376c22614e11af1bc89317762"
CONTACT_CONFIG_SHA256 = "7ae6cc15c726e8ec2c46b0c799d58ca95caeb9ce9f261aaaf3e467712df76ac5"
ROBOTWIN_ASSET_REPOSITORY = "TianxingChen/RoboTwin2.0"
ROBOTWIN_ASSET_ZIPS = ("background_texture.zip", "embodiments.zip", "objects.zip")
HF_HUB = "huggingface-hub==1.27.0"
#: Pinned direct dependencies of the isolated worker and service environments on Linux x86_64, kept
#: beside the code they serve (Torch comes from the CUDA wheel index; neither file is a transitive lock).
LIBERO_REQUIREMENTS = "ora/environment/backends/libero_pro/installation/requirements-x86_64.txt"
#: The timing interpreter's packages (``ora/environment/planners/timing/requirements.py``, which nothing in
#: ``ora`` imports), kept equal by ``tests/ora/app/test_gpu_setup.py``.
TIMING_PINS = (
    "contourpy==1.3.3", "cycler==0.12.1", "fonttools==4.68.0",
    "kiwisolver==1.5.1", "matplotlib==3.11.2", "numpy==1.26.4", "packaging==26.3", "pillow==13.0.0",
    "pyparsing==3.3.3", "python-dateutil==3.0.0", "ruckig==0.19.4", "scipy==1.17.1", "six==1.17.0",
    "toppra==0.6.10", "tzdata==2026.6",
)
PERCEPTION_REQUIREMENTS = "ora/agent/model_services/installation/requirements-x86_64.txt"
#: The code-mode sandbox interpreter's lock (``ora/agent/code_mode/sandbox.py``: ``LOCK``, built as ``LOCK_BUILD``
#: says, at ``DEFAULT_PYTHON`` under the prefix), kept equal by ``tests/ora/app/test_gpu_setup.py``.
CODE_MODE_LOCK = "ora/agent/code_mode/sandbox-requirements.txt"

#: Install order; an environment pulls in the components it runs on (its backend's ``gpu_setup``
#: hook, backends/registry.py). ``agent-core`` (npm ci in ora-pi) also installs the terminal UI,
#: which lives in ora-pi/src/watch. ``code-mode`` is the sandbox interpreter of an environment whose
#: default policy runs code mode (RoboDojo).
COMPONENTS = ("agent-core", "perception", "robotwin-python", "robotwin-assets", "libero", "robodojo",
              "motion-timing", "code-mode")
#: ``ora setup --env perception``: the services alone.
PERCEPTION = ("agent-core", "perception")
#: Upper bounds from the verification machine, in GB, including caches and zips.
DISK_GIB = {"agent-core": 1, "perception": 12, "robotwin-python": 12, "robotwin-assets": 32,
            "libero": 10, "robodojo": 90, "motion-timing": 1, "code-mode": 1}

_GRASP = "sources/cap-x/capx/third_party/contact_graspnet_pytorch"
_GRASP_CHECKPOINT = f"{_GRASP}/checkpoints/contact_graspnet/checkpoints/model.pt"
_GRASP_CONFIG = f"{_GRASP}/checkpoints/contact_graspnet/config.yaml"


def requires(environment: str) -> tuple[str, ...]:
    """The components ``ora setup --env ENV`` installs: the services', or the simulator backend's."""
    if environment == "perception":
        return PERCEPTION
    components = backend(environment).gpu_setup
    if components is None:
        raise GpuSetupError(f"ora setup has no recipe for {environment}")
    return components


def components_for(environments: Sequence[str]) -> list[str]:
    wanted = {component for name in environments for component in requires(name)}
    return [component for component in COMPONENTS if component in wanted]


def disk_gib(components: Sequence[str]) -> int:
    return sum(DISK_GIB[component] for component in components)


def _python(context: Context, name: str) -> Path:
    return context.path(f"envs/{name}/bin/python")


def _sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _file_sha256(path: Path) -> str | None:
    return _sha256(path) if path.is_file() else None


# --- toolchain ----------------------------------------------------------------------------------

def _cuda_home(context: Context) -> Path:
    candidates = [context.cuda_home] if context.cuda_home else [
        Path(context.environ["CUDA_HOME"]) if context.environ.get("CUDA_HOME") else None,
        Path("/usr/local/cuda-12.8"), Path("/usr/local/cuda")]
    for candidate in candidates:
        if candidate is not None and (candidate / "bin/nvcc").is_file():
            return candidate
    raise GpuSetupError("CuRobo needs the CUDA 12.8 toolkit (nvcc); the driver alone is not enough. "
                        "Install it or pass --cuda-home /path/to/cuda-12.8")


def cuda_arch(context: Context) -> str:
    """``TORCH_CUDA_ARCH_LIST`` for this GPU, e.g. ``12.0+PTX``."""
    value = context.cuda_arch or context.output(
        ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"]).split("\n")[0].strip()
    if not re.fullmatch(r"\d+\.\d+", value or ""):
        raise GpuSetupError("cannot read the GPU compute capability from nvidia-smi; pass --cuda-arch, e.g. 12.0")
    return f"{value}+PTX"


#: Shared libraries Isaac Sim loads from the system that minimal server images
#: lack (Ubuntu package names). Without them MDL materials never load and the
#: RoboDojo cameras time out, and the graphics-compat readback cannot open Vulkan.
SYSTEM_LIBRARIES = {"libGLU.so.1": "libglu1-mesa", "libvulkan.so.1": "libvulkan1"}
#: Where ``ora setup`` unpacks those packages without sudo; ``ora setup`` and
#: ``ora run`` put it on LD_LIBRARY_PATH, which every worker inherits.
SYSTEM_LIBRARY_DIR = "syslibs/usr/lib/x86_64-linux-gnu"


def _loadable(name: str) -> bool:
    import ctypes

    try:
        ctypes.CDLL(name)
    except OSError:
        return False
    return True


def missing_system_libraries(prefix: Path) -> list[str]:
    local = prefix / SYSTEM_LIBRARY_DIR
    return [name for name in SYSTEM_LIBRARIES if not (local / name).exists() and not _loadable(name)]


def add_library_path(environ, prefix: Path) -> None:
    """Prepend the prefix's unpacked system libraries to ``environ``'s LD_LIBRARY_PATH when present."""
    local = str(prefix / SYSTEM_LIBRARY_DIR)
    current = environ.get("LD_LIBRARY_PATH", "")
    if (prefix / SYSTEM_LIBRARY_DIR).is_dir() and local not in current.split(os.pathsep):
        environ["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, (local, current)))


#: Host tools RoboDojo needs that ``ora setup`` installs under the prefix when the machine lacks them, without
#: sudo: git-lfs for the assets (a static release, checked against its published SHA-256) and bubblewrap for
#: code mode's sandbox (the distribution package, unpacked as the system libraries above are).
GIT_LFS = ("3.7.1", "1c0b6ee5200ca708c5cebebb18fdeb0e1c98f1af5c1a9cba205a4c0ab5a5ec08")
TOOLS_BIN = "tools/bin"
BWRAP_IN_PREFIX = "tools/usr/bin/bwrap"


def add_tool_path(environ, prefix: Path) -> None:
    """Prepend the prefix's own tools (git-lfs) to ``environ``'s PATH when present."""
    local = str(prefix / TOOLS_BIN)
    current = environ.get("PATH", "")
    if (prefix / TOOLS_BIN).is_dir() and local not in current.split(os.pathsep):
        environ["PATH"] = os.pathsep.join(filter(None, (local, current)))


def _x86_64_linux() -> bool:
    return os.uname().sysname == "Linux" and os.uname().machine in ("x86_64", "amd64")


def git_lfs(context: Context) -> Step:
    def found(inner: Context) -> bool:
        return shutil.which("git-lfs", path=inner.environ.get("PATH")) is not None

    def run(inner: Context) -> None:
        version, digest = GIT_LFS
        if not _x86_64_linux():
            raise GpuSetupError("RoboDojo's assets need git-lfs; install it (https://git-lfs.com) and rerun")
        name = f"git-lfs-linux-amd64-v{version}.tar.gz"
        archive = inner.path(f"downloads/{name}")
        archive.parent.mkdir(parents=True, exist_ok=True)
        inner.run(["curl", "-fL", "--retry", "3", "-o", archive,
                   f"https://github.com/git-lfs/git-lfs/releases/download/v{version}/{name}"], timeout=1800)
        if _sha256(archive) != digest:
            archive.unlink()
            raise GpuSetupError(f"{name} does not match its published SHA-256")
        unpacked = inner.path("downloads/git-lfs")
        shutil.rmtree(unpacked, ignore_errors=True)
        unpacked.mkdir(parents=True)
        inner.run(["tar", "-xzf", archive, "-C", unpacked, "--strip-components=1"])
        inner.path(TOOLS_BIN).mkdir(parents=True, exist_ok=True)
        shutil.copy2(unpacked / "git-lfs", inner.path(TOOLS_BIN) / "git-lfs")
        add_tool_path(inner.environ, inner.prefix)

    return Step("robodojo", "git-lfs", f"git-lfs {GIT_LFS[0]} (only when the machine has none)", run,
                marker=False, done=found)


def _bwrap_found(prefix: Path) -> bool:
    """True when bubblewrap is found, or not needed: a host that runs code mode under PRoot
    (``ORA_CODE_MODE_SANDBOX=proot``) installs none."""
    from ora.agent.code_mode.sandbox import SandboxError, bwrap_path, sandbox_kind

    try:
        if sandbox_kind() == "proot":
            return True
    except SandboxError:
        pass
    if os.access(prefix / BWRAP_IN_PREFIX, os.X_OK):
        return True
    try:
        bwrap_path()
    except SandboxError:
        return False
    return True


def bubblewrap(context: Context) -> Step:
    def run(inner: Context) -> None:
        if shutil.which("apt-get") is None or shutil.which("dpkg") is None:
            raise GpuSetupError("code mode, the RoboDojo default, needs bubblewrap (bwrap); install it with your "
                                "package manager and rerun")
        download = inner.path("downloads/bubblewrap")
        download.mkdir(parents=True, exist_ok=True)
        inner.run(["apt-get", "download", "bubblewrap"], cwd=download)
        for deb in sorted(download.glob("bubblewrap*.deb")):
            inner.run(["dpkg", "-x", deb, inner.path("tools")])
        if not os.access(inner.path(BWRAP_IN_PREFIX), os.X_OK):
            raise GpuSetupError(f"the bubblewrap package did not unpack {BWRAP_IN_PREFIX}")

    return Step("code-mode", "bubblewrap", "bubblewrap for code mode's sandbox (only when the machine has none)",
                run, marker=False, done=lambda inner: _bwrap_found(inner.prefix))


def check_toolchain(context: Context, components: Sequence[str]) -> None:
    """Refuse before any download when a required tool is missing."""
    missing = [tool for tool in ("git", "uv") if shutil.which(tool) is None]
    if "perception" in components and shutil.which("curl") is None:
        missing.append("curl")
    if "robotwin-assets" in components and shutil.which("unzip") is None:
        missing.append("unzip")
    if "robodojo" in components:
        missing += [tool for tool, found in (("git-lfs", shutil.which("git-lfs") or _x86_64_linux()),
                                             ("a C++ compiler (c++)", shutil.which("c++") or shutil.which("g++")))
                    if not found]
    if missing:
        raise GpuSetupError("missing on PATH: " + ", ".join(missing)
                            + " (docs/getting-started/single-gpu.md §2 installs them without sudo; "
                            "scripts/install.sh installs uv and Node 22)")
    if shutil.which("nvidia-smi") is None:
        raise GpuSetupError("nvidia-smi is unavailable; the single-GPU path needs an NVIDIA driver (R570+)")
    if "agent-core" in components:
        _npm(context)  # Node.js >= 22.19 with npm, as the Pi agent core needs
    if "robotwin-python" in components:
        _cuda_home(context)
        cuda_arch(context)


# --- shared step builders -----------------------------------------------------------------------

def _head(context: Context, path: Path) -> str:
    return context.output(["git", "-C", path, "rev-parse", "HEAD"])


def _clean(context: Context, path: Path) -> bool:
    # A failed or timed-out status is not evidence of a clean tree.
    return (path / ".git").exists() and context.probe(
        ["git", "-C", path, "status", "--porcelain", "--untracked-files=no"], timeout=600) == ""


def _at(context: Context, path: Path, revisions: Sequence[str]) -> bool:
    return (path / ".git").exists() and _head(context, path) in revisions and _clean(context, path)


def clone_step(component: str, name: str, relative: str, url: str, revision: str, *,
               accepted: Sequence[str] = (), lfs_skip: bool = False) -> Step:
    """Clone ``url`` and detach at ``revision`` (or accept a later pinned state).

    An existing checkout at another revision or with tracked edits is refused,
    never reset.
    """
    revisions = (revision, *accepted)

    def run(context: Context) -> None:
        path = context.path(relative)
        if path.exists():
            raise GpuSetupError(f"{path} exists but is not a clean checkout of {revision[:12]}; "
                                "move it away or fix it, then rerun ora setup")
        path.parent.mkdir(parents=True, exist_ok=True)
        env = {"GIT_LFS_SKIP_SMUDGE": "1"} if lfs_skip else None
        clone = ["git", "clone", *(["--no-checkout"] if lfs_skip else []), url, path]
        context.run(clone, env=env, timeout=7200)
        context.run(["git", "-C", path, "checkout", "--detach", revision], env=env, timeout=3600)
        # Record stat data once so later read-only status checks stay fast.
        context.run(["git", "-C", path, "update-index", "-q", "--refresh"], timeout=3600)

    return Step(component, name, f"clone {Path(relative).name} @ {revision[:12]}", run,
                done=lambda context: _at(context, context.path(relative), revisions), marker=False)


def venv_step(component: str, name: str, python_version: str, *, seed: bool = True) -> Step:
    def run(context: Context) -> None:
        context.run(["uv", "venv", "--python", python_version, *(["--seed"] if seed else []),
                     context.path(f"envs/{name}")])

    def done(context: Context) -> bool:
        config = context.path(f"envs/{name}/pyvenv.cfg")
        pattern = rf"^(version|version_info)\s*=\s*{re.escape(python_version)}(\.|\s*$)"
        return config.is_file() and re.search(pattern, config.read_text(), re.M) is not None

    return Step(component, f"venv-{name}", f"Python {python_version} environment envs/{name}", run,
                done=done, marker=False)


def pip_step(component: str, name: str, env: str, title: str, arguments: Sequence[str], *,
             recipe_extra: Mapping[str, Any] | None = None, environment=None) -> Step:
    """``uv pip install --python <env> <arguments>``; done when the same recipe was installed."""
    def run(context: Context) -> None:
        context.run(["uv", "pip", "install", "--python", _python(context, env), *arguments],
                    env=environment(context) if environment else None)

    def recipe(context: Context) -> Mapping[str, Any]:
        extra = recipe_extra(context) if callable(recipe_extra) else (recipe_extra or {})
        return {"python": str(_python(context, env)), "environment": _venv_identity(context, env),
                "arguments": list(arguments), **extra}

    return Step(component, name, title, run, recipe=recipe,
                done=lambda context: _python(context, env).is_file())


def _venv_identity(context: Context, name: str) -> list[int] | None:
    """Binds package markers to one virtualenv instance: a recreated env reinstalls."""
    try:
        stat = context.path(f"envs/{name}/pyvenv.cfg").stat()
    except OSError:
        return None
    return [stat.st_ino, stat.st_mtime_ns]


def torch_step(component: str, env: str, torch: str, torchvision: str) -> Step:
    return pip_step(component, "torch", env, f"Torch {torch} / torchvision {torchvision} (cu128)",
                    [f"torch=={torch}", f"torchvision=={torchvision}", "--index-url", TORCH_INDEX])


def _npm(context: Context) -> tuple[str, dict[str, str]]:
    """npm beside the agent core's Node, and a PATH that finds that Node first."""
    try:
        node, npm = agent_core.setup_requirement(context.environ)
    except agent_core.AgentCoreError as exc:
        raise GpuSetupError(str(exc)) from None
    return npm, {"PATH": os.pathsep.join(filter(None, (str(Path(node).parent), context.environ.get("PATH"))))}


# --- components ---------------------------------------------------------------------------------

def agent_core_packages(context: Context) -> list[Step]:
    """The locked Pi packages of the default agent core; done when ``node_modules`` matches the lock."""
    root = context.project_root / "ora-pi"

    def run(inner: Context) -> None:
        npm, env = _npm(inner)
        try:
            inner.run([npm, *agent_core.NPM_CI, "--prefix", root], env=env, timeout=1200)
        except GpuSetupError as exc:
            raise GpuSetupError(agent_core.npm_ci_failure(str(exc))) from None
        status = agent_core.pi_dependencies(root)
        if not status["passed"]:
            raise GpuSetupError(f"npm ci in ora-pi finished but {status['detail']}")

    return [Step("agent-core", "npm-ci", "ORA agent core packages (npm ci in ora-pi)", run,
                 done=lambda inner: agent_core.pi_dependencies(root)["passed"], marker=False)]


def perception(context: Context) -> list[Step]:
    url, revision = CAPX
    capx = "sources/cap-x"

    def submodules_done(inner: Context) -> bool:
        root = inner.path(capx)
        return (_head(inner, root / "capx/third_party/sam3") == SAM3_SOURCE_COMMIT
                and _head(inner, root / "capx/third_party/contact_graspnet_pytorch") == CONTACT_SOURCE_COMMIT)

    def submodules(inner: Context) -> None:
        inner.run(["git", "-C", inner.path(capx), "submodule", "update", "--init", "--recursive",
                   "capx/third_party/sam3", "capx/third_party/contact_graspnet_pytorch"], timeout=3600)

    def sam3_done(inner: Context) -> bool:
        path = inner.path("models/sam3.pt")
        try:
            saved = json.loads(inner.path("manifests/ora-setup/sam3.json").read_text())
            stat = path.stat()
        except (OSError, ValueError):
            return False
        return saved == {"sha256": SAM3_SHA256, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}

    def sam3(inner: Context) -> None:
        target = inner.path("models/sam3.pt")
        partial = target.with_name("sam3.pt.partial")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not (target.is_file() and _sha256(target) == SAM3_SHA256):
            inner.run(["curl", "-fL", "--retry", "3", "-C", "-", "-o", partial, SAM3_URL], timeout=7200)
            actual = _sha256(partial)
            if actual != SAM3_SHA256:
                partial.unlink()
                raise GpuSetupError(f"sam3.pt SHA-256 {actual} differs from the pinned {SAM3_SHA256}")
            partial.replace(target)
        stat = target.stat()
        record = inner.path("manifests/ora-setup/sam3.json")
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({"sha256": SAM3_SHA256, "size": stat.st_size,
                                      "mtime_ns": stat.st_mtime_ns}) + "\n")

    def verify_grasp(inner: Context) -> None:
        for relative, expected in ((_GRASP_CHECKPOINT, CONTACT_CHECKPOINT_SHA256),
                                   (_GRASP_CONFIG, CONTACT_CONFIG_SHA256)):
            actual = _file_sha256(inner.path(relative))
            if actual != expected:
                raise GpuSetupError(f"{inner.path(relative)} SHA-256 is {actual}, expected {expected}")

    requirements = context.project_root / PERCEPTION_REQUIREMENTS
    return [
        clone_step("perception", "cap-x", capx, url, revision),
        Step("perception", "submodules", "SAM3 and Contact-GraspNet submodules", submodules,
             done=submodules_done, marker=False),
        venv_step("perception", "perception", "3.11"),
        torch_step("perception", "perception", "2.8.0", "0.23.0"),
        pip_step("perception", "requirements", "perception", PERCEPTION_REQUIREMENTS,
                 ["-r", requirements],
                 recipe_extra=lambda inner: {"requirements_sha256": _file_sha256(requirements)}),
        Step("perception", "sam3", "sam3.pt from ModelScope (3.45 GB, SHA-256 pinned)", sam3,
             done=sam3_done, marker=False),
        Step("perception", "verify-graspnet", "verify Contact-GraspNet checkpoint and config", verify_grasp,
             marker=False),
    ]


def _robotwin_requirements(context: Context) -> Path:
    return context.path("manifests/ora-setup/robotwin-requirements.txt")


def robotwin_python(context: Context) -> list[Step]:
    url, revision = ROBOTWIN
    root = "sources/RoboTwin"
    upstream = lambda inner: inner.path(f"{root}/script/requirements.txt")

    def vendored(inner: Context) -> None:
        inner.run([inner.python, "-m", "ora.environment.backends.robotwin.vendored_install",
                   "--robotwin-root", inner.path(root), "--expected-revision", ROBOTWIN_INSTALL_REVISION],
                  cwd=inner.project_root, env={"PYTHONPATH": str(inner.project_root)})

    def requirements(inner: Context) -> None:
        # Upstream pins torch==2.4.1 and leaves torchvision unpinned; both stay with the cu128 step.
        lines = [line for line in upstream(inner).read_text().splitlines()
                 if not re.match(r"^\s*(torch|torchvision)\s*([=<>!~ ]|$)", line)]
        target = _robotwin_requirements(inner)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n")
        inner.run(["uv", "pip", "install", "--python", _python(inner, "robotwin"), "-r", target,
                   "numpy==1.26.4", "setuptools==69.5.1"])

    def curobo_environment(inner: Context) -> dict[str, str]:
        home = _cuda_home(inner)
        environment = {"CUDA_HOME": str(home), "TORCH_CUDA_ARCH_LIST": cuda_arch(inner),
                       "PATH": f"{home / 'bin'}:{inner.environ.get('PATH', '')}"}
        # A conda/pip toolkit keeps its headers under targets/; nvcc finds them, the host compiler does not.
        targets = home / "targets/x86_64-linux/include"
        if not (home / "include/cuda_runtime_api.h").is_file() and (targets / "cuda_runtime_api.h").is_file():
            environment["CPATH"] = os.pathsep.join(filter(None, (str(targets), inner.environ.get("CPATH", ""))))
        return environment

    def curobo_recipe(inner: Context) -> Mapping[str, Any]:
        return {"curobo": CUROBO[1], "arch": cuda_arch(inner), "cuda_home": str(_cuda_home(inner))}

    curobo_url, curobo_revision = CUROBO
    return [
        clone_step("robotwin-python", "robotwin", root, url, revision, accepted=(ROBOTWIN_INSTALL_REVISION,)),
        Step("robotwin-python", "vendored-install", f"vendored planner install (HEAD {ROBOTWIN_INSTALL_REVISION[:8]})",
             vendored, done=lambda inner: _at(inner, inner.path(root), (ROBOTWIN_INSTALL_REVISION,)), marker=False),
        venv_step("robotwin-python", "robotwin", "3.10"),
        torch_step("robotwin-python", "robotwin", "2.7.0", "0.22.0"),
        Step("robotwin-python", "requirements", "RoboTwin requirements without torch/torchvision", requirements,
             recipe=lambda inner: {"requirements_sha256": _file_sha256(upstream(inner)),
                                   "environment": _venv_identity(inner, "robotwin"),
                                   "extra": ["numpy==1.26.4", "setuptools==69.5.1"]},
             done=lambda inner: _python(inner, "robotwin").is_file()),
        clone_step("robotwin-python", "curobo", "sources/curobo", curobo_url, curobo_revision),
        pip_step("robotwin-python", "curobo-build", "robotwin", "build CuRobo for this GPU (~6 min)",
                 ["-e", str(context.path("sources/curobo")), "--no-build-isolation"],
                 recipe_extra=curobo_recipe, environment=curobo_environment),
        pip_step("robotwin-python", "warp", "robotwin", "warp-lang 1.12.0", ["warp-lang==1.12.0"]),
    ]


_DOWNLOAD_ASSETS = """import hashlib, json, sys
from pathlib import Path
from huggingface_hub import HfApi, snapshot_download
root, repo, names = Path(sys.argv[1]), sys.argv[2], sys.argv[3].split(',')
revision = HfApi().dataset_info(repo).sha
folder = root / 'downloads' / ('robotwin-' + revision)
snapshot_download(repo_id=repo, repo_type='dataset', revision=revision, allow_patterns=names, local_dir=folder)
files = {}
for name in names:
    digest = hashlib.sha256()
    with (folder / name).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    files[name] = digest.hexdigest()
(root / 'manifests' / 'robotwin-assets.json').write_text(json.dumps(
    {'repository': repo, 'revision': revision, 'directory': str(folder), 'sha256': files}, indent=2) + '\\n')
"""


def _asset_manifest(context: Context) -> dict[str, Any] | None:
    try:
        return json.loads(context.path("manifests/robotwin-assets.json").read_text())
    except (OSError, ValueError):
        return None


def robotwin_assets(context: Context) -> list[Step]:
    root = "sources/RoboTwin"

    def download_done(inner: Context) -> bool:
        manifest = _asset_manifest(inner)
        return bool(manifest) and set(manifest.get("sha256", {})) == set(ROBOTWIN_ASSET_ZIPS) and all(
            (Path(manifest["directory"]) / name).is_file() for name in ROBOTWIN_ASSET_ZIPS)

    def download(inner: Context) -> None:
        inner.path("manifests").mkdir(parents=True, exist_ok=True)
        inner.run([_python(inner, "downloads"), "-c", _DOWNLOAD_ASSETS, inner.prefix,
                   ROBOTWIN_ASSET_REPOSITORY, ",".join(ROBOTWIN_ASSET_ZIPS)], timeout=14400)

    def unpack(inner: Context) -> None:
        manifest = _asset_manifest(inner) or {}
        target = inner.path(f"{root}/assets")
        for name in ROBOTWIN_ASSET_ZIPS:
            archive_path = Path(manifest["directory"]) / name
            with zipfile.ZipFile(archive_path) as archive:
                for member in archive.namelist():
                    resolved = (target / member).resolve()
                    if not resolved.is_relative_to(target.resolve()):
                        raise GpuSetupError(f"{name} member {member!r} escapes the assets directory")
            # unzip, as the guide: embodiments.zip carries executable files.
            inner.run(["unzip", "-o", "-q", archive_path, "-d", target], timeout=7200)

    def embodiment_paths(inner: Context) -> None:
        inner.run([_python(inner, "robotwin"), "script/update_embodiment_config_path.py"], cwd=inner.path(root))

    assets_recipe = lambda inner: {"assets": (_asset_manifest(inner) or {}).get("sha256"),
                                   "robotwin_root": str(inner.path(root)), "extractor": "unzip -o"}
    return [
        venv_step("robotwin-assets", "downloads", "3.11", seed=False),
        pip_step("robotwin-assets", "huggingface-hub", "downloads", HF_HUB, [HF_HUB]),
        Step("robotwin-assets", "download", "RoboTwin 2.0 asset zips (~14 GB, no token)", download,
             done=download_done, marker=False),
        Step("robotwin-assets", "unpack", "unpack assets into RoboTwin/assets (~16 GB)", unpack,
             recipe=assets_recipe),
        Step("robotwin-assets", "embodiment-paths", "update_embodiment_config_path.py", embodiment_paths,
             recipe=assets_recipe),
    ]


def libero(context: Context) -> list[Step]:
    code_url, code_revision = LIBERO_CODE
    data_url, data_revision = LIBERO_DATA
    requirements = context.project_root / LIBERO_REQUIREMENTS
    return [
        clone_step("libero", "libero-pro", "sources/LIBERO-PRO", code_url, code_revision),
        clone_step("libero", "libero-pro-data", "sources/LIBERO-Pro-data", data_url, data_revision, lfs_skip=True),
        venv_step("libero", "libero", "3.11"),
        torch_step("libero", "libero", "2.7.0", "0.22.0"),
        pip_step("libero", "requirements", "libero", LIBERO_REQUIREMENTS, ["-r", requirements],
                 recipe_extra=lambda inner: {"requirements_sha256": _file_sha256(requirements)}),
    ]


def motion_timing(context: Context) -> list[Step]:
    """The small CPU interpreter of the timing worker (TOPP-RA, Ruckig), which no simulator needs."""
    return [
        venv_step("motion-timing", "motion-timing", "3.11", seed=False),
        pip_step("motion-timing", "requirements", "motion-timing", "the timing interpreter's pinned packages",
                 list(TIMING_PINS)),
    ]


def code_mode(context: Context) -> list[Step]:
    """The code-mode sandbox interpreter (``ORA_CODE_MODE_PYTHON``): a Python 3.11 holding exactly the lock
    ``CODE_MODE_LOCK``, every wheel hash-checked and its bytecode compiled, as the sandbox cannot write it."""
    lock = context.project_root / CODE_MODE_LOCK
    return [
        bubblewrap(context),
        venv_step("code-mode", "code-mode", "3.11", seed=False),
        pip_step("code-mode", "requirements", "code-mode", "the code-mode sandbox interpreter's locked packages",
                 ["--require-hashes", "--no-deps", "--only-binary", ":all:", "--compile-bytecode", "-r", lock],
                 recipe_extra=lambda inner: {"lock_sha256": _file_sha256(lock)}),
    ]


def sandbox_warning(components: Sequence[str], prefix: Path | None = None) -> str | None:
    """What setup cannot install for code mode: ``bwrap`` (and the user namespaces it needs, which ``ora doctor``
    checks). None when it is not needed or is found."""
    if "code-mode" not in components:
        return None
    from ora.agent.code_mode.sandbox import (BWRAP_REMEDY, PROOT_REMEDY, SandboxError, bwrap_path, proot_path,
                                             sandbox_kind)

    try:
        proot = sandbox_kind() == "proot"
    except SandboxError as exc:
        return str(exc)
    if proot:
        try:
            proot_path()
        except SandboxError:
            return ("code mode runs model-written programs under PRoot here (ORA_CODE_MODE_SANDBOX=proot): "
                    f"{PROOT_REMEDY}; {ora_command()} doctor --env robodojo checks it and the ptrace it needs")
        return None
    if prefix is not None and os.access(prefix / BWRAP_IN_PREFIX, os.X_OK):
        return None
    try:
        bwrap_path()
    except SandboxError:
        return ("code mode, the RoboDojo default, runs model-written programs in a bubblewrap sandbox: "
                f"{BWRAP_REMEDY}; {ora_command()} doctor --env robodojo checks it and the user namespaces it needs")
    return None


ROBODOJO_INSTALLER = "ora/environment/backends/robodojo/installation/setup_runtime.py"


def robodojo_root(context: Context) -> Path:
    return context.path("robodojo-runtime")


def system_libraries(context: Context) -> Step:
    def run(inner: Context) -> None:
        packages = sorted({SYSTEM_LIBRARIES[name] for name in missing_system_libraries(inner.prefix)})
        if shutil.which("apt-get") is None or shutil.which("dpkg") is None:
            raise GpuSetupError(f"Isaac Sim needs {', '.join(packages)}; install them (or their equivalents "
                                "for your distribution) and rerun")
        download = inner.path("downloads/syslibs")
        download.mkdir(parents=True, exist_ok=True)
        inner.run(["apt-get", "download", *packages], cwd=download)
        for deb in sorted(download.glob("*.deb")):
            inner.run(["dpkg", "-x", deb, inner.path("syslibs")])
        add_library_path(inner.environ, inner.prefix)

    return Step("robodojo", "system-libraries", "libGLU and the Vulkan loader, unpacked without sudo", run,
                marker=False, done=lambda inner: not missing_system_libraries(inner.prefix))


def robodojo(context: Context) -> list[Step]:
    def stage_command(inner: Context, stage: str) -> list[Any]:
        return [inner.python, inner.project_root / ROBODOJO_INSTALLER, stage, "--root", robodojo_root(inner)]

    def downloads(inner: Context) -> None:
        import concurrent.futures

        # Sources, the 66 GB of assets and the Isaac Sim SDK are independent.
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(inner.run, stage_command(inner, stage), timeout=6 * 3600,
                                   log_name=f"robodojo.{stage}") for stage in ("sources", "assets", "sdk")]
            for future in futures:
                future.result()

    def stage(name: str):
        return lambda inner: inner.run(stage_command(inner, name), timeout=7200)

    def recipe(inner: Context) -> Mapping[str, Any]:
        text = (inner.project_root / ROBODOJO_INSTALLER).read_text()
        pins = dict(re.findall(r'^(DOJO_REV|ASSET_REV|PYTHON_VERSION|PROFILES_REV) = "([^"]+)"', text, re.M))
        return {"root": str(robodojo_root(inner)), **pins}

    steps = [git_lfs(context), system_libraries(context),
             Step("robodojo", "downloads", "RoboDojo sources, assets (66 GB, git-lfs) and Isaac Sim SDK",
                  downloads, recipe=recipe)]
    for name, title in (("resolve", "resolve"), ("packages", "packages"), ("bind-assets", "bind assets")):
        steps.append(Step("robodojo", name, title, stage(name), recipe=recipe))
    # The stage refuses to replace its receipt, which every worker verifies at
    # start; the receipt itself is the done state (rebuild: remove graphics-compat/).
    steps.append(Step("robodojo", "graphics-compat", "graphics-compat (Vulkan Profiles layer)",
                      stage("graphics-compat"), marker=False,
                      done=lambda inner: (robodojo_root(inner) / "graphics-compat/installation.json").is_file()))
    # Cheap and read-only: always rerun so every setup ends on a fresh verdict.
    steps.append(Step("robodojo", "doctor", "doctor (expect fail=0)", stage("doctor"), marker=False))
    steps.append(Step("robodojo", "manifest", "install manifest", stage("manifest"), marker=False))
    return steps


BUILDERS = {"agent-core": agent_core_packages, "perception": perception, "robotwin-python": robotwin_python,
            "robotwin-assets": robotwin_assets, "libero": libero, "robodojo": robodojo,
            "motion-timing": motion_timing, "code-mode": code_mode}


def steps_for(context: Context, components: Sequence[str]) -> list[Step]:
    return [step for component in components for step in BUILDERS[component](context)]


def env_keys(context: Context, components: Sequence[str]) -> dict[str, str]:
    """The ``.env`` keys these components own. Never a credential or the host marker."""
    keys: dict[str, str] = {}
    if "perception" in components:
        python = str(_python(context, "perception"))
        keys.update({
            "ORA_SAM_PYTHON": python, "ORA_SAM_ROOT": str(context.path("sources/cap-x/capx/third_party/sam3")),
            "ORA_SAM_CHECKPOINT": str(context.path("models/sam3.pt")), "ORA_SAM_GPU": "0",
            "ORA_GRASP_PYTHON": python, "ORA_GRASP_ROOT": str(context.path(_GRASP)),
            "ORA_GRASP_CHECKPOINT": str(context.path(_GRASP_CHECKPOINT)),
            "ORA_GRASP_CONFIG": str(context.path(_GRASP_CONFIG)), "ORA_GRASP_GPU": "0",
        })
    if "robotwin-python" in components:
        python = str(_python(context, "robotwin"))
        keys.update({"ROBOTWIN_ROOT": str(context.path("sources/RoboTwin")), "ROBOTWIN_PYTHON": python,
                     "CUROBO_ROOT": str(context.path("sources/curobo")), "CUROBO_PYTHON": python})
    if "libero" in components:
        keys.update({"LIBERO_PRO_PYTHON": str(_python(context, "libero")),
                     "LIBERO_PRO_ROOT": str(context.path("sources/LIBERO-PRO")),
                     "LIBERO_PRO_DATA_ROOT": str(context.path("sources/LIBERO-Pro-data")),
                     "MUJOCO_GL": "egl", "PYOPENGL_PLATFORM": "egl", "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD": "1"})
    if "code-mode" in components and os.access(context.path(BWRAP_IN_PREFIX), os.X_OK):
        keys["ORA_CODE_MODE_BWRAP"] = str(context.path(BWRAP_IN_PREFIX))  # the sandbox finds it by this key
    if "robodojo" in components:
        root = robodojo_root(context)
        keys.update({"ROBODOJO_RUNTIME_ROOT": str(root), "ROBODOJO_ROOT": str(root / "source/RoboDojo"),
                     "ROBODOJO_ASSET_ROOT": str(root / "assets/Assets"),
                     "ROBODOJO_PYTHON": str(root / "env/bin/python"),
                     # cuRobo V2, LIBERO's planner, is the RoboDojo runtime's own: its interpreter and
                     # the source root that interpreter imports curobo from (RoboDojo's submodule).
                     "CUROBO_V2_PYTHON": str(root / "env/bin/python"),
                     "CUROBO_V2_ROOT": str(root / "source/RoboDojo/third_party/curobo")})
    if "motion-timing" in components:
        keys["ORA_TIMING_PYTHON"] = str(_python(context, "motion-timing"))
    if "code-mode" in components:
        keys["ORA_CODE_MODE_PYTHON"] = str(_python(context, "code-mode"))
    return keys


__all__ = ["BUILDERS", "CODE_MODE_LOCK", "COMPONENTS", "PERCEPTION", "check_toolchain", "code_mode",
           "components_for", "cuda_arch", "disk_gib", "env_keys", "requires", "sandbox_warning", "steps_for"]
