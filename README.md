# Harbour Mutual Claims-Draft Review Tool (classroom demo)

A small command-line tool for a **fictional** insurer. All people, policies and
cases are synthetic.

## The problem

Claims handlers spend time drafting routine replies to customers. This tool
drafts a reply from one case record and a short written policy, so a human
handler can review it faster. The draft is a starting point, never a decision.

## What it will and will not do

It will:
- load one case from `cases.json` and the rules in `policy.md`;
- compute days from incident to report in Python (the model never does date maths);
- decide the review status, cited policy rules and missing fields in Python;
- ask a model only to write the customer draft reply;
- check the structure of the answer, run wording checks (REVIEW FLAGS) and show it for human review.

It will not:
- send messages, accept or decline claims, promise payments or change records;
- prove the draft is correct. Valid structure is not correct content, so a human must check every draft;
- provide a web UI, database, RAG, login or any production integration.

If the model is unreachable, slow, missing, or returns something invalid, the tool shows a safe
**UNAVAILABLE** state with a short reason and a manual fallback message. It never shows a traceback or an old draft.

## Setup on Windows (PowerShell) with Ollama

1. Install Ollama from https://ollama.com and start it.
2. Pull the default model: `ollama pull qwen2.5:3b`
3. In the project folder:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   python setup_env.py
   ```
   `setup_env.py` asks for provider, model name and Ollama host (press Enter for the defaults)
   and writes a project-only `.env`. `.env` is git-ignored. `.env.example` shows the variables.

### Optional: Anthropic provider

Not needed for the demo and not in `requirements.txt`. To use it, run `pip install anthropic`,
then choose `anthropic` in `setup_env.py` and enter your key (hidden input). The key is stored only as
`APP_ANTHROPIC_API_KEY` in the project `.env`; the global `ANTHROPIC_API_KEY` is never read or set.

## Running

```powershell
python app.py --case C1                      # live model call (cases C1 to C5)
python app.py --case C1 --model qwen2.5:7b   # override the model for one run
python app.py --case C1 --simulate-timeout   # no model call; shows the UNAVAILABLE fallback
python app.py --case C1 --offline-demo       # no model call; prerecorded synthetic result
```

Output has five sections: SUPPLIED FACTS, SUPPLIED POLICY, MODEL USED, DRAFT RESULT FOR HUMAN REVIEW, and REVIEW FLAGS.

Review statuses: `READY_FOR_HUMAN_REVIEW`, `NEEDS_INFORMATION`, `BLOCKED`, or `UNAVAILABLE` (tool-generated when no valid draft exists).
In the UNAVAILABLE state the result also shows one line, "Rule decision (computed in Python)", with the decided status, refs and missing fields. It is never a draft.

## Design decision: code decides, the model drafts

Version 1 let the model choose `review_status`, `evidence_refs` and `missing_information` as well as write the draft.
Python only checked the structure. The evaluation in `docs/results.md` (three models, five cases, temperature 0) found:

- **Invented missing information in every model**: qwen2.5:3b in 4 of 5 cases, qwen2.5:7b in 3 of 5, llama3.1:8b in 3 of 4. Python can see which fields are null, so this is a design problem, not a model-size problem.
- **qwen2.5:7b set the lapsed-policy case (C4) to NEEDS_INFORMATION** and told the customer "you are not covered". The 3B model had got C4's status right.
- **qwen2.5:3b told C5 that accidental damage "is covered"**, which the policy does not say.
- **Drafts leaked internal wording**, such as the rule code "H1" and placeholders like `[Customer Name]`.
- **Every dangerous result passed structure validation**, because the fields were valid but the content was wrong. A bigger model was not uniformly better.

Version 2 therefore splits the work:

- `decide_status()` applies the hard rules in Python before any model call, in this order: lapsed or cancelled policy gives BLOCKED (H3, H5); a null `policy_number` or `incident_date` gives NEEDS_INFORMATION (H2) and lists those fields; `under_assessment` gives READY_FOR_HUMAN_REVIEW (H4, H5); storm or escape of water reported within 30 days gives READY_FOR_HUMAN_REVIEW (H1, H5); anything else gives READY_FOR_HUMAN_REVIEW (H5) with a note that the policy does not cover the situation and a handler must decide.
- The model receives those decisions as fixed facts and returns only `{"draft_reply": "..."}`. Any extra key makes the result UNAVAILABLE, so the model cannot change the status, refs or missing fields.
- The prompt tells the model to use plain English, never mention rule codes, use no placeholders, make no statement about cover, state no outcome, amount or date, and not ask for information already supplied.

### Review flags

Python checks the draft and prints warnings under REVIEW FLAGS for: a rule code (H1 to H5); square brackets; the whole words "cover", "covered" or "coverage" (case-insensitive); a currency amount; and a request for a field that was already supplied.

Limits you should know about:
- The cover flag matches those three whole words only. It does **not** match "uncovered", "covers", "covering" or "recovered", so a draft can make a coverage statement without raising it.
- The supplied-field check is a simple phrase heuristic and can miss requests or flag harmless wording.
- Flags never block a draft, and "No flags raised" does not mean the draft is correct. A human must still review every draft.

## Tests

From the project root, with the virtual environment active:

```powershell
python -m pytest
```

The tests use a fake model function and never call Ollama or any external API.
