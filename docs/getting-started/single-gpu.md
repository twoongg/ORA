# Run ORA on one GPU

This guide takes a fresh Linux machine with **one NVIDIA GPU** and **one
DeepSeek API key** to an ORA run in each simulator: LIBERO / LIBERO-Pro,
RoboTwin and RoboDojo. You watch and steer each run in the terminal UI. It
needs no sudo, no Docker, no model download behind a login and no
hand-written configuration.

The whole path, once the [tools](#2-user-space-tools) are on `PATH`:

```bash
git clone https://github.com/fast-rua/ORA.git && cd ORA
uv sync --all-extras --locked
./scripts/ora setup --env robotwin                                   # install once
./scripts/ora providers key DEEPSEEK_LOW_API_KEY                     # once; or export it
./scripts/ora run --env robotwin --task click_bell --confirm-launch  # every task
```

Replace `robotwin` with `libero` (task `libero_goal/0`) or `robodojo` (task
`general_pickup`), or install several at once. Sections 1–6 explain each step
and what you will see. Section 9 lists the explicit commands that `setup` and
`run` execute, for reading or for redoing one step by hand.

## Contents

1. [What you need](#1-what-you-need)
2. [User-space tools](#2-user-space-tools)
3. [Get ORA](#3-get-ora)
4. [Install an environment](#4-install-an-environment)
5. [Run a task](#5-run-a-task)
6. [Watch, steer and inspect](#6-watch-steer-and-inspect)
7. [GPU and driver notes](#7-gpu-and-driver-notes), and [updating or removing ORA](#updating-and-removing-ora)
8. [Troubleshooting](#8-troubleshooting)
9. [Reference: what setup and run execute](#9-reference-what-setup-and-run-execute)
10. [Measured on the verification machine](#10-measured-on-the-verification-machine)

## 1. What you need

| Input | Requirement | Notes |
|---|---|---|
| GPU | One NVIDIA GPU, compute capability 7.5 or newer (Turing to Blackwell), **32 GB or more** | The perception services hold 8–10 GB (SAM3 ~6.7 GB; Contact-GraspNet ~1.7 GB, growing to ~3.7 GB with use), and the RoboTwin and RoboDojo slots require 20 GB free at launch besides them. A 24 GB card runs LIBERO only |
| Driver | R570 or newer; **R580 recommended** | CUDA 12.8 wheels, EGL and Vulkan come from the driver. See [§7](#7-gpu-and-driver-notes) for R595 |
| CUDA 12.8 toolkit | `nvcc` under `/usr/local/cuda-12.8` (or `--cuda-home`) | Builds CuRobo for your GPU; the driver alone is not enough |
| Disk | ~170 GB for any combination | RoboDojo alone is ~90 GB (66 GB of assets); LIBERO and RoboTwin install it too, for their planner, cuRobo V2 |
| Network | GitHub, PyPI, download.pytorch.org, Hugging Face, ModelScope | No account or token is needed for any download |
| Model API | A DeepSeek API key | One task used 8–70 model calls; most input tokens are cache hits |
| OS | x86_64 Linux (verified on Ubuntu 24.04) | macOS/CPU users: [Run on your computer (CPU)](local.md) |
| Sandbox (RoboDojo) | bubblewrap (`bwrap`) and unprivileged user namespaces | RoboDojo's default policy is code mode: the model's programs run in a bubblewrap sandbox. Setup installs `bwrap` when the machine has none (on Debian and Ubuntu); the user namespaces are the kernel's, and [§2](#2-user-space-tools) checks them |

## 2. User-space tools

Check what the machine already has:

```bash
nvidia-smi                                   # driver and GPU
/usr/local/cuda-12.8/bin/nvcc --version      # CUDA 12.8 toolkit
git --version; git lfs version; node --version; uv --version; unzip -v | head -1; c++ --version
ls /usr/share/glvnd/egl_vendor.d/ /usr/share/vulkan/icd.d/   # expect 10_nvidia.json and nvidia_icd.json
ldconfig -p | grep -E 'libvulkan.so.1|libGLU.so.1'          # Isaac Sim needs both; setup fetches missing ones
bwrap --version && bwrap --unshare-all --ro-bind / / true && echo sandbox-ok   # RoboDojo's code mode needs both
```

Install what is missing into `~/.local` (no sudo):

```bash
mkdir -p ~/.local/bin
curl -LsSf https://astral.sh/uv/install.sh | sh                       # uv
curl -fsSL https://nodejs.org/dist/v22.20.0/node-v22.20.0-linux-x64.tar.xz \
  | tar -xJ -C ~/.local --strip-components=1                          # Node 22.20 (ORA needs >= 22.19)
curl -fsSL https://github.com/git-lfs/git-lfs/releases/download/v3.7.1/git-lfs-linux-amd64-v3.7.1.tar.gz \
  | tar -xz -C /tmp && cp /tmp/git-lfs-3.7.1/git-lfs ~/.local/bin/      # optional: setup installs it for RoboDojo
export PATH=~/.local/bin:$PATH
echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc                # new shells and tmux panes too
```

RoboDojo runs code mode by default, so it also needs bubblewrap and
unprivileged user namespaces. `ora setup --env robodojo` unpacks `bwrap` under its prefix when
the machine has none and `apt-get` is there (Debian, Ubuntu); by hand: Without sudo, unpack the package into
`~/.local/bin` (ORA also looks there):

```bash
(cd /tmp && apt-get download bubblewrap && dpkg -x bubblewrap_*.deb /tmp/bwrap) \
  && cp /tmp/bwrap/usr/bin/bwrap ~/.local/bin/
```

If `bwrap --unshare-all --ro-bind / / true` still fails ("No permissions to
creating new namespace", "setting up uid map: Permission denied"), the kernel
refuses unprivileged user namespaces, and an administrator has to allow them:
`user.max_user_namespaces` above 0, `kernel.unprivileged_userns_clone=1` on
Debian and Ubuntu, and on Ubuntu 23.10 and later
`kernel.apparmor_restrict_unprivileged_userns=0` or an AppArmor profile for
`bwrap`; inside a container, user namespaces must be allowed (Docker's default
seccomp profile refuses them). A RunSpec with `"code_mode": null` runs RoboDojo
with tool calls and needs neither.

Make the `PATH` line persistent: the run step in §5 starts in `tmux`, whose
new shell would otherwise find the distribution's Node 18 first. ORA uses
`ORA_NODE` when set, else the first Node.js 22.19+ among `node` on `PATH`,
`~/.local/bin/node` and `~/.nvm`.

Ubuntu's packaged Node 18 is too old. With sudo, `apt-get install git-lfs
unzip build-essential libegl1 libvulkan1 bubblewrap` covers the rest. Without a CUDA
toolkit, install NVIDIA's `cuda_12.8.*_linux.run` with `--toolkit --silent
--toolkitpath=$HOME/cuda-12.8`, or a conda environment
(`conda create -p ~/cuda-12.8 -c nvidia cuda-toolkit=12.8`), and pass
`--cuda-home ~/cuda-12.8` to setup.

## 3. Get ORA

```bash
git clone https://github.com/fast-rua/ORA.git && cd ORA
uv sync --all-extras --locked
```

ORA ships the host profile this guide uses,
`ora/orchestrator/hosts/single-gpu.yaml`: a `libero-gpu<N>`,
`robotwin-gpu<N>` and `robodojo-gpu<N>` slot for each CUDA device index 0-7. `ora run`
uses GPU 0; on a machine with several GPUs, `--gpu N` runs on GPU N (§5). You do not write
a host file.

Runs bind the exact Git commit, so keep the checkout **clean**: no local
edits or untracked files. `.env`, `outputs/` and `ora-pi/node_modules/` are
ignored by Git and do not count.

## 4. Install an environment

```bash
./scripts/ora setup --env libero      # LIBERO / LIBERO-Pro
./scripts/ora setup --env robotwin    # RoboTwin
./scripts/ora setup --env robodojo    # RoboDojo (Isaac Sim 5.1)
./scripts/ora setup --env libero --env robotwin --env robodojo   # all three
```

Add `--dry-run` to list the steps first. Setup installs into one directory
outside the checkout: `--prefix DIR`, else `ORA_GPU_PREFIX`, else
`~/.local/share/ora/gpu`. It installs:

- a CUDA 12.8 PyTorch in every simulator environment;
- CuRobo, built for the compute capability `nvidia-smi` reports
  (`--cuda-arch 9.0` overrides it);
- the SAM3 and Contact-GraspNet perception services, with SAM3 weights from
  ModelScope checked against a pinned SHA-256;
- the agent core's Pi packages, which include the terminal UI
  (`npm ci --prefix ora-pi`, skipped when they already match the lock);
- the environment's simulator and assets;
- for RoboDojo, `libGLU.so.1` and the Vulkan loader when the system lacks
  them (minimal server images often do): setup downloads the Ubuntu packages
  `libglu1-mesa` and `libvulkan1` with `apt-get download` and unpacks them
  into `<prefix>/syslibs` without sudo; `ora run` puts that directory on
  `LD_LIBRARY_PATH`;
- for RoboDojo, the code-mode sandbox interpreter (`ORA_CODE_MODE_PYTHON`, a
  Python 3.11 with NumPy, SciPy, Pillow, scikit-image and Shapely from the
  hash-pinned lock `ora/agent/code_mode/sandbox-requirements.txt`, under
  `envs/code-mode`, about 300 MB);
- git-lfs (the static release, SHA-256 checked, into `<prefix>/tools/bin`) and
  bubblewrap (`apt-get download bubblewrap`, unpacked into `<prefix>/tools` and named in `.env`
  as `ORA_CODE_MODE_BWRAP`), each only when the machine has none. User namespaces stay the
  kernel's (§2); setup warns when `bwrap` is still missing.

`libero` and `robotwin` also install the RoboDojo runtime, because their motion
planner, cuRobo V2, runs under its interpreter (`CUROBO_V2_PYTHON`, `CUROBO_V2_ROOT`).
They also build the small CPU interpreter of the
motion timing worker (`ORA_TIMING_PYTHON`, about 250 MB, under `envs/motion-timing`).

Setup starts no simulator, service or model call. Each step prints `already
done` when its pinned state or recorded recipe still holds, so rerunning after
a failure resumes where it stopped. Logs are in `<prefix>/logs/ora-setup/`.

Setup writes the paths it installed into the checkout's `.env` (mode 0600)
and sets `DEEPSEEK_BASE_URL` if it is unset. It never writes a credential.
When it writes the `ORA_SAM_*` / `ORA_GRASP_*` service keys, it also replaces
the names releases before 1.0 used for those settings in `.env` with the
`ORA_` names, keeping a value it does not set itself (a port, for one).

| Environment | Largest costs | Disk (upper bound) |
|---|---|---|
| `libero` | Torch downloads, the RoboDojo runtime for cuRobo V2 (as `robodojo` below) | ~114 GB |
| `robotwin` | Torch downloads, CuRobo build, 14 GB of asset zips (unpack ~2 min), the RoboDojo runtime for cuRobo V2 (as `robodojo` below) | ~148 GB |
| `robodojo` | 66 GB of assets through git-lfs, Isaac Sim 5.1 wheels, graphics layer build (~40 s) | ~105 GB |

Downloads dominate. A rerun with everything in place takes a few minutes.

## 5. Run a task

Configure the key once. ORA asks for it without echo, keeps it in your user
config outside the repository (`~/.config/ora/pi-auth.json`, mode 0600) and
tests it with the probe every launch runs:

```bash
./scripts/ora providers key DEEPSEEK_LOW_API_KEY
```

Exporting it instead still works, and an exported key comes first
([model APIs](model-apis.md#keys-configured-once-in-the-terminal)):

```bash
export DEEPSEEK_LOW_API_KEY=sk-...
```

Then run one task per environment:

```bash
./scripts/ora run --env libero   --task libero_goal/0  --confirm-launch
./scripts/ora run --env robotwin --task click_bell     --confirm-launch
./scripts/ora run --env robodojo --task general_pickup --confirm-launch
```

Add `--provider-profile ID` to use another registered model, for example
`muse_spark_open_openrouter_high_16k_v1` with `OPENROUTER_API_KEY` set; see
[Model APIs](model-apis.md#robot-episodes).

`ora run` does the following, in order, and stops at the first problem with
the reason:

1. **Configuration.** It loads `.env` (values in your shell win) and binds
   the `single-gpu` host. It checks the key and endpoint offline, so a
   missing key fails at once.
2. **One-time qualification.** RoboTwin and RoboDojo need a qualification
   record from this machine before a task can run. If none is reusable, ORA
   prints an estimate and asks before running it. `--yes` answers in advance.
   - RoboTwin scans the task's seeds with the upstream expert and stops at
     the first seed the expert solves (`--candidates`, default 4). This took
     2.5 min for `click_bell`.
   - RoboDojo runs a card qualification on the first start on each GPU,
     driver and installation (two full-length episodes, ~9 min on an RTX PRO
     6000). It checks the card and warms Isaac's renderer caches: without it a
     fresh installation's first episode can time out while Isaac compiles its
     materials (`original camera assets did not become ready`). A task
     qualification (~4 min) runs only with `--requalify`; without one the run
     continues with a warning.

   Later runs reuse these records. LIBERO needs none.
3. **Perception services.** SAM3 and Contact-GraspNet start if they are not
   already running (~3 min the first time, 8–10 GB of GPU memory). They keep
   running between tasks. Services on the ports that this checkout did not
   start (another user's, on a shared machine), or that it started from
   another container or user namespace, are refused; see
   [§8](#8-troubleshooting).
4. **Launch.** ORA writes a RunSpec under `outputs/ora-interactive/specs/`
   and opens the terminal UI on it. The picker lists that one task: press
   Enter to start it. The full launch preflight runs again (about 10–40 s;
   it checks the GPU, simulator, services and one real model request), then
   the episode starts. The task starts only with `--confirm-launch` and your
   selection in the picker.

### Choosing a task

| Environment | `--task` | Examples | Where the task list is |
|---|---|---|---|
| LIBERO | `<suite>/<id>`, `--seed` 0–49 picks the initial state | `libero_goal/0`, `libero_spatial/3`, `libero_10/7` | Suites `libero_goal`, `libero_spatial`, `libero_object`, `libero_10` (tasks 0–9), plus the LIBERO-Pro core suites |
| RoboTwin | task name; `--task-config demo_clean` for clean scenes | `click_bell`, `adjust_bottle`, `beat_block_hammer` | `ls <prefix>/sources/RoboTwin/envs/*.py` |
| RoboDojo | task name (layout 0) | `general_pickup` | `NATIVE_ACTION_LIMITS` in `ora/environment/benchmarks/robodojo.py` (54 tasks, the 12 `*_random` variants included) |

A new RoboTwin or RoboDojo task runs its own one-time qualification (step 2).

Other options:

- `--prepare-only` stops after printing the RunSpec path. Run it later with
  `./scripts/ora interactive --spec PATH --confirm-launch` (the task picker) or
  `./scripts/ora launch PATH` (no terminal UI). For a spec on this host both read
  the checkout's `.env` and set the host marker as `ora run` does, and start the
  perception services on the spec's GPU when they are not running.
- `--requalify` ignores reusable qualification records.
- `--gpu N` runs on GPU N of this machine (0-7, default 0, as `nvidia-smi` numbers them): the
  simulator's slot and the perception services `ora run` starts. Qualification records name the
  card they were made on, so a new GPU qualifies once.
- `./scripts/ora run --env robotwin --help` lists all options.

Run **one task at a time** on the GPU.

### Over SSH

Start `ora run` inside `tmux` or `screen`. ORA finishes each run after the
episode ends (metrics, the simulator's verdict, sealing the evidence), and a
dropped SSH connection that kills the terminal can stop that midway. If you
leave the UI with Ctrl-C, the run keeps going; find it with
`./scripts/ora status` and follow it with `./scripts/ora watch RUN_ID`.

When you are done, free the GPU memory the services hold:

```bash
./scripts/ora services stop
```

## 6. Watch, steer and inspect

The terminal UI shows the run the way a coding agent shows a session: the
instruction, the model's words, each tool call with a condensed result, action
receipts, the official verdict, and a footer with round, context and token
use.

| Key | Effect |
|---|---|
| Enter | Send the running agent a message; it arrives at the next model call |
| Alt+Enter | Queue a message for the next task |
| Esc | Stop the run |
| Ctrl-C | Leave the UI; the run continues |

Runs you steer are marked human-assisted and are not scored as autonomous.
Keep the terminal open until the UI reports the run finished: ORA computes
metrics, asks the simulator for its verdict and seals the evidence after the
episode ends.

After a run:

```bash
./scripts/ora watch RUN_ID                  # replay a finished run, or follow a live one
uv run --locked ora-viewer --open-browser   # browser evidence viewer with videos and trajectories
```

The viewer listens on `127.0.0.1:5012`; on a shared machine where another
user's viewer holds that port, pass a free one: `uv run --locked ora-viewer --open-browser --port 5023`.

Runs are under `outputs/ora-interactive/runs/<run_id>/`:

| File | Contents |
|---|---|
| `result.json`, `evaluation.json` | how the run ended and the simulator's official verdict (`official_success`) |
| `agent-events.jsonl` | every model request and response, tool call, action and receipt (what `ora watch` replays) |
| `runtime-metrics.json` | decisions, model calls, tokens and timings |
| `seal.json` | the digest that seals the run's evidence; present once landing finished |

The simulator's official check supplies `official_success`; the model saying
it finished is not a success. See [interactive sessions](../usage/interactive.md)
and [run and inspect](../usage/runs.md).

## 7. GPU and driver notes

| GPU family | Compute capability | Status |
|---|---|---|
| Blackwell (RTX PRO 6000, RTX 50xx, B200) | 12.0 / 10.0 | Verified end to end on an RTX PRO 6000 (all three environments) |
| Hopper (H100, H200) | 9.0 | Same commands. On one H200 (driver 595): setup, CuRobo for sm_90, the RoboTwin seed scan, the RoboDojo card and task qualification and every launch preflight check except the model request (run with a placeholder key) passed; no episode with a model yet |
| Ada, Ampere, Turing (RTX 40xx/30xx, L40, A100, T4) | 8.9 / 8.6 / 8.0 / 7.5 | Same commands; not verified here |

- **One stack for every GPU.** Setup installs Torch 2.7 cu128 for the
  simulators and builds CuRobo for your GPU's compute capability. The older
  cu121/cu124 wheels in the per-environment guides stop at Hopper and fail on
  Blackwell with `no kernel image is available for execution on the device`.
- **RoboDojo graphics layer.** Isaac Sim 5.1 overflows a Vulkan allocation
  limit on driver R595. `ora setup --env robodojo` builds a process-local
  Khronos profiles layer (`setup-robodojo graphics-compat`) that every
  RoboDojo worker activates and checks before it starts. Nothing is installed
  system-wide. R580 does not need the limit, but the layer is still built and
  checked.
- **Isaac Sim on R595 with RTX Blackwell** has crashed during RTX startup;
  prefer R580 on Blackwell.
- **Headless.** Every environment renders offscreen: LIBERO through EGL,
  RoboTwin (SAPIEN) and RoboDojo (Isaac Sim) through Vulkan. No display or X
  server is needed, so an SSH session is enough.

### Updating and removing ORA

To update, pull and rerun setup; it reinstalls only what changed:

```bash
git pull && uv sync --all-extras --locked
./scripts/ora setup --env libero --env robotwin --env robodojo
```

Qualification records bind the code they were made with. After an update,
`ora run` may run a RoboTwin seed scan or a RoboDojo qualification again; it
asks first, as on the first run.

To remove everything ORA installed and recorded:

```bash
./scripts/ora services stop
rm -rf ~/.local/share/ora/gpu      # or your --prefix / ORA_GPU_PREFIX
rm -rf ~/.cache/ora                # run scratch
rm -rf outputs/* .harness-runtime .env   # runs, qualifications, local state and settings in the checkout
```

Setup's downloads also fill uv's cache (`~/.cache/uv`, ~20 GB after all three
environments). Other uv projects share it; `uv cache clean` empties it.

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Node.js >= 22.19 is required (found v18…)` | distribution Node 18, or an older Node 22 | Node 22 tarball into `~/.local` (§2), or `ORA_NODE` |
| `npm ci in ora-pi failed` | no route to the npm registry and an empty npm cache | run setup once with registry access, or point `npm_config_cache` at a populated cache |
| preflight `agent_core` fails | Node or the `ora-pi` packages are missing in this checkout | `./scripts/ora setup --env …` |
| `ora setup`: CUDA toolkit not found | no `nvcc` 12.8 | install the toolkit (§2) and pass `--cuda-home` |
| `no kernel image is available for execution on the device` | a cu121/cu124 Torch on a newer GPU, or CuRobo built for another GPU | rerun `ora setup --env …`; it reinstalls cu128 Torch and rebuilds CuRobo when the GPU changes |
| `provider_marker_configuration` fails | key unset, or `DEEPSEEK_BASE_URL` points at another endpoint | `./scripts/ora providers key DEEPSEEK_LOW_API_KEY` (or export it); unset the URL or set `https://api.deepseek.com` |
| key empty although exported | a sourced `.env` still has an empty `DEEPSEEK_*_API_KEY=` line | `ora setup --env …` removes those lines; or delete them by hand |
| `ora run`: … unset; run ./scripts/ora setup --env … | that environment is not installed, or `.env` lacks its paths | `./scripts/ora setup --env <env>` |
| `ora run`: the checkout has local changes | runs bind a clean commit | commit or move the changes |
| `ora run`: this step runs simulator episodes; rerun with --yes | a qualification is needed and stdin is not a terminal | read the estimate, rerun with `--yes` |
| RoboDojo run ends `incomplete` with `original camera assets did not become ready` | a fresh installation's first Isaac start compiled its materials for longer than the camera wait; the card qualification `ora run` runs on a GPU's first start warms them | rerun `ora run`; `--requalify` runs the card qualification again |
| preflight stops at `provider_marker` or the run ends `incomplete` on the Provider recovery deadline | the API returned errors (for example HTTP 503) | check the provider's status and rerun |
| `ora run`: … answers but was not started from this checkout, or `another process already listens on …` | on a shared machine another user's SAM3 / Contact-GraspNet listen on 8114/8115; `ora run` will not bind services it did not start | set free ports with `ORA_SAM_PORT` / `ORA_GRASP_PORT` in `.env` (for example 8124/8125) |
| `ora services stop`: pid … is alive but this process cannot inspect it | services were started from another container or user namespace | stop them where they were started |
| `ora run`: … was started by this checkout's model-services, not by another user: … cannot inspect it | this checkout started the services, but from another container or user namespace, so their identity cannot be confirmed from here | run `./scripts/ora services stop` where they were started, or set free ports with `ORA_SAM_PORT` / `ORA_GRASP_PORT` |
| `graphics-compat`: `Vulkan driver compatibility rejected: libvulkan.so.1: cannot open shared object file`, or a RoboDojo qualification fails with `original camera assets did not become ready` (the worker log shows `libGLU.so.1: cannot open shared object file`) | the system lacks the Vulkan loader or libGLU that Isaac Sim loads | rerun `./scripts/ora setup --env robodojo`: it unpacks `libvulkan1` / `libglu1-mesa` into `<prefix>/syslibs` without sudo. Without `apt-get`, install those libraries; when running `setup-robodojo` or a qualification by hand, put `<prefix>/syslibs/usr/lib/x86_64-linux-gnu` on `LD_LIBRARY_PATH` |
| preflight `code_mode_sandbox` fails: `bubblewrap (bwrap) is not installed` | RoboDojo's default policy, code mode, runs programs in a bubblewrap sandbox | install bubblewrap (§2), or set `ORA_CODE_MODE_BWRAP` to a `bwrap` binary |
| preflight `code_mode_sandbox` fails: `bubblewrap cannot create the sandbox's namespaces` | the kernel or the container refuses unprivileged user namespaces | allow them (§2); `./scripts/ora doctor --env robodojo` checks it. Where you cannot (a Kubernetes pod), install PRoot and set `ORA_CODE_MODE_SANDBOX=proot` |
| preflight `code_mode_sandbox` fails: `PRoot cannot trace programs on this host` | `ORA_CODE_MODE_SANDBOX=proot`, and the container's seccomp profile or `kernel.yama.ptrace_scope` refuses ptrace, or the PRoot predates 5.2 (no `--kill-on-exit`) | allow ptrace in the container; use a current PRoot; try `PROOT_NO_SECCOMP=1` |
| preflight `code_mode_sandbox` fails: `no code-mode interpreter`, or `does not match its lock` | setup ran before RoboDojo defaulted to code mode, or the interpreter was changed | rerun `./scripts/ora setup --env robodojo` |
| RoboDojo worker: `graphics-compat/installation.json` missing | the layer was not built | `./scripts/setup-robodojo graphics-compat --root <prefix>/robodojo-runtime` |
| `git lfs` not found during RoboDojo assets | no git-lfs, on a machine that is not x86_64 Linux (elsewhere setup installs it) | install git-lfs (§2) |
| Isaac prints `OptiX … Database file is not writable` | `/var/tmp` is not writable | set `OPTIX_CACHE_PATH` to a writable directory |
| `ora doctor`: OSMesa is missing (local CPU path) | no NVIDIA EGL vendor file | `./scripts/ora setup --graphics egl` if EGL is elsewhere, else install `libosmesa6` |

More: [troubleshooting](troubleshooting.md).

## 9. Reference: what setup and run execute

These are the explicit commands behind `ora setup --env` and `ora run`, as
verified. `ora setup` uses the same layout under its prefix, with RoboDojo at
`<prefix>/robodojo-runtime`.

```bash
export PREFIX=~/.local/share/ora/gpu
mkdir -p $PREFIX/{sources,envs,models,downloads}
```

### 9.1 LIBERO-Pro

```bash
git clone https://github.com/Zxy-MLlab/LIBERO-PRO.git $PREFIX/sources/LIBERO-PRO
git -C $PREFIX/sources/LIBERO-PRO checkout --detach 2b910b5b5f53016bef9907632f6f840f1ce2229c
GIT_LFS_SKIP_SMUDGE=1 git clone --no-checkout \
  https://huggingface.co/datasets/zhouxueyang/LIBERO-Pro $PREFIX/sources/LIBERO-Pro-data
git -C $PREFIX/sources/LIBERO-Pro-data checkout --detach c86fc3b8293185a6f373677018ff3e37f8391602
uv venv --python 3.11 --seed $PREFIX/envs/libero
uv pip install --python $PREFIX/envs/libero/bin/python \
  torch==2.7.0 torchvision==0.22.0 --index-url https://download.pytorch.org/whl/cu128
uv pip install --python $PREFIX/envs/libero/bin/python -r ora/environment/backends/libero_pro/installation/requirements-x86_64.txt
```

On exit, robosuite prints an `EGLError` from its context destructor. It is
harmless.

### 9.2 RoboTwin and CuRobo

```bash
git clone https://github.com/RoboTwin-Platform/RoboTwin.git $PREFIX/sources/RoboTwin
git -C $PREFIX/sources/RoboTwin checkout --detach c3ddfa8b97d5519efa828b075999bd0006778e5e
uv run --locked python -m ora.environment.backends.robotwin.vendored_install \
  --robotwin-root $PREFIX/sources/RoboTwin          # HEAD becomes cf4872cc…
uv venv --python 3.10 --seed $PREFIX/envs/robotwin
uv pip install --python $PREFIX/envs/robotwin/bin/python \
  torch==2.7.0 torchvision==0.22.0 --index-url https://download.pytorch.org/whl/cu128
# Upstream pins torch==2.4.1 and leaves torchvision unpinned; drop both lines.
grep -v -E '^(torch|torchvision)([=<> ]|$)' $PREFIX/sources/RoboTwin/script/requirements.txt > /tmp/robotwin-req.txt
uv pip install --python $PREFIX/envs/robotwin/bin/python -r /tmp/robotwin-req.txt numpy==1.26.4 setuptools==69.5.1

git clone https://github.com/NVlabs/curobo.git $PREFIX/sources/curobo
git -C $PREFIX/sources/curobo checkout --detach d64c4b005459db10c5dd867d8b30a87d5bda9bdb
nvidia-smi --query-gpu=compute_cap --format=csv,noheader              # e.g. 12.0 or 9.0
export CUDA_HOME=/usr/local/cuda-12.8 TORCH_CUDA_ARCH_LIST="12.0+PTX"  # your compute capability
uv pip install --python $PREFIX/envs/robotwin/bin/python -e $PREFIX/sources/curobo --no-build-isolation
uv pip install --python $PREFIX/envs/robotwin/bin/python warp-lang==1.12.0
```

SAPIEN 3.0.0b1, MPLib 0.2.1 and numpy 1.26.4 stay at the upstream versions;
only Torch moves. Download `background_texture.zip`, `embodiments.zip` and
`objects.zip` from the
[RoboTwin 2.0 dataset](https://huggingface.co/datasets/TianxingChen/RoboTwin2.0),
unpack them into `$PREFIX/sources/RoboTwin/assets` with `unzip`, and run
`script/update_embodiment_config_path.py` there with the RoboTwin interpreter.

### 9.3 Perception services

```bash
git clone https://github.com/capgym/cap-x.git $PREFIX/sources/cap-x
git -C $PREFIX/sources/cap-x checkout --detach 53e9966d7a8e2fa7494676772bccc35280f5c0ed
git -C $PREFIX/sources/cap-x submodule update --init --recursive \
  capx/third_party/sam3 capx/third_party/contact_graspnet_pytorch
uv venv --python 3.11 --seed $PREFIX/envs/perception
uv pip install --python $PREFIX/envs/perception/bin/python \
  torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
uv pip install --python $PREFIX/envs/perception/bin/python -r ora/agent/model_services/installation/requirements-x86_64.txt
curl -fL -o $PREFIX/models/sam3.pt https://modelscope.cn/models/facebook/sam3/resolve/master/sam3.pt
echo "9999e2341ceef5e136daa386eecb55cb414446a00ac2b55eb2dfd2f7c3cf8c9e  $PREFIX/models/sam3.pt" | sha256sum -c -
```

ModelScope serves the same `sam3.pt` (3.45 GB) that the services pin, with no
login. Hugging Face `facebook/sam3` is gated; use it only if you already have
access. With the `ORA_SAM_*` / `ORA_GRASP_*` keys in `.env`:

```bash
set -a; . ./.env; set +a
./scripts/ora services start
./scripts/ora services status    # non-zero until both are ready (~3 min)
./scripts/ora services qualify   # prints the readiness report path
```

### 9.4 RoboDojo (Isaac Sim 5.1)

```bash
R="--root $PREFIX/robodojo-runtime"
./scripts/setup-robodojo sources $R
./scripts/setup-robodojo assets $R          # 66 GB through git-lfs
./scripts/setup-robodojo sdk $R             # Python 3.11, Torch 2.7 cu128, Isaac Sim 5.1
./scripts/setup-robodojo resolve $R && ./scripts/setup-robodojo packages $R
./scripts/setup-robodojo bind-assets $R
./scripts/setup-robodojo graphics-compat $R
./scripts/setup-robodojo doctor $R && ./scripts/setup-robodojo manifest $R
```

If the system lacks `libGLU.so.1` or `libvulkan.so.1`, unpack them first
(`apt-get download libglu1-mesa libvulkan1`, then `dpkg -x <deb>
$PREFIX/syslibs`) and export
`LD_LIBRARY_PATH=$PREFIX/syslibs/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH`
before `graphics-compat` and in every shell that runs RoboDojo.

The code-mode sandbox interpreter that RoboDojo's default policy runs programs
with (`ora setup` component `code-mode`), from its hash-pinned lock:

```bash
uv venv --python 3.11 $PREFIX/envs/code-mode
uv pip install --python $PREFIX/envs/code-mode/bin/python --require-hashes --no-deps \
  --only-binary :all: --compile-bytecode -r ora/agent/code_mode/sandbox-requirements.txt
```

`doctor` checks the source, assets and task inventory (expect `fail=0`); it
skips Isaac imports, which the card qualification exercises. See the
[RoboDojo installation reference](../../ora/environment/backends/robodojo/installation/INSTALL.md).

### 9.5 `.env`

`ora setup --env` writes these keys, the `ORA_SAM_*` / `ORA_GRASP_*` service keys and
`ORA_GPU_PREFIX`. `ora run` binds the host itself. For the explicit
sequence, also set `ORA_EXECUTION_HOST=single-gpu`:

```dotenv
ORA_EXECUTION_HOST=single-gpu
DEEPSEEK_BASE_URL=https://api.deepseek.com
LIBERO_PRO_PYTHON=/home/you/.local/share/ora/gpu/envs/libero/bin/python
LIBERO_PRO_ROOT=/home/you/.local/share/ora/gpu/sources/LIBERO-PRO
LIBERO_PRO_DATA_ROOT=/home/you/.local/share/ora/gpu/sources/LIBERO-Pro-data
MUJOCO_GL=egl
PYOPENGL_PLATFORM=egl
TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1
ROBOTWIN_ROOT=/home/you/.local/share/ora/gpu/sources/RoboTwin
ROBOTWIN_PYTHON=/home/you/.local/share/ora/gpu/envs/robotwin/bin/python
CUROBO_ROOT=/home/you/.local/share/ora/gpu/sources/curobo
CUROBO_PYTHON=/home/you/.local/share/ora/gpu/envs/robotwin/bin/python
ROBODOJO_ROOT=/home/you/.local/share/ora/gpu/robodojo-runtime/source/RoboDojo
ROBODOJO_ASSET_ROOT=/home/you/.local/share/ora/gpu/robodojo-runtime/assets/Assets
ROBODOJO_PYTHON=/home/you/.local/share/ora/gpu/robodojo-runtime/env/bin/python
CUROBO_V2_ROOT=/home/you/.local/share/ora/gpu/robodojo-runtime/source/RoboDojo/third_party/curobo
CUROBO_V2_PYTHON=/home/you/.local/share/ora/gpu/robodojo-runtime/env/bin/python
ORA_TIMING_PYTHON=/home/you/.local/share/ora/gpu/envs/motion-timing/bin/python
ORA_CODE_MODE_PYTHON=/home/you/.local/share/ora/gpu/envs/code-mode/bin/python
```

Set all three `ROBODOJO_*` values: when they are unset, the runtime falls
back to `~/robodojo-runtime`. LIBERO's planner is cuRobo V2 from the RoboDojo
runtime, so `CUROBO_V2_PYTHON` is the RoboDojo interpreter and
`CUROBO_V2_ROOT` the cuRobo checkout it imports; `CUROBO_PYTHON` points at the
RoboTwin interpreter, whose CuRobo v0.7.8 RoboTwin uses. `ORA_TIMING_PYTHON` is a separate small interpreter: `uv venv
--python 3.11` and the pins of `ora/environment/planners/timing/requirements.py`.
`ORA_CODE_MODE_PYTHON` is the code-mode sandbox's interpreter; when it is unset,
ORA uses `envs/code-mode/bin/python` under `ORA_GPU_PREFIX` (else
`~/.local/share/ora/gpu`), where setup builds it.

### 9.6 One task by hand

```bash
set -a; . ./.env; set +a
REPORT=$(ls -t outputs/model-services/qualification/*.json | head -1)

# LIBERO: no qualification
./scripts/ora spec libero compose libero_goal 0 --seed 0 --experiment ora-interactive \
  --resource-profile libero-gpu0 --service-report $REPORT --cohort single-gpu-libero

# RoboTwin: scan one task for one seed the expert solves, then compose with it
./scripts/ora qualify robotwin prepare --robotwin-root $ROBOTWIN_ROOT --robotwin-python $ROBOTWIN_PYTHON \
  --tasks click_bell --task-config demo_randomized --candidate-count 4 --accepted-per-task 1 \
  --slots robotwin-gpu0
./scripts/ora qualify robotwin qualify --plan <printed plan.json> --confirm QUALIFY-ROBOTWIN:<printed hash>
./scripts/ora spec robotwin compose click_bell --task-config demo_randomized --seed <accepted seed> \
  --qualification <printed seed-manifests/click_bell.json> --experiment ora-interactive \
  --resource-profile robotwin-gpu0 --service-report $REPORT --cohort single-gpu-robotwin

# RoboDojo: card once per GPU/driver/installation, task once per task
./scripts/ora qualify robodojo card general_pickup --gpu 0 --resource-profile robodojo-gpu0 --yes
./scripts/ora qualify robodojo task general_pickup --gpu 0 --resource-profile robodojo-gpu0 \
  --card <card-qualification.json> --yes
./scripts/ora spec robodojo compose general_pickup --experiment ora-interactive --gpu 0 \
  --resource-profile robodojo-gpu0 --task-qualification <task-qualification.json> \
  --service-report $REPORT --cohort single-gpu-robodojo

./scripts/ora plan preflight outputs/ora-interactive/specs/<spec>.json
./scripts/ora interactive --spec outputs/ora-interactive/specs/<spec>.json --confirm-launch
```

Keep the checkout at the same commit from qualification through the run;
qualifications refuse a dirty tree.

## 10. Measured on the verification machine

RTX PRO 6000 Blackwell Max-Q (compute capability 12.0, 96 GB), driver
580.159.04, Ubuntu 24.04, no sudo, no Docker.
Other tenants used ~24 GB of the card. Times exclude downloads.

| Step | Result | Time |
|---|---|---|
| RoboTwin env + CuRobo (cu128, sm_120) | IK success 32/32 | CuRobo build 6 min |
| Perception services | ready, readiness report written | ~3 min to ready; 8–10 GB GPU memory |
| RoboDojo runtime | doctor fail=0 | `graphics-compat` 39 s; 86 GB |
| RoboTwin seed scan `click_bell` | seed 100000 accepted | 2.5 min |
| RoboDojo card + task `general_pickup` | written | 9 min 20 s + 4 min |
| `ora run --env libero --task libero_goal/0` | official success, 6 decisions, 13 model calls, sealed | 8.3 min |
| `ora run --env robotwin --task click_bell` | official success, 3 decisions, 8 model calls, sealed | 7.5 min |
| `ora run --env robodojo --task general_pickup` | official success, 6 decisions, 22 model calls, sealed | 24 min (slow API) |
| `ora local` (CPU) `libero_goal/0` | official success, 40 actions, 70 model calls | ~21 min |

These are single episodes that show the path works, not success rates.

The whole guide was rerun on the same RTX PRO 6000 in a fresh
user-space sandbox (new home, default prefix, a real key; §2 found no `uv`, no
`git-lfs` and Node 18):

| Step | Result | Time |
|---|---|---|
| §2 tools | uv, Node 22 and git-lfs installed | 3 s |
| `ora setup --env libero --env robotwin --env robodojo`, first run | all steps done, all downloads included | 24 min (RoboDojo's 66 GB of assets ~8 min) |
| same, rerun | every step `already done`; `doctor` and `manifest` rerun | 1–3 s |
| disk | 140 GB in the prefix, ~20 GB in `~/.cache/uv`, 0.5 GB checkout | — |
| `ora run --env robotwin --task click_bell` | official success, 4 decisions, 12 model calls, sealed | 4.8 min with service start (episode 3.7 min); seed scan beforehand 2 min |
| `ora run --env robodojo --task general_pickup` | card + task qualified in `ora run`, official success, 2 decisions, 8 model calls, sealed | card 9 min 16 s, task 4 min 5 s, episode 2.1 min |
| `ora run --env libero --task libero_goal/0`, steered once in the terminal UI | official success, 5 decisions, 11 model calls, sealed as human-assisted | episode 4.1 min |
| GPU memory peaks | services 8–10 GB; RoboTwin worker 8.0 GB; RoboDojo worker 10.4 GB; LIBERO's CuRobo planner 1.0 GB | — |

On one H200 (compute capability 9.0, driver 595.91.07), with a
placeholder key so no model was called, the same path took:

| Step | Result | Time |
|---|---|---|
| `ora setup --env libero --env robotwin --env robodojo` | all steps done; libGLU and the Vulkan loader unpacked without sudo | downloads dominate; 141 GB in the prefix |
| Perception services | ready, readiness report written | ~35 s to ready |
| RoboTwin seed scan `click_bell` | seed 100000 accepted | 2 min 32 s |
| RoboDojo card + task `general_pickup` | written | 13 min 24 s + 5 min 34 s |
| launch preflight, LIBERO / RoboTwin / RoboDojo | 23 / 17 / 25 checks passed; only the model request failed (placeholder key) | 32 s / 25 s / 8 s |
