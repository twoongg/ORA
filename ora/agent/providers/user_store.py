"""The operator's own provider configuration: keys and profiles outside every repository and run.

A provider key is read from its environment variable first (CI and scripts
set it there), then from the user-level credential store that the terminal
writes (``ora providers``, the provider selector of ``ora interactive`` and
``ora play``) and Pi's ``/login`` writes too: ``pi-auth.json`` in the ORA
config home (``$ORA_CONFIG_HOME``, else ``$XDG_CONFIG_HOME/ora``, else
``~/.config/ora``), mode 0600, in Pi's format. ORA's entries are API keys
named ``ora:<VARIABLE>``; an API key ``/login`` stored for Pi's provider
``p`` also serves the variable ``P_API_KEY`` (``deepseek`` serves
``DEEPSEEK_API_KEY``). Custom endpoints are user provider profiles in
``providers.yaml`` beside it, in the source-tracked catalog's schema.

Nothing here reaches a plan or its evidence: a plan names the profile and its
endpoint, never a key.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping, MutableMapping
from urllib.parse import urlsplit

import yaml

from .openai_wire_options import REASONING_FIELDS, validate_request_options
from .profiles import (
    BUILTIN_PROVIDER_CATALOG, CATALOG_SCHEMA, PROBE_SCHEMA, PROVIDER_PROTOCOLS, NativeProviderProfileError,
    load_builtin_provider_profiles, load_provider_profiles, select_provider_profile,
)

CREDENTIALS_FILE = "pi-auth.json"
PROFILES_FILE = "providers.yaml"
#: A new profile's context window and output budget when ``ora providers add`` names none.
DEFAULT_CONTEXT_WINDOW = 131072
DEFAULT_MAX_OUTPUT = 8192
#: The entry name of an ORA key in the shared store.
ENTRY_PREFIX = "ora:"
_VARIABLE = re.compile(r"[A-Z_][A-Z0-9_]*")
_PROFILE_ID = re.compile(r"[a-z0-9][a-z0-9_-]*")
#: Variables this process set from the store (apply_stored_credentials): still "stored" keys.
_applied: dict[str, str] = {}


class UserStoreError(ValueError):
    """The user-level provider configuration is unreadable, or a value for it is invalid."""


# -- Where it lives ---------------------------------------------------------------------

def config_home(environ: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environ is None else environ
    base = values.get("ORA_CONFIG_HOME") or str(
        Path(values.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ora")
    return Path(base).expanduser().resolve()


def credentials_path(environ: Mapping[str, str] | None = None) -> Path:
    return config_home(environ) / CREDENTIALS_FILE


def profiles_path(environ: Mapping[str, str] | None = None) -> Path:
    return config_home(environ) / PROFILES_FILE


def _write_private(path: Path, text: str) -> None:
    """Replace ``path`` atomically with ``text``, readable by the user only."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


# -- Keys ---------------------------------------------------------------------------------

def _credentials(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    path = credentials_path(environ)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise UserStoreError(f"the credential store {path} is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise UserStoreError(f"the credential store {path} is not a JSON object")
    return value


def alias_variable(provider_id: str) -> str:
    """The variable an API key ``/login`` stored for Pi's provider serves (``deepseek`` → DEEPSEEK_API_KEY)."""

    return re.sub(r"[^A-Z0-9]+", "_", provider_id.upper()).strip("_") + "_API_KEY"


def stored_keys(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Every stored key by the variable it serves; an ``ora:`` entry wins over a /login alias."""

    keys: dict[str, str] = {}
    entries = _credentials(environ)
    for name, entry in entries.items():
        key = entry.get("key") if isinstance(entry, Mapping) and entry.get("type") == "api_key" else None
        if not isinstance(key, str) or not key.strip():
            continue
        if name.startswith(ENTRY_PREFIX):
            keys[name[len(ENTRY_PREFIX):]] = key.strip()
        else:
            keys.setdefault(alias_variable(name), key.strip())
    return keys


def key_source(variable: str, target: Mapping[str, str] | None = None) -> str:
    """``environment``, ``stored`` or ``missing``: where the key of ``variable`` comes from.

    ``target`` is the environment the key is looked up in (this process's by
    default); the store is the one this process's environment names.
    """

    values = os.environ if target is None else target
    value = values.get(variable, "").strip()
    if value and _applied.get(variable) != value:
        return "environment"
    return "stored" if variable in stored_keys() else "missing"


def _variable(variable: str) -> str:
    if not _VARIABLE.fullmatch(variable or ""):
        raise UserStoreError(f"{variable!r} is not an environment variable name")
    return variable


def save_key(variable: str, key: str, environ: Mapping[str, str] | None = None) -> Path:
    """Store ``key`` as the user's key for ``variable``; other entries (``/login``'s) are kept."""

    key = (key or "").strip()
    if not key or any(character in key for character in "\r\n"):
        raise UserStoreError("an API key is one non-empty line")
    entries = _credentials(environ)
    entries[ENTRY_PREFIX + _variable(variable)] = {"type": "api_key", "key": key}
    path = credentials_path(environ)
    _write_private(path, json.dumps(entries, indent=2) + "\n")
    return path


def remove_key(variable: str, environ: Mapping[str, str] | None = None) -> bool:
    entries = _credentials(environ)
    if entries.pop(ENTRY_PREFIX + _variable(variable), None) is None:
        return False
    _write_private(credentials_path(environ), json.dumps(entries, indent=2) + "\n")
    return True


def applied_from_store() -> dict[str, str]:
    """The variables this process's environment holds from the store (``.env`` may still set them)."""

    return {variable: key for variable, key in _applied.items() if os.environ.get(variable) == key}


def apply_stored_credentials(target: MutableMapping[str, str] | None = None) -> list[str]:
    """Set every key variable ``target`` (this process's environment) leaves unset from the store.

    Returns the names it set. A user profile's base URL variable defaults to
    its endpoint, as ``ora run`` defaults a source-tracked profile's.
    """

    values = os.environ if target is None else target
    try:
        keys = stored_keys()
        profiles = user_profiles()
    except (UserStoreError, NativeProviderProfileError):
        return []  # an unreadable store gives nothing; `ora providers` reports it
    applied = []
    for variable, key in sorted(keys.items()):
        if not values.get(variable, "").strip():
            values[variable] = key
            applied.append(variable)
            if target is None:
                _applied[variable] = key
    for profile in profiles.values():
        values.setdefault(profile["base_url_env"], profile["endpoint"])
    return applied


# -- Profiles -------------------------------------------------------------------------------

def user_profiles(environ: Mapping[str, str] | None = None) -> dict[str, dict[str, Any]]:
    path = profiles_path(environ)
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, yaml.YAMLError) as exc:
        raise UserStoreError(f"the provider profiles {path} are unreadable: {exc}") from exc
    if not isinstance(value, Mapping) or not value.get("profiles"):
        return {}
    return load_provider_profiles(path)


def all_profiles(environ: Mapping[str, str] | None = None) -> dict[str, dict[str, Any]]:
    """The source-tracked profiles, then the user's (marked ``user: true``)."""

    profiles = dict(load_builtin_provider_profiles())
    for profile_id, profile in user_profiles(environ).items():
        profiles.setdefault(profile_id, {**profile, "user": True})
    return profiles


def select_profile(profile_id: str | None, environ: Mapping[str, str] | None = None) -> tuple[str, dict[str, Any]]:
    """A source-tracked profile (the catalog default for None), or the user's ``profile_id``."""

    if profile_id is not None and profile_id not in load_builtin_provider_profiles():
        profile = user_profiles(environ).get(profile_id)
        if profile is not None:
            return profile_id, profile
    return select_provider_profile(BUILTIN_PROVIDER_CATALOG, profile_id=profile_id)


def endpoint_transport(endpoint: str, *, cluster_http: bool = False) -> str:
    """``https_v1``, or ``loopback_http_v1`` for plain HTTP on this machine.

    Plain HTTP to another host is ``cluster_http_v1`` only with ``cluster_http``
    (``ora providers add --cluster-http``): a vLLM server on another node of the
    operator's own cluster, where the key travels unencrypted. Any other URL is
    refused.
    """

    parsed = urlsplit(endpoint)
    if parsed.scheme == "https" and parsed.netloc:
        return "https_v1"
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return "loopback_http_v1"
    if cluster_http and parsed.scheme == "http" and parsed.hostname:
        return "cluster_http_v1"
    raise UserStoreError(
        f"{endpoint!r}: an endpoint is HTTPS, or HTTP on this machine (127.0.0.1); "
        "reach a cluster server over plain HTTP through a local port forward (ssh -L), "
        "or add it with --cluster-http on a network you trust")


def parse_request_options(text: str) -> dict[str, Any]:
    """``request_options`` given as JSON text (``ora providers add --request-options``), validated.

    The value is checked by the same validator as the catalog's own
    ``request_options`` (``openai_wire_options.validate_request_options``).
    """

    try:
        value = json.loads(text)
    except ValueError as exc:
        raise UserStoreError(f"request_options is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise UserStoreError('request_options is a JSON object, such as \'{"temperature": 0.6}\'')
    try:
        return validate_request_options(value) or {}
    except ValueError as exc:
        raise UserStoreError(f"invalid request_options: {exc}") from exc


def new_profile(profile_id: str, *, protocol: str, endpoint: str, model: str, supports_images: bool,
                context_window_tokens: int = DEFAULT_CONTEXT_WINDOW, max_output_tokens: int = DEFAULT_MAX_OUTPUT,
                reasoning_effort: str = "provider_default", api_key_env: str | None = None,
                request_options: Mapping[str, Any] | None = None,
                recovery_window_s: float | None = None, cluster_http: bool = False,
                reasoning_field: str | None = None) -> dict[str, Any]:
    """A complete user profile for a custom OpenAI-compatible or Anthropic endpoint.

    ``request_options`` (OpenAI-compatible only) are the closed request fields
    every call adds, such as a thinking model's sampling and
    ``chat_template_kwargs``; ``recovery_window_s`` is how long each attempt
    may wait for its first token (30..1800 s; the 180 s default when absent).
    ``cluster_http`` admits plain HTTP to another host (``cluster_http_v1``,
    see ``endpoint_transport``). ``reasoning_field`` (OpenAI-compatible only)
    names the message field the server streams reasoning in and takes it back
    in: ``reasoning_content`` when absent, ``reasoning`` for current vLLM.
    """

    if not _PROFILE_ID.fullmatch(profile_id or ""):
        raise UserStoreError("a profile id is lowercase letters, digits, '-' and '_'")
    if protocol not in PROVIDER_PROTOCOLS:
        raise UserStoreError(f"protocol must be one of {sorted(PROVIDER_PROTOCOLS)}")
    if not model.strip():
        raise UserStoreError("name the model the endpoint serves")
    options = None
    if request_options is not None:
        if protocol != "openai_compatible":
            raise UserStoreError("request_options apply to an openai_compatible endpoint only")
        try:
            options = validate_request_options(request_options)
        except ValueError as exc:
            raise UserStoreError(f"invalid request_options: {exc}") from exc
    if reasoning_field is not None:
        if protocol != "openai_compatible":
            raise UserStoreError("reasoning_field applies to an openai_compatible endpoint only")
        if reasoning_field not in REASONING_FIELDS:
            raise UserStoreError("reasoning_field is reasoning_content or reasoning")
    window = None
    if recovery_window_s is not None:
        if (isinstance(recovery_window_s, bool) or not isinstance(recovery_window_s, (int, float))
                or not 30 <= float(recovery_window_s) <= 1800):
            raise UserStoreError("the recovery window is 30..1800 seconds")
        window = int(recovery_window_s) if float(recovery_window_s).is_integer() else float(recovery_window_s)
    prefix = "ORA_" + re.sub(r"[^A-Z0-9]+", "_", profile_id.upper()).strip("_")
    optional: dict[str, Any] = {}
    if options:
        optional["request_options"] = options
    if window is not None:
        optional["recovery_window_s"] = window
    if reasoning_field is not None:
        optional["reasoning_field"] = reasoning_field
    return {
        "protocol": protocol, "endpoint": endpoint.rstrip("/"), "model": model.strip(),
        "model_env": f"{prefix}_MODEL", "base_url_env": f"{prefix}_BASE_URL",
        "api_key_env": _variable(api_key_env) if api_key_env else f"{prefix}_API_KEY",
        "proxy_env": [], "reasoning_effort": reasoning_effort,
        "context_window_tokens": int(context_window_tokens), "max_output_tokens": int(max_output_tokens),
        "stream": True, "supports_images": bool(supports_images),
        "qualification_schema": PROBE_SCHEMA, "qualification_version": 1,
        "auth_scheme": "bearer" if protocol == "openai_compatible" else "x_api_key",
        "endpoint_transport": endpoint_transport(endpoint, cluster_http=cluster_http),
        "admission": "vision_qualified" if supports_images else "text_tool_qualified",
        "usage_scope": "general_api",
        **optional,
    }


def save_profile(profile_id: str, profile: Mapping[str, Any], environ: Mapping[str, str] | None = None) -> Path:
    """Add or replace the user's ``profile_id`` (never a source-tracked id); the catalog is validated."""

    if profile_id in load_builtin_provider_profiles():
        raise UserStoreError(f"{profile_id} is a source-tracked profile; choose another id")
    profiles = dict(user_profiles(environ))
    profiles[profile_id] = dict(profile)
    return _write_profiles(profiles, environ)


def remove_profile(profile_id: str, environ: Mapping[str, str] | None = None) -> bool:
    profiles = dict(user_profiles(environ))
    if profiles.pop(profile_id, None) is None:
        return False
    _write_profiles(profiles, environ)
    return True


def _write_profiles(profiles: Mapping[str, Any], environ: Mapping[str, str] | None) -> Path:
    path = profiles_path(environ)
    text = yaml.safe_dump({"schema": CATALOG_SCHEMA, "profiles": dict(profiles)}, sort_keys=True)
    if profiles:
        with tempfile.TemporaryDirectory() as directory:
            check = Path(directory) / PROFILES_FILE
            check.write_text(text, encoding="utf-8")
            load_provider_profiles(check)  # fail closed before replacing the user's file
    _write_private(path, text)
    return path


__all__ = [
    "CREDENTIALS_FILE", "DEFAULT_CONTEXT_WINDOW", "DEFAULT_MAX_OUTPUT", "ENTRY_PREFIX", "PROFILES_FILE",
    "UserStoreError", "alias_variable", "all_profiles", "applied_from_store", "apply_stored_credentials",
    "config_home", "credentials_path", "endpoint_transport", "key_source", "new_profile", "parse_request_options",
    "profiles_path", "remove_key", "remove_profile", "save_key", "save_profile", "select_profile", "stored_keys",
    "user_profiles",
]
