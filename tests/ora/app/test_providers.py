"""Provider keys and profiles configured in the terminal (``ora providers``, the provider selector).

A key entered here lands in the user-level credential store Pi's ``/login``
shares (mode 0600, outside the repository and every run) and serves its
variable when the environment sets none; the environment still wins. A custom
endpoint becomes a user profile a plan can name: the plan records the profile
and its endpoint, never the key. ``ora providers test`` runs the probe a
launch runs, here against the scripted loopback model. The selector asks for
a missing key, or for a new endpoint, and probes it before the run.
"""

from __future__ import annotations

import io
import json
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from ora.agent.providers import user_store
from ora.app import main as app_main

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def config(tmp_path, monkeypatch):
    home = tmp_path / "ora-config"
    monkeypatch.setenv("ORA_CONFIG_HOME", str(home))
    for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_LOW_API_KEY", "ANTHROPIC_API_KEY", "ORA_CLUSTER_LOCAL_API_KEY",
                 "ORA_CLUSTER_LOCAL_BASE_URL", "DEEPSEEK_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    return home


def test_a_stored_key_serves_its_variable_beside_logins_and_the_environment_wins(config):
    config.mkdir()
    # What Pi's /login wrote: an API key for Pi's anthropic provider and an OAuth login.
    (config / "pi-auth.json").write_text(json.dumps({
        "anthropic": {"type": "api_key", "key": "sk-from-login"},
        "openai-codex": {"type": "oauth", "access": "token", "refresh": "r", "expires": 0}}))
    path = user_store.save_key("DEEPSEEK_LOW_API_KEY", "sk-stored\n")
    assert path == config / "pi-auth.json" and not path.is_relative_to(ROOT)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    entries = json.loads(path.read_text())
    assert entries["ora:DEEPSEEK_LOW_API_KEY"] == {"type": "api_key", "key": "sk-stored"}
    assert entries["anthropic"]["key"] == "sk-from-login" and entries["openai-codex"]["type"] == "oauth"
    assert user_store.stored_keys() == {"DEEPSEEK_LOW_API_KEY": "sk-stored", "ANTHROPIC_API_KEY": "sk-from-login"}

    environ = {"ANTHROPIC_API_KEY": "sk-from-the-environment"}
    assert user_store.apply_stored_credentials(environ) == ["DEEPSEEK_LOW_API_KEY"]
    assert environ == {"ANTHROPIC_API_KEY": "sk-from-the-environment", "DEEPSEEK_LOW_API_KEY": "sk-stored"}
    assert user_store.key_source("ANTHROPIC_API_KEY", environ) == "environment"
    assert user_store.key_source("DEEPSEEK_LOW_API_KEY", {}) == "stored"
    assert user_store.remove_key("DEEPSEEK_LOW_API_KEY") and user_store.key_source("DEEPSEEK_LOW_API_KEY", {}) == "missing"
    with pytest.raises(user_store.UserStoreError):
        user_store.save_key("not a variable", "sk")
    (config / "pi-auth.json").write_text("{not json")
    assert user_store.apply_stored_credentials({}) == []  # an unreadable store gives nothing


def test_ora_providers_adds_tests_lists_and_removes_a_custom_endpoint(config, tmp_path, monkeypatch, capsys):
    from ora.app import interactive
    from ora.orchestrator.spec.run_spec import resolve_run_spec

    port_file = tmp_path / "port"
    server = subprocess.Popen([sys.executable, "-m", "tests.support.agent_core.fake_model_server", str(port_file),
                               "--delay", "0"], cwd=ROOT)
    try:
        deadline = time.monotonic() + 30
        while not (port_file.is_file() and port_file.read_text().strip()) and time.monotonic() < deadline:
            time.sleep(0.05)
        endpoint = f"http://127.0.0.1:{port_file.read_text().strip()}/v1"
        monkeypatch.setattr(sys, "stdin", io.StringIO("sk-cluster-local\n"))
        assert app_main.main(["providers", "add", "cluster-local", "--protocol", "openai_compatible",
                                 "--endpoint", endpoint, "--model", "fake-demo", "--images", "--stdin"]) == 0
        output = capsys.readouterr().out
        assert "✓ connected: fake-demo at " + endpoint in output and "sk-cluster-local" not in output
        assert app_main.main(["providers", "test", "cluster-local"]) == 0
    finally:
        server.kill()
        server.wait()
    profile = user_store.user_profiles()["cluster-local"]
    assert profile["api_key_env"] == "ORA_CLUSTER_LOCAL_API_KEY" and profile["endpoint_transport"] == "loopback_http_v1"
    assert "sk-cluster-local" not in (config / "providers.yaml").read_text()
    capsys.readouterr()
    assert app_main.main(["providers"]) == 0
    listed = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("cluster-local\t"))
    assert "key=stored:ORA_CLUSTER_LOCAL_API_KEY" in listed and "source=user" in listed

    # A plan names the profile and its endpoint; the key stays in the store.
    plan = resolve_run_spec(interactive.interactive_spec(provider="cluster-local", task="Move once.", actions=1))
    assert plan["policy"]["provider_profile"] == "cluster-local" and plan["policy"]["endpoint"] == endpoint
    assert plan["policy"]["api_key_env"] == "ORA_CLUSTER_LOCAL_API_KEY"
    assert "sk-cluster-local" not in json.dumps(plan)

    assert app_main.main(["providers", "remove", "cluster-local"]) == 0
    assert "cluster-local" not in user_store.user_profiles()
    assert "ORA_CLUSTER_LOCAL_API_KEY" not in user_store.stored_keys()
    # Plain HTTP off this machine is refused (reach it through a local forward), as is a source-tracked id.
    assert app_main.main(["providers", "add", "cluster", "--protocol", "openai_compatible",
                             "--endpoint", "http://10.0.0.5:30000/v1", "--model", "m", "--no-key"]) == 2
    assert app_main.main(["providers", "add", "deepseek_flash_openai_high_v1", "--protocol",
                             "openai_compatible", "--endpoint", "https://x.example/v1", "--model", "m",
                             "--no-key"]) == 2
    assert "local port forward" in capsys.readouterr().err


def test_the_endpoint_transport_follows_the_url_and_the_cluster_opt_in(config):
    """Plain HTTP off this machine is cluster_http_v1, and only when asked for; HTTPS and loopback keep theirs."""
    assert user_store.endpoint_transport("https://models.example.org/v1") == "https_v1"
    assert user_store.endpoint_transport("https://models.example.org/v1", cluster_http=True) == "https_v1"
    assert user_store.endpoint_transport("http://127.0.0.1:8000/v1", cluster_http=True) == "loopback_http_v1"
    assert user_store.endpoint_transport("http://10.0.0.5:8000/v1", cluster_http=True) == "cluster_http_v1"
    assert user_store.endpoint_transport("http://gpu-node-7:8000/v1", cluster_http=True) == "cluster_http_v1"
    with pytest.raises(user_store.UserStoreError, match="--cluster-http"):
        user_store.endpoint_transport("http://10.0.0.5:8000/v1")
    with pytest.raises(user_store.UserStoreError):
        user_store.endpoint_transport("ftp://10.0.0.5/v1", cluster_http=True)

    base = {"endpoint": "http://10.0.0.5:8000/v1", "model": "m", "supports_images": True, "cluster_http": True}
    plain = user_store.new_profile("mine", protocol="openai_compatible", **base)
    assert plain["endpoint_transport"] == "cluster_http_v1" and "reasoning_field" not in plain
    assert user_store.new_profile("mine", protocol="openai_compatible", reasoning_field="reasoning",
                                  **base)["reasoning_field"] == "reasoning"
    with pytest.raises(user_store.UserStoreError, match="reasoning_content or reasoning"):
        user_store.new_profile("mine", protocol="openai_compatible", reasoning_field="thinking", **base)
    with pytest.raises(user_store.UserStoreError, match="openai_compatible endpoint only"):
        user_store.new_profile("mine", protocol="anthropic_messages", reasoning_field="reasoning", **base)


def test_a_vllm_on_another_cluster_node_is_added_with_cluster_http_and_reaches_every_plan(config, capsys):
    """``--cluster-http`` admits ``http://NODE:PORT/v1``; ``--reasoning-field`` names where vLLM streams reasoning.

    Without the flag the endpoint is refused and nothing is stored. With it the
    profile records ``cluster_http_v1``, and a plan built from the profile
    carries the endpoint, the transport and the reasoning field.
    """
    from ora.app import interactive
    from ora.orchestrator.spec.run_spec import resolve_run_spec

    endpoint = "http://gpu-node-7:8000/v1"
    add = ["providers", "add", "node-vllm", "--protocol", "openai_compatible", "--endpoint", endpoint,
           "--model", "deepseek-v4.1-flash", "--images", "--no-key"]
    assert app_main.main(add) == 2
    assert "--cluster-http" in capsys.readouterr().err
    assert "node-vllm" not in user_store.user_profiles()

    assert app_main.main([*add, "--cluster-http", "--reasoning-field", "reasoning"]) == 0
    output = capsys.readouterr().out
    assert "cluster_http_v1" in output and "reasoning field reasoning" in output
    profile = user_store.user_profiles()["node-vllm"]
    assert profile["endpoint"] == endpoint and profile["endpoint_transport"] == "cluster_http_v1"
    assert profile["reasoning_field"] == "reasoning"

    plan = resolve_run_spec(interactive.interactive_spec(provider="node-vllm", task="Move once.", actions=1))
    assert plan["policy"]["provider_profile"] == "node-vllm" and plan["policy"]["endpoint"] == endpoint
    assert plan["policy"]["endpoint_transport"] == "cluster_http_v1"
    assert plan["policy"]["reasoning_field"] == "reasoning"


NEMOTRON_OPTIONS ={"temperature": 0.6, "top_p": 0.95, "chat_template_kwargs": {"enable_thinking": True}}
MUSE_OPTIONS = {"temperature": 1.0, "top_p": 1.0, "chat_template_kwargs": {"reasoning_effort": "high"}}


def test_request_options_are_parsed_and_checked_by_the_catalog_validator(config):
    assert user_store.parse_request_options(json.dumps(NEMOTRON_OPTIONS)) == NEMOTRON_OPTIONS
    assert user_store.parse_request_options(json.dumps(MUSE_OPTIONS)) == MUSE_OPTIONS
    refused = {
        "{temperature: 0.6}": "not valid JSON",
        "null": "is a JSON object",
        '["temperature", 0.6]': "is a JSON object",
        "{}": "non-empty mapping",
        '{"seed": 7, "temperature": 0.6}': "unsupported request_options fields: ['seed']",
        '{"max_tokens": 4096}': "unsupported request_options fields: ['max_tokens']",
        '{"temperature": 3}': "temperature must be a number in 0..2",
        '{"top_p": 0}': "top_p must be a number in (0, 1]",
        '{"top_k": 0}': "top_k must be -1 or a positive integer",
        '{"chat_template_kwargs": {}}': "chat_template_kwargs must be a non-empty mapping",
        '{"temperature": NaN}': "finite JSON values",
    }
    for text, reason in refused.items():
        with pytest.raises(user_store.UserStoreError, match=re.escape(reason)):
            user_store.parse_request_options(text)

    base = {"endpoint": "https://models.example.org/v1", "model": "m", "supports_images": True}
    with pytest.raises(user_store.UserStoreError, match="openai_compatible endpoint only"):
        user_store.new_profile("mine", protocol="anthropic_messages", request_options={"temperature": 0.5}, **base)
    with pytest.raises(user_store.UserStoreError, match="unsupported request_options fields"):
        user_store.new_profile("mine", protocol="openai_compatible", request_options={"stop": ["x"]}, **base)
    for window in (29, 1801, True, "900"):
        with pytest.raises(user_store.UserStoreError, match="30..1800 seconds"):
            user_store.new_profile("mine", protocol="openai_compatible", recovery_window_s=window, **base)
    plain = user_store.new_profile("mine", protocol="openai_compatible", **base)
    assert "request_options" not in plain and "recovery_window_s" not in plain
    assert plain["context_window_tokens"] == user_store.DEFAULT_CONTEXT_WINDOW
    assert user_store.new_profile("mine", protocol="openai_compatible", recovery_window_s=450.5,
                                  **base)["recovery_window_s"] == 450.5


def test_ora_providers_add_records_request_options_and_a_recovery_window_for_every_run(config, monkeypatch, capsys):
    from ora.agent.providers.recovery import RECOVERY_POLICY, RECOVERY_WINDOW_S
    from ora.app import interactive
    from ora.orchestrator.spec import inputs as run_spec_inputs
    from ora.orchestrator.spec.inputs import provider_policy
    from ora.orchestrator.spec.run_spec import resolve_run_spec

    assert run_spec_inputs.RECOVERY_POLICY == RECOVERY_POLICY
    for name in ("ORA_NEMOTRON_LOCAL_BASE_URL", "ORA_MUSE_LOCAL_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    endpoint = "http://127.0.0.1:30000/v1"
    assert app_main.main(["providers", "add", "nemotron-local", "--protocol", "openai_compatible",
                             "--endpoint", endpoint, "--model", "nemotron-3.5-super-vl", "--images",
                             "--context-window", "262144", "--max-output", "16384",
                             "--request-options", json.dumps(NEMOTRON_OPTIONS), "--recovery-window", "900",
                             "--no-key"]) == 0
    added = capsys.readouterr().out
    assert "Added the profile nemotron-local" in added and "its key is ORA_NEMOTRON_LOCAL_API_KEY" in added
    assert "context window 262144 tokens, max output 16384 tokens" in added and "the default" not in added
    assert ('request_options {"chat_template_kwargs":{"enable_thinking":true},"temperature":0.6,"top_p":0.95}'
            in added)
    assert "recovery window 900 s" in added

    # Without --context-window, add says it recorded the default window.
    assert app_main.main(["providers", "add", "muse-local", "--protocol", "openai_compatible",
                             "--endpoint", endpoint, "--model", "muse-spark-open", "--images",
                             "--request-options", json.dumps(MUSE_OPTIONS), "--no-key"]) == 0
    added = capsys.readouterr().out
    assert (f"context window {user_store.DEFAULT_CONTEXT_WINDOW} tokens (the default; pass --context-window "
            "with the model's own)") in added
    assert f"recovery window {RECOVERY_WINDOW_S:g} s (the default)" in added

    # providers.yaml keeps them in the catalog's own schema ...
    stored = yaml.safe_load((config / "providers.yaml").read_text())["profiles"]
    assert stored["nemotron-local"]["request_options"] == NEMOTRON_OPTIONS
    assert stored["nemotron-local"]["recovery_window_s"] == 900
    assert stored["nemotron-local"]["context_window_tokens"] == 262144
    assert stored["muse-local"]["request_options"] == MUSE_OPTIONS and "recovery_window_s" not in stored["muse-local"]
    assert user_store.user_profiles()["nemotron-local"]["request_options"] == NEMOTRON_OPTIONS

    # ... and every policy built from the profile carries them: RunSpecs, interactive and free play.
    policy = provider_policy("nemotron-local")
    assert policy["request_options"] == NEMOTRON_OPTIONS and policy["provider_recovery_window_s"] == 900
    assert policy["provider_recovery"] == RECOVERY_POLICY  # the recovery the window applies under
    assert policy["context_window_tokens"] == 262144 and policy["max_output_tokens"] == 16384
    assert provider_policy("muse-local")["request_options"] == MUSE_OPTIONS
    assert not {"provider_recovery", "provider_recovery_window_s"} & set(provider_policy("muse-local"))
    plan = resolve_run_spec(interactive.interactive_spec(provider="nemotron-local", task="Move once.", actions=1))
    assert plan["policy"]["request_options"] == NEMOTRON_OPTIONS
    assert plan["policy"]["provider_recovery_window_s"] == 900
    # A source-tracked profile with a window resolves for `ora interactive` the same way.
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    plan = resolve_run_spec(interactive.interactive_spec(
        provider="muse_spark_open_openrouter_high_16k_v1", task="Move once.", actions=1))
    assert (plan["policy"]["provider_recovery"], plan["policy"]["provider_recovery_window_s"]) == (RECOVERY_POLICY, 900)

    # The listing shows the window and options; --json shows the whole profile.
    assert app_main.main(["providers"]) == 0
    lines = {line.split("\t", 1)[0]: line for line in capsys.readouterr().out.splitlines()}
    assert "\tcontext=262144\t" in lines["nemotron-local"]
    assert ('\trequest_options={"chat_template_kwargs":{"enable_thinking":true},"temperature":0.6,"top_p":0.95}'
            in lines["nemotron-local"])
    assert "\trecovery_window=900s\t" in lines["nemotron-local"] and lines["nemotron-local"].endswith("source=user")
    assert "recovery_window" not in lines["muse-local"]
    assert "request_options=" in lines["nemotron_35_super_vl_openrouter_thinking_16k_v1"]
    assert app_main.main(["providers", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["nemotron-local"]["request_options"] == NEMOTRON_OPTIONS
    assert listed["nemotron-local"]["recovery_window_s"] == 900

    # A refused value names the reason and leaves providers.yaml as it was.
    before = (config / "providers.yaml").read_text()
    refused = [
        (["--request-options", "{temperature: 0.6}"], "not valid JSON"),
        (["--request-options", '{"seed": 7}'], "unsupported request_options fields: ['seed']"),
        (["--request-options", '{"temperature": 5}'], "temperature must be a number in 0..2"),
        (["--protocol", "anthropic_messages", "--request-options", '{"temperature": 0.5}'],
         "openai_compatible endpoint only"),
        (["--recovery-window", "5"], "30..1800 seconds"),
    ]
    for extra, reason in refused:
        arguments = ["providers", "add", "refused", "--endpoint", endpoint, "--model", "m", "--no-key", *extra]
        if "--protocol" not in extra:
            arguments += ["--protocol", "openai_compatible"]
        assert app_main.main(arguments) == 2
        assert reason in capsys.readouterr().err
    assert (config / "providers.yaml").read_text() == before and "refused" not in user_store.user_profiles()


def test_the_selector_asks_for_a_missing_key_or_a_new_endpoint_and_probes_it(config, monkeypatch, capsys):
    import getpass
    import os

    from ora.app import interactive, providers

    probed = []
    monkeypatch.setattr(providers, "probe_profile", lambda profile_id: probed.append(profile_id) or (
        True, f"connected: {profile_id}"))
    monkeypatch.setattr(interactive, "_terminal", lambda: True)
    monkeypatch.setattr(getpass, "getpass", lambda prompt: "sk-typed-here")
    picks = iter(["deepseek_flash_openai_high_v1"])
    monkeypatch.setattr(interactive, "pick", lambda title, items, **_: next(picks))
    choices = interactive.provider_choices()
    deepseek = next(item for item in choices if item["value"] == "deepseek_flash_openai_high_v1")
    assert not deepseek["ready"] and "needs DEEPSEEK_API_KEY: choose to enter it" in deepseek["description"]
    assert interactive.ADD_PROVIDER in {item["value"] for item in choices}
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")  # restored afterwards; the selector fills it from the store
    assert interactive.choose_provider(None) == "deepseek_flash_openai_high_v1"
    assert probed == ["deepseek_flash_openai_high_v1"] and "✓ connected" in capsys.readouterr().out
    assert user_store.stored_keys()["DEEPSEEK_API_KEY"] == "sk-typed-here"
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-typed-here"

    # "+ add an endpoint": its protocol, then the base URL, model, id and images, then its key.
    picks = iter([interactive.ADD_PROVIDER, "anthropic_messages"])
    answers = iter(["https://models.example.org", "my-claude", "", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.delenv("ORA_MY_CLAUDE_API_KEY", raising=False)
    monkeypatch.delenv("ORA_MY_CLAUDE_BASE_URL", raising=False)
    assert interactive.choose_provider(None) == "my-claude"
    profile = user_store.user_profiles()["my-claude"]
    assert (profile["protocol"], profile["endpoint"], profile["auth_scheme"]) == (
        "anthropic_messages", "https://models.example.org", "x_api_key")
    assert os.environ["ORA_MY_CLAUDE_BASE_URL"] == "https://models.example.org"
    assert probed[-1] == "my-claude"


def test_the_parser_names_the_user_store_s_defaults():
    """``ora providers add --help`` states the store's defaults without importing the provider package."""
    from ora.app import providers

    assert (providers.DEFAULT_CONTEXT_WINDOW, providers.DEFAULT_MAX_OUTPUT) == (
        user_store.DEFAULT_CONTEXT_WINDOW, user_store.DEFAULT_MAX_OUTPUT)
