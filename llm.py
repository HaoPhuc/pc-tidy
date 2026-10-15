"""Small helper for talking to a local Ollama model. Shared by the scripts in this repo."""

import json
import urllib.request

MODEL = "llama3.2:3b"
OLLAMA = "http://localhost:11434"


def available(model=MODEL):
    """True if Ollama is running and the model is downloaded."""
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=3) as r:
            names = {m["name"] for m in json.loads(r.read())["models"]}
    except Exception:
        print("Ollama isn't running, so the LLM step is skipped.")
        return False
    if model not in names and f"{model}:latest" not in names:
        print(f"Model {model} not found. Run:  ollama pull {model}")
        return False
    return True


def ask_json(prompt, schema, model=MODEL, timeout=180):
    """Send a prompt and get back a dict that follows the given JSON schema."""
    body = json.dumps({
        "model": model, "prompt": prompt, "format": schema,
        "stream": False, "options": {"temperature": 0},
    }).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(json.loads(r.read())["response"])
