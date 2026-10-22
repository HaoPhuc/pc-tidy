"""Small helper for talking to a local Ollama model. Shared by the scripts in this repo.

The model, Ollama's address and the time limit come from [ai] in settings.ini: the scripts pass
them to configure(). Only an address on this PC is accepted, so no file name ever leaves it.
"""

import json
import urllib.request

from pctidy import settings

_ai = {"model": settings.default_of("ai", "model"), "url": settings.default_of("ai", "url"),
       "timeout": settings.default_of("ai", "timeout_seconds")}


def configure(ai):
    """Use the model, url and timeout_seconds of an [ai] section (Settings.section("ai"))."""
    problem = settings.url_problem(ai.url)
    if problem:
        raise ValueError(problem)
    _ai.update(model=ai.model, url=ai.url.rstrip("/"), timeout=ai.timeout_seconds)


def available(model=None):
    """True if Ollama is running and the model is downloaded."""
    model = model or _ai["model"]
    try:
        with urllib.request.urlopen(f"{_ai['url']}/api/tags", timeout=3) as r:
            names = {m["name"] for m in json.loads(r.read())["models"]}
    except Exception:
        print("Ollama isn't running, so the LLM step is skipped.")
        return False
    if model not in names and f"{model}:latest" not in names:
        print(f"Model {model} not found. Run:  ollama pull {model}")
        return False
    return True


def ask_json(prompt, schema, model=None, timeout=None):
    """Send a prompt and get back a dict that follows the given JSON schema."""
    body = json.dumps({
        "model": model or _ai["model"], "prompt": prompt, "format": schema,
        "stream": False, "options": {"temperature": 0},
    }).encode()
    req = urllib.request.Request(f"{_ai['url']}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout or _ai["timeout"]) as r:
        return json.loads(json.loads(r.read())["response"])
