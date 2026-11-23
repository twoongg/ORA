"""``ora providers``: provider profiles and their keys, configured in the terminal.

    ora providers [--json]              the profiles, and where each one's key comes from
    ora providers key PROFILE|VARIABLE  enter its key (not echoed), store it, then test the profile
    ora providers key VARIABLE --remove
    ora providers add ID --protocol openai_compatible|anthropic_messages --endpoint URL --model NAME
                     [--images] [--context-window N] [--max-output N] [--reasoning-effort E]
                     [--request-options JSON] [--recovery-window SECONDS] [--key-env NAME]
                     [--cluster-http] [--reasoning-field reasoning_content|reasoning]
    ora providers remove ID            a profile added here (and its stored key)
    ora providers test PROFILE          the provider probe every launch runs (provider_marker)

Keys go to the user-level ORA credential store that Pi's ``/login`` uses too,
and custom endpoints to the user's own profiles beside it
(``ora.agent.providers.user_store``): outside every repository and run, mode
0600. An environment variable of the same name still wins, so CI and scripts
keep exporting keys. A plan records the profile id and its endpoint, never a
key.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from typing import Any, Callable, Mapping

#: ``user_store.DEFAULT_CONTEXT_WINDOW`` and ``DEFAULT_MAX_OUTPUT``, for the parser without importing the
#: provider package (tests/ora/app/test_providers.py keeps them equal).
DEFAULT_CONTEXT_WINDOW = 131072
DEFAULT_MAX_OUTPUT = 8192
PROTOCOLS = {"openai_compatible": "OpenAI-compatible (chat completions)", "anthropic_messages": "Anthropic (messages)"}


def register(sub: Any) -> None:
    providers = sub.add_parser(
        "providers", help="list, add, test and configure provider profiles and their keys",
        description=__doc__.split("\n\n", 1)[0])
    providers.add_argument("--json", action="store_true")
    actions = providers.add_subparsers(dest="providers_command")
    key = actions.add_parser("key", help="enter a profile's key (not echoed) and store it outside the run")
    key.add_argument("target", metavar="PROFILE|VARIABLE")
    key.add_argument("--remove", action="store_true", help="remove the stored key instead")
    key.add_argument("--stdin", action="store_true", help="read the key from standard input")
    key.add_argument("--no-test", action="store_true", help="store it without probing the provider")
    add = actions.add_parser("add", help="add an OpenAI-compatible or Anthropic endpoint of your own")
    add.add_argument("profile_id", metavar="ID")
    add.add_argument("--protocol", choices=sorted(PROTOCOLS), required=True)
    add.add_argument("--endpoint", required=True,
                     help="https://..., http://127.0.0.1:PORT/... on this machine, "
                          "or http://NODE:PORT/... with --cluster-http")
    add.add_argument("--model", required=True, help="the model id the endpoint serves")
    add.add_argument("--images", action="store_true", help="the model reads images")
    add.add_argument("--context-window", type=int, metavar="N",
                     help=f"the model's context window in tokens (default {DEFAULT_CONTEXT_WINDOW})")
    add.add_argument("--max-output", type=int, metavar="N", help=f"the reply's token limit (default {DEFAULT_MAX_OUTPUT})")
    add.add_argument("--reasoning-effort", default="provider_default")
    add.add_argument("--request-options", metavar="JSON",
                     help="request fields every call adds (openai_compatible): temperature, top_p, top_k, "
                          "chat_template_kwargs, reasoning, provider, return_token_ids (vLLM); e.g. "
                          "'{\"temperature\":0.6,\"chat_template_kwargs\":{\"enable_thinking\":true}}'")
    add.add_argument("--recovery-window", type=float, metavar="SECONDS",
                     help="how long each attempt may wait for its first token, and how long failures before "
                          "any content are retried before the call ends, 30..1800 s (default 180); "
                          "raise it for a slow self-hosted model")
    add.add_argument("--cluster-http", action="store_true",
                     help="admit plain HTTP to another host (cluster_http_v1), such as a vLLM server on "
                          "another node of your cluster; the key travels unencrypted, so only on a network "
                          "you trust")
    add.add_argument("--reasoning-field", choices=("reasoning_content", "reasoning"),
                     help="the message field the server streams reasoning in (openai_compatible); "
                          "current vLLM uses reasoning (default reasoning_content)")
    add.add_argument("--key-env", help="the key's variable (default ORA_<ID>_API_KEY)")
    add.add_argument("--no-key", action="store_true", help="add the profile without entering a key")
    add.add_argument("--stdin", action="store_true", help="read the key from standard input")
    add.add_argument("--no-test", action="store_true", help="add it without probing the provider")
    remove = actions.add_parser("remove", help="remove a profile added here")
    remove.add_argument("profile_id", metavar="ID")
    remove.add_argument("--keep-key", action="store_true", help="keep its stored key")
    test = actions.add_parser("test", help="probe a profile's provider with its key")
    test.add_argument("profile_id", metavar="PROFILE")


# -- Probing -------------------------------------------------------------------------------

def probe_profile(profile_id: str) -> tuple[bool, str]:
    """Run the launch preflight's provider probe for ``profile_id``; whether it passed, and what it found."""
    from ora.agent.providers import user_store
    from ora.agent.providers.credentials import redact_credentials

    from ora.orchestrator.preflight.gates import _native_provider_candidate
    from ora.orchestrator.spec.inputs import provider_policy

    policy = provider_policy(profile_id)
    variable = policy["api_key_env"]
    key = os.environ.get(variable, "").strip() or user_store.stored_keys().get(variable)
    if not key:
        return False, f"no key: set {variable} or run `ora providers key {profile_id}`"
    check = _native_provider_candidate({"policy": policy}, key, [key])
    detail = redact_credentials(check["detail"], [key])
    where = f"{policy['model']} at {policy['endpoint']}"
    if check["passed"]:
        checked = ["the marker"] + (["an image"] if policy.get("supports_images") else []) + ["a tool round trip"]
        return True, f"connected: {where} answered {', '.join(checked)}"
    found = {name: detail.get(name) for name in ("stage", "http_status", "error_type", "error") if detail.get(name)}
    reason = " · ".join(f"{name} {value}" for name, value in found.items()) or json.dumps(detail)[:300]
    return False, f"probe failed: {where}: {reason}"


def report_probe(profile_id: str, write: Callable[[str], Any] = print) -> bool:
    write(f"Testing {profile_id} with the provider probe a launch runs ...")
    passed, summary = probe_profile(profile_id)
    write(("✓ " if passed else "✗ ") + summary)
    return passed


# -- Keys ------------------------------------------------------------------------------------

def key_variable(target: str) -> tuple[str, str | None]:
    """The variable a profile id (or a variable name) stores its key under, and the profile to test.

    For a variable, that is the catalog's default profile when it uses the
    variable, else the first live profile that does (None when none does).
    """
    from ora.agent.providers import user_store

    profiles = user_store.all_profiles()
    if target in profiles:
        return str(profiles[target]["api_key_env"]), target
    users = [profile_id for profile_id, profile in profiles.items() if profile.get("api_key_env") == target
             and profile.get("admission") != "registered_only"
             and profile.get("usage_scope") != "supported_coding_tools_only"]
    default, _ = user_store.select_profile(None)
    return target, default if default in users else (users[0] if users else None)


def read_key(variable: str, *, stdin: bool = False) -> str:
    from ora.agent.providers import user_store

    if stdin:
        return sys.stdin.readline().strip()
    return getpass.getpass(f"API key for {variable} (not shown; stored in {user_store.credentials_path()}): ")


def configure_key(profile_id: str, *, write: Callable[[str], Any] = print, stdin: bool = False,
                  test: bool = True) -> bool:
    """Enter and store the key of ``profile_id``, then probe it; whether it is ready to use."""
    from ora.agent.providers import user_store

    variable, _ = key_variable(profile_id)
    key = read_key(variable, stdin=stdin)
    if not key:
        write("No key entered; nothing was stored.")
        return False
    path = user_store.save_key(variable, key)
    write(f"Stored the key for {variable} in {path} (mode 0600).")
    return report_probe(profile_id, write) if test else True


def add_endpoint(profile_id: str, *, protocol: str, endpoint: str, model: str, images: bool,
                 context_window: int | None = None, max_output: int | None = None,
                 reasoning_effort: str = "provider_default", key_env: str | None = None,
                 request_options: Mapping[str, Any] | str | None = None,
                 recovery_window: float | None = None, cluster_http: bool = False,
                 reasoning_field: str | None = None) -> dict[str, Any]:
    """Store a user profile for an endpoint; ``request_options`` may be the option's JSON text."""
    from ora.agent.providers import user_store

    if isinstance(request_options, str):
        request_options = user_store.parse_request_options(request_options)
    profile = user_store.new_profile(
        profile_id, protocol=protocol, endpoint=endpoint, model=model, supports_images=images,
        context_window_tokens=user_store.DEFAULT_CONTEXT_WINDOW if context_window is None else context_window,
        max_output_tokens=user_store.DEFAULT_MAX_OUTPUT if max_output is None else max_output,
        reasoning_effort=reasoning_effort, api_key_env=key_env,
        request_options=request_options, recovery_window_s=recovery_window, cluster_http=cluster_http,
        reasoning_field=reasoning_field)
    user_store.save_profile(profile_id, profile)
    return profile


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def describe_added(profile_id: str, profile: Mapping[str, Any], *, context_defaulted: bool = False) -> list[str]:
    """What ``add`` recorded: where, the key, the context window, request options and recovery window."""
    from ora.agent.providers import user_store

    window = f"context window {profile['context_window_tokens']} tokens"
    if context_defaulted:
        window += " (the default; pass --context-window with the model's own)"
    options = profile.get("request_options")
    recovery = profile.get("recovery_window_s")
    lines = [
        f"Added the profile {profile_id} ({user_store.profiles_path()}); its key is {profile['api_key_env']}.",
        f"  {window}, max output {profile['max_output_tokens']} tokens",
        f"  request_options {_compact(options)}" if options else "  request_options none",
        f"  recovery window {recovery:g} s" if recovery is not None else "  recovery window 180 s (the default)",
    ]
    if profile.get("reasoning_field") is not None:
        lines.append(f"  reasoning field {profile['reasoning_field']}")
    if profile.get("endpoint_transport") == "cluster_http_v1":
        lines.append(f"  transport cluster_http_v1: plain HTTP to {profile['endpoint']}; "
                     "the key and every request cross the network unencrypted")
    return lines


def add_interactively(choose: Callable[[str, list[dict[str, str]]], str | None],
                      ask: Callable[[str], str] | None = None, write: Callable[[str], Any] = print) -> str | None:
    """The provider selector's "add an endpoint": ask for it, store it with its key, probe it."""
    from ora.agent.providers import user_store

    ask = ask or input

    protocol = choose("Protocol of your endpoint", [
        {"value": name, "label": label, "description": name} for name, label in PROTOCOLS.items()])
    if protocol is None:
        return None
    endpoint = ask("Base URL (https://..., or http://127.0.0.1:PORT/v1 on this machine): ").strip()
    model = ask("Model id the endpoint serves: ").strip()
    default_id = "".join(character if character.isalnum() else "-" for character in model.lower()).strip("-") or "mine"
    profile_id = ask(f"Profile id [{default_id}]: ").strip() or default_id
    images = ask("Does the model read images? [Y/n]: ").strip().lower() not in {"n", "no"}
    try:
        profile = add_endpoint(profile_id, protocol=protocol, endpoint=endpoint, model=model, images=images)
    except (user_store.UserStoreError, ValueError) as exc:
        write(f"✗ {exc}")
        return None
    for line in describe_added(profile_id, profile, context_defaulted=True):
        write(line)
    if not configure_key(profile_id, write=write):
        if ask("Use it anyway? [y/N]: ").strip().lower() not in {"y", "yes"}:
            return None
    return profile_id


# -- The command -----------------------------------------------------------------------------

def _listing(profiles: Mapping[str, Mapping[str, Any]], *, as_json: bool) -> None:
    from ora.agent.providers import user_store

    rows = {}
    for profile_id, profile in profiles.items():
        variable = profile.get("api_key_env")
        source = user_store.key_source(str(variable)) if variable else "missing"
        rows[profile_id] = {**dict(profile), "key_source": source}
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True))
        return
    for profile_id, profile in rows.items():
        print(
            f"{profile_id}\tprotocol={profile.get('protocol')}"
            f"\tmodel={profile.get('model')}"
            f"\tadmission={profile.get('admission', 'unspecified')}"
            f"\tusage={profile.get('usage_scope', 'unspecified')}"
            f"\timages={str(profile.get('supports_images')).lower()}"
            f"\tcontext={profile.get('context_window_tokens')}"
            f"\tkey={profile['key_source']}" + (f":{profile['api_key_env']}" if profile.get("api_key_env") else "")
            + (f"\trequest_options={_compact(profile['request_options'])}" if profile.get("request_options") else "")
            + (f"\trecovery_window={profile['recovery_window_s']:g}s"
               if profile.get("recovery_window_s") is not None else "")
            + ("\tsource=user" if profile.get("user") else "")
        )


def command_providers(args: argparse.Namespace, load_builtin: Callable[[], Mapping[str, Any]]) -> int:
    from ora.agent.providers import user_store

    action = getattr(args, "providers_command", None)
    try:
        if action is None:
            user = {key: {**value, "user": True} for key, value in user_store.user_profiles().items()}
            _listing({**user, **dict(load_builtin())}, as_json=args.json)
            return 0
        if action == "key":
            variable, profile_id = key_variable(args.target)
            if args.remove:
                removed = user_store.remove_key(variable)
                print(f"Removed the stored key for {variable}." if removed else f"No stored key for {variable}.")
                return 0
            if profile_id is None:
                key = read_key(variable, stdin=args.stdin)
                if not key:
                    print("No key entered; nothing was stored.")
                    return 2
                print(f"Stored the key for {variable} in {user_store.save_key(variable, key)} (mode 0600).")
                return 0
            return 0 if configure_key(profile_id, stdin=args.stdin, test=not args.no_test) else 1
        if action == "add":
            profile = add_endpoint(args.profile_id, protocol=args.protocol, endpoint=args.endpoint, model=args.model,
                                   images=args.images, context_window=args.context_window,
                                   max_output=args.max_output, reasoning_effort=args.reasoning_effort,
                                   key_env=args.key_env, request_options=args.request_options,
                                   recovery_window=args.recovery_window, cluster_http=args.cluster_http,
                                   reasoning_field=args.reasoning_field)
            for line in describe_added(args.profile_id, profile, context_defaulted=args.context_window is None):
                print(line)
            if args.no_key:
                return 0
            return 0 if configure_key(args.profile_id, stdin=args.stdin, test=not args.no_test) else 1
        if action == "remove":
            profile = user_store.user_profiles().get(args.profile_id)
            if profile is None or not user_store.remove_profile(args.profile_id):
                print(f"{args.profile_id} is not a profile added here (`ora providers` marks them source=user).")
                return 2
            if not args.keep_key:
                user_store.remove_key(str(profile["api_key_env"]))
            print(f"Removed the profile {args.profile_id}.")
            return 0
        if action == "test":
            return 0 if report_probe(args.profile_id) else 1
    except (user_store.UserStoreError, ValueError, RuntimeError, OSError) as exc:
        print(f"ora providers: {exc}", file=sys.stderr)
        return 2
    raise AssertionError(action)


def run(args: argparse.Namespace) -> int:
    from ora.agent.providers import load_builtin_provider_profiles

    return command_providers(args, load_builtin_provider_profiles)


__all__ = ["add_endpoint", "add_interactively", "command_providers", "configure_key", "describe_added",
           "key_variable", "probe_profile", "register", "report_probe", "run"]
