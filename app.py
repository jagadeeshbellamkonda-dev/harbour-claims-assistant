"""Harbour Mutual claims-draft review tool (classroom demo), version 2.

Code decides, the model drafts: Python applies the hard policy rules
(decide_status) and the model only writes the customer draft_reply.
The draft is for HUMAN REVIEW only. This tool never sends messages, accepts or
declines claims, promises payments, or changes records.

Usage:
    python app.py --case C1
    python app.py --case C2 --model qwen2.5:3b
    python app.py --case C3 --simulate-timeout
    python app.py --case C4 --offline-demo
"""

import argparse
import json
import os
import re
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
REQUIRED_FIELDS = ["policy_number", "incident_date"]
POLICY_IDS = ["H1", "H2", "H3", "H4", "H5"]
REVIEW_STATUSES = ["READY_FOR_HUMAN_REVIEW", "NEEDS_INFORMATION", "BLOCKED"]
RESULT_KEYS = {"draft_reply", "evidence_refs", "missing_information", "review_status"}
REPORTING_WINDOW_DAYS = 30

DEFAULT_PROVIDER = "ollama"
DEFAULT_MODEL = "qwen2.5:3b"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"
DEFAULT_HOST = "http://localhost:11434"
TIMEOUT_SECONDS = 60

UNADDRESSED_NOTE = (
    "The drafting rules do not address this damage type; a handler must decide."
)
FALLBACK_MESSAGE = (
    "No draft is available. Please review this case manually using the "
    "supplied facts and policy above."
)
HUMAN_REMINDER = (
    "REMINDER: a valid structure does not prove the content is correct, and "
    "no flags does not mean the draft is right. A human must check this draft "
    "against the policy before anything is used. This tool does not send "
    "messages, decide claims, promise payments or change records."
)

# The model writes only the draft.
DRAFT_SCHEMA = {
    "type": "object",
    "properties": {"draft_reply": {"type": "string"}},
    "required": ["draft_reply"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You write a draft reply to a home insurance customer. A human claims "
    "handler will review it before anything is sent. Use ONLY the supplied "
    "facts. The review decision (status, missing information, note) was "
    "already made by the company's system; treat it as fixed fact that you "
    "cannot change or question.\n"
    "Rules for the draft:\n"
    "- Plain, polite English for a customer.\n"
    "- Never mention rule codes such as H1 or H2, or internal policy wording.\n"
    "- No placeholders in square brackets. Sign off as 'Kind regards, Harbour "
    "Mutual Claims'.\n"
    "- Never say the customer is or is not covered, and do not discuss cover.\n"
    "- Never state an outcome, an amount of money, or a date for payment, and "
    "never repeat amounts the customer mentions.\n"
    "- Never ask for information that was supplied in the facts.\n"
    "- If the status is NEEDS_INFORMATION, ask only for the items listed under "
    "ask_customer_for.\n"
    "- If the status is BLOCKED, say only that a handler will review the case.\n"
    "- If the claim is under assessment, say only that a handler will be in touch.\n"
    "- Never say you have sent, accepted, declined, approved or changed anything.\n"
    'Return ONLY a JSON object with exactly one key: {"draft_reply": "<text>"}.'
)

CUSTOMER_WORDING = {
    "policy_number": "your policy number",
    "incident_date": "the date the incident happened",
}


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


# ---------------------------------------------------------------- rule decision

def decide_status(facts):
    """Apply the hard policy rules in Python, before any model call.

    Takes the facts including days_from_incident_to_report. Returns a dict with
    review_status, evidence_refs, missing_information and note.
    """
    missing = [f for f in REQUIRED_FIELDS if facts.get(f) is None]
    days = facts.get("days_from_incident_to_report")

    if facts.get("policy_status") in ("lapsed", "cancelled"):
        status, refs, note = "BLOCKED", ["H3", "H5"], None
    elif missing:
        status, refs, note = "NEEDS_INFORMATION", ["H2"], None
    elif facts.get("claim_status") == "under_assessment":
        status, refs, note = "READY_FOR_HUMAN_REVIEW", ["H4", "H5"], None
    elif (
        facts.get("damage_type") in ("storm", "escape_of_water")
        and days is not None
        and days <= REPORTING_WINDOW_DAYS
    ):
        status, refs, note = "READY_FOR_HUMAN_REVIEW", ["H1", "H5"], None
    else:
        status, refs, note = "READY_FOR_HUMAN_REVIEW", ["H5"], UNADDRESSED_NOTE

    return {
        "review_status": status,
        "evidence_refs": refs,
        "missing_information": missing if status == "NEEDS_INFORMATION" else [],
        "note": note,
    }


# ---------------------------------------------------------------- settings

def get_settings(model_override=None):
    provider = os.environ.get("APP_MODEL_PROVIDER", DEFAULT_PROVIDER).strip().lower()
    default_model = DEFAULT_ANTHROPIC_MODEL if provider == "anthropic" else DEFAULT_MODEL
    model = model_override or os.environ.get("APP_MODEL_NAME") or default_model
    host = os.environ.get("APP_OLLAMA_HOST") or DEFAULT_HOST
    return {"provider": provider, "model": model, "host": host}


# ---------------------------------------------------------------- model call

def build_user_prompt(facts, policy, decision):
    fixed = {
        "review_status": decision["review_status"],
        "missing_information": decision["missing_information"],
        "ask_customer_for": [CUSTOMER_WORDING.get(f, f) for f in decision["missing_information"]],
        "note_for_handler": decision["note"],
    }
    return (
        "SUPPLIED FACTS (JSON):\n"
        + json.dumps(facts, indent=2, ensure_ascii=False)
        + "\n\nFIXED DECISION (cannot be changed):\n"
        + json.dumps(fixed, indent=2, ensure_ascii=False)
        + "\n\nBACKGROUND POLICY (for your understanding only; never quote it "
        "or cite rule codes):\n"
        + policy
        + '\n\nReturn only the JSON object {"draft_reply": "<text>"}.'
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
    user_prompt = build_user_prompt(facts, policy, decide_status(facts))

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
                format=DRAFT_SCHEMA,
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


def parse_draft(raw_text):
    """The model may return exactly {"draft_reply": "<text>"} and nothing else."""
    try:
        parsed = json.loads(raw_text)
    except (TypeError, ValueError):
        raise ModelUnavailable("The model response was not valid JSON.")
    if not isinstance(parsed, dict) or set(parsed) != {"draft_reply"}:
        raise ModelUnavailable(
            "The model response failed validation: it must contain only draft_reply."
        )
    draft = parsed["draft_reply"]
    if not isinstance(draft, str) or not draft.strip():
        raise ModelUnavailable("The model response failed validation: draft_reply must be non-empty text.")
    return draft


def assemble_result(draft, decision):
    """Decided fields come from Python only; the model contributes the draft."""
    return validate_result({
        "draft_reply": draft,
        "evidence_refs": list(decision["evidence_refs"]),
        "missing_information": list(decision["missing_information"]),
        "review_status": decision["review_status"],
    })


# ---------------------------------------------------------------- review flags

_SUPPLIED_FIELD_PHRASES = {
    "policy_number": ["policy number", "policy no"],
    "incident_date": [
        "date of the incident", "incident date", "date of the damage",
        "when it happened", "date it happened", "date the incident happened",
    ],
}
_ASK_WORDS = re.compile(
    r"\b(please (provide|send|confirm|tell|let us know)|could you|can you|we need|"
    r"we require|send us|provide)\b",
    re.IGNORECASE,
)


_DECISION_WORDS = re.compile(
    r"\b(approve|approved|approval|decline|declined|reject|rejected|"
    r"accept|accepted|refuse|refused|deny|denied|will pay)\b",
    re.IGNORECASE,
)


def review_flags(draft, facts, missing_information):
    """Python warnings for the human reviewer. Flags never block the draft."""
    flags = []
    if re.search(r"\bH[1-5]\b", draft):
        flags.append("Draft contains an internal rule code (H1-H5).")
    if "[" in draft or "]" in draft:
        flags.append("Draft contains square brackets (possible placeholder).")
    if re.search(r"\b(cover|covered|coverage)\b", draft, re.IGNORECASE):
        flags.append('Draft uses the word "cover", "covered" or "coverage"; check it makes no coverage statement.')
    if re.search(r"[£$€]\s?\d|\bGBP\s?\d|\d[\d,.]*\s?(pounds|GBP)\b", draft, re.IGNORECASE):
        flags.append("Draft contains a currency amount.")
    if _DECISION_WORDS.search(draft):
        flags.append("Draft uses decision language; check it does not accept, decline or promise anything.")
    sentences = re.split(r"[.!?\n]+", draft)
    for field, phrases in _SUPPLIED_FIELD_PHRASES.items():
        if facts.get(field) is None or field in missing_information:
            continue
        for sentence in sentences:
            low = sentence.lower()
            if any(p in low for p in phrases) and _ASK_WORDS.search(sentence):
                flags.append(f"Draft appears to ask for {field}, which was already supplied.")
                break
    return flags


# ---------------------------------------------------------------- results

def unavailable_result(reason, decision=None):
    return {
        "draft_reply": "",
        "evidence_refs": [],
        "missing_information": [],
        "review_status": "UNAVAILABLE",
        "reason": reason,
        "fallback_message": FALLBACK_MESSAGE,
        "decision": decision,
    }


def load_demo_result(case_id):
    with open(DEMO_PATH, encoding="utf-8") as f:
        demo = json.load(f)
    return validate_result(dict(demo[case_id]))


def get_result(facts, policy, settings, model_fn, decision=None):
    """Call the model for the draft only. Any failure becomes the safe UNAVAILABLE state."""
    decision = decision or decide_status(facts)
    try:
        raw = model_fn(facts, policy, settings)
        return assemble_result(parse_draft(raw), decision)
    except ModelUnavailable as exc:
        return unavailable_result(str(exc), decision)
    except TimeoutError:
        return unavailable_result(
            f"The model did not answer within {TIMEOUT_SECONDS} seconds.", decision)
    except ConnectionError:
        return unavailable_result(
            f"Could not connect to Ollama at {settings['host']}. Is Ollama running?", decision)
    except Exception:
        return unavailable_result("The model call failed unexpectedly.", decision)


# ---------------------------------------------------------------- output

def show(case_id, facts, policy, settings, result, decision, note=None):
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
    flags = None
    if result["review_status"] == "UNAVAILABLE":
        print(f"reason: {result['reason']}")
        d = result.get("decision") or decision
        if d:
            print(
                "Rule decision (computed in Python): "
                f"status={d['review_status']}; "
                f"refs={', '.join(d['evidence_refs']) or '(none)'}; "
                f"missing={', '.join(d['missing_information']) or '(none)'}"
            )
            if d["note"]:
                print(f"note: {d['note']}")
        print(result["fallback_message"])
    else:
        print(f"evidence_refs: {', '.join(result['evidence_refs']) or '(none)'}")
        print(f"missing_information: {', '.join(result['missing_information']) or '(none)'}")
        if decision and decision["note"]:
            print(f"note: {decision['note']}")
        print("draft_reply:")
        print(result["draft_reply"])
        flags = review_flags(result["draft_reply"], facts, result["missing_information"])
    print(f"\n{bar}\nREVIEW FLAGS\n{bar}")
    if flags is None:
        print("No draft to check.")
    elif flags:
        for flag in flags:
            print(f"- {flag}")
    else:
        print("No flags raised.")
    print(f"\n{HUMAN_REMINDER}")


def run(case_id, model_override=None, simulate_timeout=False, offline_demo=False, model_fn=None):
    case = load_case(case_id)
    policy = load_policy()
    facts = build_facts(case)
    decision = decide_status(facts)
    settings = get_settings(model_override)
    note = None

    if simulate_timeout:
        result = unavailable_result("Simulated timeout: no model call was made.", decision)
        note = "No model was called (simulated timeout)."
    elif offline_demo:
        try:
            result = load_demo_result(case_id)
        except Exception:
            result = unavailable_result("The offline demo file could not be read.", decision)
        settings = {"provider": "offline-demo", "model": "none (prerecorded synthetic result)", "host": ""}
        note = "No model was called. This is a prerecorded SYNTHETIC result from demo_outputs.json."
    else:
        result = get_result(facts, policy, settings, model_fn or call_model, decision)

    show(case_id, facts, policy, settings, result, decision, note)
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
