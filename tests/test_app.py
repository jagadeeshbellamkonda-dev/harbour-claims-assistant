import json
from pathlib import Path

import pytest

import app

ROOT = Path(__file__).resolve().parent.parent
GOOD_DRAFT = "A handler will review your claim and be in touch."


def draft_json(text=GOOD_DRAFT):
    return json.dumps({"draft_reply": text})


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


def facts_for(case_id):
    return app.build_facts(app.load_case(case_id))


# ---- decide_status (code decides)

EXPECTED = {
    "C1": ("READY_FOR_HUMAN_REVIEW", ["H1", "H5"], []),
    "C2": ("NEEDS_INFORMATION", ["H2"], ["incident_date"]),
    "C3": ("READY_FOR_HUMAN_REVIEW", ["H4", "H5"], []),
    "C4": ("BLOCKED", ["H3", "H5"], []),
    "C5": ("READY_FOR_HUMAN_REVIEW", ["H5"], []),
}


@pytest.mark.parametrize("case_id", app.CASE_IDS)
def test_decide_status_all_cases(case_id):
    d = app.decide_status(facts_for(case_id))
    status, refs, missing = EXPECTED[case_id]
    assert (d["review_status"], d["evidence_refs"], d["missing_information"]) == (status, refs, missing)


def test_c5_has_not_covered_note_and_others_do_not():
    assert "handler must decide" in app.decide_status(facts_for("C5"))["note"]
    for cid in ("C1", "C2", "C3", "C4"):
        assert app.decide_status(facts_for(cid))["note"] is None


def test_lapsed_beats_missing_field():
    f = facts_for("C4")
    f["incident_date"] = None
    assert app.decide_status(f)["review_status"] == "BLOCKED"


def test_cancelled_is_blocked():
    f = facts_for("C1")
    f["policy_status"] = "cancelled"
    d = app.decide_status(f)
    assert d["review_status"] == "BLOCKED" and d["evidence_refs"] == ["H3", "H5"]


def test_missing_policy_number_is_needs_information():
    f = facts_for("C1")
    f["policy_number"] = None
    d = app.decide_status(f)
    assert d["review_status"] == "NEEDS_INFORMATION"
    assert d["missing_information"] == ["policy_number"]


def test_thirty_day_boundary():
    f = facts_for("C1")
    f["days_from_incident_to_report"] = 30
    assert app.decide_status(f)["evidence_refs"] == ["H1", "H5"]
    f["days_from_incident_to_report"] = 31
    d = app.decide_status(f)
    assert d["evidence_refs"] == ["H5"] and d["note"]


# ---- model drafts only; it cannot change decided fields

def test_successful_c1(capsys):
    out = run_case("C1", fake_returning({"draft_reply": GOOD_DRAFT}), capsys)
    for section in ("SUPPLIED FACTS", "SUPPLIED POLICY", "MODEL USED",
                    "DRAFT RESULT FOR HUMAN REVIEW", "REVIEW FLAGS"):
        assert section in out
    assert "review_status: READY_FOR_HUMAN_REVIEW" in out
    assert "evidence_refs: H1, H5" in out
    assert GOOD_DRAFT in out
    assert "A human must check" in out
    assert '"days_from_incident_to_report": 4' in out


@pytest.mark.parametrize("extra", [
    {"review_status": "BLOCKED"},
    {"evidence_refs": ["H1"]},
    {"missing_information": ["policy_number"]},
])
def test_extra_keys_from_model_rejected(extra, capsys):
    out = run_case("C1", fake_returning({"draft_reply": GOOD_DRAFT, **extra}), capsys)
    assert "review_status: UNAVAILABLE" in out
    assert GOOD_DRAFT not in out


def test_model_cannot_change_decided_fields():
    facts = facts_for("C4")
    result = app.get_result(facts, "policy", {"host": "h"}, fake_returning({"draft_reply": GOOD_DRAFT}))
    assert result["review_status"] == "BLOCKED"
    assert result["evidence_refs"] == ["H3", "H5"]
    assert result["missing_information"] == []
    assert set(result) == app.RESULT_KEYS


# ---- failures become the safe UNAVAILABLE state

@pytest.mark.parametrize(
    "payload",
    [
        "this is not json",
        {},
        {"draft_reply": ""},
        {"draft_reply": 42},
        ["draft_reply"],
    ],
)
def test_invalid_responses_are_unavailable(payload, capsys):
    out = run_case("C1", fake_returning(payload), capsys)
    assert "review_status: UNAVAILABLE" in out
    assert "reason:" in out
    assert "Traceback" not in out


def test_unavailable_shows_rule_decision_not_a_draft(capsys):
    out = run_case("C2", fake_raising(ConnectionError("refused")), capsys)
    assert "review_status: UNAVAILABLE" in out
    assert "Rule decision (computed in Python): status=NEEDS_INFORMATION; refs=H2; missing=incident_date" in out
    assert "draft_reply:" not in out
    assert "No draft to check." in out


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
    assert "Rule decision (computed in Python): status=READY_FOR_HUMAN_REVIEW; refs=H1, H5" in out


# ---- review flags

def flags_for(draft, case_id="C1"):
    d = app.decide_status(facts_for(case_id))
    return app.review_flags(draft, facts_for(case_id), d["missing_information"])


@pytest.mark.parametrize("draft,fragment", [
    ("This falls under H1 of our rules.", "rule code"),
    ("Dear [Customer Name], thanks.", "square brackets"),
    ("We will pay £4,000 soon.", "currency"),
    ("You are covered for this.", "cover"),
    ("You have cover for this.", "cover"),
    ("Check your coverage.", "cover"),
    ("You are NOT COVERED.", "cover"),
])
def test_flags_raised(draft, fragment):
    assert any(fragment in f for f in flags_for(draft))


def test_clean_draft_has_no_flags():
    assert flags_for(GOOD_DRAFT) == []


def test_cover_flag_is_whole_word_only():
    assert flags_for("The roof was recovered by the builder.") == []
    assert flags_for("Some items are uncovered.") == []


def test_asking_for_supplied_field_is_flagged():
    assert any("policy_number" in f for f in flags_for("Please provide your policy number."))
    assert any("incident_date" in f for f in flags_for("Could you tell us the date of the incident?"))


def test_asking_for_missing_field_is_not_flagged():
    d = "Could you please tell us the date the incident happened?"
    assert flags_for(d, "C2") == []


def test_flags_do_not_block_the_draft(capsys):
    out = run_case("C1", fake_returning({"draft_reply": "Dear [Customer Name], you are covered."}), capsys)
    assert "review_status: READY_FOR_HUMAN_REVIEW" in out
    assert "draft_reply:" in out
    assert "- Draft contains square brackets" in out
    assert "UNAVAILABLE" not in out


# ---- offline demo

@pytest.mark.parametrize("case_id", app.CASE_IDS)
def test_offline_demo_all_cases(case_id, capsys):
    out = run_case(case_id, never_called, capsys, offline_demo=True)
    assert "No model was called" in out
    assert "SYNTHETIC" in out
    assert "UNAVAILABLE" not in out
    assert "draft_reply:" in out
    assert "No flags raised." in out


@pytest.mark.parametrize("case_id", app.CASE_IDS)
def test_demo_matches_decide_status(case_id):
    demo = app.load_demo_result(case_id)
    d = app.decide_status(facts_for(case_id))
    assert demo["review_status"] == d["review_status"]
    assert demo["evidence_refs"] == d["evidence_refs"]
    assert demo["missing_information"] == d["missing_information"]


# ---- days calculation

def test_days_calculation():
    assert app.days_between("2026-11-10", "2026-11-14") == 4
    assert app.days_between(None, "2026-11-15") is None
    assert app.days_between("2026-11-10", None) is None


def test_facts_include_derived_days():
    assert facts_for("C1")["days_from_incident_to_report"] == 4
    assert facts_for("C2")["days_from_incident_to_report"] is None


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
