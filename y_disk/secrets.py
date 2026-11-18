"""Convenience loader for OAuth tokens kept in a local secrets file."""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_SECRETS_PATH = "~/Code/.secrets"


def load_token(name: str, secrets_path: str = DEFAULT_SECRETS_PATH) -> str:
    """Return the token stored under ``name`` in the secrets file."""
    path = Path(secrets_path).expanduser()
    text = path.read_text()
    data = _parse_env_style(text)

    if name not in data:
        raise KeyError(f"No secret named {name!r} found in {path}")
    return data[name]


def _parse_env_style(text: str) -> dict:
    result: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result
