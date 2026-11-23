# Model APIs

ORA's agent core, Pi, calls a
model directly through OpenAI-compatible Chat Completions or Anthropic
Messages. There is no managed coding-agent session between ORA and the
endpoint.

## First episode

The [quickstart API section](../quickstart.md#model-api) contains the complete
setup for OpenAI, self-hosted compatible endpoints, and Anthropic.

| Variable | Purpose |
|---|---|
| `ORA_API_KEY` | Credential; falls back to the protocol's usual API-key variable |
| `ORA_PROVIDER` | `openai_compatible` or `anthropic_messages` |
| `ORA_BASE_URL` | Endpoint URL |
| `ORA_MODEL` | Model identifier served by that endpoint |
| `ORA_REASONING_EFFORT` | Optional reasoning setting supported by the endpoint |
| `ORA_SUPPORTS_IMAGES` | Set to `0` for a text-only Synthetic endpoint |

Credentials do not belong in RunSpecs or Git.

## Keys, configured once in the terminal

A provider profile names the variable that holds its key
(`DEEPSEEK_LOW_API_KEY` for the shared default). Enter it once; ORA keeps it
for you and tests it with the same probe every launch runs:

```bash
./scripts/ora providers key DEEPSEEK_LOW_API_KEY      # asked without echo, then tested
./scripts/ora providers                               # every profile and where its key comes from
./scripts/ora providers test deepseek_flash_openai_low_16k_v1
```

`ora interactive` and `ora play` ask the same way: a profile marked *needs
VARIABLE* in the provider list asks for its key when you choose it, stores it
and shows the probe's result before the run starts. Pi's `/login` and
`/logout`, available in every run's terminal, use the same store.

The store is `pi-auth.json` in the ORA config directory (`$ORA_CONFIG_HOME`,
else `$XDG_CONFIG_HOME/ora`, else `~/.config/ora`), mode 0600, outside the
repository and every run. An environment variable still comes first, so CI and
scripts keep exporting keys (`export DEEPSEEK_LOW_API_KEY=sk-...`), and
`ora run` lets the checkout's `.env` win over a stored key too. A plan and
its evidence record the profile id and endpoint, never a key. Sealing refuses
a run that holds a file named like a credential (`credential`, `api-key`,
`auth.json`), except in a code-mode run's sandboxed workspace, whose file names
the model chooses; it does not scan file contents, so keep keys out of
anything the agent writes. `./scripts/ora providers key
VARIABLE --remove` deletes a stored key (as `/logout` does).

## Robot episodes

Robot recipes select a provider profile when composing a RunSpec. Inspect
registered profiles without contacting a service:

```bash
uv run --locked ora providers
```

The shared default is `deepseek_flash_openai_low_16k_v1`; the standalone
Synthetic quickstart has its own explicit default. A robot endpoint needs the
image, tool-call, and continuation capabilities declared by its surface.
Registration and doctor discovery do not establish those capabilities; live
preflight checks them.

`ora run` takes `--provider-profile ID` to run a task with another registered
profile; it checks that profile's key and endpoint instead of DeepSeek's. On
Synthetic, `ora run --env synthetic --provider-profile ID` uses the profile
instead of the `ORA_*` variables. Two
open-weight models released on 2026-10-15 are registered through OpenRouter
(set `OPENROUTER_API_KEY`):

| Profile | Model | Settings (from the model card) |
|---|---|---|
| `muse_spark_open_openrouter_high_16k_v1` | Meta Muse Spark Open | reasoning effort `high`; temperature 1.0, top_p 1.0 |
| `nemotron_35_super_vl_openrouter_thinking_16k_v1` | NVIDIA Nemotron 3.5 Super VL | thinking on; temperature 0.6, top_p 0.95; routed to Together, whose endpoint accepts a forced tool choice |

```bash
export OPENROUTER_API_KEY=sk-or-...
./scripts/ora run --env libero --task libero_goal/0 \
    --provider-profile muse_spark_open_openrouter_high_16k_v1 --confirm-launch
```

### Measured on the robot-episode sweep

ORA's open-model sweep runs 130 robot episodes per model with the default
policy composition and budgets, one attempt per episode: 80 LIBERO-Pro
episodes (the four suites, original and object-swapped variants, tasks 0-9,
initial state 1) and 50 RoboTwin tasks (one seed each, `demo_randomized`).
Measured on ORA commit `bf2d107d` (2026-10-22), both models self-hosted with
the settings of their model cards:

| Model | Settings | Success | LIBERO-Pro /80 | RoboTwin /50 | Reasoning tokens / episode | Model-call latency p50 | Self-declared give-ups |
|---|---|---:|---:|---:|---:|---:|---:|
| Qwen3.8-Flash-Next | thinking on; temperature 1.0, top_p 0.95, top_k 20 | 108 (83%) | 70 | 38 | 121k | 23 s | 20 |
| DeepSeek-V4.1-Flash-1017 | reasoning effort `low` | 97 (75%) | 65 | 32 | 79k | 11 s | 32 |

Flash-Next succeeds on 16 episodes where DeepSeek fails and DeepSeek on 5
where Flash-Next fails (exact McNemar p = 0.027). Each task has a single
attempt and seed, so per-task differences are within run-to-run noise; read
the comparison in aggregate only. Latency depends on the serving stack.

A profile may carry `request_options`, a closed set of request fields added to
every call: `temperature`, `top_p`, `top_k`, `chat_template_kwargs` (vLLM and
SGLang chat-template switches such as `enable_thinking` or `reasoning_effort`),
OpenRouter's `reasoning` and `provider` objects, and vLLM's `return_token_ids`
(`true` only: every stream then carries the prompt and sampled token ids, which
`ora export-sft --view wire` exports). The agent core sends them
(the Pi core's TypeScript provider), and so does free
play (`ora play`) on every request to the profile's model, including after
`/model` switches back to it; a model of another provider gets none.
To serve a model yourself, add it with `ora providers add` and its
`--request-options` ([self-hosted endpoints](#self-hosted-endpoints)), or copy a
profile into your own catalog, point it at your endpoint and pass
`--provider-catalog` to the spec builders.

Read [configuration](../reference/configuration-and-evidence.md) and
[host setup](../environments/common.md) before a robot launch.

## Self-hosted endpoints

Add your own OpenAI-compatible or Anthropic endpoint as a profile of yours
(kept in `providers.yaml` beside the key store, never in Git), from the
provider list's *+ add an endpoint* or the command line:

```bash
./scripts/ora providers add cluster-deepseek --protocol openai_compatible \
    --endpoint http://127.0.0.1:30000/v1 --model deepseek-v4 --images
./scripts/ora interactive --provider cluster-deepseek
./scripts/ora providers remove cluster-deepseek
```

It asks for the key (stored as `ORA_<ID>_API_KEY`, or `--key-env NAME`) and
runs the probe. Use the model ID exposed by the service. Plain HTTP is allowed
on loopback; a remote service needs HTTPS, a local port forward
(`ssh -L 30000:node:30000 ...`) or `--cluster-http`. Set `SSL_CERT_FILE` to
the trust bundle for a private certificate authority. A vLLM server started
without `--api-key` accepts any key: store any non-empty value, such as `EMPTY`.

`--cluster-http` admits plain HTTP to another host, such as a vLLM server on
another node of your own cluster (`http://NODE:8000/v1`). The profile records
`endpoint_transport: cluster_http_v1`; the key and every request then cross the
network unencrypted, so use it only on a network you trust. Such a profile
sends no session affinity and binds no gateway. The probe goes through
`HTTP_PROXY` when the machine sets one, so add the server's host to `NO_PROXY`.

vLLM streams a thinking model's reasoning in the `reasoning` field. Pass
`--reasoning-field reasoning` (OpenAI-compatible only) so the agent records
that reasoning and returns it on later turns; without it the profile reads
`reasoning_content`, the field DeepSeek's own API uses.

Give the profile what the model card asks for. `--context-window N` is the
context window the server serves, in tokens (vLLM's `--max-model-len`, at most
the model's own); without it the profile records 131072, which cuts a 262k
model short. `--max-output N` is the output budget (8192 by
default). A thinking model usually needs its sampling and chat-template switch
on every request: pass them as `--request-options JSON` (OpenAI-compatible
endpoints only), with the same closed fields and checks as a catalog profile's
`request_options` above. A slow self-hosted model that takes long to its first
token needs a longer `--recovery-window SECONDS` (30..1800; 180 by default):
how long each attempt may wait for its first token before it is retried, and
how long failures before any content are retried before the call ends. A
reply that keeps streaming has no total limit ([provider
recovery](../reference/configuration-and-evidence.md#provider-recovery)):

```bash
# NVIDIA Nemotron 3.5 Super VL on vLLM: thinking on, the card's sampling
./scripts/ora providers add nemotron-local --protocol openai_compatible \
    --endpoint http://127.0.0.1:30000/v1 --model nemotron-3.5-super-vl --images \
    --context-window 262144 --max-output 16384 --recovery-window 900 \
    --request-options '{"temperature":0.6,"top_p":0.95,"chat_template_kwargs":{"enable_thinking":true}}'

# Meta Muse Spark Open: reasoning effort through the chat template, served with a 262k window
./scripts/ora providers add muse-local --protocol openai_compatible \
    --endpoint http://127.0.0.1:30001/v1 --model muse-spark-open --images \
    --context-window 262144 --max-output 16384 \
    --request-options '{"temperature":1.0,"top_p":1.0,"chat_template_kwargs":{"reasoning_effort":"high"}}'

# vLLM on another node of your cluster, served with --enable-auto-tool-choice and its
# --tool-call-parser; the key (vLLM's --api-key) is read from standard input
./scripts/ora providers add node-vllm --protocol openai_compatible \
    --endpoint http://NODE:8000/v1 --model deepseek-v4.1-flash --images \
    --cluster-http --reasoning-field reasoning --context-window 131072 \
    --recovery-window 900 --stdin < vllm-key.txt
./scripts/ora run --env robodojo --task general_pickup --provider-profile node-vllm --confirm-launch
```

`add` prints the context window, output budget, request options and recovery
window it recorded, and `./scripts/ora providers` lists the context window and
any request options and recovery window of every profile (`--json` shows the
whole profile). A refused value (invalid JSON, a field outside the closed set,
a temperature outside 0..2, a window outside 30..1800 s) stores nothing. Every
plan built from the profile carries them: `ora run --provider-profile ID`,
`ora interactive --provider ID` and `ora play --provider ID`. To change a value, add
the profile again under the same id.

See [troubleshooting](troubleshooting.md) for 401, 429, TLS, and capability failures.
