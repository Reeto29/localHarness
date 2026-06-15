"""Minimal Ollama HTTP client. Stdlib only — no SDK, no pip deps.

Talks to a local `ollama serve` (default http://localhost:11434).
Supports the /api/chat endpoint with tool-calling.
"""

import json
import urllib.request
import urllib.error

OLLAMA_HOST = "http://localhost:11434"


def chat(model, messages, tools=None, stream=False, options=None):
    """Call Ollama's /api/chat. Returns the full parsed response body.

    The reply text/tool-calls are in body["message"]. Token/timing counts live
    at the top level: prompt_eval_count, eval_count, total_duration, etc.

    messages: list of {"role", "content", ...}
    tools:    optional list of tool schemas (OpenAI-style function defs)
    options:  optional dict of model params (temperature, num_ctx, ...)
    """
    payload = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }
    if tools:
        payload["tools"] = tools
    if options:
        payload["options"] = options

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"Ollama HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Could not reach Ollama at {OLLAMA_HOST}. Is `ollama serve` running? ({e})"
        ) from e

    return body


def generate(model, prompt, system=None, options=None):
    """One-shot completion (no tools, no history). Returns text."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = chat(model, messages, options=options)
    return body["message"].get("content", "")
