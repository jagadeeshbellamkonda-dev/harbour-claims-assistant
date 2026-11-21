from dotenv import dotenv_values

import setup_env


def scripted(answers):
    it = iter(answers)
    return lambda prompt="": next(it)


def test_defaults_write_ollama_env(tmp_path, capsys):
    env = tmp_path / ".env"
    setup_env.main(input_fn=scripted(["", "", ""]), getpass_fn=lambda p: "unused", env_path=env)
    values = dotenv_values(env)
    assert values["APP_MODEL_PROVIDER"] == "ollama"
    assert values["APP_MODEL_NAME"] == "qwen2.5:3b"
    assert values["APP_OLLAMA_HOST"] == "http://localhost:11434"
    assert "APP_ANTHROPIC_API_KEY" not in values


def test_anthropic_key_written_but_not_printed(tmp_path, capsys):
    env = tmp_path / ".env"
    secret = "sk-test-SECRET-123"
    setup_env.main(
        input_fn=scripted(["anthropic", "some-model", ""]),
        getpass_fn=lambda p: secret,
        env_path=env,
    )
    assert dotenv_values(env)["APP_ANTHROPIC_API_KEY"] == secret
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err


def test_existing_env_not_overwritten_without_yes(tmp_path):
    env = tmp_path / ".env"
    env.write_text("KEEP=me\n", encoding="utf-8")
    setup_env.main(input_fn=scripted(["n"]), getpass_fn=lambda p: "", env_path=env)
    assert env.read_text(encoding="utf-8") == "KEEP=me\n"
