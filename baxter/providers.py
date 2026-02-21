import json
import os
import urllib.error
import urllib.request


PROVIDERS = {
    "groq": {
        "env_key": "GROQ_API_KEY",
        "default_model": "llama-3.1-8b-instant",
        "url": "https://api.groq.com/openai/v1/chat/completions",
    },
    "openai": {
        "env_key": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
        "url": "https://api.openai.com/v1/responses",
    },
    "anthropic": {
        "env_key": "ANTHROPIC_API_KEY",
        "default_model": "claude-3-5-haiku-latest",
        "url": "https://api.anthropic.com/v1/messages",
    },
}

PROVIDER_MODELS = {
    "groq": [
        "llama-3.1-8b-instant",
        "openai/gpt-oss-120b",
        "groq/compound",
    ],
    "openai": [
        "gpt-4o-mini",
        "gpt-5-mini",
        "codex-3.5",
    ],
    "anthropic": [
        "claude-3-5-haiku-latest",
        "claude-3-5-sonnet-latest",
        "claude-3-7-sonnet-latest",
    ],
}

OPENAI_MODEL_ENV = "OPENAI_MODELS_ALLOWLIST"


def provider_has_key(provider: str) -> bool:
    spec = PROVIDERS.get(provider)
    if not spec:
        return False
    return bool(os.getenv(spec["env_key"]))


def get_default_model(provider: str) -> str:
    spec = PROVIDERS.get(provider)
    if not spec:
        raise RuntimeError(f"unknown provider: {provider}")
    return spec["default_model"]


def get_provider_models(provider: str) -> list[str]:
    if provider == "openai":
        fetched = _list_openai_models()
        if fetched:
            allowlist = _openai_allowlist()
            filtered = [m for m in fetched if m in allowlist]
            if filtered:
                return filtered
    models = PROVIDER_MODELS.get(provider, [])
    if models:
        return list(models)
    return [get_default_model(provider)]


def _request_json(
    url: str, payload: dict, headers: dict, timeout_sec: int = 60
) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body}") from e


def _request_json_get(url: str, headers: dict, timeout_sec: int = 60) -> dict:
    req = urllib.request.Request(
        url,
        headers=headers,
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body}") from e


def _list_openai_models() -> list[str]:
    spec = PROVIDERS["openai"]
    api_key = os.getenv(spec["env_key"])
    if not api_key:
        return []

    try:
        result = _request_json_get(
            "https://api.openai.com/v1/models",
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            },
        )
    except Exception:
        return []

    ids = []
    for item in result.get("data", []):
        if isinstance(item, dict):
            model_id = item.get("id")
            if isinstance(model_id, str) and model_id.strip():
                ids.append(model_id.strip())
    return sorted(set(ids))


def _openai_allowlist() -> list[str]:
    raw = os.getenv(OPENAI_MODEL_ENV, "").strip()
    if raw:
        seen = set()
        parsed = []
        for part in raw.split(","):
            model_id = part.strip()
            if model_id and model_id not in seen:
                seen.add(model_id)
                parsed.append(model_id)
        if parsed:
            return parsed
    return list(PROVIDER_MODELS.get("openai", []))


def _call_openai_compatible(
    provider: str, messages, model: str, temperature: float
) -> str:
    spec = PROVIDERS[provider]
    api_key = os.getenv(spec["env_key"])
    if not api_key:
        raise RuntimeError(f"{spec['env_key']} is missing. Put it in .env and restart.")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    result = _request_json(
        spec["url"],
        payload,
        {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )
    return result["choices"][0]["message"]["content"]


def _call_openai_responses(messages, model: str, temperature: float) -> str:
    spec = PROVIDERS["openai"]
    api_key = os.getenv(spec["env_key"])
    if not api_key:
        raise RuntimeError(f"{spec['env_key']} is missing. Put it in .env and restart.")

    system_parts = []
    input_items = []
    for m in messages:
        role = str(m.get("role", "user"))
        content = str(m.get("content", ""))
        if role == "system":
            system_parts.append(content)
            continue
        if role not in {"user", "assistant"}:
            role = "user"
        input_items.append(
            {
                "role": role,
                "content": [{"type": "input_text", "text": content}],
            }
        )

    payload = {
        "model": model,
        "input": input_items,
    }
    if system_parts:
        payload["instructions"] = "\n\n".join(system_parts)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }

    result = _request_json(spec["url"], payload, headers)

    output = result.get("output")
    if isinstance(output, list):
        text_parts = []
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") in {
                    "output_text",
                    "text",
                }:
                    text_parts.append(str(content.get("text", "")))
        combined = "".join(text_parts).strip()
        if combined:
            return combined

    output_text = result.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    raise RuntimeError(f"OpenAI response had no text output: {json.dumps(result)}")


def _call_anthropic(messages, model: str, temperature: float) -> str:
    spec = PROVIDERS["anthropic"]
    api_key = os.getenv(spec["env_key"])
    if not api_key:
        raise RuntimeError(f"{spec['env_key']} is missing. Put it in .env and restart.")

    system_parts = []
    anthropic_messages = []
    for m in messages:
        role = m.get("role")
        content = str(m.get("content", ""))
        if role == "system":
            system_parts.append(content)
        elif role in {"user", "assistant"}:
            anthropic_messages.append({"role": role, "content": content})

    payload = {
        "model": model,
        "max_tokens": 2048,
        "messages": anthropic_messages,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)

    result = _request_json(
        spec["url"],
        payload,
        {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": "Mozilla/5.0",
        },
    )

    content_blocks = result.get("content") or []
    text_parts = []
    for block in content_blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(str(block.get("text", "")))
    return "".join(text_parts)


def call_provider(provider: str, messages, model: str, temperature: float = 0.2) -> str:
    if provider not in PROVIDERS:
        raise RuntimeError(f"unknown provider: {provider}")
    try:
        if provider == "groq":
            return _call_openai_compatible(provider, messages, model, temperature)
        if provider == "openai":
            return _call_openai_responses(messages, model, temperature)
        if provider == "anthropic":
            return _call_anthropic(messages, model, temperature)
        raise RuntimeError(f"unsupported provider: {provider}")
    except Exception as e:
        raise RuntimeError(f"[{provider}] {e}") from e
