# Configuration and evidence

## RunSpec and RunPlan

A RunSpec (`ora.run-spec.v1`) names `experiment`, `config`, `milestone`, `task`, `task_instruction`, `seed`, `evidence_tier`, `output_workspace`, `policy`, `runtime` and `budgets`. Additional qualification, session, prerequisite and launch-admission blocks are validated when selected, as are `episode_mode` and `play` ([play episodes](#play-episodes)). The optional `result_tier` (default `zero_shot`) labels what the policy carried into the episode and is recorded in the plan; `zero_shot_no_check` resolves the baseline's allowlist without `check_task`, the tier must match the allowlist both ways on a surface that offers `check_task`, a free-play plan stays `zero_shot`, and `with_experience` and `frozen_program` are refused ([result tiers](../evaluation/protocol.md#result-tiers)). The optional `latency_profile` (default `sim_benchmark`; `realtime`) resolves to the explicit switches every plan records as `latency_profile` ([latency profile](latency-profile.md)). Resolution is implemented in `ora/orchestrator/spec/run_spec.py` and the section modules beside it in `ora/orchestrator/spec/`; the resolved plan is the exact execution contract.

Policy kinds are `native_api` and `scripted`. Native providers are `openai_compatible` and `anthropic_messages`. A Native policy binds an explicit endpoint/model, credential-variable names, context/output limits, tool surface, action codec and observation/context policy. The selected ToolSurface's capabilities and schema compatibility are validated together. `memory_policy` is `disabled`.

### Agent core

`policy.agent_core` names the implementation that runs a `native_api` policy's agent loop (provider calls, request assembly, context management, agent-side evidence). Resolution is in `ora/orchestrator/episode/agent_core.py`:

| Value | Implementation | Status |
|---|---|---|
| `pi` | The TypeScript core in `ora-pi/` on the exactly pinned Pi packages (`@earendil-works/pi-*` 1.3.0, `undici` 8.10.2), run by Node.js >= 22.19 under Node's permission model; Python serves tools, observations and authority (`ora.agent.pi`) | The only agent core |
| `native` | The Python Native core | Removed from ORA (it remains in the robot-harness repository); refused |

A fresh plan resolves to `pi` for every context composition and retention (`native_slots_v3` with `rewrite_compact_v1` or `action_step_lossless_v4`, `native_slots_v4` with `action_step_lossless_v4`). A RunSpec with `policy.agent_core: native`, `ORA_AGENT_CORE=native` and `--agent-core native` are refused with the reason; `pi` is the only value either takes (`--agent-core`, on `ora run`, `launch`, `validate`, `plan`, `context` and `preflight`, is hidden and sets `ORA_AGENT_CORE` for one command). The resolved value is part of the plan and its `plan_sha256`, not of `policy_identity.model_visible_sha256`: the Pi core reproduced the Native core's requests byte for byte (the goldens in `tests/data/agent_core/golden/` and the offline replays of the sealed acceptance runs), so a sealed Native plan and a Pi plan of the same RunSpec let the model see the same thing.

A persisted plan resolved for the Native core, one that records `agent_core: native` or one resolved before the field existed, does not start: building its session, preflight, `ora play` and the Pi terminal UI refuse it and say to resolve the RunSpec again. Its sealed evidence is unchanged and still evaluates, seals, opens in the viewer and replays with `ora watch` and `ora replay`, none of which selects a core. The names that carry "native" in that evidence stay: the policy kind `native_api`, the run's `native/` directory, `NATIVE_SLOTS_*`, the `ora.native-*` schemas and the `native-policy-` session ids.

Preflight's `agent_core` check, and `ora doctor`, verify that the Pi core can start: a Node.js >= 22.19 binary (`ORA_NODE`, else `node` on `PATH`, `~/.local/bin/node`, then nvm installs, newest first) and every runtime package of `ora-pi/package-lock.json` installed at its locked version. A failed check names the Node install command and `npm ci` in `ora-pi`; a session that cannot start the Pi core refuses with the same message.

### Provider recovery

`policy.provider_recovery` decides how a failed model call is retried with the same credential. The Pi core applies it (`ora-pi/src/provider/recovery.ts`, `transport.ts`); the Python providers (`ora/agent/providers/recovery.py`, used by launch preflight's one-shot probes) implement the same decisions, and the agent-core parity tests compare the two.

| Value | Bound on one logical call | Status |
|---|---|---|
| `first_token_deadline_v1` | No limit on the call's total duration. Each attempt must deliver its first stream event (for `stream: false`, its whole body) within `provider_recovery_window_s` (180 s when absent), counted from the attempt's start through connection and headers; after that event, no SSE line may be more than 180 s (`STALL_S`) apart. Either limit ends the attempt as a retryable transport failure. A stall follows content, so it is a stream break and spends the `STREAM_BREAK_RETRIES` (3) budget; first-token timeouts spend a budget of their own of the same size. An attempt that fails before its first event, for any retryable reason (HTTP 429/5xx, a transient connection or TLS fault, a first-token timeout), opens the no-content window, the same `provider_recovery_window_s`, when none is open; the window opens when that failure is seen. A failure before content is retried with the same backoff when its attempt started before the window's end, and raises its error when its attempt started at or after it. The decision goes by the attempt's start because the first-token limit equals the window: judged by when it is seen, a first-token timeout inside an open window would always end the call, losing the retry that recovers a request a restarting server dropped. Neither the backoff nor a Retry-After is shortened to fit the window, so the last attempt can start up to one backoff step, or one Retry-After, after the window's end. An attempt that delivers content (a reply, or a stream that broke after content) closes the window, and the next failure before content opens a new one. So an endpoint that refuses connections or answers 5xx is given up on about one window after its first failure; one that accepts and never answers after two retries, before the first-token budget is spent (that budget ends a call only when content separates its timeouts); and a restart of the endpoint shorter than the window, whether requests fail fast or hang during it, always gets an attempt after it ends. None is retried for the run's whole wall-time budget. Other failures keep the deadline policy's rules: HTTP 401/402/403 and certificate failures are terminal, 429 and 5xx back off (Retry-After, else 1, 2, 4 ... 20 s), a credential echo fails closed. Only the run's remaining wall-time budget bounds the call as a whole, ending it with `ProviderRecoveryDeadline` | The default: every maintained baseline names it (`native_full_explicit_motion_direct_v4` and `robodojo_toolkit_v6`, all moved from `same_credential_deadline_v1`), RoboDojo plans without a baseline inherit it, and a provider profile's `recovery_window_s` selects it |
| `same_credential_deadline_v1` | One fixed deadline, `provider_recovery_window_s` (180 s when absent), bounds the whole call: every attempt, its full streamed body and every backoff; when it passes the call ends with `ProviderRecoveryDeadline`, which is not retried | Kept so that plans that name it still resolve, replay and verify; no maintained baseline names it since the two legacy-visible toolkit baselines that did were retired on 2026-10-31 |
| `legacy_v1` | `max_retries` retries without a deadline; takes no window | A RunSpec that names neither |

`provider_recovery_window_s` (30..1800 s) is accepted with either deadline policy and refused with `legacy_v1`. A Python attempt runs in a transport subprocess; under `first_token_deadline_v1` the parent also enforces both limits, 10 s later than the worker, and kills and reaps a worker that misses them (one stuck in a DNS resolver thread). Each failed attempt is a `provider_attempt_failed` event (`ora.provider-attempt-failure.v1`); under either deadline policy it carries `recovery_policy` and `stream_break_retry_limit`, and transport-phase records the running `stream_breaks`. Under `first_token_deadline_v1` transport-phase records add the running `first_token_timeouts` and `no_content_failures` (attempts of the call that failed before their first event) with `no_content_window_s`, the no-content window; a failure before content adds `no_content_elapsed_s`, how long its window had been open when the failed attempt started (0.0 for an attempt that started before the window opened, such as the one whose failure opens it; the window allows a retry only while it is below `no_content_window_s`), and an attempt a limit ended adds `attempt_timeout` (`first_token` or `stall`) and `attempt_timeout_s`, the limit's length; `partial_event_count` says how many events had arrived. Records written before the no-content window carry `no_content_retry_limit` (8, a count of retries) in place of `no_content_window_s` and `no_content_elapsed_s`; they still verify and replay.

### Context compactor

The rollout compactor writes the lossless retention's episode and emergency checkpoints, one tool-free call each ([context assembly](../context-assembly.md#history-and-artifacts)). By default it calls the policy's own provider and model. The optional `policy.context_compactor` binds it to another Provider profile, for example a small local policy model with its checkpoints written by an API model:

```yaml
policy:
  context_compactor:
    provider_profile: deepseek_flash_openai_low_16k_v1
    provider_catalog: path/to/catalog.yaml   # optional
```

The field is opt-in and no default or builder sets it. A RunSpec without it resolves exactly as before, and its plan has no `context_compactor` key.

`ora/orchestrator/spec/context_compactor.py` resolves the profile as `provider_policy` resolves a policy's: built-in profiles first, then the user's `providers.yaml`, or only the named catalog (a project-relative or absolute path). The plan records, under the same key, the binding `ora.context-compactor-binding.v1`:

| Fields | Content |
|---|---|
| `provider_profile`, `profile_source`, `provider_catalog` | The profile id; `builtin`, `user` or `catalog`; the catalog's `{path, sha256}` when one is named |
| `provider`, `model`, `endpoint`, `endpoint_transport`, `reasoning_effort`, `stream`, `supports_images`, `context_window_tokens`, `max_output_tokens` | The profile's wire fields |
| `base_url_env`, `api_key_env`, `proxy_env` | The profile's variable names; never a value |
| `provider_recovery`, `provider_recovery_window_s` | A profile's recovery window selects `first_token_deadline_v1` ([provider recovery](#provider-recovery)); otherwise the policy's `provider_recovery` |
| `auth_scheme`, `output_token_parameter`, `reasoning_field`, `request_options`, `provider_session_affinity` | Only when the profile has them |

The binding passes the same protocol and endpoint checks as a policy. It is part of `plan_sha256` and of `policy_identity.model_visible_sha256`, because the checkpoint text it writes is what the policy model reads; the catalog's path and hash are masked there like every pin. Resolution refuses any other key under the field, and refuses a binding:

- unless the policy is `native_api` with `context_retention: action_step_lossless_v4` and `summary_policy: rollout_checkpoint_v1`, the only retention with a compactor;
- in a free-play plan, where Pi compacts its own context;
- for a text-only profile, since compactor packets carry images;
- for `max_output_tokens` below 8,192, the compactor's fixed output ceiling;
- for a context window smaller than the policy's;
- for a gateway-bound profile (`gateway_client_binding: true`).

The Pi agent core runs a bound compactor (`ora/agent/providers/compactor_binding.py`, `ora/agent/pi/factory.py`, `ora-pi/src/core/context_wiring.ts`). Its calls use the binding's model, reasoning effort and stream, and a provider built from the binding: its own HTTP client, no standby keys, no retries and, when the profile has session affinity, its own `X-Session-ID` `ora:<run_id>:compactor`. What the compactor sees does not change: the same packets, the same private-evidence scan and the same 8,192-token output ceiling. The binding's provider supplies the checkpoint request options, as the policy's provider does for an unbound compactor (`reasoning_effort: low` and `response_format: json_object` for the DeepSeek models). A scripted or replayed session keeps its scripted compactor. Every `context_compactor_*` event of a bound compactor records `provider_binding` ([episode evidence](#episode-evidence)). Launch preflight probes the bound endpoint as `compactor_provider_marker` ([runtime lifecycle](runtime-lifecycle.md#admit-and-launch)).

Supported runtime kinds are `synthetic`, `robotwin`, `libero_pro` and `robodojo`. LIBERO and LIBERO-Pro both use `libero_pro` runtime kind with different source-paired task catalogs. Runtime inputs identify transport, simulator interpreter/source, task/seed/layout, renderer, control/motion profiles and execution resource as applicable.

Budgets bound robot decisions, model calls, simulation steps and wall time. Backend-specific limits may further restrict actions or physics steps. A model context window must leave a safe input margin after its output budget. Persistent rounds are explicit session inputs; they are not implied by task identity.

## Fresh benchmark specifications

Use the maintained builders rather than copying a resolved plan:

```bash
./scripts/ora spec robotwin tasks
./scripts/ora spec libero tasks
./scripts/ora spec robodojo --help
./scripts/ora spec robocasa --help
./scripts/ora spec robolab compose --help
```

The builders take task/provider/host/resource inputs, exact qualification and service reports where required, and an explicit execution namespace/cohort. The default resolver selects `native_full_explicit_motion_direct_v4`, except on RoboDojo, whose default is `robodojo_toolkit_v6` (`robodojo_placement_toolkit_v5`; a RunSpec naming no baseline also gets the surface's required `check_task` grant). A deliberate custom surface is recorded as such; registration alone does not make it the default.

The [environment pages](../support.md) show one-cell recipes. `ora baselines --explain` previews the selected baseline and tool bindings. `ora plan show RUN_SPEC` shows the source, hashes, placement and budgets before launch.

### RoboTwin and LIBERO cells

`ora spec robotwin` and `ora spec libero` (`ora spec libero_pro` is the same;
`ora/orchestrator/spec/benchmark.py`) compose one RoboTwin cell, or one
original LIBERO base suite or LIBERO-Pro core suite cell, on the default
baseline. RoboDojo cells use `ora spec robodojo` (`ora/orchestrator/backends/robodojo/spec.py`).

- `ora spec robotwin tasks` prints the RoboTwin roster with each public
  instruction and seed-named flag; `ora spec libero tasks` each base and core
  suite's ten task languages.
- `ora spec robotwin spec|compose TASK --seed N [--task-config demo_randomized|demo_clean]
  --qualification MANIFEST [--episode-names FILE]` and `ora spec libero spec|compose
  SUITE TASK_ID --seed N` (a base suite such as `libero_goal` composes an
  original LIBERO cell, a core suite such as `libero_10_swap` a LIBERO-Pro
  cell), each with `--experiment NS --resource-profile SLOT
  --service-report REPORT --cohort ID [--host HOST] [--provider-profile ID]
  [--provider-catalog PATH] [--config ID] [--milestone M] [--output PATH]`.
  `spec` prints the RunSpec; `compose` writes `outputs/NS/specs/<config>.json`
  (or `--output`), imports the RoboTwin seed manifest under
  `outputs/NS/qualification/seed-manifests/` and prints the spec path. A seed
  the manifest does not admit, a slot the host does not register for the
  backend, or a stale or re-pointed readiness report is refused before anything
  is written. It starts no simulator, service or Provider and grants no launch.

### Official limit and the extended budget

A benchmark's official limit ends its published protocol, but a slow success is
worth seeing. `runtime.official_limit_multiple` lets the episode run on to that
multiple of the limit. Fresh RoboDojo, RoboLab and RoboCasa365 specs default to
1000 (`TOOL_BUDGET_LIMIT_MULTIPLE`, `ora/contracts/official_limit.py`; RoboLab and
RoboCasa365 since 2026-11-05, when the user dropped the simulation-time limit;
`DEFAULT_OFFICIAL_LIMIT_MULTIPLE` = 3 remains for a backend that sets none): the shortest
RoboDojo task gets 200,000 native actions, past six hours of wall time, so
`max_robot_decisions`, `max_model_calls` and `wall_time_s` end the run and no
native-action count does. A success past the official limit is
`success_after_official_limit`. The range is 1 to 1000, and 1 stops at the official
limit. An absent field also means 1, so sealed plans and every qualification
keep their stop. On RoboDojo the budget follows:
`build_spec(official_limit_multiple=...)` and
`ora spec robodojo --official-limit-multiple` set both the field and
`budgets.max_sim_steps` (`physics_step_ceiling(task, multiple)`); a lower
explicit `max_sim_steps` still binds first. `max_robot_decisions`,
`max_model_calls` and `wall_time_s` are unchanged.

| Backend | Official limit | What the multiple extends |
| --- | --- | --- |
| RoboDojo | `step_lim` in native actions (`NATIVE_ACTION_LIMITS`, 200 to 1900) | the wrapper's `native_action_ceiling`; `step_lim` itself stays official |
| RoboCasa365 | the evaluation horizon (450 to 4350 control steps, `dataset_registry.py`) | the session's stop (`extended_horizon`); the environment is built with `ignore_done=True` |
| RoboLab | `episode_length_s` at 15 Hz (750 or 900 actions) | the Isaac Lab episode length; the official limit stays the roster's |
| RoboTwin | `step_lim` (400 to 1700), counted per sparse decision | nothing: 440 recorded runs used at most 141, so the limit does not bind under `max_robot_decisions: 80`, and the verdict is unchanged |
| LIBERO, LIBERO-Pro | none: `max_sim_steps` is a Harness budget | nothing |
| YAM | a Harness tick limit, no benchmark | nothing |

The two results never share a field. `official_success` keeps its meaning, a
success inside the official limit, so it compares with every earlier run; no
report adds a later success to it. Under a multiple above 1 the verdict, the
run or round record and `evaluation.json` add:

| Field | Meaning |
| --- | --- |
| `official_native_action_limit` | The official limit, in the backend's native actions (RoboCasa control steps) |
| `official_limit_multiple`, `extended_native_action_limit` | The plan's multiple and the stop it gives |
| `official_limit_snapshot` | `{official_success: false, native_actions: <limit>}` when the limit passed without a success; null otherwise |
| `success_after_official_limit` | True when the official predicate first held past the limit |
| `success_after_official_limit_native_actions` | The native actions used at that success; null otherwise |

The official predicate is the same on both sides of the limit and its evaluator
is unchanged; only the field it lands in differs. RoboDojo's final check belongs
to the official limit, so a task that registered one ends there whatever the
plan allows; no shipped task does. A late success stops the episode, as an
official success does. A multi-round session counts them in
`round_success_after_official_limit_count`, beside `round_success_count`.

The ending says what ended the round. On RoboDojo a simulator ending past the
limit is `round_end_basis: official_terminal_transition` with
`upstream_ending_condition` `success_after_official_limit`,
`official_failure_latch`, or `native_action_limit` at the extended limit only. A
Harness budget or `finish_episode` that stops the extension is
`harness_budget_exhausted` or `policy_finished_episode`, and leaves the result
settled at the limit in place with `verdict_basis:
official_result_at_official_limit`. Runs sealed before 2026-11-02 keep their
records, where any false ending past the limit reads `native_action_limit`. A
fault after the limit keeps its null verdict. `run_report` names the ending
`success_after_official_limit`, keeps `official_success` false and counts these
in `successes_after_official_limit`, outside `successes` and `success_rate`.

The policy sees neither the limit nor the snapshot. `native_actions_limit` and
`native_actions_remaining` count down to the real stop, and RoboCasa's
`control_steps_budget` and RoboLab's `native_action_limit` report that stop too.
At `TOOL_BUDGET_LIMIT_MULTIPLE` no native-action count is a stop, so RoboDojo's
`remaining_budget` leaves both fields out, as RoboTwin's does, unless an explicit
`max_sim_steps` binds below it. Nothing happens to the episode at the official
limit. A longer allowance can
change behaviour inside the official limit, so a comparison of rates still
states the multiple each side ran at.

### RoboDojo motion profile

A RoboDojo plan binds its ARX X5 motion profile as `runtime.motion_profile`, the
`{path, sha256}` of `ora/policy/embodiments/arx-x5/motion-profile-v<N>.json`
(`ora.arx-x5-motion-profile.v1`; the RunSpec names the path, the plan hashes the
bytes, `policy.lock` lists the file, and `benchmarks/robodojo.validate_motion_profile_binding`
refuses any other path or shape, for benchmark and free-play runtimes alike). The
profile holds what the toolkit runs at `fast` (`fast.pace`, `fast.settle`) and its
three jaw forms (`gripper.close`, `gripper.release` for an open that releases a
held close, `gripper.open`). The episode's toolkit host verifies the file against
the plan's hash and puts it in every tool call's configuration as `motion.profile`
(`ora.motion-profile-context.v1`, `agent/toolkit/host/motion.profile_entry`);
`normal`, `careful`, `speed_m_s` and the recovery retrace never read it. The
default baselines `robodojo_toolkit_v7` and `robodojo_toolkit_v6` bind profile v2
since 2026-11-15. A plan without the field, which is every plan sealed before then,
runs the toolkit's built-in values, which `motion-profile-v1.json` states: such a
run's tool calls build exactly the requests, kept-plan keys and actions they
recorded, so its toolkit replay and its control replay are unchanged. The tool
help states profile v2's numbers. The RoboDojo motion gate plans with the profile
the runtime binds, else the default baseline's, and records it
(`session.motion_profile`). See [motion speed](motion-speed.md#robodojo-motion-profiles).

## Play episodes

`episode_mode` is `benchmark` (the default) or `play`, and only a play plan carries the key, so a benchmark plan's bytes and `plan_sha256` are those of a RunSpec that never named it. A play RunSpec (`ora play`, experiment `ora-play`) loads one benchmark task's scene and initial state and nothing else of the task. `ora/orchestrator/play/scenes.py` owns the scenes and the backend bindings; `spec/play.py` resolves the `play` block (`ora.play-session.v1`):

- `play.scene` is the current scene and `play.scenes` the `/reset` candidates, each with `environment`, `task`, `seed` and `qualification` (the record that admits it), plus `scene_id`, `label` and the LIBERO suite and task id or the RoboTwin scene configuration. A backend resets in place only to another seed or initial state of the same task and configuration. The task id serves the backend and the evidence; it is not policy input, and `task_instruction` is the session framing, never the scene's task language. The reviewed completion contract is withheld.
- `bind_play_runtime` binds the plan runtime to the backend's play binding, which loads the scene with its official predicate withheld: LIBERO `environment_driver: direct_libero_play_v1` (the BDDL `_check_success` reads false on the one problem instance, so no step reports success, reward or a success termination) and RoboTwin `environment_signal_policy: play_scene_v1` (the task's `check_success` reads false, so `eval_success` never latches). Nothing a task would count as success ends or changes a scene; it ends at the operator's reset or quit, a budget or the environment's own step limit, and its verdict is `official_success: null`. A benchmark runtime cannot select either binding, and the baseline template refuses a RunSpec that names one itself.
- Scenes come only from this host's records: the LIBERO-Goal and LIBERO-Object init-state files of the pinned LIBERO-Pro checkout (`LIBERO_PRO_ROOT`, bound by sha256, 50 states each), and RoboTwin seeds a local seed manifest admits for the task, its scene configuration and the pinned revision (`backends.robotwin.seed_manifest.seed_admission`; the plan binds that manifest, so preflight re-admits the seed). Synthetic is the built-in world. RoboDojo offers only ORA's own scene sets (below); its official tasks are not play scenes, because their success and failure latch inside the benchmark worker bridge.
- RoboTwin also offers ORA's own scene sets, only when a play session names one (`scene_set`; `ora play --scene-set`, or `--scene` naming one of its scenes). A set's scenes (registry `ora/environment/benchmarks/robotwin_play_scenes.py`, worker-only modules in `ora/environment/backends/robotwin/play_scenes/`; `tabletop-v1`: `table_setting_sequence`, `shell_game_memory_3`, `obstacle_rearrangement_6`; `household-v1`: `breakfast`, `kitchen`; all on `demo_clean`) are `Base_Task` subclasses that place RoboTwin library assets and `create_box` primitives on the standard table, with no predicate (`check_success` is false), expert or instruction. A scene record carries `scene_set`, and `bind_play_runtime` binds `runtime.play_scene_set` beside `play_scene_v1`; the worker then builds the scene from ORA's module instead of `envs.{task}`, and the session's source manifest binds the set's ORA sources (`play_scene_source`) instead of `envs/{task}.py`. No other plan may carry `runtime.play_scene_set`, and a play session's scenes share one scene configuration and scene set. The RoboTwin checkout and its pinned revision are unchanged. A set's source binding covers `play_scenes/base.py` and its own module only, so an edit to one set's module leaves the other set's audits admissible. `household-v1` (`play_scenes/household_v1.py`) builds each object itself (`HouseholdScene.place`), from the library model's collision and visual files at its calibrated scale (or the mesh's own units, as `create_actor`), or at an explicit scale for an asset without one (`EXPLICIT_SCALES`: jam jar, steamer, tomato, onion, cutting board), with a household mass (`mass_kg`, 0.12 to 0.60 kg), the inertia PhysX computes for the shape at 1000 kg/m³ scaled by the same mass ratio (RoboTwin's `Actor` sets 10 g and keeps that inertia), and static/dynamic friction 0.6/0.5 on its collision shapes (`OBJECT_FRICTION`; RoboTwin's library shapes have 0.3/0.3); no limit or material of the robot, the table or the scene changes. Its scenes frame the operator camera on their own (`HouseholdScene.operator_view`, merged over `base.OPERATOR_VIEW`). A scene-set scene also has ORA's operator camera (`play_scenes/base.py`: `operator_camera`, 1280 × 720, 44° vertical field, from the operator's side of the table at three quarters, ray-traced at 8 samples per pixel while RoboTwin's cameras keep 32), added to the SAPIEN scene after the objects and never to RoboTwin's camera manager, so no observation, point cloud or policy camera includes it; the operator view answers with it, and the execution capture records it beside `head_camera` and `front_camera` (`PlaySceneTask.capture_images` renders only those three, not RoboTwin's `update_picture`, which renders the wrist cameras too), rendering a frame only once the capture writer holds at most one queued frame (`backends/robotwin/execution_capture.py`, `PLAY_CAPTURE_WRITER_BACKLOG`; without the wait the writer, which shares the interpreter with the simulation, dropped 104 of 253 frames). `build_play_spec` sets a scene-set plan's `runtime.execution_capture` to `{enabled: true, fps: 30}` (`robotwin_play_scenes.PLAY_EXECUTION_CAPTURE`). After each action of a scene-set scene (each skill stage included) the session settles the scene (`backends/robotwin/play_settle.py`, entered from the execution-capture `execute` wrapper after the receipt is written and before the action's `action_end` capture frame): it steps the scene on with every drive target held, writing none, 10 physics steps at a time until over one window no dynamic object moved more than 1 mm or turned more than 1° (poses, not velocities), at most 500 steps (2.0 s), ignoring objects more than 0.44 m below the table top (fallen to the floor), and appends one line to `native/post-action-settle.jsonl` (`ora.robotwin-play-post-action-settle.v1`: `action_id` (a stage's parent action; the stage's own id as `dispatched_action_id`), `action`, `action_sequence`, `round_index`, `physics_step_start` on the capture's `sim_step` clock (physics steps since the round's reset), `physics_steps`, `physics_dt_s`, `simulated_s`, `windows`, `ended` `at_rest` or `step_limit`, `moving_at_start`, the first and last window's largest displacement and turn and the object that moved most, `off_table`, `bounds` and `wall_s`). The settle changes no receipt or observation, does not run the official-success latch (a play scene has none) and does not count toward `take_action_cnt`; a benchmark task and play on an official task never settle.
- A scene of a set is admitted by a play-scene audit (`robotwin_play_scene_audit`, `ora.robotwin-play-scene-audit.v1`), written by `./scripts/ora play audit --env robotwin` to `outputs/ora-play/qualification/play-scene-audits/<UTC>-<ns>/<set>/<scene>.json` with the seed evidence and camera images beside it. For each seed the RoboTwin worker builds the scene as the session does and records that it builds, settles (no object moves more than 2 mm or turns more than 2° over 250 further physics steps, and none ends faster than 0.01 m/s or 0.1 rad/s), rests on the table (lowest point within 4 mm of the table top), lies inside the arms' workspace (`robotwin_play_scenes.PLAY_WORKSPACE_XY_M`, the envelope of the official tasks' object placements), interpenetrates nothing deeper than 2 mm and touches no robot link, and renders every camera (head, front, both wrists, observer and both world cameras, and ORA's operator camera); each object row records its `mass_kg`; the thresholds are recorded as `criteria`. The record binds the pinned revision (the checkout must be clean at it), the scene configuration, the ORA commit and the set's source binding (`scene_source`: the sha256 of the committed scene modules); `audit_sha256` covers the rest. `play_scene_admission` admits a seed when the record is intact, names this set, scene, configuration and revision, its `scene_source` equals the current one, and the seed is accepted with passed evidence: `play_scenes` lists by it and preflight re-admits every scene seed by it, so a changed scene module needs a new audit. No expert runs, no robot action is published and `verdict` is null.

- RoboDojo's scenes are ORA's own scene sets only (registry `ora/environment/benchmarks/robodojo_play_scenes.py`; `isaac-tabletop-v1`, the default: `breakfast` on `cpu` physics, `desk`, `toys`, `kitchen_tidy` and `blocks` on `cuda:0`). A scene is a RoboDojo task-config YAML of library objects (`ora/environment/backends/robodojo/play_scenes/scenes/<set>/<scene>.yml`), listed with its label, `physics_device`, `settle_steps`, `assets` and operator description in the set's `index.yml` (data the registry code reads; `robodojo_play_scenes.scene_definitions`), that `ora/environment/benchmarks/robodojo_play_layouts.py` turns, with a seed and the pinned assets, into a saved layout in RoboDojo's `Eval_Layout` format. The RunSpec runtime is the default RoboDojo instance's with the scene as `task_variant`, `eval_seed` 0, `native_action_limit` 5000 (the play step limit; `max_sim_steps` 50000), the scene's `physics_device`, `expected_task_language` set to a fixed no-task text the worker reads back (the policy never receives it), `play_scene_set` and `execution_capture` `{enabled: true, fps: 30}` (`robodojo_play_scenes.PLAY_EXECUTION_CAPTURE`; benchmark plans keep the default 10 fps); `definitions.validate_runtime_extension` validates it with `robodojo_play_scenes.validate_play_runtime` instead of the official-task rule, and it carries no `layout_*` field and no `infrastructure_qualification`. `bind_play_runtime` binds `play_scene_set`, `physics_device`, `play_layouts` (`{seed: sha256}` of the generated layout of every scene in `play.scenes`), `play_kernel_sha256` (the audited play kernel) and `play_scene_source_sha256` (the scene's audited source binding, carried by every drawn seed's qualification); no benchmark plan may carry them, and a play session's scenes are audited seeds of one scene, of one play kernel and one source binding. `episode_bindings` binds no infrastructure qualification for such a plan. The coordinator builds the environment through `ora/environment/robodojo_play_factory.py`, whose worker config is the benchmark RoboDojo config with `backend: robodojo_play`, `eval_seed: 0` and the scene seed as `play_seed`; `factory.py` and the benchmark route are unchanged. The play config also carries the shared motion planner's interpreter and source, resolved from `runtime.motion_planner`'s `python_env` and `root_env` as `factory.build_robodojo_worker_session` resolves them, and the session binds the shared motion base at start as the benchmark session does (`shared_motion.start`): the timing worker preflight bound (`motion_timing.bind_preflight`), the planner on the worker's card and its FK parity check (`native/motion-planner/binding.json`). A planner that does not start, or whose forward kinematics differs from the simulator's, refuses the start (`robodojo_start_failed`, `native/session-start-error.txt`); a plan whose runtime names no `motion_planner` (a RunSpec that bypasses the baseline instance) starts without one, and every planner request then answers `no_planner` (the move takes its joint route, a reach row is unreachable with that reason). So a free move's route is the planner's (`global`), a contact path is checked against the observed world (`contact_path_blocked`), the reach annotations of `move_pose`, `move_to`, `rotate_wrist`, `rotate_pitch` and `grasp_plan` answer from the planner's IK, and moves are timed by the timing worker, as in a benchmark episode. A relaunch drops the last observation's world, since the new simulation's physics clock starts again, and `close` closes the planner process before the worker ends. The worker (`play_scenes/session.py`, a `RobodojoSession` subclass) regenerates each bound layout and refuses one whose sha256 differs, runs a reward-free `TaskEnv` lifecycle (`play_scenes/lifecycle.py`: no official terminal, `persist_official_verdict` refused; every non-kinematic rigid body of the scene's objects, rigid objects and articulation links but never the robot or the table, gets `PLAY_RIGID_BODY_LIMITS` when it spawns, at the simulation's setup and after every later spawn: PhysX `maxDepenetrationVelocity` 0.5 m/s, `maxLinearVelocity` 3.0 m/s (above the arm's fastest free-space motion, so a carried object keeps up with the jaws) and `maxAngularVelocity` 720 deg/s instead of PhysX's 3 m/s, unlimited and 5729.58 deg/s, so an object the arm sweeps or the jaws squeeze tips over or slides away rather than being launched; the reset's scene registration records them as `rigid_body_limits` (`ora.robodojo-play-rigid-body-limits.v1`: the three limits, the number of bodies and the stage's metres per unit). The limits are part of the play kernel, so every play-scene audit binds them; benchmark tasks and the worker kernel do not carry them), settles the scene after every action (physics otherwise advances only inside an action, so an object the arm knocked over stood frozen in mid-fall until the next one): once the action's receipt is written, `play_scenes/session.py` steps the scene on with every robot target held (`lifecycle.hold_steps`: no control, no native action, each step offered to the execution capture as a control step) until the scene is at rest, at most 500 physics steps (2.0 s of simulated time; `PLAY_POST_ACTION_SETTLE`): at once when no rigid object of the scene moves faster than 0.01 m/s or turns faster than 0.1 rad/s and no articulation joint moves faster than 0.1 rad/s (or m/s) (`lifecycle.object_motion`), otherwise after the first window of 10 physics steps over which no object moved more than 1 mm or turned more than 1° and no joint moved more than 0.005 (`lifecycle.object_poses`, `lifecycle.displaced`; a velocity can jitter at rest, a pose does not), inside the action's execution-capture window (the settle's frames are `control` frames with the action's id, after the record's `physics_step_start`, and the action's `action_end` frame follows the settle); it does not run between the children of an action sequence, changes no receipt and does not count toward `sim_steps` (the session's `max_sim_steps` budget), and each settle is a line of `native/post-action-settle.jsonl` (`ora.robodojo-play-post-action-settle.v1`: `action_id`, `action_sequence`, `round_index`, `physics_step_start` since the round's reset, `physics_steps`, `simulated_s`, `ended` `at_rest` or `step_limit`, `windows`, the objects moving at its start (by speed) and at its end (by the last window), the fastest speeds at its start and its wall time); the reset's scene registration records the bounds as `post_action_settle` (`ora.robodojo-play-post-action-settle-config.v1`), changes scene only by relaunching the simulation on another bound seed, and writes `source-manifest.json` with schema `ora.robodojo-play-source-manifest.v2` (the scene's source binding, which must equal `runtime.play_scene_source_sha256` or the worker refuses to start, the generated layouts and every asset metadata file they read, instead of a task YAML and official layout), `reset.json` with `ora.robodojo-play-reset.v1` and `round-resets/round-NNNN.json` with `ora.robodojo-play-round-reset.v1` (relaunch evidence and an isolation proof: the seed's layout loaded, the robot home, no carried channel, no reward or terminal). It ends without an in-process Kit teardown.
- The play kernel (`ora/orchestrator/robodojo_play_kernel.py`, `ora.robodojo-play-kernel.v2`) is the RoboDojo play worker's code identity: the benchmark worker kernel (`robodojo_kernel.WORKER_KERNEL_PATHS`, as manifested at the commit) followed by `PLAY_KERNEL_PATHS` (the play modules, the registry's code, the layout generator and the play factory; Python only, no scene data), hashed by content at one commit with `worker_kernel_sha256`'s length-prefixed scheme. A scene's data is bound per scene instead: `robodojo_play_scenes.source_binding(set, scene)` (`ora.robodojo-play-scene-source.v2`) is `{entry, entry_sha256, files, sha256}` of the scene's canonical index entry (every field but the operator's `description`, plus the set and the YAML path), its YAML and the generator, and no other scene's entry or YAML. That is everything the worker reads of a scene: the entry's name, set, physics device, YAML path and settle steps, and the YAML (each seed's layout and the object blocks of upstream's task config); `tests/ora/environment/backends/robodojo/test_robodojo_play_worker.py` records the files those worker steps open and shows that another scene's YAML or entry changes nothing they use. The benchmark manifest does not include any play module: `worker.py` reaches `play_scenes/session.py` only on its `robodojo_play` branch, a pruned dispatch edge (`robodojo_kernel_review.DISPATCH_PRUNED_EDGES`), and `play_kernel_gaps` holds `PLAY_KERNEL_PATHS` to the play worker's import closure as `kernel_manifest_gaps` holds the benchmark manifest to the benchmark worker's.
- A RoboDojo scene is admitted by a play-scene audit (`robodojo_play_scene_audit`, `ora.robodojo-play-scene-audit.v2`), written by `./scripts/ora play audit --env robodojo` to `outputs/ora-play/qualification/play-scene-audits/<UTC>-<ns>/<set>/<scene>.json` with the worker reports (`ora.robodojo-play-scene-audit-worker.v1`, one per pass), generated layouts and camera images beside it. Each pass is one worker process that starts the play session and reaches every further seed through `restart_round`; a `cuda:0` scene adds a `contacts` pass on `cpu` physics (`audit_physics_device`, a worker-config field no plan can carry), where PhysX reports contacts. Per seed (`ora.robodojo-play-scene-audit-seed.v1`) it checks the settle (every object present, on the table, within 3 cm and 20° of its generated pose), a 500-step stability window (at most 5 mm, 1 cm peak, 2°, joint drift 0.02), contacts (no penetration deeper than 3 mm, no robot contact), the reach envelope (`PLAY_REACH_ENVELOPE_XY_M`, nearer base within 0.613 m; an object whose `relative_plane` names another object is checked through its table-level support), IK reachability of every movable object from at least one declared approach side (`APPROACH_SIDES`: top, left, right, front, back) for either arm, through the session's read-only `align_plan`, asked after the seed's observation so an IK that answers in the observed world reads this seed's, with align_plan's own pose synthesis (link6, the `arx_x5_link6` controlled frame, against the side's direction from the object's near face, its settled box top for `top` and otherwise the footprint's nearest extent at the box centre's height, at the standoff that stops the open fingertips 0.1576 m along link6 2 cm short of that face, 0.1776 m, within align_plan's permitted `standoff_m`; jaw rolls every 30° over the full turn; `ik_fingertip_clearance_m`, `ik_roll_step_deg`; align_plan's default 0.10 m from the centre puts the fingertips below the table for a top approach to any object under about 10 cm, where the planner refuses every pose), recorded per side as the face, link6 position, reachable arms, reachable rolls and counted rejection reasons (`ik.sides`, `ik.reachable_sides`, `ik.standoff_m`); an object no side reaches passes when a tilted approach plans (`ik.rule` `any_declared_side_else_tilted_plan`): the top and right approaches (`tilt_sides`), both from the arms' side of the table, turned about their fingertips by 10°, then 20°, then 30° (`tilt_approach_deg`), the top approach leaning toward +y (`tilt_top_azimuths_deg`) and the right approach tilting down, at jaw roll 0° (`tilt_rolls_deg`), each planned by the shared planner from the measured joints with nothing moved (`shared_motion.plan_path`), the arm with the nearer base first, until one plans; `ik.tilted` records what planned (`arm`, `side`, `tilt_deg`, `azimuth_deg`, `roll_deg`, `approach`, `link6_pose_xyzw`), `plans_tried`, the refusals and `seconds`, 3 cm between every table-level object's settled footprint and each gripper's measured start footprint, and three rendered cameras; the thresholds are recorded as `criteria`. The record binds the pinned RoboDojo revisions (`robodojo_revisions`; the checkouts must be clean at them), the scene's physics device, the scene's own `scene_source` binding, the `play_kernel` (`{commit, sha256, play_paths}`; the kernel and the audited scenes' index and YAMLs must be committed), each seed's `layout_sha256`, and the audit worker's Vulkan compatibility fingerprint and device binding; `audit_sha256` covers the rest. `robodojo_play_audit.play_scene_admission` admits a seed when the record is intact, names this set, scene, device and the pinned revisions, its play kernel equals the one given (the working tree's when `play_scenes` lists scenes; the plan commit's at launch, which must also equal `runtime.play_kernel_sha256`), its scene source equals the scene's current one (so another scene's edit leaves it admissible, while a play-code or generator edit retires every scene), and the seed is accepted with passed evidence and, when given, the bound layout. Launch preflight (`backends.robodojo.play_preflight`) replaces the infrastructure-qualification checks with this admission (`seed_qualification`, which also requires `runtime.play_scene_source_sha256` to equal the scene's binding at the plan's clean commit) plus the host marker, the shared motion base's `motion_timing_worker` and `shared_motion_planner_environment` checks (`preflight.shared_motion_checks`, as for a benchmark plan), roots, clean pinned checkouts, the package stack, the regenerated layouts, the GPU's free memory and the driver with the audit's Vulkan compatibility fingerprint. The audit's worker runtime is the one a play plan of the scene resolves to (`robodojo_play_audit.audit_runtime`), so its session starts the shared motion planner as a play session does: the audit needs `CUROBO_V2_PYTHON` and `CUROBO_V2_ROOT`, and a planner that does not start fails the pass. No robot action is published, no model is called and `verdict` is null.

`list_play_scenes(environment, task=..., task_config=..., scene_set=...)` lists the scenes, `draw_play_scene(environment, rng)` draws a task first and then one of its scenes, so a seeded `random.Random` repeats the draw, and `build_play_spec(environment, host=..., rng=...)` returns the RunSpec with `play.scene`; `ora play` adds the Provider policy, operator control, framing, candidates and instruction budgets. `write_play_spec` writes it under `outputs/ora-play/specs/` and imports a RoboTwin seed manifest or a RoboTwin or RoboDojo play-scene audit by content. Without `scene_set` the RoboTwin listing, draw and plans are those of the official tasks, unchanged; RoboDojo always lists its scene set (`isaac-tabletop-v1` unless named). The defaults are listed in `tests/data/default-expectations.json` (`default_episode_mode`, `play_scene_bindings`).

## Practice initial states and evaluation sets

Practice must not draw from the episodes official evaluation uses.
`ora/environment/benchmarks/evaluation-sets-v1.json` (`ora.evaluation-sets.v1`)
lists, per benchmark, the evaluation set, its practice side and the RunPlan
field that tells them apart; `evaluation_sets.evaluation_set_hit(plan)` returns
why a plan's episode is in an evaluation set, or `None` when it is
practice-eligible, and a practice launcher refuses every plan it names a
reason for. An environment the manifest does not list has no practice side.

A RunSpec selects `partition: practice` (default `evaluation`). Resolution
(`ora/orchestrator/spec/partition.py`) refuses a practice plan that
`evaluation_set_hit` names, a practice plan on an environment without a
practice side and practice in free play; an accepted practice plan records
`partition: {name: practice, evaluation_sets_sha256}`, and every plan records
its partition name. `evaluation.json` repeats it, and the run report keeps
practice apart ([partitions](../evaluation/protocol.md#practice-and-evaluation-partitions)).
A LIBERO-Pro practice plan on the default baseline sets
`runtime.environment_driver: direct_libero_practice_v1` and a seed of at
least 1,000,000; the baseline instance names the official driver
`direct_libero_v1` and admits this one substitute
(`ora/orchestrator/spec/defaults.py`), so the practice episode keeps every other
baseline setting. Any other driver is still a baseline conflict.

ORA records the partition and keeps practice results apart; it does not turn
practice runs into experience. Reviewing a practice run and checking what a
lesson may teach belong to the research code that consumes these runs, not to
ORA.

- **LIBERO-Pro.** Every episode on `direct_libero_v1` (or the play driver)
  loads official initial state `seed % trial_count` (0 to 49) of its task's
  init-state file and is in the evaluation set. A practice episode selects
  `runtime.environment_driver: direct_libero_practice_v1`: the worker seeds
  the task's BDDL region samplers with the episode seed (at least
  `PRACTICE_SEED_FLOOR`, 1000000; RunSpec resolution and a practice round
  refuse a lower one), resets without loading an official state, keeps the
  official predicate, and refuses a sample equal to one of the task's official
  states (atol 1e-6). `reset.json` and every `round-resets/round-NNNN.json`
  record `initial_state`: `{source: practice_sampled, official: false,
  practice_seed, sampled_state_sha256, official_states_compared}`, or
  `{source: official, official: true, init_state_index}`; `reset_state_id` is
  null for practice. The same seed reproduces the same sampled state.
  Robustness cases do not admit practice. Both files also record `egl_device`,
  the Mesa software EGL device a Mesa lane rendered on, or null
  ([software-rendered slots](../environments/libero.md#software-rendered-mesa-slots)).
- **RoboTwin.** The evaluation set is the requested seeds from
  `100000 * (1 + outer_seed)` upward that pass the expert check
  (`benchmarks/robotwin.py evaluation_protocol`, upstream
  `script/eval_policy.py`); a plan whose `seed` is at least 100000 is an
  evaluation episode, and practice uses seeds below it.
- **RoboCasa365.** The target split is the evaluation set (the only split the
  backend constructs); the pretrain split is its practice side.

## Identity and policy assets

`source` binds the exact Git commit and dirty state. Launch requires clean committed source. `policy_identity` records the policy-lock digest and model-visible digest. The plan identity also binds resolved configuration, source/version contracts and content references. Schema strings and asset paths are serialized data, so namespace migration changes hashes even when motion behavior remains the same.

`policy_identity.model_visible_sha256` (`ora/orchestrator/evaluation/model_visible.py`) is one digest of what the plan lets the model see: the advertised tool definitions and result schemas, the text of each prompt layer, the embodiment card and its projected facts, the observation projection, and the `policy`, `runtime` and `budgets` settings with pins masked, plus the latency profile's model-facing switches that are off. It leaves out the episode identity, `source`, qualification, session and launch records, the policy keys that name the agent-loop implementation or restate another setting (`agent_core`, `context_scheme`, `context_stages`), where the run executes (`runtime.resource_binding`, `required_host`, `cuda_visible_devices`, `renderer_gpu`) and what the run records for people: a present `runtime.execution_capture` is compared as the recorder's default config (`{enabled: true, fps: 10, queue_capacity: 32}`), whatever it says, since only people read the frames (`ora web`, the Viewer, `ora replay` video). Every plan resolved with the default capture keeps the digest it was sealed with, and cells recorded at 2 fps or with capture off have the digest of the same cells at 10 fps. A plan sealed before 2026-11-06 with another capture setting (a free-play plan at 30 fps, a `realtime` plan) recorded a digest that included it; recomputed now, its digest is that of the same plan at the default. `scripts/dev/compare-model-visible BASE CANDIDATE` recomputes the same items for two commits across a fixed RunSpec matrix.

`policy.lock` pins tracked policy assets and catalogs in canonical sha256sum format. Run `ora lock` after intentional policy edits and commit the lock together. `ora lock --check` detects drift; `ora lock --strip-pins` maintains path-only assets and compiled embodiment manifests.

Host profiles name environment variables, not credential values or machine-local paths. ORA ships two generic profiles in `ora/orchestrator/hosts/`: `local-cpu` (`ora local`) and `single-gpu` (`ora setup --env` / `ora run --env`: one `libero-gpu0`, `robotwin-gpu0` and `robodojo-gpu0` slot on CUDA device 0; the slots are different resource classes, so one task at a time is that profile's operating rule, not a lease). A profile in `ORA_HOSTS_DIR` with the same id takes precedence. For any other host, copy `examples/hosts/example-host.yaml` to an ignored/local host directory and set `ORA_HOSTS_DIR` and `ORA_EXECUTION_HOST`. Exact simulator slots are registered in that profile. Host/resource placement is distinct from policy-visible composition.

## Filesystem roots

Source checkouts contain tracked code/assets. A runtime workspace holds `.harness-runtime/` leases and the reconstructable run index. By default linked Git worktrees share the primary checkout's runtime workspace. `ORA_WORKSPACE_ROOT` explicitly selects an absolute workspace. `ORA_OUTPUTS_ROOT` selects an existing shared evidence root; `ORA_EXTRA_OUTPUT_ROOTS` supplies additional read-only discovery roots. Use `ora workspace` to inspect actual resolution.

Logical run namespaces are `outputs/<experiment>/`; a run is keyed by its exact run ID. Keep generated artifacts, qualification, seeds, credentials and media outside Git. The Synthetic quickstart writes `outputs/quickstart/runs/<run-id>/` beneath its selected evidence root.

`runtime.execution_capture` resolves for RoboTwin, LIBERO and RoboDojo
always (enabled by default), and for Synthetic only when the RunSpec names it:
`quickstart.synthetic_spec`, and so `ora run --env synthetic` and `ora
interactive`, set `{enabled: true}`, as does the Synthetic scene of free play
(`play_scenes.build_play_spec`). Synthetic
then records a drawn top view of its end effectors (`backend: synthetic`,
`step_unit: synthetic_capture_step`, five 0.1 s steps per action) in
`native/execution-capture/`, Control-only; its policy observation is
unchanged. A RoboDojo free-play plan sets `{enabled: true, fps: 30}`: the capture samples
simulated time on the 4 ms physics grid, so that is one frame every ninth
physics step (27.8 per simulated second), and each frame keeps its own
camera sizes (`images.<camera>.shape`: the policy cameras 640 × 480, the
operator camera 1280 × 720). A RoboTwin plan of one of ORA's scene sets
sets the same rate and records `head_camera`, `front_camera` (320 × 240) and
the scene's `operator_camera` (1280 × 720); other RoboTwin plans record the
head and front cameras at the default 10 fps.

`latency_profile` (top level of the RunSpec, default `sim_benchmark`) decides
whether execution capture is on by default: `realtime` resolves it with
`enabled: false` unless the RunSpec's `runtime.execution_capture` names
`enabled`. Every plan records the resolved switches as `latency_profile`
([latency profile](latency-profile.md)).

`runtime.ground_truth_capture` (RoboDojo only, Boolean, default `false`)
records private simulator ground truth for developer-side analysis. It is
never policy-visible and never inside the run: the worker writes
`<workspace>/ground-truth/<run-id>/` beside `runs/` (it refuses a run
directory that is not `runs/<run-id>` and a non-empty target), so the sealed
run is the run it would have been without capture. `manifest.json`
(`ora.robodojo-ground-truth-capture.v1`) gives frames, units, the row count
and two error counts: `errors` for reads that failed as a whole and
`field_errors` (with `first_field_error`) for single fields recorded as
`{"error": ...}` inside an otherwise good row or round file;
`physics/round-NNNN.json` (`ora.robodojo-ground-truth-physics.v1`),
written after each round reset, lists every layout instance's asset (USD
path, model, scale), its configured physics block and the mass, static and
dynamic friction and restitution read back from the simulator, and the world
pose of static props once;
`ticks.jsonl` (`ora.robodojo-ground-truth-tick.v1`) has one `round_start` row
after each round reset, then one row after every native action (10 physics
steps), with each rigid instance's env-relative pose as the official evaluator
reads it and its linear and angular velocity, and the scene's articulation
joint positions and velocities (not the robot's links), keyed by
`round_index`, `action_sequence`, `action_id`, `segment`, `native_action` and
`physics_clock` (the simulator clock, which includes the reset's steps). Every
read is a getter; a reader failure is recorded in its row and never fails the
episode (`environment/backends/robodojo/ground_truth.py`).
A control replay of such a run keeps capture off and writes its own
`private/object-poses.jsonl`.

## Interactive operator control

A Native RunSpec may declare `operator_control: {mode: file_inbox_v1}`, as
`ora interactive` and `ora local` do; their runs use the `ora-interactive`
namespace. The operator inbox lives outside the run directory, under the
workspace state root (`ora-interactive/<run_id>`), and only that run's
Supervisor consumes it. Each accepted message is appended to the run's
evidence as an `operator_message` event before the model sees it, and the
context manifest records it as protected human guidance. Follow-ups queued
for a later run are bound into that run's plan as `initial_messages`.

A run with any accepted message records `human_intervention: true`,
`operator_message_count` and `behavioral_sample_valid: false` (per round for
multi-round sessions). Evaluation checks these fields against the delivery
events, and learning eligibility excludes the run (`human_intervention`).
An intervened sample is not an invalid finish: a `give_up` round with an
accepted message keeps its policy-lifecycle authority and official verdict,
and its evaluation stays valid.
Enabling the channel without sending a message leaves a run autonomous.

## World view and remote sessions

While a terminal is attached to a Pi-core run (`ora interactive --ui pi`,
`ora play`, `ora attach`), the attaching command serves the run's world view
(`ora.orchestrator.world_view`) on `127.0.0.1`: read-only, bound to the run's
`native/execution-capture/`, gone when the terminal detaches. It is on in a
terminal or with `--world-port`/`--world-public-url`, off with `--world-view
off`; the attach client receives its state on the pipe named by
`ORA_WORLD_FD` and relays it to the TUI (`world {state}` in the attach
protocol). Nothing of it is written to the run.

A command run by `ora-remote` carries `ORA_REMOTE_SESSION=<command>-<16 hex>`
(derived on the laptop from host, checkout and command line). The run it
attaches is recorded in `.harness-runtime/ora-remote/<session>.json`
(`ora.remote-session.v1`: run id, run root, host name, time); the same
`play` or `interactive` command then attaches to that run while its
Supervisor is alive on the same host, unless `ORA_REMOTE_FRESH=1`
(`ora-remote --new`). The record is runtime state, not evidence.

With the TUI host (or `ORA_PI_RPC=1`), the agent process also serves Pi's
RPC protocol (ora-pi/src/rpc/) and records `native/pi-rpc.json`
(`ora.pi-rpc-endpoint.v1`: socket, pid, `protocol: pi-rpc`, `pi_version`,
run id, start time), beside `native/pi-tui.json`; the socket itself is
outside the run (the temporary directory, or `/tmp` when that path is too
long). `ora-rpc-attach` takes `ORA_RPC_RUN` (the run) and `ORA_RPC_ROLE`
(`controller` by default, or `observer`). `ora web` keeps its web server's
log and sockets in a private `/tmp/ora-web-*` directory removed when it
stops, reads the run's stored images (`native/agent-artifacts/`) and world
view, and writes nothing into the run. `ora replay` reads a finished run's
`native/pi-session/*.jsonl`, `events.jsonl`, `agent-events.jsonl`,
`native/execution-capture/` (manifest and timeline), `native/post-action-settle.jsonl`, the operator views and
the stored images, in the same private temporary directory, and writes
nothing into the run either; its export writes only the video and its report
beside it (`ora.replay-video.v1`: size, frame rate, frames, duration, the camera box's shape, speed
and motion speed, the seconds of robot motion, each stretch's rate with its
video, recorded and simulated times, the replay's notes, and the run's ID
unless published), with its frames drawn in the system temporary directory and
removed. The display choice of the page (`ora.web.display`) and its language
(`ora.web.lang`) live in the browser. The built page records the digest of
the sources it was built from in `ora-pi/web/dist/ora-build.json`
(`ora.web-build.v1`). `ora-remote --web` passes `ORA_WEB_SOCKET` and `ORA_WEB_TOKEN_FILE` to
the attaching command; the token file (0600, in
`$XDG_RUNTIME_DIR/ora-remote/`, else `~/.cache/ora-remote/`) is removed
when read, and files there older than 12 hours are swept by the next
session. Neither the token nor the cookie is logged.

## Free play

`ora play` ([guide](../usage/play.md)) resolves a plan with
`episode_mode: "play"` (a benchmark plan carries no such key) under the
`ora-play` experiment, with `operator_control` and a `play` block
(`ora.play-session.v1`):

- `scene` is the scene the run starts in: `environment`, `task`, `seed` and
  `qualification`, with `scene_id`, `label` and the LIBERO `suite` and
  `task_id` or the RoboTwin `task_config` (and `scene_set` for a scene of
  ORA's own RoboTwin or RoboDojo scene sets), from
  `ora.orchestrator.play.scenes.build_play_spec`;
- `scenes` lists the scenes `reset` may load in turn, 1 to 32 of them,
  starting with `scene`, all on the plan's task;
- `instruction_budgets` holds `max_robot_decisions`, `max_model_calls` and
  `wall_time_s`, each at most the session's `budgets`;
- `context_images` (default `{"observations": 3, "tool_results": 3,
  "prune_at": 20}`, each 1 to 50, `prune_at` at least the other two) is which
  observation messages and tool results with images keep their images in a
  model request. With `prune_at`, the images all stay until an instruction
  begins with that many messages of a kind still carrying theirs; then all but
  the latest `observations` (or `tool_results`) leave at once, and 50 messages
  of a kind prune inside an instruction that never ends. Nothing sent earlier
  changes while an instruction runs, so its calls share a prefix. Without
  `prune_at` (a RunSpec that names the other two only, and every plan resolved
  before it existed) the window slides: only the latest `observations` and
  `tool_results` keep their images at every call. Pi's `context` hook
  (`ora-pi/src/play/context_images.ts`) replaces every older image of that
  request, never of the session, with one text line naming its observation
  step and camera, or its tool; images the operator attached are always sent,
  and the `play_provider_request` artifact is the request as sent. Sent in
  full, a long session outgrew a provider's body limit (HTTP 413 at 50.8 MB);
- `request_priority` (optional, `ora play --request-priority`;
  `{"correction": C, "default": D}`, integers in -1000..1000) adds a
  `priority` field to every OpenAI-compatible request of the profile's model,
  for a self-hosted server's priority scheduling: `C` on the call that first
  reads an operator message joined to the running instruction, `D` on every
  other call. vLLM runs lower values first and refuses the field unless
  started with `--scheduling-policy priority`; SGLang runs higher values first
  unless started with `--schedule-low-priority-values-first`. Absent (the
  default), requests carry no `priority`. The `play_provider_request` artifact
  carries it as sent;
- `pipeline_predict` (optional, `ora play --pipeline-predict`; `true` or
  absent, refused with `code_mode`) predicts the next step while a motion
  runs (`ora-pi/src/play/pipeline.ts`). When a reply's world-changing call is
  submitted, one more request to the profile's model goes out at once: the
  submitting call's context, its reply, the call's result, and a note asking
  for the next step on the assumption that the motion succeeds. Nothing runs
  from it. At the next real call its reply is used only when the fresh
  observation's receipt (`last_action_effect`) says the motion reached its
  target, or that a closing gripper stalled at least 5 mm open (on an object),
  and the real request extends the prediction's by exactly the reply, the
  result and that observation (an operator's joined message discards it).
  Then the prediction's reply answers the real call, and the Supervisor
  admits and checks its tool call like any other; otherwise the real request
  is sent. Either way the agent trace records a `pipeline_prediction` event
  (`prediction_id`, `outcome` `used` or `discarded`, `reason`, `tool_call_id`,
  `started_ms`, `ended_ms`, and `replayed_ms` when used), its
  `play_pipeline_prediction` artifact holding the prediction's request as sent
  and its reply; a used call's `play_provider_request` artifact names the
  prediction (`ora_pipeline_replay`) in place of a request. OpenAI-compatible
  profiles only;
- `draw` records how the scenes were drawn.

A `session` block is refused, and so is a plan naming the retired native
agent core. Free play runs on the Pi core. The plan runtime is the scene's play binding
(`play.binding.bind_play_runtime`), which loads the scene with its official
predicate withheld and which a benchmark plan cannot select; a RoboTwin scene's
seed manifest (or, for a scene of a scene set, its play-scene audit, with
`runtime.play_scene_set`) and a RoboDojo scene's play-scene audit must be the
one the plan binds.

`task_instruction` is the play framing. For a baseline instance the defaults
compose `episode-task-play-v2.md` as the episode-task layer, record
`policy.task_contract` as withheld (`free_play_session_withholds_the_task`),
and advertise the surface's tools without `check_task` and the lifecycle
tools. The backend's own identity checks still compare
`runtime.expected_task_language`, which is never shown to the policy.

The Pi core runs a free-play act as Pi's own agent (`ora-pi/src/play/`).

- **Pi:** the user's Pi configuration (`PI_CODING_AGENT_DIR` or Pi's default),
  Pi's coding tools with the run's `sandbox/workspace` as their working
  directory, Pi's context and compaction, and Pi's model registry. The ORA
  provider profile is registered as provider `ora`. Its `request_options` are
  the `ora` model's sampling parameters, so every request Pi builds for that
  model (a turn, compaction, a branch summary) carries them, validated and
  refused as an episode's provider validates them, and no other provider's
  model does. A profile that names its own `reasoning` object has Pi state the
  reasoning effort as `reasoning_effort`, as the episode provider does. A Pi
  `models.json` that overrides the `ora` model's sampling ends the start.
- **Robot tools:** they stay Python tools. A world-changing call ends Pi's
  run, and the Supervisor executes it.
- **Scripts:** Pi's `codemode` tool is on, without its `models` global. A
  codemode script's robot call reaches the run's robot socket, as `ora-robot`
  from the shell does: the Supervisor runs it inside the turn, and the trace
  records it with `payload.origin.kind` `codemode` and the codemode call that
  made it (`parent_tool_call_id`); the socket names a call codemode only when
  the agent process itself made it. A script's other nested calls carry the
  same origin.
- **Credentials:** the agent process gets the user's environment minus
  credential-shaped variables. `/login` writes the user-level store
  (`$ORA_CONFIG_HOME` or `~/.config/ora`, file `pi-auth.json`) in every mode,
  the store `ora providers` keeps ORA's keys in; a benchmark run reads it once
  into memory.
- **Restarts:** the agent process is not restarted.

The Supervisor (`episode/play.py`) adds an `awaiting_instruction` phase to the
interaction state. The operator inbox also admits `instruction`,
`queued_instruction`, `stop_instruction`, `reset` and `quit`. A
`queued_instruction` sent while an instruction runs is held until that
instruction ends and then opens the next one (announced as `message_queued_next`);
with no instruction running it opens one at once. Its `operator_message` event
keeps `type: queued_instruction` and counts as an instruction.

- Instructions, and the messages the operator sends while one runs, are
  delivered as `operator_message` events (`type: instruction`) before the next
  model call. Controls are recorded as `play_control` events and
  acknowledged.
- Each instruction is a `play_instruction_started` /
  `play_instruction_finished` pair (`ora.play-instruction.v1`). The pair
  records `ended`, `decisions`, `refused_proposals`, `model_calls`, the
  observation steps and the wall time.
- A new instruction starts on a fresh observation, published before its first
  model call through the opening scene's path. The policy-state snapshot has
  cause `instruction_start`, beside `reset` and `after_action`. The started
  record's `observation` is `{"fresh": true, "observation_step": N}`. When the
  look fails, it is `{"fresh": false, "observation_step": N, "error": ...}`:
  the instruction runs on the last observation (step N), an
  `auxiliary_failure` (stage `play:instruction_observation`) is recorded, and
  the instruction's `operator_message` payload carries the `observation_note`
  the model reads after that observation. A message that joins a running
  instruction takes no new observation. Records written before this change
  carry no `observation`.
- A message sent while an instruction runs joins it: the instruction's
  record lists it in `joined_messages`, and only the first message of an
  instruction opens one. Evaluation requires every delivered instruction-type
  message to have opened an instruction or joined one. Records written before
  this change carry no `joined_messages`; in them a newer instruction ended the
  running one as `superseded`.
- A text-only reply that is the model's first reply after it read a joined
  message is that message's answer, not the instruction's report. The Pi
  driver knows which model call first read each joined message (Pi steers a
  message in after the current tool batch, so that may be the next act's
  first call: `PlayDriver.noteContext` from Pi's `context` hook) and names the
  messages a text-only last reply answers in the act outcome (`answers`). The
  Supervisor records a `play_answer` event (`ora.play-answer.v1`:
  `instruction_id`, `instruction_index`, `message_id`, `observation_step`)
  for each answered message that joined the running instruction and has no
  answer yet, lists it in the instruction record's `answered_messages`, and
  keeps the instruction running: the next act brings the same observation.
  Each joined message earns at most one continuation; a text-only reply that
  answers no newly read message is the report (`ended: reported`). Evaluation
  requires each `play_answer` to name a message its instruction lists in
  `joined_messages`, once, matching `answered_messages`
  (`play_answer_invalid`).
- A reply the provider's output limit cut off before any tool call
  (`stopReason: "length"`, with or without text) is no report. As in an
  episode (`afterTruncatedOutput`), the Pi driver records a derived
  `provider_output_truncated` event (`tool_call_count: 0`, `text_chars`,
  `policy_outcome`) and, from Pi's `turn_end`, adds ORA's continuation as an
  `ora.continuation` custom message (`details.ora_reason:
  "output_truncated"`) that the next model call reads on the same
  observation (a joined message the cut-off call read first counts as read
  by that call for its answer); that call's boundary still checks the
  operator's inbox and the instruction's budget. When the act's own model
  calls or time (the session's
  remainder) are spent, the act ends with termination `output_truncated` and
  the Supervisor ends the instruction `ended: output_truncated`
  (`policy_outcome: "output_truncated"`); an act that ends on a cut-off reply
  is never `policy_no_action`.
- The instruction's model calls and wall time are checked before every model
  call.
- A scene change is a `reset` event and a `play_scene_reset` event. A scene the
  environment ends (`play_scene_ended`) waits for `reset` or `quit`.
- No official verdict is persisted.

### Operator view

An environment session may give the operator a better picture than the
policy's cameras, for example a third-person camera. It is the operator's
alone: no observation, context, tool result, agent event or provider request
carries it.

- **Session contract** (`ora/environment/base.py`): an optional method
  `operator_view() -> {"png": bytes, "camera": str, "width": int, "height": int} | None`.
  `png` is one PNG image (at most 8 MiB) whose IHDR size equals `width` ×
  `height` (each 1 to 8192); `camera` is a short name
  (`[A-Za-z0-9_.-]{1,64}`). None means no view now. A session without the
  method has no operator view. The method must not advance physics, change
  the scene's random state or touch anything the policy observes.
- **Transport:** `DescribedEnvironmentSession.operator_view()` forwards and
  validates it (`validate_operator_view`). Over the local worker transport
  (`local_rpc`), the worker writes the PNG to
  `native/.operator-view-transfer.png` and the client reads it back and
  removes it; the image never travels inside the JSON line.
- **When:** a free-play Supervisor asks after each published observation (the
  reset, every action, every script action, every scene reset), once the
  policy has that observation.
- **Evidence:** the PNG goes to `native/operator-view/<observation_step:06d>.png`
  and `events.jsonl` gains `{"type": "operator_view_frame", "observation_step",
  "path", "camera", "width", "height", "sha256", "time"}`; the live port gets
  the same record as an `operator_view_frame` event. Play evaluation requires
  each named file to exist with its hash (`play_operator_view_frame_invalid`).
  A failing view is an `auxiliary_failure` (`play:operator_view`); after three
  in a row the session stops asking.
- **Backends:** Synthetic draws a front view of its arms (`operator_front_view`,
  640 × 400) when its execution capture is on. RoboTwin returns its
  `observer_camera` (320 × 240), and a scene of ORA's RoboTwin scene sets its
  own `operator_camera` (1280 × 720, also an execution-capture camera; see
  the scene sets above). A RoboDojo play scene returns `operator_camera`
  (`backends/robodojo/play_scenes/operator_view.py`): a USD camera 1280 × 720
  (15 mm lens, 16:9) looking at the table at three quarters from its far side, with both arms in
  frame, attached after each reset and released before a relaunch closes the
  stage, never registered with RoboDojo's camera or capture manager; the play
  session reads its Replicator annotator's latest frame without rendering, and
  by default records it as a fourth execution-capture camera beside `head`,
  `left_wrist` and `right_wrist`, its frames tagged with the executing
  action's id. The worker-config field `operator_view_mode` (`capture`, the
  default; `view`; `off`), which no plan can carry, exists to measure its
  rendering cost; the play source manifest records the mode and the camera.
  LIBERO has none (its environment renders only the policy's cameras). A backend that also lists the view's camera among its
  execution-capture cameras gets live and recorded motion in that view too
  (the web page plays motion for the view's camera when the capture has it).

What the operator's clients see is display only and never evidence:

- **Announcements** (`ora.play-state.v1`, to the live port, the TUI and Pi
  RPC clients): besides `instruction` (now with `message: {id, text,
  instruction}`), `instruction_finished`, `control`, `scene`, `scene_ended`,
  `awaiting_instruction` and `ended`, the Supervisor announces
  `message_queued` (a message admitted while an instruction runs, which will
  join it), `message_delivered` (`message: {id, text, instruction, joined,
  observation_step}`), `action_started` and `action_finished` (`action:
  {action_id, observation_step, duration_s}`), the robot executing an action
  of the policy or of a script, and `answer` (`answer`: the `play_answer`
  record; the instruction keeps running, so no `instruction_finished`
  follows it).
- **Delivery records:** each free-play `operator_message` payload also names
  `instruction_id` (the instruction it opened or joined) and `joined`.
- **Pi's conversation:** a joined message reaches Pi as a custom message
  `ora.play_message` (a user message on the wire, the operator's words only;
  `details` = `{ora_message_id, ora_instruction_id, ora_instruction,
  ora_observation_step, ora_joined}` for the terminal and the page); the
  instruction stays Pi's user message.
- **Language:** the TUI's ORA words follow `ORA_UI_LANG` (`zh` or `en`), else
  a `zh*` locale; the web page follows the browser (`ora.web.lang` in its
  local storage once chosen).
- **Demos:** `ORA_SYNTHETIC_ACTION_WALL_S` (0 to 10, default 0) makes each
  Synthetic action take that many wall seconds, its world capture paced in
  real time, so a demo shows the robot moving.

The agent trace adds these events: `provider_request` / `provider_response`
(with the provider and model each call used), `tool_call_proposed` /
`tool_result` for Pi's tools, `operator_shell_command` / `operator_shell_result`,
`model_selected`, `thinking_level_selected`, `conversation_compacted`,
`conversation_navigated` and `conversation_switched`.

The result carries `play` (instructions, scenes, control count),
`official_success: null` and `behavioral_sample_valid: false`. Evaluation
checks those records against the events (`evaluation/play.py`), and it checks
the agent trace for completeness only (`ora/agent/evidence/play_trace.py`: the
hash chain, artifacts, a response for every request, a result for every tool
call, a receipt for every action). It skips the benchmark's context audit,
context manifest, request grammar and official-verdict checks. Learning
eligibility adds `free_play_session`, and `seal.json` carries
`episode_mode: "play"`.

## Episode evidence

The run directory binds `plan.json`, `preflight.json`, `launch.json`, process identity, canonical events, policy-state/trajectory evidence, public observations, action proposals and measured receipts. `native/agent-session.json` (`ora.native-policy-session.v1`) records the core that ran in `agent_core` (`pi` or `native`); preflight's `agent_core` check records the Node binary, its version and the Pi version. A Pi-core run also keeps `native/pi-session/` (the Pi session file, the durable conversation record), `native/pi-core.log` and, after a restart of the core, `native/pi-restarts.jsonl`. A free-play run's robot socket adds `native/robot-socket.json` (its address), `native/robot-socket.jsonl` (every request and its outcome) and `native/robot-client/` (the client copy on the agent's `PATH`); its script calls are in the agent trace with `payload.origin` and its script actions in `events.jsonl` as `script_action` (see [free-play robot scripting](public-interfaces.md#free-play-robot-scripting)). `result.json` records termination/state and the official environment verdict; `evaluation.json` repeats the plan's `evidence_tier`, `result_tier` and partition name. Provider requests/responses and content-addressed artifacts are archived for reconstruction; exact filenames and schemas are owned by the evidence modules and validated by their tests.

The rollout compactor's calls are recorded as `context_compactor_attempt`, `context_compactor_response` and `context_compactor_failed` in the agent trace. With a [bound compactor](#context-compactor), each of the three payloads adds `provider_binding: {provider_profile, model, endpoint}`, the attempt's `model` and `provider_protocol` are the binding's, and the run's native metrics list the bindings under `context_compactor.provider_bindings`. Without a binding the payloads have no such key.

Derived metrics, context manifests/audits and behavioral-ownership reports do not replace raw evidence. SFT export is a derived provider-request projection. Original capture and reconstructed replay are separately labeled in the viewer.

### Wire export

`ora export-sft RUN_ID --view wire` (`ora/agent/evidence/wire_export.py`, manifest schema `ora.sft-wire-export.v2`) exports every provider exchange of a run whose seal still matches (when sealed) and whose agent trace verifies. `manifest.trace.status` is `valid`, `incomplete` (only `request_without_response` errors: every request exports, an unanswered one with `answered: false` and a null reply) or `sealed_valid_unverified_here` (sealed, its evaluation valid, this checkout's verifier disagrees; the errors are listed). It does not require learning eligibility; the manifest records `evaluation_status` and `learning_eligible`, which is true only for a sealed run whose evaluation is valid and eligible. `RUN_ID` may be a run directory when `--destination` is given.

| File | One line per | Fields |
| --- | --- | --- |
| `requests.jsonl` | model call | `index`, `model_call_id`, `answered`, `protocol`, `request_source` (`wire`, or `canonical` for a non-OpenAI protocol), `request` and `request_sha256`; for a non-OpenAI protocol also `wire_request` and `wire_request_sha256`; `epoch`, `epoch_prefix`, `epoch_tail` |
| `responses.jsonl` | model call | `index`, `model_call_id`, `answered`, `accepted` (null when unanswered), every other field of the `provider_response` payload (`finish_reason`, `attempts`, `usage`, `output_rejection`, ...), `message` (without `_`-prefixed internal keys) and `provider_reply` (`content`, `reasoning_content`, `tool_calls`, `finish_reason`, `usage` rebuilt from an OpenAI-format stream or body; null for another shape) |
| `streams.jsonl` | model call | `index`, `model_call_id`, `response` (the archived provider response) and `response_sha256` |
| `compactor.jsonl` | context compactor call | the `requests.jsonl` fields without the epoch, `call_kind`, `reply_event`, `reply` (as in `responses.jsonl`), `response`, `response_sha256` |
| `images.jsonl` | image file | `path` (`images/<sha256>.<ext>`), `sha256`, `media_type`, `bytes`, `calls` |
| `epochs.jsonl` | epoch | `epoch`, `messages` |
| `tokens.jsonl` | policy call | `model_call_id`, `usage` (`input_tokens`, `output_tokens`, `image_tokens` from the provider's reply), `prompt` (null without ids: `source` `stream` or `tokenize`, `length`, `sha256` of the ids as little-endian uint32, `images`, `image_form` `expanded`, `placeholder` or null when neither matches usage, `prefix` `{model_call_id, length}` shared with the previous call's prompt, `suffix`, and `tokenized` when a tokenize answer differs from the streamed ids), `output` (null without ids: `source`, `length`, `sha256`, `ids`), `checks` (`prompt_length`, `output_length`, `tokenize_equals_stream`: true, false, or null when not measurable), `extends_previous` |

An image is an OpenAI `image_url` data URL or an Anthropic `base64` source; it is replaced by its file path only when its base64 round-trips exactly, and otherwise stays inline. A call's messages equal `epochs[epoch].messages[:epoch_prefix]`, followed by `epoch_tail` when it is not null. `manifest.replay_check` counts the requests rebuilt from their lines, from their epochs and (for another protocol) from `wire_request`, the compactor requests, and the streams, each matched to the archived sha256 after the images are put back and the value is serialized as `canonical_bytes`; it also counts the provider replies that equal a fresh parse of their streams and the images whose file, sha256, size and calls match `images.jsonl`. Any failure is listed and refuses the export. `--check` also requires `manifest.json` and every export file and no other file (`missing_file`, `extra_file`, an unindexed file in `images/` included), checks the sha256 and size of each file in `manifest.files`, the manifest's key set (`manifest_keys`) and its `replay_check` against a fresh check of intact files (`manifest_replay_check`), and derives every row and the manifest's description again from the run (`source_*` failures). `manifest.images.recorded_unsent` counts, by event type, the distinct recorded image artifacts whose bytes no request carried (never sent, or sent in another encoding or size).

`--redact-host-paths` (off by default; without it the export is unchanged) replaces the host filesystem paths that name the machine the run was made on, in every request, wire body, stream and reply record before anything is derived from them (`ora/agent/evidence/host_paths.py`). The prefixes are a fixed list, each as written and as resolved, longest first, and match only where the next character does not continue a file name: `run` (the run directory) as `<host-run>`, `outputs`, `workspace` and `checkout` (`launch.json` `workspace.outputs_root`, `workspace_root`, `source_worktree`) as `<host-outputs>`, `<host-workspace>`, `<host-checkout>`, `python` (the code-mode interpreter `preflight.json`'s `code_mode_sandbox` check recorded, its environment and the base prefix its `pyvenv.cfg` `home` names: the prefixes the sandbox's tracebacks carry) as `<host-python>`, `home` (the home directory of the run directory's owner and of the exporting account) as `<host-home>`, and each `--redact-path PREFIX` as `<host-path>`. The rest of the path stays (`<host-python>/lib/python3.11/json/encoder.py`); nothing else that looks like a path (the sandbox's `/work/...`) and no image (a data URL, an Anthropic `base64` source) is touched. A call whose request changed carries no prompt token ids (its stream's `prompt_token_ids` become null and it is not tokenized); a stream whose text changed has its sampled `token_ids` null too. A prefix still found after that, split across stream chunks, refuses the export. Nothing derived from the redacted bytes is exported, since it would let a reader confirm a guessed prefix (put it back, hash, compare): no prefix and no hash of one, a changed row's `request_sha256`, `wire_request_sha256` or `response_sha256` is null, and while any row changed `manifest.trace` leaves out `final_event_sha256` (the event chain hashes the sealed requests and streams); an unchanged row keeps its sealed sha256. `manifest.redaction` records `rules` (`rule`, `placeholder`, `replacements`, per rule present in `RULES` order), the total `replacements`, counted once per request, stream or reply record (epochs and the package's calls and conversations are derived from them), and `rows`: each changed row's `file`, `model_call_id`, `field` (`request`, `wire_request` or `response`), `replacements` and `redacted_sha256` (of its redacted canonical serialization). The check takes a listed row's `redacted_sha256` and counts it under its check's name with `_redacted` (`requests_redacted`, `streams_redacted`, ...); a listed row that does not exist or still has its sealed sha256 is a `redaction_record` failure. With the option and no host path the record has every rule with zero replacements and no rows, and everything else is as without it. `--check` reads the redaction from the manifest (it takes no `--redact-host-paths`), redacts the run again with the prefixes found there and for this account (each `--redact-path` given again), compares every row with the export, fails `redaction_rules` when the re-derived record differs (another account, or a `--redact-path` not given again) and states what it proved in its report's `redaction`.

Token ids (`ora/agent/evidence/token_ids.py`) come from an archived stream that carries `prompt_token_ids` and per-choice `token_ids` (vLLM with `request_options.return_token_ids: true`), or, with `--tokenize-url URL [--tokenize-key-env NAME]`, from that engine's tokenize endpoint, sent the wire request's `model`, `messages`, `tools` and `chat_template_kwargs`, a set `reasoning_effort` merged into `chat_template_kwargs` (with `enable_thinking: effort != "none"` unless the request set it) as vLLM's chat endpoint renders it (`token_ids.tokenize_payload`). A call of another wire protocol is not tokenized. `prompt_length` is true when the ids number the usage's `input_tokens` (`expanded`), or do after each image's one placeholder token is counted as the usage's `image_tokens` (`placeholder`, sglang); `output_length` compares the sampled ids with `output_tokens`. A false check is recorded and does not refuse the export; a tokenize request that fails does. `manifest.tokens` tallies the rows by prompt source, image form and each check, counts `output_ids`, and names the `tokenizer` (`url`, `fields`, `reasoning_effort: chat_template_kwargs`; never a key). `--check` rebuilds every row from the recorded prompt chain (a broken `prefix` is a `tokens` failure), the stream rows and the replies' usage, taking tokenize answers as recorded.

### OpenAI package

`ora export-sft RUN_ID --view openai` (`ora/agent/evidence/openai_package.py`, `package.json` schema `ora.openai-package.v1`) writes the wire export to `wire/` and derives from it, with `openai_package_tool.derive`:

| File | One line per | Fields |
| --- | --- | --- |
| `conversations.jsonl` | epoch with an answered call | `run_id`, `epoch`, `model_call_ids` (its answered calls), `messages` (the epoch's last answered request with every image a data URL again and each `reasoning` key renamed `reasoning_content`, then that call's `provider_reply` as an assistant message), `tools` |
| `calls.jsonl` | model call | `index`, `model_call_id`, `epoch`, `protocol`, `answered`, `accepted`, `request` (as sent, images as `wire/images/...`, `reasoning` renamed), `response` (null when unanswered), `finish_reason`, `usage`, `token_counts` (`prompt`, `sampled`, `prompt_equal_to_usage`, `sampled_equal_to_usage`) |

It copies the run's `replay-program/` when there is one, copies `openai_package_tool.py` as `ora_package.py`, renders `README.md` from the plan and the wire manifest, and writes `package.json`: `schema`, `run_id`, `wire_manifest_sha256` and `files` (`path`, `sha256`, `bytes` of every file outside `wire/` but itself). The package is moved into place only when `openai_package_tool.verify` passes. `verify` imports only the standard library and fails on: `package_schema`, `wire_manifest`, `package_file`, `wire_file`, `image_index`, `image`, `request`, `request_from_epoch`, `stream`, `prompt_ids` (a broken prefix chain or a sha256 or length that differs), `prompt_length_usage`, `prompt_ids_stream`, `sampled_ids_stream`, `conversations.jsonl`, `calls.jsonl` (each row against `derive`), and with a tokenize URL `engine_tokenize`. A file a user added outside `wire/` is reported in `unlisted` and does not fail. `--check` (`check_openai_package`) adds `check_wire_export` of `wire/` against the run, its failures prefixed `wire_`.

With `--redact-host-paths` the wire export is redacted as above, and so are `README.md` and the text of `replay-program/`; `package.json` then has `redaction.files` (each such file's replacements), and the README a "Host paths" row and, when anything changed, a Redaction section that says what was replaced, that a redacted call ships no prompt ids, and what verification can prove. `verify` checks each row `wire/manifest.json` `redaction.rows` lists against its `redacted_sha256` (`request_redacted`, `request_from_epoch_redacted`, `stream_redacted`) and every other row against its sealed sha256 as before; it fails `redaction_record` when `package.json` and the wire manifest disagree about the redaction, a listed row does not exist or still carries a sealed sha256, and `redaction_ids` when a call whose request (or stream text) changed still carries prompt (or sampled) ids. Its report's `redaction` gives `requests`, `streams`, `replacements`, `files` and a `statement`: that the redacted rows match the sha256 recorded at export and their sealed sha256 is not shipped, so the package alone cannot prove they are what the model was sent, and that the sealed run can (`--check`, which derives them redacted again from the run, or an export without the option). `ora_package.py verify` prints that statement and ends `PACKAGE VERIFIED AS REDACTED: N requests and M streams ...` instead of `PACKAGE VERIFIED` when any row or file changed.

### Trace dataset

`ora export-traces --runs LIST --destination DIR` (`ora/agent/evidence/trace_dataset.py`, `dataset.json` schema `ora.trace-dataset.v1`) exports each listed run with `wire_export.export_wire` into `--work` (redacted with `host_paths` unless `--keep-host-paths`), derives one `ora.trace-episode.v1` record from it, and assembles the records in the list's order:

| File | Contents |
| --- | --- |
| `episodes/episodes-NNNNN.jsonl[.gz]` | One record per line: `schema`, `episode_id`, `model`, `reasoning_effort`, `benchmark`, `task`, `seed`, `instruction`, `mode` (`code` or `tool_calls`), `result` (`success`, `termination`, `learning_eligible`), `labels` (the list entry's), `tools`, `segments`, `usage` (`model_calls`, `prompt_tokens`, `completion_tokens`, `reasoning_tokens`), `unanswered_calls`, `ora_commit`, `redaction` (`host_paths`, `run_id`: replacements). A new shard starts at `--shard-mb` uncompressed megabytes |
| `index.jsonl` | One line per episode: its identity, result and labels, `model_calls`, `segments`, `images`, token totals, `shard` and `line` |
| `images/images-NNNNN.tar`, `images.jsonl` | Each image once across the dataset, in ustar shards of at most 2 GiB; one line per image: `sha256`, `ext`, `media_type`, `bytes`, `shard`, `offset` (of its bytes in the shard) |
| `ora_traces.py`, `README.md`, `dataset.json` | `trace_dataset_tool.py`; the data card; `episodes` (`count`, `shards`), `images` (`count`, `bytes`, `shards`), `host_paths_redacted`, `files` (`path`, `sha256`, `bytes` of every file but itself) |

A segment is an epoch of the wire export that has an answered call: `messages` (the epoch's last answered request, `reasoning` renamed `reasoning_content`, image references `images/<sha256>.<ext>`, then that call's reply), `calls` (one per answered call: `prefix` and `tail`, so that its request is `messages[:prefix]` plus `tails[tail]`; `finish_reason`; `usage`), `tails` (rolling camera messages, when the run has them), and `tools` only when the calls' tool definitions differ (the episode's `tools` is then empty). `model` and `benchmark` come from the first request and the plan unless the list entry gives them. An entry without `episode_id` gets `ep-` and the first 12 hex digits of the run id's sha256. The run id is replaced by the episode id in every string of `instruction`, `tools` and `segments` (the run directory's path is a host path). An episode whose strings hold a `--forbid` string is refused; a run that the wire export refuses, or that has no answered call, fails; both are listed in `DIR.report.json`, and `DIR.run-map.jsonl` has `episode_id`, `run_id` and `run` for each exported one. Neither file is inside the dataset.

The dataset is moved into place only when `trace_dataset_tool.verify` passes with every image's bytes checked. `verify` imports only the standard library and fails on: `manifest`, `schema`, `file_missing`, `file_changed`, `episode_schema`, `episode_twice`, `index_place`, `index_images`, `image_unknown`, `image_unused`, `call_place`, `episode_count`, and with `--images` `image_bytes`. A file added to the dataset is reported in `files_not_listed`.

### SFT token accounting

`ora export-sft` writes one `tokens.jsonl` line per exported record and a `token_accounting` block (`ora.sft-token-accounting.v1`) in `manifest.json`.

| Field | Contents |
| --- | --- |
| `tokens.jsonl` `index`, `model_call_id` | The record and the model call it came from |
| `tokens.jsonl` `provider_usage` | `input_tokens`, `output_tokens`, `reasoning_output_tokens`, `cached_input_tokens` from the call's `provider_response.usage`; `null` where the provider reported none |
| `token_accounting.provider_usage` | The usage totals over the records, with `records` and `records_reported` |

These are the serving model's counts for each call's wire request. The exported record is the canonical request without the call's earlier reasoning, so its own length differs and depends on the tokenizer it is trained with. The context pipeline's pressure estimates (`estimated_input_tokens` in `context_projected`) are not token counts and never enter the export.

### LIBERO planner world model

`runtime.motion_planner.world_model` selects what the LIBERO CuRobo planner
collides with. `none` plans against the robot alone: joint limits and
self-collision; no maintained baseline selects it since the provider-tape replay
baseline `native_full_explicit_motion_direct_replay_v1` was retired on 2026-11-01.
`public_depth_voxel_v1`, the default baseline's since 2026-10-30, adds a voxel world
built for every request from public depth only
(`ora/environment/controllers/depth_world.py`): both cameras' metric depth and
calibration, the measured base pose, joints and fingers, and the planner's own
robot spheres; no simulator state and no segmentation. The world removes the
robot's own depth, carves the measured start and an oriented box around the
goal hand (hand and fingers to about 1 cm past the fingertips, and a 5 cm
approach standoff), moves the held load from the world to the hand, and is
loaded as a 1 cm ESDF. The grid covers x -0.20..1.01, y -0.60..0.61 and
z -0.25..0.76 m in the robot base frame; CuRobo's voxel kernel reads the outer
three voxels of each face as unobserved (free), so the loaded grid is that
volume grown by three voxels per face around the same centre. Plans and the
reach check's IK both use the world, each goal in its own carved world.
`depth_world.curobo_v2_grid` exports the same world for cuRobo V2, whose voxel
kernel reads distances negative inside, as float16, from `round(dims / voxel)`
centred voxels per axis with no unobserved border (a query outside reads
free): the export is the volume above without the border (121 x 121 x 101
voxels of 1 cm, centred 5 mm past the grid centre on each axis, so that V2
voxel `i` is the v0.7.8 voxel `i + 3` at the same centre), with the sign
flipped. Both kernels see the same occupancy; `sha256` hashes the export's
layout and values (`ora.curobo-v2-voxel-grid.v1`).
Replay is unaffected: it reads the recorded joint commands.

The two-arm robots carry their own parameters (`ROBOT_WORLDS`). RoboTwin's ALOHA
uses `PUBLIC_DEPTH_VOXEL_ALOHA_V1`: the depth of the policy's four cameras as
`get_obs` publishes them at the request, read without advancing physics, a 1.46 x
1.86 x 0.86 m grid centred at (0.45, 0, 0.25) in the arm's base frame, and the other
arm's spheres at its measured joints in the world. The YAM station's arm uses
`PUBLIC_DEPTH_VOXEL_YAM_V1`: the depth of every calibrated public camera (`overhead`,
`front`, `wrist_left`, `wrist_right`) read when the plan is made, a 1.06 x 1.56 x
0.86 m grid centred at (0.25, 0, 0.30) in the moving arm's base frame (the table top
is the base plane on the CAD cell), the hand convention's origin at the public TCP
(the fingertips' facets: `fingertip_m` 0.002, `finger_span_m` 0.055, the hand box
0.11 x 0.18 x 0.195 m from 0.175 m behind the TCP), and the other arm's spheres at
its measured joints in the world.

The default LIBERO surface (`libero_placement_direct_overhead_v1`) describes
this world to the policy. Its `move_pose` help says that global CuRobo routes
and every IK check avoid a world rebuilt per request from both cameras' depth,
with the robot removed, the goal hand's region cleared, a held object moving
with the hand and unseen space free; that a target with a fingertip more than
1 cm below the supporting surface is refused before planning; and that the
bounded local OSC segments do not check the world. Its `grasp_plan` IK
selection names the same solver. The text changed in place on 2026-10-30
(the depth-world sentences, then of the layer `published_limits_v1/libero-overhead-help.json`
and now of the file-backed surface's `explicit_motion_v3/move_pose.json`);
a sealed run keeps the tool definitions its plan recorded. The reach check
answers with the planner's world model: `ik_model:
libero_reach_ik_depth_world_v1` and the depth-world `claim_boundary` in world
mode, `libero_reach_ik_robot_only_v1` and the robot-only claim under `none`.
The new texts name behaviour ("the global planner", "the IK check") rather
than the planner library, so the planning backbone can change under them; the
planner binding and evidence keep naming the exact implementation and
version.
The planning-diagnostics `note` likewise reads "Self-collision and the depth
collision world are not separated." or "World collision model is absent.".
The delivery trim (`observation_delivery_trim_v2`) leaves a fixed
`claim_boundary`, `evidence_scope` or `note` text out of a result or action
effect only when a tool description delivered in the same run states it word
for word (`trim.stated_prose` over the session's delivered tool list, in the
written or degree form). The table of such texts
(`ora/agent/context/delivery/trim_prose.py`) keeps both LIBERO forms: today's
help states the depth-world texts, so a `none` run keeps its robot-only claim
and note in each result, while a sealed run replayed with the definitions its
plan recorded, whose help states the robot-only texts, has them left out as it
did when it ran. A text edited in place keeps its old form in that table, with
no backends, for the same reason.

On LIBERO `grasp_plan` adds a top-down fallback (`panda_public_rgbd_topdown_fallback_v1`,
`agent/toolkit/host/grasp/panda_topdown.py`). Backend id
`contact_graspnet_then_panda_rgbd_topdown`: when Contact-GraspNet returns no candidate,
up to two top-down candidates are given instead, with the support-aware height rule
from the target mask and scene depth, pads centred on the grip site and fingertips at
least 5 mm above the support. They are never shown beside learned candidates, so a
call the network answers is unchanged. A target too
sparse for this family costs only this family. `diagnostics` then uses the
two-family shape (`learned`, `public_rgbd_topdown`, `candidate_family_counts`). On
the recorded inputs of 222 LIBERO calls, 24 of the 29 calls with no candidate get
one; the other 5 exceed the 80 mm opening. RoboCasa365 and RoboLab do not declare it.

Neither carve box clears the support under its hand: the dominant horizontal
surface found from the depth within 15 cm of the fingertips (the lowest level
that tops at least 100 grid columns, so the table rather than an object
standing on it). A goal with a fingertip more than 1 cm below that surface
(`support_tolerance_m`, which absorbs the depth noise in the surface height) is
refused before any solver runs: a plan answers `refused` with
`goal_below_support` and a planning-diagnostics recovery line
that says how far below the surface the fingertips are, and an IK query is
unsolved without a solve. A goal within the tolerance goes to the solvers
with the support still in the world, so it reaches only as far into it as
collision allows. The goal carve's spheres (a held load's, swept over the
standoff, and a joint goal's robot spheres) leave that support too. Held spheres
over-cover their load, so the ones attached for a goal stop one voxel above the
support, and a load set down on it plans. When the load's own seen points lie
more than the tolerance below the surface, the spheres stop at those points
instead, in the support, and the solvers refuse the goal. A start's spheres
do clear the support, because a measured start must be free.
When the arm hides the table within 15 cm of the
fingertips, the support is found over the whole grid. A load is held while
the gripper is commanded closed and its jaws stopped between 3 mm and 7.5 cm
apart (the world inputs carry no jaw-stall outcome; jaws at the Panda's 8 cm
never closed). It is the set of occupied voxels connected to the depth between
the jaws above the support, within 15 cm of the pad slab and never through the
slab of a support below them: the whole object and none of the table under it.
A component that reaches that 15 cm bound is joined to the scene (a container
or shelf the object touches), so then only the depth between the jaws is held
and the rest stays world (`held_component: seed_only`, otherwise `whole`). So is
a component that reaches more than `held_overhang_m` (6 cm) past the jaws along
the closing axis and stands on that support in at least `held_rest_columns`
(40) grid columns, within two voxels of its top: a held object touching a cup
on a plate. Width alone does not say, because a bowl held by its rim reaches
13 cm past the jaws, so such a load stays `whole` once it leaves the support; the
move that lifts it plans with only its middle held.
The held voxels leave the world and at most 32 spheres fitted to them are
attached to `panda_hand`.

Every request loads its own world and restarts the planner's seeds, so no
earlier request changes its answer. The world itself (`world.sha256`) is exact,
and replay reads the recorded joint commands without a planner, so a sealed run
replays exactly, whichever planner recorded it.

The client (`MotionPlannerClient`, `ora/environment/controllers/motion_planner.py`)
starts the worker with `--world-model`, checks that readiness reports the same
model, records it in the planner binding (`world_model`, so a plan reference's
source identity changes with it) and refuses a request of the other mode
before sending it. In world mode every plan and IK request carries
`world_input: {path, sha256}`, the snapshot file the worker verifies and builds
from; a plan record adds `world` (`world_model`, `sha256` over the occupancy,
held spheres, parameters and input arrays, `inputs_sha256`, point counts,
`occupied_voxels`, `carved_voxels` for start and goal, `support` for start
and goal (`z_m`, `columns`, grid `level_index` and `top_index`, `window: local|grid`,
or null),
`goal_refusal` (null, or `reason: fingertips_below_support` with `fingertip_z_m`,
or `reason: load_below_support` with `load_z_m`, for at least eight seen points of
the load more than the tolerance below the support and over no lower surface;
either with `support_z_m`, `depth_m`, `tolerance_m` and a `message`; the heights are the arm's base frame, as
every height of the summary is; the cuRobo V2 worker's answer adds `in_world` with `frame: world` and the same heights
in the world frame of the policy's poses, and its `message` names the support at that world height, "(world frame)"),
`holding`, `finger_gap_m`,
`held_voxels`, `held_component`, `held_spheres`), an IK record adds `world: {inputs_sha256,
queries}` with one such summary per solved query, and a start in collision is
refused with `start_in_collision`. The plan summary in a receipt
(`ora.curobo-plan-summary.v1`) and the IK diagnostics evidence record the
`world_model`; the summary adds the record's `world`, which the policy
projection leaves out. Learning eligibility accepts a planner record of either
model.

In world mode the LIBERO session writes the snapshot before each planner call
(`ora/environment/backends/libero_pro/world_inputs.py`):
`curobo/world-inputs/action-NNNN.npz` before a move's plan (the action's
sequence number, as in `curobo/plans/`), `probe-NNNNNN.npz` once per
`probe_motion` request, shared by all its queries and variants, and
`reach-NNNNNN.npz` before a reach check. Each file (`ora.libero-world-input.v1`,
a NumPy `.npz` read without pickles) holds `<camera>_depth_m`,
`<camera>_intrinsic_cv`, `<camera>_cam2world_cv` and `<camera>_depth_range_m`
for `agentview` and `wrist`, exactly as the observation publishes them,
`base_pose_world_xyzw`, `joint_positions` (the request's), `gripper_finger_qpos`
and `gripper_directive`. The depth is the last executed step's rendering,
which is the current scene: every step, joint trajectory and reset refreshes
it. The plan summary records `world_input: {path, sha256}` with the path
relative to the run's native root.

### Motion planner contract

Backends ask their planner through one planner-neutral contract
(`ora/environment/controllers/motion_planner.py`), so the planner behind it can
be replaced. A plan request (`ora.motion-plan-request.v1`) carries `robot`
(`franka_panda`), `start: {joint_names, joint_positions}`, a `goal` that is
either `{tool_frame, pose_base_xyzw}` (the tool frame's pose in the robot base
frame, `xyzw` quaternion) or `{joint_positions}`, `world` (`{model: none}` or
`{model: public_depth_voxel_v1, input: {path, sha256}}`), `held_object`
(`{source: none}`; `{source: world}`, the load the world builder finds between
the jaws; or `{source: spheres, spheres_tool_frame}`) and `constraints`
(reserved; empty). An answer (`ora.motion-plan-answer.v1`) carries `status`
(`planned`, `refused` or `error`), `reason`, `trajectory` (`joint_names`,
`positions`, `velocities` or null, `dt_s`), `start_tool_pose_base_xyzw`,
`world` (`model`, `sha256`, `inputs_sha256` and the world's summary),
`planner` (`implementation`, and whatever identity the implementation adds),
`timing_s` (`total` and its parts), the request it
answers, and `detail`, the implementation's own evidence; a `goal_unreachable`
of a pose goal in a world has `detail.goal_diagnosis`, below. A refusal names one
reason: `goal_unreachable` (no IK solution in the world), `goal_in_collision`
(a joint goal touches the world or the robot), `goal_below_support` (the world
refused the goal before any solver ran), `start_in_collision`,
`start_outside_limits`, or `path_blocked` (a valid goal without a
collision-free trajectory, or with only a trajectory the path check below
refuses). An error is `planner_error` or `timeout` and says
nothing about the goal. A planner library's own status names appear only in
`detail`. IK requests and answers (`ora.motion-ik-request.v1`,
`ora.motion-ik-answer.v1`) chain queries: `from_index` seeds a query from an
earlier one's solution, and a query whose seed failed is skipped; a result adds
`refusal: goal_below_support` when the world refused its pose before a solve.

An implementation is a worker started as `python -m <module>` under its own
interpreter (`IMPLEMENTATIONS`); it speaks `ora.motion-planner-protocol.v1`,
one JSON object per line: `ready`, `plan`, `ik` and `close`. `prepare`
(`ora.motion-prepare-request.v1`: `robot` and one or two `worlds`, each
`{start, world}`; answered `ora.motion-prepare-answer.v1` with `status`
`prepared` or `error`, `prepared` and `timing_s`) has a worker build the
goal-independent scenes of those world inputs before a request needs them; it
answers no planning question, and a failed one is an answer, never a retired
worker. The client serializes its exchanges (one lock), so a backend may send
`prepare` from a thread of its own, and never starts a worker for it. The
cuRobo V2 worker keeps the last two scenes (`SCENES_KEPT`, one per arm of a
two-arm robot), each with the world inputs it was built from, so a request for
one arm no longer rebuilds the other's; a kept scene is the one the same input
builds again, so no answer changes. RoboDojo prepares both arms after each
observation (`backends/robodojo/shared_motion.prebuild`): the two world
snapshots are written on the simulator's thread when the observation is
published, and a thread of its own sends one `prepare` per arm while the policy
decides, the arm whose joints moved since the last observation first, so a
request that arrives before both are built waits for at most one; a newer
observation supersedes the arms not yet asked. Each prebuild is a line of
`native/motion-planner/prebuild.jsonl` (`observation_id`, `status` `prepared`,
`superseded` or `error`, `arms`: per arm asked `arm`, `status`, `reason` and
`timing_s`, or `error`, and `wall_s`); a prebuild that fails leaves every
request to build its scene as before.
`MotionPlannerClient` checks the source (a clean checkout at
`required_revision`, first on the worker's `PYTHONPATH`), records the binding
(`ora.motion-planner-binding.v1`: `implementation`, `revision`, `python` and
`python_sha256`, `root`, `device`, `robot`, `world_model` and the worker's
readiness identity), refuses a request of another robot or world model before
sending it, and retires a worker whose answer times out or breaks the
contract. `planner_client` builds this client from a `runtime.motion_planner`
block, which must name its `implementation`.
`motion_planner.planning_diagnostics` gives a
neutral answer the `ora.planning-diagnostics.v1` feedback, with the reason as
its `outcome` and the recovery lines, led by `refusal_message`, the
sentence that says why: `goal_refusal.message` or `goal_diagnosis.message`. Every
backend carries it to the model: LIBERO and RoboTwin in the arm's
`planner.planning_diagnostics`, RoboCasa and RoboLab in the receipt's
`planning_diagnostics`, RoboDojo as the refusal's own message. An implementation that keeps its
IK seed populations puts them in `detail.ik_attempts`, one record per IK
attempt (`status:
captured`, each seed's joints, position and rotation errors, `feasible`,
`converged`, `accepted`, `returned_success`, `outside_position_limits`, and
`candidate_counts` and `rejection_counts`); the diagnostics then carry the last
captured attempt's counts with `details_available: true` and `evidence_scope:
last_recorded_original_ik_attempt`.

### cuRobo V2 planner worker

`ora/environment/planners/curobo_v2/` implements the contract on cuRobo V2,
the cuRobo the RoboDojo SDK uses (`IMPLEMENTATIONS["curobo_v2"]`). The worker
runs under an interpreter with V2's dependencies (torch, warp, cuda-core),
with a clean V2 checkout first on `PYTHONPATH`. It holds one `MotionPlanner`
(IK, trajectory optimisation and graph search on one scene collision checker)
and one `RobotCollisionChecker` on that checker and the same robot model, so a
start or goal check, an IK query and a plan see the same world and the same
held load. The robot is V2's bundled `franka.yml` with ORA's changes
(`ora/environment/planners/curobo_v2/robots.py`) and 32 sphere slots on
`attached_object`. LIBERO simulates robosuite's `PandaGripper`, whose finger
collision mesh has the same vertices as the Franka description's but sits
6.5 mm closer to the hand: robosuite mounts the finger 0.0524 m from
`right_hand`, which lies 0.5 mm behind V2's `panda_hand` at the same joints,
where the Franka description mounts it 0.0584 m from `panda_hand`. So the
simulated finger ends 0.1057 m from `panda_hand`, 2.3 mm past the 0.1034 m
fingertip point, while V2's bundled finger spheres reach 0.1124 m. ORA keeps
V2's fit of the finger (two 11 mm spheres per finger) and moves it 6.5 mm
toward the hand; every sphere, the fingers' included, keeps the v0.7.8
planner's 4 mm buffer (V2 ships 0). The lowest finger point is then 0.1099 m
from `panda_hand`, 4.2 mm past the simulated fingertip, instead of 0.1164 m.
The rest of the robot equals v0.7.8's, but the fingers differ, so the world
built from the same snapshot (which removes the robot's own spheres from the
depth) could differ from the retired v0.7.8 worker's and has its own `sha256`. Every
answer's `planner` carries `robot_sha256` (the whole robot configuration) and
`collision_spheres_sha256` (the sphere set: every collision link's spheres,
the buffer and the held-load slots), and the readiness identity carries both
and ORA's changes. The settings are the v0.7.8 LIBERO planner's: 32 IK and
4 trajectory seeds, up to four attempts with graph seeding from the third,
5 mm / 0.05 convergence tolerances and 0.02 s interpolation. The world is one
voxel layer in a fixed-size cache, filled for each goal from
`depth_world.curobo_v2_grid`, and the world summary adds
`planner_grid_sha256`, the hash of the exported grid. CUDA graphs are on;
switching between pose and joint goals re-captures them, which the worker
enables as the SDK does.

The X5's joints 1 to 5 are +/-10 rad in `X5A.urdf` and in the simulated
`ARX.usd`, a placeholder that let IK answer a whole turn away. The planner keeps
them to `X5_JOINT_LIMITS_RAD` (`planners/curobo_v2/robots.py`, through
`position_limit_clip`): 3.7, 3.5, 3.9, 2.4 and 1.56 rad. Joints 1 to 4 take the
smallest half-range holding both the arm's rated range and every measured start,
but for joint 1's runs wound a whole turn on routes with no planner (476 of 468,584
records; holding them would keep every alias). Joint 5 stays under the stop the
simulated arm meets near 90 degrees either way, with self-collisions on: a plan past
it ends in a tracking stop. Joint 6 keeps the file's +/-3.14 rad. cuRobo clips both ends
alike, so joints 2 and 3 stay legal below zero, where the arm itself is not. This
binds the shared planner only: the simulator and the straight and joint routes keep
their own limits. A measured start past a limit gets `start_outside_limits`, and
RoboDojo's `lib/motion_plan` takes that move by the joint route (`plan_global`, as
with no planner), which can bring the arm back into range; that route may still
refuse its own move, and nothing has moved then. `return_home` keeps its own
screened joint line when the planner does not answer.

`yam_linear_4310` is one arm of the real I2RT YAM station for either arm
(`planners/curobo_v2/yam.py`): its `--robot-file` is i2rt's
`yam_station_linear_4310_d405.urdf`, around which ORA writes the V2 file with
`left_base` as the base link, the public TCP `left_tcp` as the tool frame, 190
fitted spheres on the arm's links, fingertips and wrist camera, and the fingers
locked open. Its `collision_spheres_sha256` with the 32 held-load slots is
`robots.YAM_SPHERE_SET_SHA256`. A request is in the moving arm's base frame and
names `left_joint1` to `left_joint6`, whichever arm moves, in `none` or
`public_depth_voxel_v1` with `PUBLIC_DEPTH_VOXEL_YAM_V1`. The YAM backend plans
on it with `runtime.motion_planner` (`ora play --motion-planner`; exactly
`benchmarks.yam.MOTION_PLANNER`, `public_depth_voxel_v1`, nothing else admitted),
off by default (`backends/yam/shared_planner.py`). Preflight starts the worker
once with i2rt's station URDF as the robot file (`shared_motion_planner`, whose
detail binds the interpreter and source the session reads from the run's
`preflight.json`; a direct session takes `CUROBO_V2_PYTHON` and `CUROBO_V2_ROOT`,
and with neither the station plans by itself). The session checks the planner's
forward kinematics against the station model at start (`fk_parity`, around both
arms' measured joints; a failure refuses the start) and writes
`native/motion-planner/binding.json`; every request, answer summary and world
input is under `native/motion-planner/` (`plans.jsonl`, `ik.jsonl`,
`checks.jsonl`, `world-inputs/`). A request carries the cell's calibration: the
moving arm's corrected base as its frame, the encoder zero offsets on every joint
sent and off every row answered, and a start at most 2 degrees past a limit
planned from 2 mrad inside it, the step in first in the path. The receipt's
`motion.planned_by` (in the sealed receipt only, `policy_receipt`) names per arm
`implementation` (`curobo_v2`, or `yam_mink_ik` with its `reason` when the planner
failed and the station planned), `route` (`plan` or `line`), `status`, `reason`,
`world_sha256`, `solve_s` and any `start_inside_limits`; a free-space move the
planner planned reports `motion_strategy: planned_joint_path`.

A measured start joint at most `START_LIMIT_SLACK_RAD` (5 mrad) past its limit is
the arm resting on its stop, not outside the range: the worker plans from
`START_LIMIT_INSET_RAD` (1 mrad) inside the limit and names each such joint in
`detail.start_inside_limits` (`joint`, `measured`, `planned_from`), so the path's
first row differs from the measured joints by at most 6 mrad. LIBERO's Panda
parks joints 2, 5, 6 and 7 on their stops (0 to 0.2 mrad past), which V2 read as
outside its limits: on 465 recorded LIBERO requests 29 were refused that way, some
runs with every later move refused; planned from inside, all 29 plan and 24 turn
the hand no more than they ask. A start further out still gets
`start_outside_limits`.

A plan is then read against what its goal carve took. The carve clears the hand box at the goal, back past the
wrist, and the planner sees that one grid from the path's first row, so a wrist, a wrist camera or an arm link
could pass through a wall the world no longer held and stand in it at the goal. `path_clearance.py` measures each
row's spheres, the attached load's too, against the scene with no goal carve; a path deeper than one voxel
(`TOLERANCE_M`, 1 cm) is measured again in `FingerScene`, whose carve stops where the fingers begin
(`FINGERS_FROM_M`, 0.05 m along the approach) and whose joint goal clears only the fingers' spheres. A path that
passes either is the plan as the planner gave it. One that fails both is planned again in the finger world; when
no plan stays clear the answer is `goal_unreachable` with the usual diagnosis, `goal_in_collision`, or
`path_blocked` with `detail.goal_diagnosis` of cause `path_in_collision` (`link`, `part`, `penetration_m`,
`before_goal_m`, and a `message` naming the point "in world coordinates" and what stands there: the other arm, the
supporting surface, or what the depth shows). `detail.path_clearance` and the world summary's `path_clearance` keep `status`
(`clear`, `replanned`, `blocked`), `clearance_m`, `row`, `link`, `radius_m` and `point_base_m`; `timing_s` adds
`path_clearance`. A contact path's check (`ora.motion-check-request.v1`) uses the same finger world. An IK query
solves in the goal's whole carve and then reads its solution as the one row a plan to the pose ends on: one deeper
than `TOLERANCE_M` in the scene with no goal carve and in the finger world is solved again in the finger world, and
answered unsolved (a reach check's `ik_failed`) when none there is clear, so a reach check holds what a plan to the
pose would; the query's world summary is the world the answer holds for and keeps `path_clearance` (`clear`,
`replanned`, `blocked`). On a desk play scene the IK alone had answered solved for two poses whose plans were
refused, the hand 4.9 cm inside a stapler's depth that only the carve took
(`tests/data/planners/desk-stapler-reach.npz`). The check knows only what the depth shows: rows near the start stay
inside the start carve, and an object out of sight is not in any of these worlds.

A pose plan is then read by the pacing's wrist rule (`motion_pace.check_unwind`, on the pacing's joint grid with the
tool frame's kinematics) and compared with straight joint lines from the start, spaced `JOINT_GRID_SPACING_RAD` apart,
that reach the same pose: `same_goal`, to the plan's own last row, when the plan turns the hand more than
`MAX_UNREQUESTED_TURN_DEG` (45 degrees) beyond its start-to-goal turn; `alias`, for every plan whose goal has a joint
more than half a turn from its start with a 2 pi equivalent inside that joint's limits (each such joint moved to its
equivalent nearest the start: the same link poses; only the ALOHA's +/-4.2 rad leave room for one); `ik_pool`, when the
plan turns past the rule, to the other IK solutions of the pose in `detail.ik_attempts` (the converged, feasible
seeds inside the limits, each at its nearest equivalent, counted once, the `POOL_LINES` (8) nearest the start). The
candidates and, when the rule accepts it, the plan (`planned`) are ranked within the rule first, then by the smallest
path turn, the plan winning a tie, rather than in the order the planner found them. The first line in that order
that passes the planner's own checks (every row inside the joint
limits and clear of the robot itself and of the goal's loaded world, by the start and goal check; the whole line, the
attached load included, within `TOLERANCE_M` of the world a joint path to that goal is read in, `FingerScene` as a
contact path's check reads it) is the answer's trajectory, at rest at both ends and at the plan's `dt_s`. A line the
rule refuses is never answered. `detail.wrist_detour` (and the world summary's `wrist_detour`) keeps `status`
(`replaced`, `kept`), `chosen` (the source answered, `planned`, or null), `max_unrequested_turn_deg`, `planned` (its
`path_turn_deg` and `requested_turn_deg`) and `candidates`, each with `source`, its turns, `goal_joints` for a goal
other than the plan's, and `outcome` (`chosen`; `unwinds`; `blocked` with `blocked`: `check` `planner` with the row
and its check costs, or `world` with the path clearance; `ranked_lower`, never checked); `timing_s` adds `wrist_detour` for every pose plan. A plan
within the rule with no equivalent goal nearer leaves no record. Joint goals are never read this way.

Per goal the worker loads the goal's world, attaches the held spheres, resets
every seed, and refuses in this order: the world's refusal
(`goal_below_support`, no solver runs; never in the worker's `kitchen` scene,
`ORA_PLANNER_SCENE`, which `ready` names), a start outside the joint limits
(`start_outside_limits`), a start touching the world or the robot itself
(`start_in_collision`), a joint goal outside the limits (`goal_unreachable`) or
in collision (`goal_in_collision`), a pose goal no IK attempt solved
(`goal_unreachable`), anything else not planned (`path_blocked`). A
`goal_unreachable` in a world then solves IK once more with nothing in the way and
keeps why in `detail.goal_diagnosis` (`cause`, a `message` and, where it applies,
`link`, `part` and `penetration_m`): `out_of_reach` when that solve fails too;
`robot_in_collision` or `held_object_in_collision` when the goal's world holds the
robot's spheres at that solution, or the load, deepest, and the `message` names what
stands there as `path_in_collision`'s does; `self_collision` when the
seeds of the last IK attempt that reached the pose were stopped by the arm itself
and not the world (up to `goal_nearby.BLOCKING_SEEDS` checked);
`no_clear_solution_found` otherwise. It also says what would work. `nearby` is the
smallest tilt of the approach that plans in the goal's own world, kept at the grip
point between the fingertips: `tilt_deg` from `goal_nearby.TILTS_DEG` (10, 20, 30, 45)
in `TILT_DIRECTIONS` (8) directions, 32 IK solves at most, and only a tilt with a joint
solution is planned, one attempt each, at most `NEARBY_PLANS` (2) of them; `nearby` is
null when none planned. `other_arm_reaches` says on a two-arm robot with an empty hand
whether the other arm reaches the pose with nothing in the way, null otherwise.
`message` joins all of it, the approach in world coordinates, and `timing_s` keeps the
search as `goal_diagnosis`. The search is finite: it reports what it found, never that
nothing exists. The start
and goal checks use zero activation distance: a configuration is refused only
when a sphere overlaps. Each IK query is solved in the world a plan to its pose
would use, seeded from the start joints or a chained query's solution. The
answer's `detail` keeps the start and goal check costs, the IK successes per
planning attempt and, in `ik_attempts`, each attempt's seed population: the
32 seeds the solver's own result extraction ranked (the starting seeds when it
exits early on them, `populations_evaluated: 1`, else the optimised ones),
copied from that extraction without another solve. Rotation errors are V2's
orientation metric (`rotation_error_units: curobo_v2_orientation_metric`).

### Timing a path: retime

Timing is a service of its own on the same protocol
(`ora.motion-planner-protocol.v2`, which adds the `retime` command to `ready`,
`plan`, `ik` and `close`). `TimingClient` starts
`python -m ora.environment.planners.timing.worker` under `ORA_TIMING_PYTHON`,
with this checkout alone on `PYTHONPATH`; the worker answers `ready`, `retime`
and `close`. `motion_pace.pace_path` is its usual caller; see
[Motion speed](motion-speed.md#the-timing-worker) for what it does.

`ora.motion-retime-request.v1` is closed:

| Field | Meaning |
|---|---|
| `joint_names` | The row order |
| `path.joint_positions`, `path.tool_poses_xyzw` | The path, start first, at most 0.02 rad apart and at most 20000 rows, and the controlled point's pose at every row (the caller's forward kinematics) |
| `speed`, `slow_approach` | The request's wire form or `null` (the default), and whether the slow final segment applies to this path |
| `limits` | `joint_velocity_rad_s` and `joint_acceleration_rad_s2` (the backend's ceiling per joint, or `null`), `joint_jerk_rad_s3` and `jerk_source` |
| `tick_s`, `budget_ticks`, `cut_to_budget` | The control tick, what one action may spend, and whether to cut rather than refuse |
| `wrist` | `joint_index` and `limits_rad` |

`ora.motion-retime-answer.v1` has `status` `timed`, `refused` or `error`.
`reason` is `paced_path_wrist_unwind`, `paced_motion_budget` or
`timing_infeasible` (with `detail.refusal`, its `message` and `constraints`), or
`timeout` or `planner_error` (with `detail.error`). A timed answer holds
`trajectory` (`joint_names`, `positions`, `velocities`, `accelerations`, `dt_s`
equal to the tick; rows from the start at rest to the goal at rest) and `report`
(`ticks`, `duration_s`, `fractions`, `slow_from_tick`, `fraction_of_move`,
`needed_ticks`, `part_segments`, `peaks`, `wrist`). `planner` names
`toppra_ruckig`, the library `versions` and the `settings`; `detail` holds
`jerk_source`, `attempts`, `acceleration_scale`, `ruckig_extended_segments`,
`max_deviation_m`, `gridpoints`, `pieces`, `raw_duration_s`, `stretch`,
`solve_ms`, `ruckig_ms`, `spline_deviation_radians` and `verification`.

`ora.motion-plan-request.v2` is v1 plus `timing`, `null` or the fields above
without the path, plus `controlled_point` (`tool_frame` and `offset_xyzw`). It is
answered by `ora.motion-plan-answer.v2`, v1 plus `timing`: one call for the
backend, two workers behind it. V2's interpreter holds no timing library, so the
cuRobo V2 worker answers the planner's trajectory and keeps
`detail.timing_path`, the path on the joint grid with the controlled point's pose
from V2's kinematics; `MotionPlannerClient`, given a `TimingClient`, has it timed.
A planned answer's `trajectory` is then the timed rows, with the planner's in
`detail.planned_trajectory` and the retime answer in `timing`; a timing refusal
makes the plan `refused` with the timing's reason. v1 requests and answers stay
valid, so sealed runs still read. No backend sends v2 yet: LIBERO times in the
worker through `pace_path`, with MuJoCo's kinematics of the grip site, and RoboDojo
through the `kinematics.pace_path` query.

### LIBERO behind a neutral planner

`runtime.motion_planner.implementation` selects LIBERO's planner. The default
baseline (`native_full_explicit_motion_direct_v4`, since 2026-10-31) selects
`curobo_v2`, the cuRobo V2 worker, under `CUROBO_V2_PYTHON` with
`CUROBO_V2_ROOT` (for now the RoboDojo runtime's interpreter and the cuRobo
checkout it imports):

```json
"motion_planner": {
  "implementation": "curobo_v2", "python_env": "CUROBO_V2_PYTHON", "root_env": "CUROBO_V2_ROOT",
  "required_revision": "d17b54ce32cba095c0b000c4c58777075d11de0e",
  "request_timeout_s": 180, "world_model": "public_depth_voxel_v1",
  "timing": {"implementation": "toppra_ruckig", "python_env": "ORA_TIMING_PYTHON"}
}
```

A `motion_planner` block without the field is refused: the v0.7.8 worker was
retired on 2026-11-01. Runs it recorded still replay, because replay needs no
planner. Preflight starts and stops
the selected worker (check `curobo_motion_planner`),
and the run's `motion_planner_binding` is the neutral binding.

RoboTwin's default baseline carries the same block with `robot: aloha_agilex`,
under the same `CUROBO_V2_PYTHON` and `CUROBO_V2_ROOT`. The worker's config has no
field for it (its session fields feed the seed scans' hash), so the block reaches
the worker through the run's own `preflight.json`: check `shared_motion_planner`
resolves the client without starting it, and its detail is the binding. The
session then starts the planner once per process, refuses an interpreter whose
sha256 changed, and refuses the planner when its forward kinematics of both arms'
measured joints misses the simulator's link6 (`fk_parity`: 2 mm, 0.02 rad;
`backends/robotwin/shared_motion.py`). One worker plans both arms, whose chains are
the same joint for joint, each request in the moving arm's base frame; a start wound
past a full turn is wrapped into +/-pi and the answer shifted back. Its evidence is
under `native/motion-planner/`: `binding.json` (the neutral binding and the parity
report), `plans.jsonl` and `ik.jsonl` (status, reason, world and timings of each
request), `world-inputs/` (one snapshot per physical state and arm) and
`worker.stderr.log`. A refusal stands, but for `start_in_collision` of the arm
against itself alone: a measured start says nothing about the goal, so that plan
goes to the vendored planner too. ALOHA's `fl_link3` and `fl_link5` are checked
from the sphere gap of -7.4 mm where the simulator's links stopped each other
(`ALOHA_FOLD_BUFFER_M`), so a measured arm pressed past it starts there.
An error falls back to the vendored planner,
with `fallback: true` in `plans.jsonl`, so a failing worker costs time, not the
move. With no planner bound (a direct session, a qualification, the expert, the
seed scans) the vendored planner plans as before. A planner uses about 1.2 GiB of
GPU memory beside the simulator (measured 2026-11-01). The pinocchio
`validate_collision` of the paced knots stays: it is the only check of two arms
moving together, and it re-checks every single-arm plan against the other arm.

`runtime.motion_planner.timing` selects how a global route is timed. The default
baseline sets `{"implementation": "toppra_ruckig", "python_env": "ORA_TIMING_PYTHON"}`
inside its `motion_planner` block. Preflight starts and stops that worker (check
`motion_timing_worker`, whose detail is the timing binding), and
`supervisor_binding` hands the interpreter it started to the isolated worker in
the controller binding (`motion_timing`: `python`, `python_sha256`), because that
worker's environment is allowlisted and holds no `ORA_TIMING_PYTHON`. The session
starts the same interpreter, refuses one whose sha256 changed after preflight, and
`motion_timing_binding` names it (`ora.motion-timing-binding.v1`: the interpreter
and its sha256, the worker's library versions and settings). Without the field,
`motion_timing.configure(None)` keeps
`motion_pace`'s own timing and the binding says `motion_pace`.

RoboTwin's and RoboCasa365's default instances set the same value as
`runtime.motion_timing`, with the same preflight check. Their workers are built
from fixed config fields that cannot carry the Supervisor's binding, so each takes
the check's `python` and `python_sha256` from the run's `preflight.json`, beside its
`native/` (`motion_timing.bind_preflight`), writes the started worker's binding to
`native/timing/binding.json`, and refuses a changed sha256. A run whose preflight has
no such check takes `motion_pace`'s timing; a
session with no preflight record falls to `ORA_TIMING_PYTHON`, which the isolated
worker does not see, so it too runs `motion_pace`'s.
`NeutralPlannerPort` (`ora/environment/backends/libero_pro/planner_port.py`)
keeps the session's requests and records: each answer becomes an
`ora.libero-plan-record.v1` with the fields the session
reads (`status`, `reachable`, `planner_status`, `valid_query`, `world_model`,
`planning_time_s`, the start pose, the trajectory, `request`, `world` as the
world's summary, `planning_diagnostics`), the answer's `detail.ik_attempts` as
`planning_diagnostic_evidence` (`ora.libero-ik-diagnostics.v1`, which the
motion probe's IK candidate tally reads) and
the whole neutral answer under `answer`. `planner_status` is `success` or the neutral reason, and so is the
receipt's `planning_failure_reason`; the policy projection maps
`start_in_collision` and `start_outside_limits` to `invalid_start_state` and
`timeout` to `planner_error`, and the planning
diagnostics' `outcome` is the reason. The record is the canonical plan
artifact (`curobo/plans/action-NNNN.json`, `curobo/probes/`); each one names the planner that made it, `planner_implementation` (`curobo_v2`; a record of the retired worker says `curobo_v0.7.8` or lacks the field), evidence only and never projected. IK answers
become `ora.libero-ik-record.v1`; a robot-only reach check answers `ik_model:
libero_reach_ik_robot_only_v1` with the robot-only claim. The motion probe's
refusal message reads "the global planner admitted no trajectory".

LIBERO's tool files (the layer `delivery_trim_v2/libero-help.json` until its
surface was written out as files) and the
`LIBERO_REACH_ROBOT_ONLY`, `LIBERO_REACH_UNSOLVED_ROBOT_ONLY` and
`LIBERO_PLANNING_NOTE*` trim entries keep the texts the retired worker wrote: a
sealed run's recorded tool definitions still state them, and the help text is
model-visible, so it did not change with the worker.

Comparison on recorded requests (2026-10-31, H200), with ORA's Franka model:
the 266 recorded plan requests of 20 LIBERO runs, replanned on V2 against the
v0.7.8 world-mode results of the first comparison, agree on planned or refused
in 263. The three that differ are goals v0.7.8 refused and V2 plans (two IK
failures, one trajectory-optimisation failure carrying a held load), the same
three as with V2's bundled fingers. With the bundled fingers, which reach
13 mm past the fingertip point, V2 had also refused five goals near a surface
that v0.7.8 planned; with LIBERO's finger placement it plans all five. Four
requests v0.7.8 refused before IK under the earlier support rule (fingertips
0.1 to 0.8 cm below the support, refused at any depth then) are within today's
1 cm tolerance; V2 refuses them as `goal_unreachable`. Table approaches at
round 1's spots in five snapshots: V2 plans every goal with the fingertip
point 1, 2 or 5 cm above the table (v0.7.8 too) and refuses every goal at or
below the table in four snapshots (3 and 2 cm below by the world's refusal,
1 cm below and touching by IK). In the fifth the robot holds a load, and the
goal carve, which clears the load's spheres swept over the approach standoff,
also clears the table under the spot, so V2 plans 1 cm below and touching
there; v0.7.8 plans touching there as well. Paths: by an exact
sphere-to-voxel-cube test, 6 of V2's 219 plans touch an occupied voxel, by at
most 1.1 mm, within what an ESDF taken between voxel centres can miss at a
voxel corner (3.7 mm), and none by a held sphere; by the centre-value metric 3
(2.4 mm at most). Every plan answer kept its IK seed populations (399 attempts,
all captured), and the diagnostics of 259 answers carry candidate counts. A
V2 world-mode answer repeats bit for bit: all 266 requests planned twice in a
row, and the whole set in reverse order, gave identical statuses, trajectories
and world hashes. Per request V2 took 0.72 s (p50; 0.96 s p90), of which
0.06 s planning and the rest building the world, against 2.4 s (1.7 s
planning) for v0.7.8. The worlds differ from those built with V2's bundled
fingers in 55 of 266 requests, since the planner's own spheres are removed
from the depth.

### LIBERO paced routes

Under motion profile v4 a planned `free_space` move's sparse receipt carries
`arms.right.route_choice` (`controllers/osc_transit.route_choice`): `route`
(`joint_path` or `osc_straight_line`), `reason` (`planner_path_clear_and_straight`,
or why the joint path ran: `not_free_space`, `slow_approaching_requested`,
`previewed_plan_runs_as_planned`, `turn_above_osc_limit`,
`planner_path_unavailable`, `planner_path_not_straight`), `rotation_rad`,
`path_deviation_m` (the planner's grip-site path's largest distance from the
straight line, when measured), `limits` and `collision_checked`
(`planner_joint_path`, or `planner_gripper_path_only` on the OSC line). A move
that ran as the line adds `planner` (the plan's evidence, its canonical artifact
under `curobo/plans/`) and `line` (`length_m`, `turn_rad`, `planned_ticks`,
`settle_ticks_budget`); its `executor` is `libero_robosuite_osc_pose`, its
segment the local OSC receipt of a runtime `move_pose`, its `motion` the line's
`planned_ticks` with `route: osc_straight_line`, and its controls the
`libero_osc_pose` entries of the controller replay. A dry run of such a move
answers `route: osc_line` with the line and its ticks. Every LIBERO receipt
carries `native_actions`, its `steps_used` in control ticks (a two-segment move
sums both), which the episode's native-action allowance counts down.

Every global planner route on LIBERO, whichever planner answered it, is paced
at the grip site before it runs ([Motion speed and wrist checks](motion-speed.md)).
The hybrid receipt (`ora.libero-pro-hybrid-motion-receipt.v2`) gains `pacing`:
`speed`, `wrist`, `planned_ticks` and `duration_s`, or for a refused route
`speed` and `refused` (`code`, `message` and the refusal's constraints). The
sparse receipt (`ora.libero-sparse-motion-receipt.v1`) carries the move's
`motion`: the global segment's `pacing` once it ran or refused, otherwise the
request's caps. The joint-trajectory command and its execution evidence carry
`timed: true` and `dt_s: 0.05`; the knots, not the planner's raw rows, are the
executed `positions`, and the replay recorder's metadata adds `paced_ticks`.
The execution evidence (`tracking/joint-action-NNNN.json`) also carries
`controller_gains`, the JOINT_POSITION gains the segment ran with
(`hybrid_motion.libero_joint_controller`: `mode`, `kp`, `damping_ratio`,
`output_max_rad`, `lead_s`; `null` under motion profile v1 or v2, which run
RoboSuite's defaults), and `torque` (`source`
`robosuite_joint_position_unclipped`, `peak_abs_nm` and `limit_nm` per joint,
`substeps`, `clipped_substeps`: the controller's torque before RoboSuite clips it
to the actuator range, on every physics substep of the segment; `null` when the
plant exposes no controller). Its `terminal_settle` adds `rest_step_rad` (v3's
`terminal_settle_rest_step_rad`, `null` before v3), `settled_by`
(`joint_tolerance`, `at_rest_in_local_bound` or `null` when the settle did not
end) and `trace`, one entry per check (`joint_error_max_abs_rad`, `position_m`,
`orientation_rad` and `last_tick_step_rad`, the largest joint step of the tick
before it). None of these reaches the receipt. Under v3 the replay
recorder's metadata adds `joint_controller`, the same gains, and a replay loads
exactly them (`harness_replay_controller_mode`); an entry without it, every run
bound to v1 or v2, replays on RoboSuite's defaults as it ran
([Motion speed and wrist checks](motion-speed.md#the-joint-executor)).
The canonical plan artifact keeps the planner's own trajectory. A refusal by the
pacing is `plan_rejected` at stage `pacing` (`paced_path_wrist_unwind`,
`paced_motion_budget`), with no controller write. A wrist refusal of a pose
move with the jaws open retries its jaw twin
([Motion speed and wrist checks](motion-speed.md#the-wrist)): the twin's plan is
kept as `curobo/plans/action-NNNN-jaw-twin.json` beside the refused one; a
twin that runs is the segment's `planner` record and the pose its
`feedback.endpoint_error` is measured from, its `pacing.wrist` (and the sparse
receipt's `motion.wrist`) carries `jaw_flip_retry`, and the sparse receipt's
settle, local approach and `pose_error` read the target's twin; a twin refused
too leaves the refusal with `pacing.refused.jaw_flip_retry`. A move_pose that names a
speed carries it in its action (`speed`), so the request is in the recorded
action and the receipt's `arguments`; runs sealed before keep their receipts and
replay their recorded controller commands unchanged.

### RoboTwin, RoboCasa365 and RoboLab pacing

The sparse receipts of these backends carry the move's `motion` the same way
([Motion speed and wrist checks](motion-speed.md)): on a paced route `speed`,
`wrist`, `planned_ticks` and `duration_s`; for a route the pacing refused `speed`
and `refused` (`code`, `message` and the refusal's constraints; on RoboTwin also
`arm`); otherwise `speed` alone, the request and its caps. RoboTwin's `wrist` holds
one report per moved arm, and its `motion.timing` one entry per moved arm (the sealed
receipt only: each observation embeds `policy_receipt` of the receipt, without it).
A refusal by the pacing is `plan_rejected` at stage
`pacing`, with no controller write; on RoboTwin a two-arm plan whose paced knots
break inter-arm clearance is `plan_rejected` at `plan_validation`
(`inter_arm_collision`). On RoboCasa365 and RoboLab a wrist refusal of a pose
move with the jaws open retries its jaw twin as on LIBERO: a twin that runs is
the receipt's target pose (`arms.right.pose_error` reads it) and its
`motion.wrist.jaw_flip_retry` says so; a twin refused too leaves the refusal with
`motion.refused.jaw_flip_retry`. RoboTwin does not retry.

On RoboTwin the paced knots are the transaction's stages: the recorded
`issued_{arm}_arm__*` arrays, and with them the controller replay, hold what ran,
one row per physics step, so replay stays bit-exact. The motion-plan artifact
keeps the planner's rows as `planned_*` and adds `paced_{arm}_joint__position`,
and its `validation.pacing` is the receipt's `motion`. A local stage under a
requested speed issues several rows per servo iteration, and
`iteration_wait_steps` then has one entry per row. RoboCasa365's and RoboLab's
hold what reached the simulator rather than the plan (see Replay program below).

### Replay program

A sealed RoboTwin, LIBERO-Pro, RoboCasa365, RoboDojo or RoboLab run carries `replay-program/`, written by `seal` just before its inventory from evidence the run already holds, without a simulator (`ora/environment/replay_program.py`). `program.json` (`ora.replay-program.v1`) lists one step per agent action in order: `controls` runs the action's recorded controls (for a RoboTwin move stopped by a guard after its physics step, the prefix that ran; for RoboCasa365 one action vector per control step), `hold` steps the scene a recorded number of times with no new target (a RoboTwin `wait`), `ticks` feeds a RoboDojo action's recorded native ticks, `skip` is an action that moved nothing, and `unreplayable` marks a record the replay cannot reproduce (the program's `status` is then `unreplayable` and `problems` says why). Each step carries the receipt's end pose per arm (`expect`; on RoboCasa365 also `base`, the receipt's `base_after`, and `state_before` and `state_after`, the recorded hashes of the whole scene), and the program carries the verdict (`expect`: the official success and the first successful action for RoboTwin, the first terminating action for LIBERO-Pro and RoboLab, the first successful action and control step for RoboCasa365, the terminal state and native action count for RoboDojo), the task, seed, source commit and the record it was built from. `program.py` is the same steps as Python, one line per action (`program.controls(3)`, `program.hold(6, steps=120)`, ...). A run whose program cannot be written records `replay-program/unavailable.json` (`ora.replay-program-unavailable.v1`) and seals anyway; Synthetic and other backends get nothing.

**RoboCasa365's record.** `native/controller-replay/` (`ora.controller-replay-manifest.v2`, backend `robocasa`, controller `robocasa_composite_action`) holds one entry per action: `action-NNNN.npz` with `commands`, float64 `[control step, 13]`, each vector exactly as `OmronIO.step` sent it to robosuite's composite controller (arm, gripper, base, torso, mode), so the arm's paced path, the torso, the gripper and the `move_base` servo are all one thing to a replay; `physics_steps` counts control steps, 25 MuJoCo steps each; an action that sent none has no artifact and `replayable: false`. `metadata.state_sha256.before` and `.after` hash the scene's `[time, qpos, qvel]` as float64, objects and fixtures with the robot. `region-order-round-NNNN.json` (`ora.robocasa-region-order.v1`) keeps the order each counter listed its regions in while the round's kitchen was built, because the seed does not fix a kitchen ([RoboCasa365 replay](robocasa-robolab-backends.md#replay)). Recording copies each vector before the step and writes after the action; it moves no random state and adds no simulator call, and the record is not policy-visible.

A RoboLab run records its controls under `native/controller-replay/` in the layout of RoboTwin and LIBERO-Pro (`manifest.json`, `events.jsonl`, `action-NNNN.npz`; backend `robolab`, controller `robolab_joint_targets`; `ora/environment/backends/robolab/control_record.py`): for an action of T native actions, `native_actions` (T, 8) is the float32 tensor `env.step` received (flange position, quaternion wxyz, finger action) and `joint_targets` (T, 8, J) every joint's position target at each physics step as it entered PhysX, which is what the differential-IK term computed from the measured state, or a planned path's row. An action that moved nothing has no arrays; the reset's two held native actions are not recorded, since a replay runs the same reset. The record reaches no policy. A write that fails ends the record and leaves `recording-failed.json` beside it; the run goes on and seals with `unavailable.json`.

A RoboCasa365 or RoboLab run sealed before its record existed holds receipts only, a program is never rebuilt from them, and it stays without one: `ora replay RUN --check OUT` and `--scene` refuse it and `--program DIR` writes `DIR/unavailable.json` and exits 1.

`ora replay RUN --check OUT` (or `python replay-program/program.py --check OUT`) re-executes the program and writes `OUT/replay-program-check.json` (`ora.replay-program-check.v1`); nothing is written into the run. RoboTwin and LIBERO-Pro run in the plan's simulator interpreter through the Viewer replay's per-action code (`ora/environment/replay_worker.py`; job `ora.replay-program-job.v1` and result `ora.replay-program-result.v1` in `replay_program_worker.py`), with the caller's `CUDA_VISIBLE_DEVICES` taking precedence over the recording's; the check fails when any action's end pose differs from its receipt in any bit or the replay's verdict differs from the run's. RoboCasa365 runs the same way through the run's own session without a planner: `OmronIO.apply` and the session's success latch for each recorded vector, each round's kitchen from the seed its reset recorded and the region order the run kept. Its check also fails on the base's end pose and on either scene hash, so an object that moved fails it even where the robot's pose agrees. RoboLab runs in the environment worker a run uses (`factory.build_robolab_worker_session`, whose `start` runs the job the worker configuration names in `replay_program_job`), with the plan's runtime less its planner and on the caller's `CUDA_VISIBLE_DEVICES`, which needs `ROBOLAB_PYTHON`, `ROBOLAB_ROOT` and `ROBOLAB_GRAPHICS_RUNTIME`; each recorded native action sends its tensor and writes each physics step's recorded joint targets over the buffer before it reaches PhysX, so no IK or executor decides anything again, and the session's own bookkeeping latches the verdict. RoboDojo runs the qualification control replay ([RoboDojo motion qualification](robodojo-motion-qualification.md)) on `--resource-profile` with the run's host marker and compares its terminal state, success and native action count. `ora replay RUN --program DIR` writes the program of any run, sealed or not, to a directory outside it. A `program.py` edited away from `program.json` runs without receipt expectations (RoboTwin, LIBERO-Pro, RoboCasa365 and RoboLab); a RoboDojo program runs only as recorded.

`ora replay RUN --scene [--scene-traces DIR] [--resource-profile P]` runs that check on a RoboTwin, LIBERO-Pro, RoboCasa365, RoboDojo or RoboLab run and keeps the scene of the same re-execution (`ora/environment/scene_trace.py`; RoboTwin `backends/robotwin/scene_trace.py`, LIBERO-Pro and RoboCasa365 `mujoco_scene_trace.py` and `backends/{libero_pro,robocasa}/scene_trace.py`, job field `scene_trace`; RoboDojo `backends/robodojo/isaac_scene.py` and `scene_trace.py`, the control replay's `--scene-trace DIR`, on the `--resource-profile` slot `--check` needs; RoboLab `isaac_scene.py` and `backends/robolab/scene_trace.py`, job field `scene_trace`, in the environment worker): `<experiment>/scene-trace/<run_id>/`, or `DIR/<run_id>/`, outside the run and never policy-visible. `manifest.json` (`ora.scene-trace.v1`) names the run, `source: replay_program`, the clock (`sapien_step_since_round_reset`, `mujoco_step_since_round_reset`, `physx_step_since_round_reset` or `physx_step_since_round_start`, its rate and stride), the bodies (`id`, `name`, `kind` `object`, `robot`, `static` or `backdrop`, `mesh`, `faces`), each action's span and, on LIBERO-Pro, RoboCasa365 and RoboLab, `tcp`: per arm the name of the body and the fixed pose of the TCP in that body's frame (`body`, `offset_xyz_xyzw`), which is the receipts' TCP; `poses.bin` is little-endian float32 `[row, body, 7]` (xyz, quaternion xyzw, world frame, NaN while a body is not in the round's scene); `steps.jsonl` has one line per row (`index`, `round`, `action_sequence`, `action_id`, `native_action`, `physics_step`, `sim_time_s`); `meshes/<id>.glb` is each body's visual geometry in metres in its own frame, with vertex colours and at most 20000 faces; `replay-program-check.json` is the check it passed. Rows are the round's first state, every `clock.stride`-th physics step since the round reset (about 50 Hz: every fifth SAPIEN step, every tenth MuJoCo step; RoboDojo every tenth PhysX step, one row per native action at 25 Hz, its `native_action` column still counting the action's physics steps; RoboCasa365 every 25th MuJoCo step, one row per control step at 20 Hz; RoboLab every eighth PhysX step, one row per native action at 15 Hz) and each action's last step. A MuJoCo row reads `data.xpos` right after `mj_step`, the same arrays a receipt and a camera read at that step, which hold the `qpos` of the step before. RoboCasa365 steps through robosuite's lite physics (`MjSim.step1`, `step2`), so the replay counts its control steps instead of a step tap, and reads each row when `environment.step` returns; only a body past 2 m in two directions (walls, floors) is `backdrop` there, so a long counter stays `static`. RoboLab's clock starts at the state the policy first sees, after the reset's two held native actions, where its receipts' steps start: it has no execution capture, and its trace is on the receipts' clock. An Isaac body is a prim with an enabled rigid body, its pose read from one PhysX rigid-body view, or an instance the layout gives no rigid body (a table, a fixed collider, the room), posed once from USD; on RoboLab that is what the env root and the task scene draw with no rigid body under it, its poses are in the robot root's frame, and a body's cubes and cylinders are tessellated. A deformable or fluid is not in the trace. The trace is published by one rename and only when the check passes and every action spans the steps the execution capture recorded (RoboLab: its receipts' native actions, accumulated in action order); otherwise the command exits 1 and the report stays in a work directory beside the traces.

### Incident replay

`ora replay RUN --incident N --report OUT` (`ora/orchestrator/evaluation/incident_replay.py`) replays a sealed run up to action `N` and runs `N` again, with no model and no Provider, to see what this checkout does with a move that once went wrong. `N` is the receipt's number (`native/receipts/action-NNNN.json`). Everything is written under `OUT`, outside the run; `OUT/incident-replay.json` is `ora.incident-replay.v1`.

| | RoboTwin, LIBERO-Pro | RoboDojo |
|---|---|---|
| Runs in | `ora/environment/incident_replay_worker.py`, under the plan's interpreter | `ora toolkit replay --live --execute-action` |
| Prefix | The replay program's steps through the Viewer replay's per-action code; every action's end pose compared with its receipt bit for bit | The recorded native ticks; every physics line compared with the original journal |
| `recorded` branch | The incident's recorded controls | None; the sealed receipt stands for it |
| `current` branch | The receipt's `arguments` as an `ora-motion-action.v3` through `session.execute` | The parent tool call's recorded arguments through today's tool (minus `native_action_budget`, which that tool refuses), on the default baseline's stack |
| Needs | `--stack-run RUN`: a run sealed on this checkout, such as a merge smoke | A toolkit run (`native/toolkit-io`), `--resource-profile` on the run's own host, a clean checkout |

A run's plan pins the motion stack it ran on, so the `current` branch takes its `runtime` from `--stack-run` and keeps only the episode from the source run (task, suite, seed, workspace bounds, public context). A stack run on another simulator revision is refused, because that prefix could not be reproduced. The worker config comes from `build_environment_session`, the path a live episode takes. The report names `stack_commit`; a stack run of an older commit tests that commit's motion stack.

With `--preview` a RoboDojo incident runs nothing: it asks the action's dry run at the state it ran from, on the same stack, and writes `OUT/preview.png` and the record under `preview` in the report.

A RoboDojo incident takes no stack run. Its `current` branch runs on what a fresh run of this checkout uses (`robodojo_stack`): the default baseline's tool surface, `runtime.motion_planner` and `runtime.motion_timing`, over a copy of the source plan's runtime, whatever that run pinned. The sealed plan is not changed. The recorded tool configuration's `motion` section, `surface_id` and execution flags are replaced to match, because a tool reads its routes from them, and the worker gets the timing record a preflight would leave (`live-worker/preflight.json`), because its isolated environment holds no timing interpreter. The recorded allowance and the rest of the tool configuration stay the source run's. The report's `stack` names what the action was given and what the source run was sealed on; its branch's `motion` says what the worker moved with, from its own records: `tool_surface`, `planner` (null when none started), `plans` (one row per planner call, empty when the tool's route never asked it) and `timing`. `ora toolkit replay --live` keeps the run's own stack, and `ora replay --check` is unchanged.

The prefix bypasses `session.execute`, so before the `current` branch the session is told the step count, the last receipt and each arm's gripper directive. It is not told recovery targets: a `recover_motion` incident is refused, and so is a `plan_ref` move, whose probe no longer exists. A RoboDojo tool restores no tool state: its holding intentions start empty, and the gripper command decides whether the jaw holds (`move_pose._holding`). A held object's attachment for clearance is therefore not modelled, and a later action of one tool call runs on the publication the call started from.

Each branch reports the prefix verdict, the receipt or the refusal, and `scene`: the bodies that moved at least 2 mm or 2 degrees during the action, from private simulator state (free joints on LIBERO, actors on RoboTwin, the evaluator's rigid instances on RoboDojo). The report is developer evidence, reaches no policy-readable path and is not a task result: it says what one move did from one state.

The agent trace (`agent-events.jsonl`, `ora.agent-event.v1`) closes every `action_proposed` with the Supervisor's `environment_receipt` and a later `fresh_observation_published`. The one other valid closure is `action_discarded`: a round budget ended the round after the turn proposed the action and before the Supervisor executed it (the post-turn model-call guard, see [runtime lifecycle](runtime-lifecycle.md#execute)). The event is recorded by the agent core with `visibility: "derived"`, `causal_parent_id` set to the proposal's event, the proposal's `observation_step` and payload `{action_id, reason}`, where `reason` is a round budget (`model_call_budget`, `wall_time_budget` or `robot_decision_budget`); the Supervisor records the same decision in `events.jsonl` as `action_discarded` after its `termination_authority_decision` (source `post_policy_budget_guard`). The trace verifier accepts it only as the direct closure of the pending proposal, with no policy activity (observation, model request, tool call, action or receipt) after it until the round's recorded end, and only when that end is the same budget: the run's `run_terminated` termination, or in a persistent session the round's `rounds/round-NNNN/result.json` termination. An unclosed proposal stays `action_without_fresh_observation`, a discard on a different termination is `discarded_action_termination_mismatch` and a malformed one `action_discarded_contract`, each invalidating the run. A discarded action is no decision and has no receipt. While the invocation that proposed an action is open, the Supervisor's refusal of the action before it started (its `environment_receipt` artifact is `ora.tool-action-refusal.v1` with status `refused` and zero native actions, `orchestrator/episode/tool_execution.py`) closes it by that receipt alone, in code mode (`policy.code_mode`) and tool-call mode alike: nothing ran and nothing was published, so no fresh observation is due and another action may follow on the same observation step, a script's next call or, in tool-call mode, the action of the model's next request (a zero-spend refusal leaves the turn open); it may carry the same action identity. The invocation (the script's call, or the synchronous tool's call) must then list that receipt in its `tool_result`'s execution ledger as a refusal that used nothing, else `refusal_not_in_execution` (reported at the end of the trace too when that `tool_result` never came, besides `missing_tool_result`). Any other refused action still waits for a fresh observation. The trace's other readers take such a proposal (one whose receipt is that refusal, `trace_verify.is_supervisor_refusal`) as not executed too: after a Supervisor refusal the same action identity may be proposed again, while a repeated identity after an executed action is still an error. The live world view (`ora/orchestrator/world_view.py`) accepts an action identity again only after its refusal, the Viewer's process timeline shows a refused proposal and its receipt as refused instead of joining them to the later action with that identity, and the Runtime metrics leave it out of the proposing turns that `latency.turn_complete_to_action_publish_s` pairs with the Supervisor's actions; the execution ledger and Pi's check of it apply the same identity rule ([toolkit execution](toolkit-execution.md)).

The privileged-field audit of the trace (`ora/agent/evidence/trace_privacy.py`) refuses contact data, except a receipt's public contact detail: `detail.contact_motion` holding only the public contact and force fields (`motion_feedback.py`), where an observation carries the last action's effect and where a synchronous execution's ledger carries each action's receipt (`execution.actions[].receipt`, `action_step_inventory[].actions[].receipt`). Any other contact data under those keys still invalidates the run.

An argument name the run's own tool surface declares (a top-level `inputSchema` property of one of the plan's `tool_surface.definitions`) may hold a forbidden term, as a toolset's `contact_speed_m_s` does; it passes the audit only where it holds the policy's own argument (`trace_privacy.declared_argument_names`, `policy_argument_values`). That is a key with a value the policy wrote for that name in a tool call (the `payload.arguments` of any `tool_call_proposed`, a code-mode script's calls included, at any depth) or the default the surface declares for it, wherever that exact value appears: the call itself, the call in the provider request and response, and a tool result, receipt or later request that echoes it at whatever path the tool chose (numbers compare as numbers, so `3` and `3.0` are one value). The other is the name's declaration in a provider request's tool schema (`tools[].function.parameters.properties` or `tools[].input_schema.properties` at the root of a canonical or wire request artifact). The same key with any other value, in a tool result or anywhere else, is audited like any other key and invalidates the run, so declaring an argument cannot blind the audit to state a tool returns under its name (an `arguments` key in a tool result is no exception). A declared name that is exactly a forbidden term (`contact`, `contacts`, `segmentation`) is never exempt, and the values under an exempt key are still scanned.

In code mode the audit leaves out what a script wrote itself. In the result of the model's own `bash` call (`run_python` in a run sealed before that tool was retired) (the `tool_result` of a `tool_call_proposed` that names one of those tools and is not itself a script's call, `payload.origin`, in a plan with `policy.code_mode`, or `policy.glue_code` in plans made before the rename), it skips `value`, `stdout`, `stderr`, `output` (`trace_privacy.SCRIPT_AUTHORED_FIELDS`) and `handed_back`'s `reason` and `data` (`SCRIPT_HANDED_BACK_FIELDS`). A script runs in a sandbox that sees no run, simulator or credential and reaches the world only through its calls: each is recorded as its own `<call>#NNN` event whose result is audited in full, an action's receipt as its `environment_receipt`, and an action that did not start returns only the harness's error message (`action_not_started`). So these fields hold the model's own computation over what its calls returned, under names the model chose: a point it hands back as `contact` is not contact sensing. The rest of that result (`calls`, `actions_executed`, `action_limit`, `script_error`, `error_detail`, any other `handed_back` key, the workspace listing), its payload with the execution ledger, the script's calls and every other event are audited as before; a plan without `policy.code_mode` (tool-call mode, free play) has no such result.

A later request repeats a script's result, so the audit reads it there the same way. In the requests of the model (`REQUEST_KINDS`) and of the context compactor (`rollout_compactor_canonical_request`, `rollout_compactor_wire_request`), and in the context packets whose `compactor_messages` the compactor's request repeats (`lossless_context_semantic_packet`, `episode_context_capsule_packet`; before 2026-11-04 the packets were read without it, and a script's handed-back `p_contact` refused a run's seal), the result of such a call, whether a `role: tool` message, an Anthropic `tool_result` block or the compactor packet's text block after its heading, is audited without those fields (`trace_privacy.without_script_authored_messages`); the SFT and wire exports scan it the same way, and export it unchanged. The exemption goes by the call and not by the field: every other tool's result under those names, and the rest of the request, stay audited.

The audit also leaves out the arguments the model wrote in its tool calls (2026-11-04). Privileged state reaches the policy only through what the harness shows it, which stays audited; a call's arguments are the model's own text, every key a name it chose, so a key holding a forbidden term is no evidence of a leak (a malformed `write` call whose one key held a whole module, with "pad contact" in a docstring, refused a run's seal). The provenance is exact (`trace_privacy.reply_tool_arguments`, `model_tool_arguments`): the reply that wrote the call (`provider_response` `payload.message.tool_calls[].function.arguments`); any later request or packet that repeats it as a tool call with the same id and the same arguments text (OpenAI `function.arguments`), or as an Anthropic `tool_use` block with that id whose `input` is what the text parses to; the proposal that parsed it (`tool_call_proposed` `payload.arguments`, equal to that parse); and, in code mode, the arguments a script the model called sent with its own calls (`payload.origin` of a script kind naming that script's call), which the gateway changes only by dropping nulls and making integral floats integers. A call the context rewrote (an aged or externalized arguments marker), a call no reply wrote and a proposal that differs from its reply are audited as before, and so is every tool result, receipt or request message that echoes an argument, the declared names of the rule above aside, and a tool schema that declares a forbidden name. The SFT and wire exports apply the same rule to the requests and replies they export. The agent core's own check (below) already skips the model's messages.

The agent core's own check of each provider request (`causalMessageViolations`, which ends a run with `causal_context_forbidden_fields`) reads a script's result the same way, for the same reason. Python sends it the script tools, only in code mode, with the two field lists (`scriptTools`, `scriptAuthoredFields`, `scriptHandedBackFields` in `ora/agent/pi/domain.py`). The compactor's check (`compactorPrivateViolations`) does not: the compactor reads the packet, so a reserved key such as `contact` or `task_success` refuses that compaction whoever wrote it.

### Sandbox retention (`save_sandbox`)

`sandbox/workspace` is the execution model's working directory: its `AGENTS.md`, `contract/` and the images handed to it (`inputs/action-observations/`, `inputs/current-visual/`, derived again after every action from the run's `policy-observations/`). In a long RoboDojo episode it holds over a gigabyte. The run option `save_sandbox` (RunSpec top level and task file; `--set save_sandbox=false`) chooses what is kept:

- `true` (the default): every file stays, as before the option existed. The plan does not carry the key, so default plans keep their hashes. Keeping the sandbox by default, with `false` opt-in, is a recorded default-activation decision (user decision, 2026-10-30).
- `false`: the plan records `save_sandbox: false`. The run is evaluated and sealed with its sandbox as usual (the seal inventory hashes every file); right after the landing seal (or `ora seal`) the files under `sandbox/` are removed and `sandbox/hashes.json` (`ora.sandbox-hashes.v1`) lists each one's path, size and sha256 with the seal's `inventory_sha256`. A file that drifted before removal keeps the sandbox as it is and is recorded in `native/sandbox-hashes.refusal.json`.

Readers of a run without its sandbox: `ora export` archives every sealed file it still has plus `sandbox/hashes.json` (a removed file is accepted when the manifest lists it with the sealed sha256 and size); `ora export-sft` reads no sandbox file and proceeds; `ora evaluate` (and revalidation) refuse with the message that the sandbox files were not saved, and the `evaluation.json` written at seal time stands. Toolkit replay, the viewer and execution capture never read `sandbox/`. Execution capture is a separate option, `runtime.execution_capture` (enabled, 10 fps by default).

Evaluation validates source/action/trace/artifact boundaries before sealing. A seal binds the immutable inventory and final trace identity. The inventory (`evaluate.inventory`) refuses a symbolic link (the sandbox's reserved instruction aliases aside), a hard link, a non-regular file and a path named like a credential (`CREDENTIAL_NAME_FRAGMENTS`: `credential`, `api-key`, `auth.json`), except under `native/code-workspace/` in a code-mode run outside free play (`MODEL_NAMED_DIRECTORY`, 2026-11-04): only the policy's `write` and `edit` calls and its commands write there, in a sandbox with a cleared environment that binds no run, home directory or credential store, so a name there is the model's and says nothing of the file (`spots_auth.json` held measured hole positions and refused a run's seal). In free play Pi's own shell runs on the host, and the rule applies there too. A failed seal is recorded as a refusal. Later revalidation writes separate reports without changing the original run. Synthetic's `official_success` remains `null`; only a benchmark's official boundary may supply a boolean.

## Credentials and launch

Credential environment variables, TLS trust and simulator roots are local operator configuration. A provider key comes from its named environment variable first, then from the user-level ORA credential store (`ora.agent.providers.user_store`: `pi-auth.json` in `$ORA_CONFIG_HOME`, else `$XDG_CONFIG_HOME/ora`, else `~/.config/ora`, mode 0600), which `ora providers`, the provider selector of `ora interactive` and `ora play`, and Pi's `/login` write; the `ora` entry point fills only variables the environment (or `ora run`'s `.env`) leaves unset. User provider profiles for custom endpoints live in `providers.yaml` beside it, in the source-tracked catalog's schema (HTTPS, loopback HTTP, or cluster HTTP with `--cluster-http`), including the optional `request_options`, `recovery_window_s` and `reasoning_field` that `ora providers add --request-options/--recovery-window/--reasoning-field` records under the catalog's own checks. `provider_policy` copies both into every policy it builds; a profile's recovery window also selects `provider_recovery: first_token_deadline_v1`, the recovery the window applies under as each attempt's first-token limit and the call's no-content window (the default baseline already names it; see [provider recovery](#provider-recovery)). A plan records the profile id and endpoint, never a key. Preflight checks configuration/qualification but never turns an installation into benchmark success evidence. `ora doctor` reports credential presence and setup discovery only, including the agent core's Node version and `ora-pi` package readiness (and, when none of `ORA_API_KEY`, `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is set, a line naming what `ora run --env synthetic` needs); it does not authenticate, render, initialize CUDA or qualify physics. The Pi core receives credentials through its process environment only; they never enter the provider spec it logs.

A [bound compactor](#context-compactor)'s key comes from its own profile's `api_key_env` by the same rule: the environment first, then the user credential store, which the `ora` entry point applies. Its base URL variable may be unset, in which case the binding's endpoint serves (as `ora run` defaults a profile's); a set value must equal that endpoint. A missing key or a different endpoint refuses the session, naming the variable or the endpoint, never the key. The Pi core receives the compactor's key in `ORA_PI_COMPACTOR_API_KEY` (a parent process's own value of that name never passes through), and the compactor's provider spec in InitParams without it. The environment worker is not handed the binding's base URL, key or proxy variables, as it is not handed the policy's. The key is never written to the plan, the events or the run directory.

`ora run --env synthetic` uses `ORA_API_KEY` (or the chosen provider's conventional key variable), `ORA_BASE_URL`, `ORA_MODEL`, `ORA_PROVIDER`, optional `ORA_REASONING_EFFORT` and `ORA_SUPPORTS_IMAGES`. Its default limits are explicit in `quickstart.py` and documented in the [quickstart](../quickstart.md). With `--provider-profile ID` it takes that profile's policy instead (`ora.app.quickstart.profile_policy`, the `provider_policy` of every profile): its endpoint, model, key variable and limits. As on a GPU task, the profile's base-URL variable defaults to its endpoint and a missing key or another endpoint is refused before preflight; preflight then probes the provider as it does for any profile. The benchmark builders use their selected provider profiles instead.

`ora launch RUN_SPEC` requires exact preflight and confirmation; `--yes` is explicit non-interactive authorization for that plan. `ora run --env synthetic` starts only its bounded Synthetic plan. Retry cannot rewrite a sealed run or repeat an attempt that already dispatched a robot action.

## Local model APIs and gateways

`loopback_http_v1` admits a direct HTTP model API on localhost. Credentials come from the policy's named environment variables. Set `gateway_client_binding: true` only for a CLIProxyAPI gateway whose config/client files should be loaded and checked; the bundled gateway profiles select it explicitly. The default is no gateway file binding. HTTPS profiles cannot select gateway binding.

`cluster_http_v1` admits plain HTTP to another host: a model server on the operator's own network, such as a vLLM node of their cluster. A profile or RunSpec admits it only by naming it (`ora providers add --cluster-http` records it), because the key and every request cross that network unencrypted. Its endpoint must be `http` without credentials, query or fragment; it cannot select gateway binding or `provider_session_affinity`.

Synthetic runs in process, so its scratch directory is checked for capacity and writability without an unused Unix RPC socket path limit. Isolated simulator workers retain that limit.

## Provider wire tool names

Strict providers reject dotted function names, so the OpenAI-compatible wire maps tool names reversibly (`ora/agent/providers/tool_names.py`, ported byte for byte in `ora-pi/src/provider/tool_names.ts`). A name outside `^[A-Za-z][A-Za-z0-9_-]{0,63}$` becomes a deterministic alias, its slug plus eight hex digits of its SHA-256 (`robot.submit_action` → `robot_submit_action_<hash8>`); definitions, a forced tool choice, assistant tool-call history and named tool results use the same map, including tools retained from an earlier phase, and responses return canonical names before their continuation state is bound. An alias collision fails closed. A returned tool name that is not a wire alias, not a name the request already carries and not strict (a provider tool-call parse defect, such as a parameter tag read as the function name) takes its alias before it enters the canonical history, so it never extends the disclosure below; the Runtime refuses it as `tool_unavailable` and the provider's raw events keep the name as sent. When any name is renamed, the mapping is disclosed to the model as a `<wire_tool_aliases>` JSON block appended, after a blank line, to the last leading system message (a text part for list content; the whole content when it is empty or null; a new index-0 system message only when the request has none). The wire never gains a second system message: Qwen3.x chat templates on vLLM and SGLang reject any system message after index 0 with HTTP 400. A leading system message whose content is not text is refused. The disclosure is byte-identical across the turns of one session, so the provider prefix stays cacheable. Surfaces that declare `canonical_tool_names_v1` (the default LIBERO-Pro, RoboTwin and RoboDojo surfaces) advertise strict names and send no disclosure, so their wire bytes do not depend on this rule. Requests that carry dotted names (the Synthetic quickstart's `robot_runtime_v2`, frozen surfaces, custom tools) are affected: runs recorded before this layout sent the same disclosure text as a separate system message after the leading ones, so their archived wire requests differ from a rerun only in that message boundary. The Anthropic Messages projection sends the disclosure as its own `system` block and is unchanged.

## Rejected tool replies

A provider reply whose tool calls cannot be dispatched as they stand is a
recoverable reply that changes nothing in the world, on both wires
(`ora-pi/src/provider/openai.ts` and `anthropic.ts`; the Python providers'
`ora/agent/providers/transport_failures.py` and `anthropic_reply.py`, which the
agent-core parity tests compare): its raw events stay in the evidence, every
tool call of the reply leaves the canonical conversation, the model call
counts, and the policy retries on the same observation after ORA's
continuation (`afterRejectedOutput`, event `provider_output_rejected`). The
output rejection records `code`, `provider_stop_reason`,
`rejected_tool_blocks` (index, call id, tool name, the fragment's length and
SHA-256, reason) and `discarded_tool_block_count`.

- `truncated_tool_call`: the reply reached the output limit while it proposed
  tools; every call is rejected (`provider_output_limit`), complete or not.
- `invalid_tool_input_json`: a complete reply in which any call's arguments do
  not decode to a JSON object (`invalid_json`, or `not_an_object` for an
  array, string or number); the whole batch is discarded with it.

ORA never guesses or repairs arguments. On the OpenAI-compatible wire the
second case came on 2026-11-07: before, such a call (a Flash-Next `bash` call
with empty arguments) stayed in the history and failed only at dispatch, and
replaying its arguments made strict servers (SGLang) refuse every later
request with HTTP 400 ("function.arguments must be valid JSON"), which ended
the run as `trusted_fault`.

## Path-only asset references

Composable policy assets name owned components by repository-relative path. `policy.lock` supplies their current bytes and content hashes; do not duplicate mutable owned-asset SHA literals inside a composable record. External source/model pins, reviewed evidence and generated compiled manifests retain their distinct identities. A reference escaping the tree, absent from the lock or using an inadmissible inline pin is refused. Regenerate compiled artifacts and the lock deliberately after changing their inputs.

A descriptor runs were sealed with keeps its bytes when a source it names moves: a source-only successor (a new descriptor file naming the new path, compiling the same card) becomes the binding for new runs, and `ora/agent/composition/embodiment/moved_sources.py` holds the one table `(recorded path, sha256) -> path that answers for it now`. A recorded path that is gone resolves to the new place only while the bytes there hash to the recorded sha256 (the provenance audit, `load_public_geometry_binding`, the jaw-centre re-hashes); an observation whose loaded-geometry verification names the predecessor's sha256 holds for the successor only while the predecessor still has those bytes and both bind the same robot geometry. Anything else is refused as before. The ARX X5 public-geometry descriptor v6 is v5 with the gripper module at `ora/agent/toolkit/host/grasp/arx_x5_gripper.py`; v5 stays for the RoboDojo runs sealed with it. The PandaOmron public-geometry descriptor v2 adds the arm mount, its reach admission and the wrist camera's mount to v1 (card facts, the same robot geometry; schema v2's optional `topology.manipulators[].mount`, `geometry.reach_admission` and `sensing.cameras[].mount_in_controlled_frame`, which a v1 descriptor refuses); v1 stays for the RoboCasa runs sealed with it and `MOVED` maps it to v2. The Franka Panda `...-session-split-v1-flat-surface-v1` descriptor cites the written-out LIBERO surface manifest for the workspace facts where its predecessor cited `explicit_motion_v1/libero-definitions.json`, which left the tree; the predecessor stays for the LIBERO runs sealed with it, and since nothing composes it, its source audit (which would refuse the missing file) never runs.

## Storage admission floors

Synthetic defaults to 1 GiB free in the evidence workspace and 0.25 GiB in scratch. Isolated robot runtimes retain the 20 GiB workspace and 2 GiB scratch defaults. A host profile's explicit `runtime_storage` floors take precedence for either kind; capacity and writability are still probed. These are admission reserves, not predicted episode sizes.
