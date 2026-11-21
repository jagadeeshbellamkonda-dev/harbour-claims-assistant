"""Interactively write a project-only .env file. Never prints the API key."""

import getpass
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent / ".env"
PROVIDERS = ("ollama", "anthropic")
DEFAULT_PROVIDER = "ollama"
DEFAULT_MODEL = "qwen2.5:3b"
DEFAULT_HOST = "http://localhost:11434"


def _ask(prompt, default, input_fn):
    answer = input_fn(f"{prompt} [{default}]: ").strip()
    return answer or default


def collect(input_fn=input, getpass_fn=getpass.getpass):
    while True:
        provider = _ask("Provider (ollama or anthropic)", DEFAULT_PROVIDER, input_fn).lower()
        if provider in PROVIDERS:
            break
        print("Please enter ollama or anthropic.")
    model = _ask("Model name", DEFAULT_MODEL, input_fn)
    host = _ask("Ollama host", DEFAULT_HOST, input_fn)
    values = {
        "APP_MODEL_PROVIDER": provider,
        "APP_MODEL_NAME": model,
        "APP_OLLAMA_HOST": host,
    }
    if provider == "anthropic":
        key = getpass_fn("Anthropic API key (hidden): ").strip()
        if key:
            values["APP_ANTHROPIC_API_KEY"] = key
    return values


def write_env(values, path=ENV_PATH):
    lines = [f"{k}={v}" for k, v in values.items()]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(input_fn=input, getpass_fn=getpass.getpass, env_path=ENV_PATH):
    env_path = Path(env_path)
    if env_path.exists():
        if input_fn(f"{env_path.name} already exists. Overwrite? [y/N]: ").strip().lower() != "y":
            print("Left the existing file unchanged.")
            return 0
    values = collect(input_fn, getpass_fn)
    write_env(values, env_path)
    print(f"Wrote {env_path.name} with: {', '.join(values)}")
    if "APP_ANTHROPIC_API_KEY" in values:
        print("The API key was saved to the file (not shown).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
