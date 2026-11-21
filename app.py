"""Harbour Mutual claims-draft review tool (classroom demo).

Drafts a customer reply for HUMAN REVIEW only. It never sends messages,
accepts or declines claims, promises payments, or changes records.

Usage:
    python app.py --case C1
    python app.py --case C2 --model qwen2.5:3b
    python app.py --case C3 --simulate-timeout
    python app.py --case C4 --offline-demo
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CASES_PATH = BASE_DIR / "cases.json"
POLICY_PATH = BASE_DIR / "policy.md"
DEMO_PATH = BASE_DIR / "demo_outputs.json"
ENV_PATH = BASE_DIR / ".env"

CASE_IDS = ["C1", "C2", "C3", "C4", "C5"]
CASE_FIELDS = [
    "case_id",
    "policy_number",
    "policy_status",
    "customer_question",
    "damage_type",
    "incident_date",
    "reported_date",
    "claim_status",
    "claim_details",
]
POLICY_IDS = ["H1", "H2", "H3", "H4", "H5"]
REVIEW_STATUSES = ["READY_FOR_HUMAN_REVIEW", "NEEDS_INFORMATION", "BLOCKED"]
RESULT_KEYS = {"draft_reply", "evidence_refs", "missing_information", "review_status"}

DEFAULT_PROVIDER = "ollama"
DEFAULT_MODEL = "qwen2.5:3b"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"
DEFAULT_HOST = "http://localhost:11434"
TIMEOUT_SECONDS = 60

FALLBACK_MESSAGE = (
    "No draft is available. Please review this case manually using the "
    "supplied facts and policy above."
)
HUMAN_REMINDER = (
    "REMINDER: a valid structure does not prove the content is correct. "
    "A human must check this draft against the policy before anything is used. "
    "This tool does not send messages, decide claims, promise payments or "
    "change records."
)

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "draft_reply": {"type": "string"},
        "evidence_refs": {
            "type": "array",
            "items": {"type": "string", "enum": POLICY_IDS},
        },
        "missing_information": {
            "type": "array",
            "items": {"type": "string", "enum": CASE_FIELDS},
        },
        "review_status": {"type": "string", "enum": REVIEW_STATUSES},
    },
    "required": sorted(RESULT_KEYS),
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You draft customer replies for a home insurance claims handler. A human "
    "will review your draft. Use ONLY the supplied facts and policy; do not "
    "invent facts. Never accept or decline a claim, never promise or imply a "
    "payment amount or date, and never say you have sent or changed anything. "
    "Return ONLY a JSON object with exactly these keys: "
    "draft_reply (string), "
    "evidence_refs (list of policy IDs from H1, H2, H3, H4, H5), "
    "missing_information (list of case field names that are missing and needed), "
    "review_status (one of READY_FOR_HUMAN_REVIEW, NEEDS_INFORMATION, BLOCKED)."
)


class ModelUnavailable(Exception):
    """Raised with a short plain-English reason when no usable result exists."""


# ---------------------------------------------------------------- inputs

def load_case(case_id):
    with open(CASES_PATH, encoding="utf-8") as f:
        cases = json.load(f)
    return cases[case_id]


def load_policy():
    return POLICY_PATH.read_text(encoding="utf-8")


def days_between(incident_date, reported_date):
    """Whole days from incident to report, or None if either date is missing."""
    if not incident_date or not reported_date:
        return None
    try:
        return (date.fromisoformat(reported_date) - date.fromisoformat(incident_date)).days
    except ValueError:
        return None


def build_facts(case):
    facts = dict(case)
    facts["days_from_incident_to_report"] = days_between(
        case.get("incident_date"), case.get("reported_date")
    )
    return facts


# ---------------------------------------------------------------- settings

def get_settings(model_override=None):
    provider = os.environ.get("APP_MODEL_PROVIDER", DEFAULT_PROVIDER).strip().lower()
    default_model = DEFAULT_ANTHROPIC_MODEL if provider == "anthropic" else DEFAULT_MODEL
    model = model_override or os.environ.get("APP_MODEL_NAME") or default_model
    host = os.environ.get("APP_OLLAMA_HOST") or DEFAULT_HOST
    return {"provider": provider, "model": model, "host": host}


# ---------------------------------------------------------------- model call

def build_user_prompt(facts, policy):
    return (
        "SUPPLIED FACTS (JSON):\n"
        + json.dumps(facts, indent=2, ensure_ascii=False)
        + "\n\nSUPPLIED POLICY:\n"
        + policy
        + "\n\nReturn only the JSON object."
    )


def _explain_error(exc, settings):
    """Turn a library exception into a short plain-English reason."""
    name = type(exc).__name__
    text = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timeout" in name.lower() or "timed out" in text:
        return f"The model did not answer within {TIMEOUT_SECONDS} seconds."
    if isinstance(exc, ConnectionError) or "connect" in name.lower():
        return f"Could not connect to Ollama at {settings['host']}. Is Ollama running?"
    if name == "ResponseError" and ("not found" in text or "pull" in text):
        return f"The model '{settings['model']}' is not available. Run: ollama pull {settings['model']}"
    return "The model call failed."


def call_model(facts, policy, settings):
    """The single place a model is called. Returns the raw response text."""
    provider = settings["provider"]
    user_prompt = build_user_prompt(facts, policy)

    if provider == "ollama":
        try:
            import ollama

            client = ollama.Client(host=settings["host"], timeout=TIMEOUT_SECONDS)
            response = client.chat(
                model=settings["model"],
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                format=RESULT_SCHEMA,
                options={"temperature": 0},
            )
            return response["message"]["content"]
        except ImportError:
            raise ModelUnavailable("The 'ollama' package is not installed.")
        except Exception as exc:  # mapped to a safe, short reason
            raise ModelUnavailable(_explain_error(exc, settings))

    if provider == "anthropic":
        key = os.environ.get("APP_ANTHROPIC_API_KEY")
        if not key:
            raise ModelUnavailable("APP_ANTHROPIC_API_KEY is not set.")
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=key, timeout=TIMEOUT_SECONDS)
            response = client.messages.create(
                model=settings["model"],
                max_tokens=1000,
                temperature=0,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        except ImportError:
            raise ModelUnavailable("The optional 'anthropic' package is not installed.")
        except Exception as exc:
            if "timeout" in type(exc).__name__.lower():
                raise ModelUnavailable(f"The model did not answer within {TIMEOUT_SECONDS} seconds.")
            raise ModelUnavailable("The Anthropic call failed.")

    raise ModelUnavailable(f"Unknown provider '{provider}'. Use ollama or anthropic.")


# ---------------------------------------------------------------- validation

def validate_result(result):
    """Return the result if structurally valid, else raise ModelUnavailable."""
    def bad(why):
        raise ModelUnavailable(f"The model response failed validation: {why}.")

    if not isinstance(result, dict):
        bad("not a JSON object")
    if set(result) != RESULT_KEYS:
        bad("keys must be exactly " + ", ".join(sorted(RESULT_KEYS)))
    if not isinstance(result["draft_reply"], str) or not result["draft_reply"].strip():
        bad("draft_reply must be non-empty text")
    for key, allowed, label in (
        ("evidence_refs", POLICY_IDS, "policy ID"),
        ("missing_information", CASE_FIELDS, "case field"),
    ):
        value = result[key]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            bad(f"{key} must be a list of text items")
        for item in value:
            if item not in allowed:
                bad(f"'{item}' in {key} is not a known {label}")
    if result["review_status"] not in REVIEW_STATUSES:
        bad("review_status is not an allowed value")
    return result


def parse_and_validate(raw_text):
    try:
        parsed = json.loads(raw_text)
    except (TypeError, ValueError):
        raise ModelUnavailable("The model response was not valid JSON.")
    return validate_result(parsed)


# ---------------------------------------------------------------- results

def unavailable_result(reason):
    return {
        "draft_reply": "",
        "evidence_refs": [],
        "missing_information": [],
        "review_status": "UNAVAILABLE",
        "reason": reason,
        "fallback_message": FALLBACK_MESSAGE,
    }


def load_demo_result(case_id):
    with open(DEMO_PATH, encoding="utf-8") as f:
        demo = json.load(f)
    return validate_result(dict(demo[case_id]))


def get_result(facts, policy, settings, model_fn):
    """Call the model and validate. Any failure becomes the safe UNAVAILABLE state."""
    try:
        raw = model_fn(facts, policy, settings)
        return parse_and_validate(raw)
    except ModelUnavailable as exc:
        return unavailable_result(str(exc))
    except TimeoutError:
        return unavailable_result(f"The model did not answer within {TIMEOUT_SECONDS} seconds.")
    except ConnectionError:
        return unavailable_result(
            f"Could not connect to Ollama at {settings['host']}. Is Ollama running?"
        )
    except Exception:
        return unavailable_result("The model call failed unexpectedly.")


# ---------------------------------------------------------------- output

def show(case_id, facts, policy, settings, result, note=None):
    bar = "=" * 60
    print(f"{bar}\nSUPPLIED FACTS\n{bar}")
    print(json.dumps(facts, indent=2, ensure_ascii=False))
    print(f"\n{bar}\nSUPPLIED POLICY\n{bar}")
    print(policy.rstrip())
    print(f"\n{bar}\nMODEL USED\n{bar}")
    print(f"provider: {settings['provider']}\nmodel: {settings['model']}")
    if note:
        print(note)
    print(f"\n{bar}\nDRAFT RESULT FOR HUMAN REVIEW\n{bar}")
    print(f"review_status: {result['review_status']}")
    if result["review_status"] == "UNAVAILABLE":
        print(f"reason: {result['reason']}")
        print(result["fallback_message"])
    else:
        print(f"evidence_refs: {', '.join(result['evidence_refs']) or '(none)'}")
        print(f"missing_information: {', '.join(result['missing_information']) or '(none)'}")
        print("draft_reply:")
        print(result["draft_reply"])
    print(f"\n{HUMAN_REMINDER}")


def run(case_id, model_override=None, simulate_timeout=False, offline_demo=False, model_fn=None):
    case = load_case(case_id)
    policy = load_policy()
    facts = build_facts(case)
    settings = get_settings(model_override)
    note = None

    if simulate_timeout:
        result = unavailable_result("Simulated timeout: no model call was made.")
        note = "No model was called (simulated timeout)."
    elif offline_demo:
        try:
            result = load_demo_result(case_id)
        except Exception:
            result = unavailable_result("The offline demo file could not be read.")
        settings = {"provider": "offline-demo", "model": "none (prerecorded synthetic result)", "host": ""}
        note = "No model was called. This is a prerecorded SYNTHETIC result from demo_outputs.json."
    else:
        result = get_result(facts, policy, settings, model_fn or call_model)

    show(case_id, facts, policy, settings, result, note)
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Harbour Mutual claims-draft review tool")
    parser.add_argument("--case", required=True, choices=CASE_IDS)
    parser.add_argument("--model", help="override the model name for this run")
    parser.add_argument("--simulate-timeout", action="store_true",
                        help="make no model call and show the UNAVAILABLE fallback")
    parser.add_argument("--offline-demo", action="store_true",
                        help="make no model call and show a prerecorded synthetic result")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv(ENV_PATH)

    return run(args.case, args.model, args.simulate_timeout, args.offline_demo)


if __name__ == "__main__":
    sys.exit(main())
