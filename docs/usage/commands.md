# Commands and what they write

`ora` is the generic operator surface for an already composed
RunSpec and an indexed run. This page says what each command writes and
when to use it. From a source checkout, use the repository wrapper:

```bash
./scripts/ora --help
```

The wrapper runs `.venv/bin/python` from the checkout when it exists, else
`$PYTHON`, else `python3`. If your environment lives elsewhere (for example via
`UV_PROJECT_ENVIRONMENT`), use `uv run --locked ora ...` or set `PYTHON` to that
environment's interpreter. An installed wheel exposes the same command as `ora`.
`ora run --env`, `ora local` and `ora play` select a task and seed and compose
the RunSpec themselves; the per-environment composers (`ora spec
robotwin|libero|robodojo|robocasa|robolab`, the tools each backend declares in
`ora/orchestrator/backends/registry.py`) write one for the review and launch
commands below. `ora --help` lists the commands in five groups (running an
episode, formal runs and evidence, qualification and suites, configuration,
developer); `ora <command> --help` gives one command's options, and the
[command reference](cli-reference.md), generated from the parser by
`ora dev cli-reference`, prints all of them. `ora help topics` lists the
short topic pages shipped with ORA. One
internal command is deliberately excluded from the public command set and
from `ora --help`: `experiments`, a development view of the
[experiment-identity registry](../reference/experiment-identity.md#inspection-and-lifecycle).
A launch's supervisor is its own process (`python -m ora.orchestrator.supervise`),
which an operator never invokes.

`ora --version` prints `ora <version>`. Run from a source checkout (the
repository's `pyproject.toml` beside the `ora` package), it is the version in
that `pyproject.toml`, even when an older editable install's metadata says
otherwise; from a wheel install, it is the installed `ora-agent` package's
version (`ora.version`). The terminal transcript header shows the same
version.

## Public commands

The table order is canonical and is checked against the parser in pytest.
“Writes” describes local Runtime evidence or process effects; it does not mean
that a command sends a robot action unless explicitly stated.

| Command | Syntax | Writes or external effect | Purpose |
|---|---|---|---|
| `setup` | `setup [--root PATH] [--python PATH] [--graphics auto\|cgl\|glfw\|egl\|osmesa] [--json]`; `setup --env libero\|robotwin\|robodojo\|perception [--env …] [--prefix DIR] [--cuda-home DIR] [--cuda-arch X.Y] [--dry-run] [--json]` | Without `--env`: pinned LIBERO sources and data, the CPU simulator environment and, unless already installed as locked, `npm ci` in `ora-pi/` (the agent core and its terminal UI). With `--env`: the same install, then the single-GPU stacks under the prefix and their non-credential `.env` keys | Install what `ora local` or `ora run --env` needs; starts no simulator, service or model call. |
| `doctor` | `doctor [--env synthetic\|robotwin\|libero_pro\|robodojo\|local] [--root PATH] [--json]` | None | Discover Python, ORA, credential presence, the agent core's Node.js (>= 22.19) and locked `ora-pi` packages, simulator interpreters, asset directories and graphics libraries without starting a simulator. `doctor` names the LIBERO / LIBERO-Pro backend `libero_pro`; `setup`, `run` and `play` call the same environment `libero`. For `robodojo` it also loads `libvulkan.so.1` in the simulator interpreter (honouring `LD_LIBRARY_PATH`) and, on drivers that need it (595.x), verifies the Vulkan compatibility-layer receipt. |
| `run` | `run --env synthetic [--provider-profile ID]`; `run --env libero\|robotwin\|robodojo --task TASK [--seed N] [--task-config demo_randomized\|demo_clean] [--candidates N] [--yes] [--requalify] [--prepare-only] [--confirm-launch] [--provider-profile ID]` | Synthetic: calls the configured model and writes a sealed episode. GPU: may start the perception services, run a confirmed one-time qualification, writes a RunSpec under `outputs/ora-interactive/specs/`; with `--confirm-launch` and a picker selection launches one run | `synthetic` runs the bounded, simulator-free `native_api` quickstart. A GPU environment prepares one task on the shipped `single-gpu` host and opens `ora interactive` on it ([guide](../getting-started/single-gpu.md)). Every `native_api` run uses the Pi agent core ([agent core](../reference/configuration-and-evidence.md#agent-core)). |
| `play` | `play --env synthetic\|libero\|robotwin\|robodojo [--provider PROFILE\|env] [--host HOST] [--scenes N] [--draw-seed N] [--prepare-only] [--confirm-launch] [--no-attach]`; `play --env ENV --spec PLAY.json --confirm-launch`; `play audit --env robotwin\|robodojo …` | Calls the chosen provider and launches a free-play run under `ora-play` (a real environment only with `--confirm-launch`); `--prepare-only` writes the RunSpec under `outputs/ora-play/specs/`; writes instructions and controls to the run's inbox | Free play in the run's own Pi terminal UI: a random qualified scene, no task; the agent is Pi as a coding agent with the robot as a device. Enter sends the next instruction, Esc stops the running one, `/reset` loads another scene, `/quit` ends and seals; Pi's own commands work ([guide](play.md)). `play audit --env ENV` hands the rest of the line to the play-scene audit that environment's backend declares (`backends/robotwin/play_audit.py`, `robodojo_play_audit.py`; see [environment tools](#environment-tools)). |
| `replay` | `replay RUN_ID\|RUN_DIR [--speed S] [--motion-speed X\|same] [--from S] [--view compact\|detailed] [--expand-thinking] [--lang en\|zh] [--publish] [--port N] [--no-browser]`; `replay RUN --export OUT.mp4 [--speed S] [--motion-speed X\|same] [--fps N] [--size WxH] [--scale N] [--zoom Z] [--from S] [--to S] [--workers N] [--crf N] [--ffmpeg PATH] [--chromium PATH] [--export-python PY]` | Serving: ORA's web server and gateway in a private temporary directory, nothing in the run. `--export` writes the video and its report (`OUT.mp4.json`) | Replay a finished run in the browser on `ora web`'s page, rebuilt read-only from its evidence: the waiting at `--speed` (16× by default), each robot motion by its captured frames' simulated time at `--motion-speed` (1×: real time; `same`: `--speed` throughout), with play, pause and a scrubber; a live run is refused. `--export` draws the replay into an H.264 video at those rates in a headless Chromium (needs Playwright and ffmpeg) ([guide](remote.md#replay-a-finished-run)). `--incident N --report OUT` replays the run up to action N and runs N again on this checkout's motion stack, with no model ([reference](../reference/configuration-and-evidence.md#incident-replay)). |
| `web` | `web setup`; `web [RUN_ID\|RUN_DIR] [--last] [--port N \| --socket PATH] [--token-file PATH] [--no-browser]` | `setup` runs `npm ci` and the page's build in `ora-pi/web`; serving starts ORA's web server and a gateway in a private temporary directory, nothing in the run | Show a live Pi-core run in the browser: the conversation, the robot's cameras and each step's images on one screen, behind a one-time launch token ([guide](remote.md#web-view)). A run that is not live is refused. |
| `cameras` | `cameras [RUN_ID\|RUN_DIR] [--last] [--protocol auto\|kitty\|iterm2] [--interval S] [--once]` | nothing | A run's cameras in the terminal, two by two, redrawn at every captured frame, with the observation step the agent last read: run it in the pane beside Pi's terminal. Needs a terminal with images (kitty, Ghostty, iTerm2, WezTerm; inside tmux `allow-passthrough on`); any other is refused with that reason ([free play](play.md)). |
| `interactive` | `interactive [--provider PROFILE\|env] [--task TEXT] [--actions N] [--no-attach] [--world-view auto\|off] [--world-port N] [--world-browser auto\|off] [--world-public-url URL]`; `interactive --spec PATH --confirm-launch --ui pi`; `interactive --spec PATH [--spec PATH ...] [--confirm-launch] [--emit jsonl]` | Without `--spec`: calls the chosen provider and launches a Synthetic episode under `ora-interactive` on the Pi agent core; with `--spec` and `--confirm-launch` (and a picker selection), launches the prepared task; writes operator messages to the run's inbox | Without `--spec` (or with `--ui pi`): one run in the run's own Pi terminal UI, steerable, detachable, with the run's cameras on a loopback browser page while attached ([guide](interactive.md)). With `--spec`: pick a prepared Native benchmark task and steer it from the task picker. |
| `attach` | `attach [RUN_ID\|RUN_DIR] [--last] [--world-view auto\|off] [--world-port N] [--world-browser auto\|off] [--world-public-url URL]` | Sends the operator's keys and messages to the run's agent process; `/stop` requests the run's stop; serves the run's camera frames on a loopback port while attached | Show a running Pi-core run's terminal UI on this terminal; Ctrl+D detaches and the run continues. Re-attaches by itself when the Supervisor restarts the agent process. Without a run, offers the running ones. In a terminal (or with a world port or URL) it serves the run's world view and the TUI header shows its address ([remote use](remote.md)). |
| `sessions` | `sessions [--json]`; `sessions export RUN_ID\|RUN_DIR [--output PATH.html\|PATH.jsonl]` | `export` writes one file | List runs that have a Pi session (state, attachable, decisions, model calls, sealed, human-assisted); export one as Pi's HTML transcript or its raw session JSONL. |
| `local` | `local [--suite S] [--task-id N] [--seed N] [--root PATH] [--confirm-launch] [--world-view auto\|off]` | With `--confirm-launch` and a picker selection, launches one run under `ora-interactive` | Open the local CPU MuJoCo LIBERO task picker ([guide](../getting-started/local.md)); uses the bundled `local-cpu` host profile and the DeepSeek Low provider. |
| `plan` | `plan validate RUN_SPEC`; `plan show RUN_SPEC`; `plan context RUN_SPEC`; `plan preflight RUN_SPEC` | `validate` and `show`: none. `context`: temporary files only. `preflight`: writes a content-addressed preflight record | `validate` parses, validates and content-binds one RunSpec and prints its plan identity. `show` prints the resolved content-addressed RunPlan, including policy, Runtime, budgets, hashes and the launch confirmation code (the last six hex digits of the plan sha256). `context` previews the exact project-owned context, composition and tools without starting an episode; Native provider requests are inspectable. `preflight` runs source, host, environment, provider and agent-core admission gates without launching an episode; a live provider check may be billable when required by the RunSpec. |
| `launch` | `launch RUN_SPEC [--yes]` | Creates a run and starts its supervisor | Repeat preflight, require the plan confirmation code, atomically acquire the RunPlan's execution-resource lease, enforce optional plan-bound launch admission and launch one episode. A `single_attempt_cell` claim is consumed by this generic path, including when called outside an experiment wrapper. Unprofiled and physical Runtime plans retain host-wide exclusivity; explicitly qualified simulator slots may coexist. `--yes` is only for an already reviewed higher-level workflow. |
| `retry` | `retry RUN_ID [--yes]` | Creates a distinct retry run | Retry only an unsealed, incomplete attempt that published zero robot decisions and whose RunPlan permits retry. A `single_attempt_cell` plan rejects retry. It never overwrites or resumes the old run directory. |
| `status` | `status [RUN_ID]` | None | Print one status document or summarize every active resource lease and the complete indexed local run registry. |
| `stop` | `stop RUN_ID` | Sends `SIGTERM` to one verified process group | Request graceful termination only when the exact run ID, its resource lease and process command agree; sibling slots are unaffected. |
| `watch` | `watch RUN_ID\|RUN_DIR [--speed S] [--ui auto\|pi\|ansi\|plain] [--raw]` | None | Follow a live run, or replay a finished one, as ORA's terminal transcript: the model's visible words, condensed tool results, refusals, receipts and per-round verdicts. A finished run replays on recorded time (`--speed 0` prints at once). `--ui pi` draws with pi-tui (`ora-pi/src/watch/`; needs Node.js 22.19+ and `npm ci` in `ora-pi/`); `auto` uses it in a terminal when installed, else the built-in ANSI view. `--raw` prints the canonical causal events as JSON lines instead. Read-only: it writes nothing under the run and takes no lease; `Ctrl-C` stops watching, not the run. |
| `workspace` | `workspace` | None | Show the Git-common evidence root, state root, run index and legacy discovery roots used across worktrees. |
| `task` | `task resolve\|run\|submit FILE [--task T] [--seed S] [--layout L] [--set KEY=VALUE]` | `run`: a RunSpec under `outputs/NS/specs/`, a run like `launch --yes`, and its task record `outputs/NS/tasks/RUN_ID.json`; `submit`: a scheduler request | Resolve a task file (`ora.task.v1`) with overrides, run it once on a run binding (`--binding` or `ORA_ROUTER_ALLOCATION`) and wait for it, or send one run to a scheduler (`--print` shows the request). See [task files](../reference/tasks.md). |
| `evaluate` | `evaluate RUN_ID` | Writes deterministic evaluation evidence | Validate the completed trace and bind the official environment result without asking the policy to judge success. |
| `seal` | `seal RUN_ID [--backfill]` | Writes the seal and makes the inventory immutable | Seal only an evaluator-valid run; sealed evidence cannot be retried or rewritten. Landing attempts this automatically (mode `landing`, refusals recorded as `seal-refusal.json`); `--backfill` seals an old unsealed run with the current evaluator while pinning its recorded source identity and marking the seal as a backfill. |
| `revalidate` | `revalidate [RUN_DIR ...] [--run-list LIST.json] --output-dir OUT [--workers N]` | Writes separate reports only | Review exact completed run directories with checker/source hashes, original/new evaluations and differences; preserve all original files and defer active prefixes. |
| `export` | `export RUN_ID [--destination PATH]` | Writes a transfer archive | Export one sealed run with its verified inventory. The default destination is the experiment workspace's `exports/` directory. |
| `export-sft` | `export-sft RUN_ID [--destination PATH] [--view sft\|wire\|openai] [--check] [--tokenize-url URL [--tokenize-key-env NAME]] [--redact-host-paths [--redact-path PREFIX]]` | Writes a training projection or a wire export | `sft` (default): eligible Native model/tool calls as OpenAI tool-call JSONL from canonical request evidence, with each call's provider usage in `tokens.jsonl`. `wire`: every call as sent, each image once, rebuilt and checked against the archived requests, with each policy call's token ids in `tokens.jsonl` (from streams that carry them, or the engine at `--tokenize-url`). `openai`: a self-contained package of standard OpenAI conversations (images inline) and calls with the wire export, the replay program, a README and a standard-library verify tool. `--check` re-checks an existing wire export or package. `--redact-host-paths` replaces the host paths that name the machine (run, outputs, workspace and checkout roots, the code-mode interpreter's prefixes, the home directory, each `--redact-path`) by placeholders and records the redaction; redacted rows verify against the sha256 recorded at export ([reference](../reference/configuration-and-evidence.md#wire-export)). Neither replaces the canonical trace. |
| `export-traces` | `export-traces --runs LIST --destination DIR [--work DIR] [--workers N] [--keep-host-paths] [--redact-path PREFIX] [--forbid TEXT] [--no-compress] [--shard-mb N]` | Writes a dataset directory, and beside it `DIR.report.json` and `DIR.run-map.jsonl` | Export many runs as one trace dataset: one record per episode with its context segments in order, each image once across the dataset, an index and a standard-library reader (`ora_traces.py`). `LIST` has one run directory per line, or one JSON object per line (`run`, and optionally `episode_id`, `model`, `benchmark`, `labels`). Each run passes the wire export's checks first; host paths are replaced by placeholders unless `--keep-host-paths`; the run id is replaced by the episode id; an episode whose text holds a `--forbid` string is refused. Exits 2 when a run was refused or failed; the report lists them. See [the RL interface](rl-interface.md#a-dataset-of-many-runs). |
| `report` | `report RUN [RUN ...] [--json FILE] [--markdown FILE]`; `report --namespace NS --cohort ID [--cohort ID ...]` (the report's own options: `ora report --help`) | Writes only the `--json`/`--markdown` files it is given, never inside a run | The read-only stop-reason, validity, effort, token and API-equivalent cost report of sealed or in-progress runs (`ora/orchestrator/reports/run_report.py`). |
| `metadata` | `metadata RUN_ID` | None | Print the run's exact context/environment manifest. |
| `state` | `state RUN_ID [--step N]` | None | Verify the policy-state hash chain and print the latest or selected public state. |
| `metrics` | `metrics RUN_ID` | Rebuilds context audit and Runtime metrics | Deterministically derive context, token, cache, image and latency measurements from raw evidence. Run before evaluation and sealing. |
| `ownership` | `ownership RUN_ID` | Rebuilds the behavioral-ownership audit (`ownership-audit.json`) | Deterministically count Language-Model decisions, explicit robot actions, hidden planner/controller work and re-observation boundaries from append-only evidence. Selection and delegation only — no competence or success claim. |
| `trace-validate` | `trace-validate RUN_ID` | None | Validate the canonical Agent trace, request hashes and artifact graph; exit 2 when invalid. |
| `spec` | `spec robotwin\|libero\|robodojo {tasks,spec,compose} …`; `spec robocasa {language,compose} …`; `spec robolab {tasks,compose} …` (`ora spec ENV` lists an environment's tools; each tool's own options: `ora spec robodojo compose --help`) | `compose` writes a RunSpec under `outputs/NS/specs/` | List an environment's tasks and compose fresh RunSpecs on the default baseline, with the tools its backend declares (`spec_tools` in `ora/orchestrator/backends/registry.py`): RoboTwin and LIBERO (`ora/orchestrator/spec/benchmark.py`, [the builder](../reference/configuration-and-evidence.md#fresh-benchmark-specifications); a LIBERO base suite or LIBERO-Pro core suite by its name), RoboDojo, RoboCasa365 and RoboLab (`ora/orchestrator/backends/<backend>/spec.py`). Starts no simulator, service or Provider and grants no launch. |
| `qualify` | `qualify robotwin {configs,prepare,qualify,merge,seal} …`; `qualify robodojo {card,task,smoke,revalidate,physics-probe,motion-replay,motion-metrics} …`; `qualify libero motion …`; `qualify robocasa motion …`; `qualify robolab {motion,contract-witness} …` (`ora qualify ENV` lists an environment's tools; each tool's own options: `ora qualify robotwin prepare --help`) | Qualification records under the environment's outputs; `prepare`/`qualify` and the RoboDojo producers run confirmed simulator work | Environment, lane and seed qualification with the tools each backend declares (`qualify_tools` in `ora/orchestrator/backends/registry.py`): RoboTwin evaluation seeds ([reference](../reference/robotwin-qualification.md)), the RoboDojo lane (card, task, scene smoke and gate), physics probe and motion qualification ([RoboDojo motion qualification](../reference/robodojo-motion-qualification.md)), RoboCasa365 and RoboLab scripted sparse-motion records, and RoboLab's seed-0 task-contract witnesses ([RoboLab tasks](../reference/robolab-tasks.md)). Never launches a policy episode. |
| `suite` | `suite run --cells CELLS.json --experiment EXP --cohort ID --group G --lanes PROFILE ... --service-report REPORT --yes`; `suite status\|drain --experiment EXP --group G` | `run` launches one single-attempt run per listed cell across fixed lanes | The official-suite queue (`ora/orchestrator/suite/queue.py`): one attempt per cell, never repaired or resealed; `drain` stops new launches and lets running cells finish ([evaluation protocol](../evaluation/protocol.md)). |
| `providers` | `providers [--json]`, `providers key PROFILE\|VARIABLE [--remove]`, `providers add ID --protocol P --endpoint URL --model M [--images] [--context-window N] [--max-output N] [--reasoning-effort E] [--request-options JSON] [--recovery-window SECONDS] [--cluster-http] [--reasoning-field F] [--key-env NAME] [--no-key] [--stdin] [--no-test]`, `providers remove ID`, `providers test PROFILE` | `key`, `add` and `remove` write your user-level ORA config (`~/.config/ora`; keys mode 0600) | List provider profiles (yours included) with admission, usage scope, capabilities, context window, any request options and recovery window, and where each key comes from; enter a key without echo into the user-level store `/login` shares, add or remove an endpoint of your own, and test a profile with the launch probe. `add` records the context window (131072 unless `--context-window`), `--request-options` (a JSON object checked like a catalog profile's `request_options`, OpenAI-compatible only) and `--recovery-window` (30..1800 s), admits plain HTTP to another host only with `--cluster-http` (`cluster_http_v1`, for a vLLM node of your own cluster), records `--reasoning-field` (`reasoning` for vLLM), prints what it recorded, and stores nothing for a refused value ([self-hosted endpoints](../getting-started/model-apis.md#self-hosted-endpoints)). |
| `services` | `services start\|status\|stop\|qualify [--state DIR]` | `start` and `stop` start or stop the resident services; `qualify` writes a hash-named readiness report | Operate the resident SAM3 and Contact-GraspNet services from the shared workspace: `status` exits 1 until both are ready, `stop` refuses while Runtime leases exist. |
| `cell` | `cell init [--example PATH]`, `cell check\|read\|record-rest`, `cell grippers [--no-save]`, `cell calibrate --method arm-poses\|charuco\|wrists [--square-mm N] [--role R] [--no-save]`, `cell calibrate --method hand-eye [--square-mm N] [--pattern CxR] [--placements N] [--arms A,B] [--records DIR …] [--ask terminal\|broker] [--no-save]`, `cell board [--port N] [--host H] [--depth-service URL]`, `cell depth-service …`, `cell home\|hold\|float\|release\|estop\|diagnose\|stop\|kill\|cameras\|can`, `cell pending`, `cell approve [--id ID] [--value V]`, `cell decline [--id ID] [--reason TEXT]`; every action but `depth-service` takes `[--cell CELL.yaml] [--json] [--yes] [--board-port N]` | `record-rest` writes the rest pose, and `grippers` and `calibrate` their results unless `--no-save`, into the cell's calibration file; `init --example` writes an example config; `calibrate --method hand-eye` writes the measured fixed cameras, wrist mounts, arm bases and joint zeros only after you approve its result; `grippers`, `calibrate --method arm-poses\|hand-eye`, `home`, `hold`, `float` move or hold the real arms; `release` and `estop` disable motors (arms fall); `can` re-initialises the CAN interfaces (sudo); `cameras` hardware-resets the cameras; `approve` and `decline` write one answer to the worker's waiting question (the approval broker's folder) | Set up, watch and recover a real robot cell from its cell config (`--cell`, else `$ORA_CELL`, else `~/.config/ora/cell.yaml`), in the robot's own interpreter. The operator verbs act at once, also while a play session moves the arms; with a cell board running they go through it, and `hold`/`float` need one. `pending`, `approve` and `decline` answer the questions a play session's worker asks (enable, each motion, release in place), by id, without the lock or a board. `--json` prints `{"ok": true, "result": …}` or `{"ok": false, "error": …}` (exit 1) ([guide](real-cells.md)). |
| `baselines` | `baselines [--json]`, `baselines --explain [--environment KIND] [--json]` | None | Inspect the Native robot default catalog, its source hash, per-environment tools, prompts and prerequisites; never probe or start services. `--explain` derives from code why the default is selected, its per-environment resolution chain, the maintained baselines, and each tool's effect, toolkit registry entry and, for a proposal, the host adapter that admits it and its route to the backend. |
| `lock` | `lock [--check]` | Writes `policy.lock` (without `--check`) | Regenerate `policy.lock`, the sorted `sha256sum`-format inventory of every tracked policy asset and of the path-loaded assets outside `ora/policy/`. `--check` writes nothing, lists assets that are missing from, extra in or changed against the lock, and exits 1 on any drift; CI runs it before the tests. Run it after editing, adding or deleting an asset (a new asset counts once it is `git add`ed) and commit the result. |
| `toolkit` | `toolkit replay RUN --list`; `toolkit replay RUN --at K --script PATH [--args JSON] [--service-report REPORT] [--output DIR] [--json]` | Writes a new evaluation directory, never the source run; external perception only when explicitly bound | Try trusted Python at a recorded parent-call boundary using replay data and lib; new simulator queries fail, actions only compare, gaps refuse. `--tool` remains unsupported before M4 ([toolkit I/O](../reference/toolkit-io.md)). |
| `dev` | `dev embodiment …`; `dev prompt-bundle {template,audit} …`; `dev task-contracts {template,render,audit} …`; `dev robolab-tasks {generate,regression,scan-assets,promote,tasks} …`; `dev cli-reference [--write\|--check]` (each tool's own options) | Writes only the files a tool is asked to write | Authoring tools: compile embodiment descriptors into cards, create or audit a prompt bundle ([guide](../developer/authoring/prompt-bundle.md)), the task-contract review ([guide](../developer/authoring/task-contract.md)), RoboLab-120's generated roster and contracts ([RoboLab tasks](../reference/robolab-tasks.md)), and the [command reference](cli-reference.md) generated from the parser. The Viewer's videos and LIBERO previews are `ora-viewer compile videos\|previews` ([viewer](../../viewer/README.md)). |
| `help` | `help`; `help COMMAND [SUBCOMMAND]`; `help topics`; `help TOPIC` | None | The grouped overview `ora --help` prints (in Chinese with `ORA_UI_LANG=zh` or a `zh` locale), one command's help, or a short topic page shipped with ORA (`ora/app/help/<topic>.md`): `runs`, `evidence`, `play`, `remote`, `safety` and `rl-interface`, each distilled from the usage page it names. A topic comes before the command of the same name: `ora help play` prints the page, `ora play --help` the command's options. |

## Normal lifecycle

Use the owning experiment CLI to compose the RunSpec, then follow the generic
control lifecycle:

```bash
./scripts/ora plan validate path/to/run-spec.yaml
./scripts/ora plan show path/to/run-spec.yaml
./scripts/ora plan context path/to/run-spec.yaml
./scripts/ora plan preflight path/to/run-spec.yaml
./scripts/ora launch path/to/run-spec.yaml

./scripts/ora status RUN_ID
./scripts/ora watch RUN_ID
./scripts/ora metrics RUN_ID
./scripts/ora evaluate RUN_ID
./scripts/ora seal RUN_ID
./scripts/ora export RUN_ID
```

`launch` is the only command in that sequence that starts an episode. A
preflight can still contact the configured provider and therefore may require
explicit billable confirmation in the owning experiment CLI. Use `stop` only
for the exact active run; do not replace it with a broad process kill.

For Native live-marker policies, `plan preflight`, `launch` and `retry` can probe
the configured standby credential pool. HTTP 401, 402, 403 or 429 advances to
the next distinct key, with at most one complete qualification attempt per
candidate. Other errors stop admission. A successful launch pins the qualified
credential in the child environment; reports contain indices, never secrets.
`launch` and `retry` require their own fresh qualification, not a previously
saved report. See [configuration and evidence](../reference/configuration-and-evidence.md).

## Remote GPU server: `ora-remote`

`./scripts/ora-remote HOST [--repo PATH] [--local-port N] [--remote-port N]
[--connect-timeout S] [--no-browser] [--new] [--web]
{play,interactive,attach,watch,web} [ORA arguments ...]` runs on your own
computer. It runs that `ora` command on `HOST` over one `ssh -tt` connection
that forwards one loopback port, shows the command's Pi terminal UI in this
terminal (passing this terminal's image protocol as pi-tui settings) and opens
the forwarded world view in a local browser once it answers. `--web` also
opens ORA's web page for the run, and the `web` command opens only the page
(the host needs `ora web setup`). The same `play` or `interactive` command attaches
again to the run it left going (`--new` starts another). It never adds
`--confirm-launch`, retries SSH or launches anything itself; see
[Using ORA on a remote GPU server](remote.md).

## What “tested” means

The locked pytest suite checks every public command name, their parser order,
each command's one-line help and `--help` (in process, at the parser; one
subprocess runs `scripts/ora --help`), their dispatch branch, the generated
command reference, the topic pages and this table.
It also tests the underlying deterministic RunSpec, context, preflight,
workspace/index, policy-state, metrics, evaluation, sealing, archive and SFT
contracts with fake or Synthetic boundaries.

Pytest does **not** launch a paid model or a live LIBERO/RoboTwin simulator.
Consequently, `launch`, `retry`, `watch` and `stop` have deterministic dispatcher,
parser and help coverage. Supervisor leases, run indexing and fake-process
boundaries are tested separately, but the exact real host/process/environment
path is qualified only by a source-bound experiment preflight or live
qualification. Passing pytest means the CLI wiring and fail-closed contracts
are intact; it is not evidence that a particular provider, host or robot task
is currently qualified.

## Environment tools

`ora spec ENV`, `ora qualify ENV` and `ora play audit --env ENV` hand the rest
of the line to the tools the environment's backend declares (`spec_tools`,
`qualify_tools` and `play_audit` in `ora/orchestrator/backends/registry.py`);
`ora spec ENV` and `ora qualify ENV` list them, and each tool's `--help` gives
its options. What they check and write is described with each environment:

- RoboTwin evaluation seeds (`ora qualify robotwin`) and the play-scene audit
  (`ora play audit --env robotwin`):
  [RoboTwin seed and scene qualification](../reference/robotwin-qualification.md).
- The RoboTwin and LIBERO RunSpec builders (`ora spec robotwin|libero`):
  [fresh benchmark specifications](../reference/configuration-and-evidence.md#fresh-benchmark-specifications).
- The RoboDojo lane, physics probe and motion qualification (`ora qualify
  robodojo`): [RoboDojo](../environments/robodojo.md#qualify-the-lane) and
  [RoboDojo motion qualification](../reference/robodojo-motion-qualification.md).
- RoboCasa365 and RoboLab (`ora spec robocasa|robolab`, `ora qualify
  robocasa|robolab motion`):
  [RoboCasa365 and RoboLab backends](../reference/robocasa-robolab-backends.md).

## Generic command maintenance

When adding, renaming or removing a public command, update all of the following
in the same change:

1. the command's module in `ora/app/` (`register` and `run`), and its entries in
   `COMMANDS` and `GROUPS` in `ora/app/main.py` (the public commands are the
   commands `GROUPS` lists, in its order);
2. the command table above;
3. the generated [command reference](cli-reference.md): run
   `./scripts/ora dev cli-reference --write` (`tests/ora/app/test_cli_reference.py`
   fails on a stale page);
4. the command's deterministic contract or dispatcher test;
5. any topic page in `ora/app/help/` that shows the command
   (`tests/ora/app/test_help.py` fails on a page naming a command that does not
   exist); and
6. `scripts/README.md` when the operator workflow changes.

A new environment's `spec`, `qualify` and play-audit tools need no app change:
its backend declares them (`spec_tools`, `qualify_tools`, `play_audit` in
`ora/orchestrator/backends/registry.py`) and `ora spec|qualify ENV` and `ora
play audit --env ENV` find them. Regenerate the command reference, whose
tables list them.

Do not add another shell wrapper merely to fill in a task, config or milestone.
Those selections belong to the explicit experiment CLI so the chosen study
identity remains visible.

## Startup binding and refusal diagnostics

Generic launch and preflight share the controller-source check with the RoboTwin
factory and select resident endpoints from the same RunPlan bindings as policy
tools. Missing experiment-wrapper environment setup no longer redirects a bound
SAM3/grasp client to a default port. Conflicting explicit endpoint overrides still
fail identity validation. A duplicate single-attempt claim prints its cohort/cell,
original run/plan and creation time to stderr and returns a failure exit code;
missing historical owner metadata is explicitly unavailable.

`ora services --help` lists the resident-service commands
(`start`, `status`, `stop`, `qualify`); `examples/official_suite/run.py
--help` lists the official-suite cell, compose and queue commands.
