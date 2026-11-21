import json
from pathlib import Path

import pytest

import app

ROOT = Path(__file__).resolve().parent.parent
GOOD = {
    "draft_reply": "A handler will review your claim and be in touch.",
    "evidence_refs": ["H1", "H5"],
    "missing_information": [],
    "review_status": "READY_FOR_HUMAN_REVIEW",
}


def fake_returning(payload):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return lambda facts, policy, settings: text


def fake_raising(exc):
    def fn(facts, policy, settings):
        raise exc
    return fn


def never_called(facts, policy, settings):
    raise AssertionError("model must not be called")


def run_case(case_id, model_fn, capsys, **kw):
    app.run(case_id, model_fn=model_fn, **kw)
    return capsys.readouterr().out


# ---- successful result and output sections

def test_successful_c1(capsys):
    out = run_case("C1", fake_returning(GOOD), capsys)
    for section in ("SUPPLIED FACTS", "SUPPLIED POLICY", "MODEL USED", "DRAFT RESULT FOR HUMAN REVIEW"):
        assert section in out
    assert "READY_FOR_HUMAN_REVIEW" in out
    assert "A handler will review your claim" in out
    assert "A human must check" in out
    assert '"days_from_incident_to_report": 4' in out


# ---- failures become the safe UNAVAILABLE state

@pytest.mark.parametrize(
    "payload",
    [
        "this is not json",
        {**GOOD, "review_status": "APPROVED"},
        {**GOOD, "evidence_refs": ["H9"]},
        {**GOOD, "missing_information": ["shoe_size"]},
        {k: v for k, v in GOOD.items() if k != "review_status"},
        {**GOOD, "extra": 1},
    ],
)
def test_invalid_responses_are_unavailable(payload, capsys):
    out = run_case("C1", fake_returning(payload), capsys)
    assert "review_status: UNAVAILABLE" in out
    assert "reason:" in out
    assert GOOD["draft_reply"] not in out
    assert "Traceback" not in out


def test_connection_error(capsys):
    out = run_case("C1", fake_raising(ConnectionError("refused")), capsys)
    assert "review_status: UNAVAILABLE" in out
    assert "Ollama" in out
    assert "Traceback" not in out


def test_timeout_error(capsys):
    out = run_case("C1", fake_raising(TimeoutError()), capsys)
    assert "review_status: UNAVAILABLE" in out
    assert "60 seconds" in out


def test_unexpected_error_is_safe(capsys):
    out = run_case("C1", fake_raising(RuntimeError("boom")), capsys)
    assert "review_status: UNAVAILABLE" in out
    assert "boom" not in out


def test_simulate_timeout_makes_no_call(capsys):
    out = run_case("C1", never_called, capsys, simulate_timeout=True)
    assert "review_status: UNAVAILABLE" in out
    assert "No model was called" in out
    assert "draft_reply:" not in out


# ---- offline demo

@pytest.mark.parametrize("case_id", app.CASE_IDS)
def test_offline_demo_all_cases(case_id, capsys):
    out = run_case(case_id, never_called, capsys, offline_demo=True)
    assert "No model was called" in out
    assert "SYNTHETIC" in out
    assert "UNAVAILABLE" not in out
    assert "draft_reply:" in out


def test_demo_outputs_expected_statuses():
    expected = {
        "C1": "READY_FOR_HUMAN_REVIEW",
        "C2": "NEEDS_INFORMATION",
        "C3": "READY_FOR_HUMAN_REVIEW",
        "C4": "BLOCKED",
        "C5": "READY_FOR_HUMAN_REVIEW",
    }
    for cid, status in expected.items():
        assert app.load_demo_result(cid)["review_status"] == status


# ---- days calculation

def test_days_calculation():
    assert app.days_between("2026-11-10", "2026-11-14") == 4
    assert app.days_between(None, "2026-11-15") is None
    assert app.days_between("2026-11-10", None) is None


def test_facts_include_derived_days():
    assert app.build_facts(app.load_case("C1"))["days_from_incident_to_report"] == 4
    assert app.build_facts(app.load_case("C2"))["days_from_incident_to_report"] is None


# ---- input files

def test_cases_file_valid():
    cases = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))
    assert list(cases) == app.CASE_IDS
    for cid, case in cases.items():
        assert set(case) == set(app.CASE_FIELDS)
        assert case["case_id"] == cid


def test_policy_has_rules_h1_to_h5():
    policy = (ROOT / "policy.md").read_text(encoding="utf-8")
    for pid in app.POLICY_IDS:
        assert f"\n{pid}. " in policy


# ---- settings

def test_settings_defaults_and_override(monkeypatch):
    for var in ("APP_MODEL_PROVIDER", "APP_MODEL_NAME", "APP_OLLAMA_HOST"):
        monkeypatch.delenv(var, raising=False)
    s = app.get_settings()
    assert (s["provider"], s["model"], s["host"]) == ("ollama", "qwen2.5:3b", "http://localhost:11434")
    assert app.get_settings("other:1b")["model"] == "other:1b"
