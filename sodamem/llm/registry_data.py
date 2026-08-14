"""Model capability registry — a Python dict constant rather than a JSON file
read off disk at import time.

Two problems with a JSON-file version this eliminates:
  - Packaging: a data file has to be declared in `package-data`/MANIFEST and
    survive the wheel build; miss that step and every model silently falls
    back to bare deepseek ids in production, with no error until someone
    diffs behavior against the JSON's contents — a known wheel-packaging
    failure mode.
  - Silent degradation: the JSON loader wrapped `json.loads()` in
    `except Exception: logger.warning(...); _MODEL_REGISTRY = {}` — a read
    failure (missing file, bad JSON) silently emptied the registry instead
    of failing the import (spec §6.7). A dict literal can't fail to "read";
    the failure mode collapses to "won't import," which is what we want.

NOTE: `deepseek-chat` deprecated 2026-07-24 15:59 UTC. The deadline was
missed — the default was still the alias on 0806, thirteen days after it
began routing server-side to `deepseek-v4-flash` with no error of any kind.
`factory._DEFAULT_MODELS[DEEPSEEK]` is now `deepseek-v4-flash`, the model that
actually answers. The alias keeps its registry row so an explicit
`create_provider_for_model("deepseek-chat")` still resolves; nothing defaults
to it any more.
"""
from __future__ import annotations

MODEL_REGISTRY: dict[str, dict] = {
    "deepseek-v4-flash": {
        "provider": "deepseek",
        "api_model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com",
        "max_input_tokens": 1000000,
        "max_output_tokens": 393216,
        "thinking": False,
    },
    "deepseek-v4-flash-thinking": {
        "provider": "deepseek",
        "api_model": "deepseek-v4-flash-thinking",
        "base_url": "https://api.deepseek.com",
        "max_input_tokens": 1000000,
        "max_output_tokens": 393216,
        "thinking": True,
    },
    # Deprecated 2026-07-24; the API reroutes it to deepseek-v4-flash. Kept
    # so an explicit request by that name still resolves — never a default.
    "deepseek-chat": {
        "provider": "deepseek",
        "api_model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "max_input_tokens": 1000000,
        "max_output_tokens": 393216,
        "thinking": False,
    },
}
