"""OpenAI-compatible provider: the Chat Completions wire, streams, tool names and recovery.

The provider's failures inside an episode (an output-limited tool batch, a
stream broken after content and its attempt records) are checked on the agent
core in ``tests/agent_core/test_provider_faults.py``.
"""

from __future__ import annotations
import copy
import json
import ssl
from pathlib import Path
from typing import Any
import httpx
import pytest
from ora.agent.contracts import PolicySessionError
from ora.agent.evidence.canonical import canonical_bytes
from ora.agent.session_core.gateway import openai_tool_definitions
from ora.agent.providers.openai_compatible import OpenAICompatibleProvider
from ora.agent.providers.openai_wire_options import normalize_usage
from tests.support.agent.provider_adapters import native_run_spec


def test_openai_tool_projection_flattens_union_without_weakening_runtime() -> None:
    submit = openai_tool_definitions(["robot.submit_action"])[0]["function"]
    parameters = submit["parameters"]
    assert "oneOf" not in parameters
    assert parameters["type"] == "object"
    assert parameters["required"] == ["action", "arm"]
    assert parameters["additionalProperties"] is False
    assert parameters["properties"]["action"]["enum"] == [
        "move_pose", "set_gripper",
    ]
    assert set(parameters["properties"]) == {
        "action", "arm", "position_m", "orientation_xyzw", "gripper",
    }


def test_openai_provider_reassembles_fragmented_sse_and_never_records_headers() -> None:
    events = [
        {"choices": [{"delta": {"reasoning_content": "look "}}]},
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "call-1", "function": {
                "name": "robot.submit_action", "arguments": "{\"action\":",
            },
        }]}}]},
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "function": {"arguments": "\"set_gripper\"}"},
        }]}, "finish_reason": "tool_calls"}], "usage": {
            "prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25,
            "prompt_tokens_details": {"cached_tokens": 3},
        }},
    ]

    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def iter_lines(self):
            for event in events:
                yield "data: " + json.dumps(event)
            yield "data: [DONE]"

    class Client:
        def __init__(self): self.calls = []
        def stream(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return Response()
        def close(self): return None

    client = Client()
    provider = OpenAICompatibleProvider("https://provider.invalid/v1", "secret", client=client)
    request = {"model": "m", "messages": [], "stream": True}
    response = provider.complete(request=request, model_call_id="call-0")
    assert response["message"]["reasoning_content"] == "look "
    assert response["message"]["_provider_continuation_state"][
        "provider_protocol"
    ] == "openai_chat_completions"
    assert response["message"]["tool_calls"][0]["function"]["arguments"] == '{"action":"set_gripper"}'
    assert response["usage"]["cached_input_tokens"] == 3
    assert response["usage"]["cache_read_input_tokens"] == 3
    assert response["usage"]["cache_read_status"] == "reported"
    assert response["usage"]["cache_creation_input_tokens"] is None
    assert response["usage"]["cache_creation_status"] == "missing"
    assert normalize_usage({
        "prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5,
    })["cache_read_status"] == "missing"
    assert normalize_usage({
        "prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5,
        "prompt_tokens_details": {"cached_tokens": 0},
    })["cache_read_status"] == "reported"
    # sglang puts reasoning at the top level; completion_tokens_details wins when both are present.
    assert normalize_usage({"prompt_tokens": 23495, "completion_tokens": 775, "reasoning_tokens": 668})[
        "reasoning_output_tokens"] == 668
    assert normalize_usage({"reasoning_tokens": 5, "completion_tokens_details": {"reasoning_tokens": 7}})[
        "reasoning_output_tokens"] == 7
    sent = json.loads(client.calls[0][1]["content"])
    assert sent == request
    continued_message = copy.deepcopy(response["message"])
    continued_message.pop("reasoning_content")
    replay = provider.prepare_request({
        "model": "m", "messages": [continued_message], "stream": True,
    })
    replayed_assistant = next(message for message in replay["messages"] if message["role"] == "assistant")
    assert replayed_assistant["reasoning_content"] == "look "
    assert "_provider_continuation_state" not in replayed_assistant
    assert "authorization" not in json.dumps(sent).lower()
    configured = OpenAICompatibleProvider(
        "https://provider.invalid/v1", "secret", timeout_seconds=900,
    )
    assert configured.client.timeout.connect == 10.0
    assert configured.client.timeout.read == 900
    configured.close()


@pytest.mark.parametrize(
    "error_type", [httpx.ReadError, httpx.RemoteProtocolError, ssl.SSLEOFError],
)
def test_openai_provider_retries_a_partial_stream_broken_by_transport(
    monkeypatch: pytest.MonkeyPatch,
    error_type,
) -> None:
    """A snapped socket is a transport fault whether or not bytes arrived.

    This test previously fixed the opposite rule: any exception after the
    first event became permanently fatal. That cost three runs inside
    seventy-five minutes -- 53, 51 and 18 model calls, each episode discarded
    on a transport fault that the layer below already classifies as transient.
    Nothing is committed when a stream breaks: the partial events are scanned
    for credentials and recorded as transport evidence, never delivered to the
    policy, so a second answer replaces nothing.

    What stays fatal is a stream this adapter rejected as malformed, which is
    the case below.
    """

    monkeypatch.setattr("ora.agent.providers.openai_compatible.time.sleep", lambda _seconds: None)

    class PartialThenWhole:
        status_code = 200
        attempts = 0
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def iter_lines(self):
            PartialThenWhole.attempts += 1
            yield 'data: {"choices":[{"delta":{"content":"partial"}}]}'
            if PartialThenWhole.attempts == 1:
                raise error_type("connection lost")
            yield ('data: {"choices":[{"delta":{"content":" rest"},'
                   '"finish_reason":"stop"}]}')
            yield 'data: [DONE]'

    class Client:
        calls = 0
        def stream(self, *_args, **_kwargs):
            self.calls += 1
            return PartialThenWhole()
        def close(self): return None

    PartialThenWhole.attempts = 0
    client = Client()
    provider = OpenAICompatibleProvider(
        "https://provider.invalid/v1", "secret", client=client, max_retries=2,
    )
    provider.complete(
        request={"model": "m", "messages": [], "stream": True},
        model_call_id="partial-call",
    )
    assert client.calls == 2, "the broken attempt is asked again, once"


def test_openai_provider_does_not_retry_a_stream_it_rejected_as_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asking the same question again would get the same malformed answer."""

    monkeypatch.setattr("ora.agent.providers.openai_compatible.time.sleep", lambda _seconds: None)

    class Malformed:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"partial"}}]}'
            yield 'this line is not SSE framing'

    class Client:
        calls = 0
        def stream(self, *_args, **_kwargs):
            self.calls += 1
            return Malformed()
        def close(self): return None

    client = Client()
    provider = OpenAICompatibleProvider(
        "https://provider.invalid/v1", "secret", client=client, max_retries=2,
    )
    with pytest.raises(Exception, match="not retried"):
        provider.complete(
            request={"model": "m", "messages": [], "stream": True},
            model_call_id="malformed-call",
        )
    assert client.calls == 1


def test_openai_provider_retries_a_transport_fault_before_any_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ora.agent.providers.openai_compatible.time.sleep", lambda _seconds: None)

    class OfflineClient:
        calls = 0
        def stream(self, *_args, **_kwargs):
            self.calls += 1
            raise httpx.ConnectError(
                "offline", request=httpx.Request("POST", "https://provider.invalid")
            )
        def close(self): return None

    offline = OfflineClient()
    provider = OpenAICompatibleProvider(
        "https://provider.invalid/v1", "secret", client=offline, max_retries=2
    )
    with pytest.raises(httpx.ConnectError):
        provider.complete(
            request={"model": "m", "messages": [], "stream": True},
            model_call_id="offline-call",
        )
    assert offline.calls == 3


def test_openai_provider_rejects_content_after_finish_reason() -> None:
    class Response:
        status_code = 200

        def __enter__(self): return self
        def __exit__(self, *_args): return None

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'
            yield 'data: {"choices":[{"delta":{"content":"late"}}]}'
            yield "data: [DONE]"

    class Client:
        def stream(self, *_args, **_kwargs): return Response()
        def close(self): return None

    provider = OpenAICompatibleProvider(
        "https://provider.invalid/v1", "secret", client=Client(),
    )
    with pytest.raises(PolicySessionError, match="after completion"):
        provider.complete(
            request={"model": "m", "messages": [], "stream": True},
            model_call_id="late-content",
        )


@pytest.mark.parametrize("system", [
    "Harness system.",
    [{"type": "text", "text": "Harness system."}],
    None,
])
def test_openai_alias_disclosure_joins_the_single_leading_system_message(system) -> None:
    """Qwen3.x templates reject a system message after index 0 (400)."""
    from ora.agent.providers.tool_names import tool_alias
    alias = tool_alias("vla.execute_local")
    provider = OpenAICompatibleProvider("https://provider.invalid", "secret",
                                        client=httpx.Client())
    tools = [{"type": "function", "function": {"name": "vla.execute_local",
              "parameters": {"type": "object", "properties": {}}}}]
    leading = [] if system is None else [{"role": "system", "content": system}]
    first = {"model": "m", "tools": tools,
             "messages": [*leading, {"role": "user", "content": "Go."}]}
    later = copy.deepcopy(first)
    later["messages"] += [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1",
         "type": "function", "function": {"name": "vla.execute_local", "arguments": "{}"}}]},
        {"role": "tool", "name": "vla.execute_local", "tool_call_id": "t1", "content": "{}"},
    ]
    original = copy.deepcopy(first)
    wires = [provider.prepare_request(first), provider.prepare_request(later)]
    assert first == original
    disclosure = ("<wire_tool_aliases>\n" + json.dumps({"vla.execute_local": alias})
                  + "\n</wire_tool_aliases>")
    for wire in wires:
        roles = [message["role"] for message in wire["messages"]]
        assert roles.count("system") == 1 and roles[0] == "system"
        content = wire["messages"][0]["content"]
        if system is None:
            assert content == disclosure
        elif isinstance(system, str):
            assert content == "Harness system.\n\n" + disclosure
        else:
            assert content == [*system, {"type": "text", "text": disclosure}]
    # Continuation keeps the provider prefix byte-identical for prefix caching.
    assert canonical_bytes(wires[0]["messages"][:2]) == canonical_bytes(wires[1]["messages"][:2])


@pytest.mark.parametrize("content", [None, ""])
def test_openai_alias_disclosure_fills_an_empty_leading_system_message(content) -> None:
    from ora.agent.providers.tool_names import tool_alias
    provider = OpenAICompatibleProvider("https://provider.invalid", "secret",
                                        client=httpx.Client())
    wire = provider.prepare_request({"model": "m", "messages": [
        {"role": "system", "content": content}, {"role": "user", "content": "Go."}],
        "tools": [{"type": "function", "function": {"name": "robot.submit_action",
                   "parameters": {"type": "object", "properties": {}}}}]})
    assert [message["role"] for message in wire["messages"]] == ["system", "user"]
    assert wire["messages"][0]["content"] == (
        "<wire_tool_aliases>\n"
        + json.dumps({"robot.submit_action": tool_alias("robot.submit_action")})
        + "\n</wire_tool_aliases>")


def test_openai_alias_disclosure_refuses_a_non_text_system_message() -> None:
    provider = OpenAICompatibleProvider("https://provider.invalid", "secret",
                                        client=httpx.Client())
    with pytest.raises(PolicySessionError, match="system message content is not text"):
        provider.prepare_request({"model": "m", "messages": [
            {"role": "system", "content": {"text": "not a part list"}},
            {"role": "user", "content": "Go."}],
            "tools": [{"type": "function", "function": {"name": "robot.submit_action",
                       "parameters": {"type": "object", "properties": {}}}}]})


@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize("reasoning_text", ["inspect image", ""])
def test_openai_strict_tool_names_roundtrip_and_signed_history(stream, reasoning_text) -> None:
    from ora.agent.providers.tool_names import tool_alias
    alias = tool_alias("vision.segment")
    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        assert body["tools"][0]["function"]["name"] == alias
        assert body["tool_choice"]["function"]["name"] == alias
        assert "<wire_tool_aliases>" in body["messages"][0]["content"]
        message = {"role":"assistant", "reasoning_content":reasoning_text, "content":"",
            "tool_calls":[{"id":"t1", "type":"function", "function":{"name":alias,"arguments":"{}"}}]}
        if not stream:
            return httpx.Response(200, json={"choices":[{"message":message,"finish_reason":"tool_calls"}]})
        # DeepSeek can send an explicit empty reasoning fragment first, then
        # null fragments while emitting a complete tool call. Preserve the
        # empty string rather than treating this accepted turn as missing data.
        initial = {"choices":[{"delta":{"reasoning_content":reasoning_text},"finish_reason":None}]}
        event = {"choices":[{"delta":{**message,"reasoning_content":None},"finish_reason":"tool_calls"}]}
        return httpx.Response(200, text="data: "+json.dumps(initial)+"\n\ndata: "+json.dumps(event)+"\n\ndata: [DONE]\n\n")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider("https://provider.invalid", "secret", client=client)
    request = {"model":"m", "messages":[{"role":"user","content":"Use vision.segment"}],
        "stream":stream, "tools":[{"type":"function","function":{"name":"vision.segment",
            "parameters":{"type":"object","properties":{}}}}],
        "tool_choice":{"type":"function","function":{"name":"vision.segment"}}}
    original = copy.deepcopy(request)
    response = provider.complete(request=request, model_call_id="strict-alias")
    assert request == original
    assistant = response["message"]
    assert assistant["tool_calls"][0]["function"]["name"] == "vision.segment"
    # A later tool-less phase still preserves the historical wire names and
    # validates reasoning against the canonical assistant before projection.
    history = {"model":"m", "messages":[assistant,
        {"role":"tool","name":"vision.segment","tool_call_id":"t1","content":"{}"}]}
    wire = provider.prepare_request(history)
    replay = next(m for m in wire["messages"] if m["role"] == "assistant")
    assert replay["tool_calls"][0]["function"]["name"] == alias
    assert assistant["reasoning_content"] == reasoning_text
    assert replay["reasoning_content"] == reasoning_text
    assert wire["messages"][-1]["name"] == alias
    assert assistant["tool_calls"][0]["function"]["name"] == "vision.segment"
    changed = copy.deepcopy(history)
    changed["messages"][0]["tool_calls"][0]["function"]["name"] = "vision.other"
    with pytest.raises(PolicySessionError):
        provider.prepare_request(changed)
    client.close()


@pytest.mark.parametrize(("returned", "declared_name"), [
    ('approach_world" string="false', None),  # a parameter header read as the name
    ("move_pose", "move_pose"),
    ("approach_world", "approach_world"),
    ("vision.segment", "vision.segment"),
    ("vision_segment_alias", "vision.segment"),
])
def test_openai_returned_tool_name_never_rewrites_the_wire_prefix(returned, declared_name) -> None:
    """An undeclared non-strict name takes its alias; every other name keeps its identity."""
    from ora.agent.providers.tool_names import tool_alias
    if returned == "vision_segment_alias":
        returned = tool_alias("vision.segment")
    expected = tool_alias(returned) if declared_name is None else declared_name

    def handler(request):
        call = {"index": 0, "id": "chatcmpl-tool-1", "type": "function",
                "function": {"name": returned, "arguments": '{"position_m": [4.649, -0.8, 0.79]}'}}
        events = [{"choices": [{"delta": {"reasoning_content": "move_pose"}, "finish_reason": None}]},
                  {"choices": [{"delta": {"tool_calls": [call]}, "finish_reason": "tool_calls"}]}]
        return httpx.Response(200, text="".join(
            "data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n")

    tools = [{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
             for name in ("observe", "move_pose", "vision.segment")]
    first = {"model": "m", "stream": True, "tools": tools, "messages": [
        {"role": "system", "content": "Harness system."}, {"role": "user", "content": "Open the drawer."}]}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider("https://provider.invalid", "secret", client=client)
        assistant = provider.complete(request=first, model_call_id="model-call-000037")["message"]
        name = assistant["tool_calls"][0]["function"]["name"]
        later = copy.deepcopy(first)
        later["messages"] += [assistant, {"role": "tool", "name": name,
                                          "tool_call_id": "chatcmpl-tool-1", "content": "{}"}]
        wires = [provider.prepare_request(first), provider.prepare_request(later)]
    # The cached prefix (system message 0 with any alias disclosure) is unchanged.
    assert canonical_bytes(wires[0]["messages"]) == canonical_bytes(wires[1]["messages"][:2])
    assert name == expected


def test_openai_tool_alias_collision_is_rejected_before_dispatch() -> None:
    from ora.agent.providers.tool_names import tool_alias
    request = {"model":"m", "messages":[], "tools":[
        {"type":"function", "function":{"name":name, "parameters":{"type":"object"}}}
        for name in ("vision.segment", tool_alias("vision.segment"))]}
    with pytest.raises(PolicySessionError, match="alias collision"):
        OpenAICompatibleProvider.prepare_request(request)


def test_openai_fragmented_tool_names_restore_aliases_and_multiple_indices():
    from ora.agent.providers.tool_names import tool_alias
    names = ['robot.submit_action', 'observe']
    aliases = [tool_alias(n) for n in names]
    events = []
    for half in range(2):
        calls = [{'index': i, **({'id': f'c{i}'} if half == 0 else {}),
                  'function': {'name': alias[:4] if half == 0 else alias[4:],
                               'arguments': '{' if half == 0 else '}'}}
                 for i, alias in enumerate(aliases)]
        events.append({'choices': [{'delta': {'tool_calls': calls},
                                     'finish_reason': 'tool_calls' if half else None}]})
    def handler(request):
        return httpx.Response(200, text=''.join(
            'data: ' + json.dumps(e) + '\n\n' for e in events) + 'data: [DONE]\n\n')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider('https://offline.invalid', 'offline-key', client=client)
        response = provider.complete(request={'model': 'model', 'stream': True,
            'messages': [{'role': 'user', 'content': 'act'}], 'tools': [
                {'type': 'function', 'function': {'name': n, 'parameters': {'type': 'object'}}}
                for n in names]}, model_call_id='fragments')
    calls = response['message']['tool_calls']
    assert [c['function']['name'] for c in calls] == names
    assert [c['function']['arguments'] for c in calls] == ['{}', '{}']
    assert [c['id'] for c in calls] == ['c0', 'c1']


@pytest.mark.parametrize('has_partial', [True, False])
def test_openai_clean_premature_eof_retries_exact_request(monkeypatch, has_partial):
    monkeypatch.setattr('ora.agent.providers.openai_compatible.time.sleep', lambda _: None)
    requests, failures = [], []
    def handler(request):
        requests.append(request.content)
        if len(requests) == 1:
            event = {'choices': [{'delta': {'reasoning_content': 'discard partial'}}]}
            return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\n' if has_partial else '')
        event = {'choices': [{'delta': {'content': 'done'}, 'finish_reason': 'stop'}]}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider('https://offline.invalid', 'offline-key', client=client,
            attempt_observer=failures.append)
        response = provider.complete(request={'model': 'model', 'stream': True,
            'messages': [{'role': 'user', 'content': 'act'}]}, model_call_id='eof')
    assert len(requests) == 2 and requests[0] == requests[1]
    assert response['attempts'] == 2
    assert response['message'] == {'role': 'assistant', 'content': 'done'}
    assert 'discard partial' not in json.dumps(response['raw_events'])
    assert len(failures) == 1 and failures[0]['retry_scheduled'] is True
    assert failures[0]['partial_event_count'] == int(has_partial)
    assert failures[0]['tool_execution_started'] is False


# --- Transport breaks after response content (run 1acf0b36b8d9) --------------

_INCOMPLETE_CHUNKED = ("peer closed connection without sending complete message "
                       "body (incomplete chunked read)")
_PARTIAL_REASONING = {"choices": [{"delta": {"role": "assistant", "reasoning_content": "H"},
                                   "finish_reason": None}]}


def test_explicit_legacy_profiles_keep_their_historical_stream_break_budget(monkeypatch):
    """legacy_v1 still grants max_retries (default two) retries, no more."""
    monkeypatch.setattr("ora.agent.providers.openai_compatible.time.sleep", lambda _seconds: None)
    failures, calls = [], []

    class Broken:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def iter_lines(self):
            yield "data: " + json.dumps(_PARTIAL_REASONING)
            raise httpx.RemoteProtocolError(_INCOMPLETE_CHUNKED)

    class Client:
        def stream(self, *_args, **kwargs):
            calls.append(kwargs["content"])
            return Broken()
        def close(self): return None

    p = OpenAICompatibleProvider("https://offline.invalid", "offline-primary-key", client=Client(),
                                 attempt_observer=failures.append)
    with pytest.raises(PolicySessionError, match="retryable transport fault"):
        p.complete(request={"model": "m", "messages": [], "stream": True}, model_call_id="legacy")
    assert len(calls) == 3
    assert [row["retry_scheduled"] for row in failures] == [True, True, False]
    assert all("stream_break_retry_limit" not in row and "stream_breaks" not in row for row in failures)
    p.close()


class _UnaryChatClient:
    """Records each Chat Completions call and answers one non-streamed reply."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def stream(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))

        class Response:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return json.dumps({
                    "choices": [{"message": {"role": "assistant", "content": "ok"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                }).encode()

        return Response()

    def close(self) -> None:
        return None


def _unary_chat(provider: OpenAICompatibleProvider) -> dict[str, Any]:
    return provider.complete(request={
        "model": "gpt-6-astra", "messages": [{"role": "user", "content": "ok"}],
        "max_tokens": 16, "stream": False,
    }, model_call_id="affinity")


def test_openai_provider_sends_session_header_only_when_configured_on_loopback() -> None:
    """X-Session-ID keeps a loopback gateway's prompt cache (2026-10-02: 0 -> ~97% cached)."""
    from ora.agent.providers.factory import build_provider

    client = _UnaryChatClient()
    provider = build_provider(
        "openai_compatible", base_url="http://127.0.0.1:8319/v1", api_key="secret",
        session_affinity_id="ora:run-1:native",
        output_token_parameter="max_completion_tokens",
    )
    provider.client.close()
    provider.client = client
    _unary_chat(provider)
    _unary_chat(provider)
    (first_args, first), (_args, second) = client.calls
    assert first_args[1] == "http://127.0.0.1:8319/v1/chat/completions"
    assert first["headers"]["authorization"] == "Bearer secret"
    # One stable run identity on every request of the session.
    assert first["headers"]["x-session-id"] == second["headers"]["x-session-id"] == (
        "ora:run-1:native"
    )
    assert json.loads(first["content"])["max_completion_tokens"] == 16

    unbound = _UnaryChatClient()
    _unary_chat(OpenAICompatibleProvider("http://127.0.0.1:8319/v1", "secret", client=unbound))
    assert "x-session-id" not in unbound.calls[0][1]["headers"]

    for endpoint in ("https://api.deepseek.com", "https://127.0.0.1:8319/v1"):
        OpenAICompatibleProvider(endpoint, "secret", client=_UnaryChatClient(),
                                 session_affinity_id="ora:run-1:native")
    for endpoint in ("http://provider.invalid/v1", "http://10.0.0.5:30743/v1"):
        never = _UnaryChatClient()
        with pytest.raises(PolicySessionError, match="only for a loopback gateway or an HTTPS endpoint"):
            OpenAICompatibleProvider(endpoint, "secret", client=never,
                                     session_affinity_id="ora:run-1:native")
        assert never.calls == []
    with pytest.raises(PolicySessionError, match="invalid provider session-affinity identity"):
        OpenAICompatibleProvider("http://127.0.0.1:8319/v1", "secret", client=_UnaryChatClient(),
                                 session_affinity_id="run id with spaces")


def test_openai_runspec_admits_session_affinity_on_loopback_and_https(tmp_path: Path) -> None:
    from ora.orchestrator.spec.run_spec import RunSpecError, resolve_run_spec

    spec = native_run_spec(tmp_path)
    plain = resolve_run_spec(spec, root=tmp_path)
    loopback = copy.deepcopy(spec)
    loopback["policy"].update({
        "endpoint": "http://127.0.0.1:8319/v1", "endpoint_transport": "loopback_http_v1",
        "provider_session_affinity": "x_session_id_run_v1",
        "output_token_parameter": "max_completion_tokens",
    })
    resolved = resolve_run_spec(loopback, root=tmp_path)
    assert resolved["policy"]["provider"] == "openai_compatible"
    assert resolved["policy"]["provider_session_affinity"] == "x_session_id_run_v1"
    assert resolved["plan_sha256"] != plain["plan_sha256"]
    https = copy.deepcopy(spec)
    https["policy"]["provider_session_affinity"] = "x_session_id_run_v1"
    assert resolve_run_spec(https, root=tmp_path)["policy"]["provider_session_affinity"] == "x_session_id_run_v1"
    unknown = copy.deepcopy(loopback)
    unknown["policy"]["provider_session_affinity"] = "x_session_id_episode_v1"
    with pytest.raises(RunSpecError, match="unsupported policy.provider_session_affinity"):
        resolve_run_spec(unknown, root=tmp_path)


def test_openai_loopback_profile_may_declare_affinity_and_output_parameter(tmp_path: Path) -> None:
    from ora.agent.providers.profiles import NativeProviderProfileError, load_provider_profiles

    def catalog(**fields: Any) -> Path:
        profile = {
            "protocol": "openai_compatible", "endpoint": "http://127.0.0.1:8319/v1",
            "endpoint_transport": "loopback_http_v1", "model": "gpt-6-astra",
            "model_env": "CODEX_PROXY_MODEL_ID", "base_url_env": "CODEX_PROXY_OPENAI_BASE_URL",
            "api_key_env": "CODEX_PROXY_API_KEY", "proxy_env": [], "reasoning_effort": "high",
            "context_window_tokens": 262144, "max_output_tokens": 16384, "stream": True,
            "supports_images": True, "session_affinity": "x_session_id_run_v1",
            "output_token_parameter": "max_completion_tokens",
            "qualification_schema": "ora.native-provider-probe.v1",
            "qualification_version": 1, **fields,
        }
        path = tmp_path / "catalog.yaml"
        path.write_text(json.dumps({"schema": "ora.native-provider-catalog.v1",
                                    "profiles": {"astra": profile}}))
        return path

    loaded = load_provider_profiles(catalog())["astra"]
    assert loaded["session_affinity"] == "x_session_id_run_v1"
    assert loaded["output_token_parameter"] == "max_completion_tokens"
    https = load_provider_profiles(catalog(endpoint="https://api.openai.invalid/v1", endpoint_transport="https_v1"))
    assert https["astra"]["session_affinity"] == "x_session_id_run_v1"
    with pytest.raises(NativeProviderProfileError, match="invalid output_token_parameter"):
        load_provider_profiles(catalog(output_token_parameter="max_output_tokens"))
    with pytest.raises(NativeProviderProfileError, match="invalid output_token_parameter"):
        load_provider_profiles(catalog(protocol="anthropic_messages"))


def test_a_cluster_http_profile_is_plain_http_without_affinity_or_a_gateway(tmp_path: Path) -> None:
    from ora.agent.providers.profiles import NativeProviderProfileError, load_provider_profiles

    def catalog(**fields: Any) -> Path:
        profile = {
            "protocol": "openai_compatible", "endpoint": "http://gpu-node-7:8000/v1",
            "endpoint_transport": "cluster_http_v1", "model": "deepseek-v4.1-flash",
            "model_env": "ORA_NODE_MODEL", "base_url_env": "ORA_NODE_BASE_URL",
            "api_key_env": "ORA_NODE_API_KEY", "proxy_env": [], "reasoning_effort": "provider_default",
            "context_window_tokens": 131072, "max_output_tokens": 8192, "stream": True,
            "supports_images": True, "reasoning_field": "reasoning",
            "qualification_schema": "ora.native-provider-probe.v1",
            "qualification_version": 1, **fields,
        }
        path = tmp_path / "catalog.yaml"
        path.write_text(json.dumps({"schema": "ora.native-provider-catalog.v1",
                                    "profiles": {"node": profile}}))
        return path

    loaded = load_provider_profiles(catalog())["node"]
    assert loaded["endpoint_transport"] == "cluster_http_v1" and loaded["reasoning_field"] == "reasoning"
    assert load_provider_profiles(catalog(endpoint="http://10.0.0.5:8000/v1"))["node"]["endpoint"] == (
        "http://10.0.0.5:8000/v1")
    with pytest.raises(NativeProviderProfileError, match="unsafe endpoint"):
        load_provider_profiles(catalog(endpoint="https://gpu-node-7:8000/v1"))
    with pytest.raises(NativeProviderProfileError, match="unsafe endpoint"):
        load_provider_profiles(catalog(endpoint="http://user:secret@gpu-node-7:8000/v1"))
    with pytest.raises(NativeProviderProfileError, match="limits session affinity"):
        load_provider_profiles(catalog(session_affinity="x_session_id_run_v1"))
    with pytest.raises(NativeProviderProfileError, match="gateway binding requires loopback_http_v1"):
        load_provider_profiles(catalog(gateway_client_binding=True))


def test_openai_runspec_admits_plain_http_to_a_cluster_host_only_as_cluster_http_v1(tmp_path: Path) -> None:
    from ora.orchestrator.spec.run_spec import RunSpecError, resolve_run_spec

    spec = native_run_spec(tmp_path)
    remote_http = copy.deepcopy(spec)
    remote_http["policy"]["endpoint"] = "http://gpu-node-7:8000/v1/"
    with pytest.raises(RunSpecError, match="must use HTTPS"):
        resolve_run_spec(remote_http, root=tmp_path)
    cluster = copy.deepcopy(remote_http)
    cluster["policy"]["endpoint_transport"] = "cluster_http_v1"
    resolved = resolve_run_spec(cluster, root=tmp_path)
    assert resolved["policy"]["endpoint"] == "http://gpu-node-7:8000/v1"
    assert resolved["policy"]["endpoint_transport"] == "cluster_http_v1"
    assert resolved["plan_sha256"] != resolve_run_spec(spec, root=tmp_path)["plan_sha256"]
    for field, value, reason in (
        ("endpoint", "https://gpu-node-7:8000/v1", "cluster_http_v1 endpoint must use HTTP"),
        ("endpoint", "http://gpu-node-7:8000/v1?token=x", "must not contain credentials, query or fragment"),
        ("provider_session_affinity", "x_session_id_run_v1", "session affinity is admitted only"),
        ("gateway_client_binding", True, "requires loopback_http_v1"),
    ):
        changed = copy.deepcopy(cluster)
        changed["policy"][field] = value
        with pytest.raises(RunSpecError, match=reason):
            resolve_run_spec(changed, root=tmp_path)


def _stream_client(events):
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def iter_lines(self):
            for event in events:
                yield "data: " + json.dumps(event)
            yield "data: [DONE]"

    class Client:
        def stream(self, *args, **kwargs): return Response()
        def close(self): return None

    return Client()


def test_openai_provider_reads_and_replays_a_vllm_reasoning_field() -> None:
    # vLLM streams reasoning as `reasoning` and reads only `reasoning` back from
    # history; the text is still held internally as reasoning_content.
    from ora.agent.providers.openai_compatible import OpenAICompatibleProvider
    client = _stream_client([
        {"choices": [{"delta": {"reasoning": "think "}}]},
        {"choices": [{"delta": {"reasoning_content": "ignored"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-1", "function": {
            "name": "robot.submit_action", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]},
    ])
    provider = OpenAICompatibleProvider("https://provider.invalid/v1", "secret", client=client,
                                        reasoning_field="reasoning")
    response = provider.complete(request={"model": "m", "messages": [], "stream": True}, model_call_id="call-0")
    assert response["message"]["reasoning_content"] == "think "
    replay = provider.prepare_request({"model": "m", "messages": [copy.deepcopy(response["message"])]})
    assistant = next(m for m in replay["messages"] if m["role"] == "assistant")
    assert assistant["reasoning"] == "think " and "reasoning_content" not in assistant
    with pytest.raises(PolicySessionError, match="reasoning_field"):
        OpenAICompatibleProvider("https://provider.invalid/v1", "secret", client=client, reasoning_field="thoughts")
    with pytest.raises(PolicySessionError, match="unsigned"):
        OpenAICompatibleProvider.prepare_request({"model": "m", "messages": [
            {"role": "assistant", "content": "", "reasoning": "forged"}]})


def test_openai_wire_omits_an_empty_tools_array() -> None:
    # vLLM rejects "tools": [] with HTTP 400 (the preflight continuity-resume probe sends it).
    from ora.agent.providers.openai_compatible import OpenAICompatibleProvider
    body = OpenAICompatibleProvider.prepare_request({"model": "m", "messages": [], "tools": []})
    assert "tools" not in body


def test_reasoning_field_and_recovery_window_are_validated_by_profile_and_factory(tmp_path) -> None:
    from ora.agent.providers.factory import build_provider
    from ora.agent.providers.profiles import NativeProviderProfileError, load_provider_profiles
    provider = build_provider("openai_compatible", base_url="https://example.invalid", api_key="test",
                                     reasoning_field="reasoning")
    assert provider.reasoning_field == "reasoning"
    provider.close()
    with pytest.raises(PolicySessionError, match="reasoning_field"):
        build_provider("anthropic_messages", base_url="https://example.invalid", api_key="test",
                              reasoning_field="reasoning")
    base = {"protocol": "openai_compatible", "endpoint": "https://example.invalid/v1", "model": "m",
            "model_env": "M_ID", "base_url_env": "M_URL", "api_key_env": "M_KEY", "proxy_env": [],
            "reasoning_effort": "low", "context_window_tokens": 100000, "max_output_tokens": 1000,
            "stream": True, "supports_images": False,
            "qualification_schema": "ora.native-provider-probe.v1", "qualification_version": 1}
    catalog = tmp_path / "profiles.yaml"
    for extra, valid in (({"reasoning_field": "reasoning", "recovery_window_s": 900}, True),
                         ({"reasoning_field": "thoughts"}, False), ({"recovery_window_s": 5}, False)):
        catalog.write_text(json.dumps({"schema": "ora.native-provider-catalog.v1",
                                       "profiles": {"p": dict(base, **extra)}}), encoding="utf-8")
        if valid:
            loaded = load_provider_profiles(catalog)["p"]
            assert loaded["reasoning_field"] == "reasoning" and loaded["recovery_window_s"] == 900
        else:
            with pytest.raises(NativeProviderProfileError):
                load_provider_profiles(catalog)
