# Harbour Mutual Claims-Draft Review Tool

A command-line tool that drafts customer replies for a **fictional** home insurer, using synthetic cases, for a human claims handler to review.

## The problem

Claims handlers spend time writing routine replies. A reply that wrongly tells a customer they are, or are not, covered is costly. This project tests where a language model can be trusted in that job. It finds that the model should write the draft but should not make the decisions, and that a human must still check every draft.

## Results at a glance

Three local models (qwen2.5:3b, qwen2.5:7b, llama3.1:8b), five synthetic cases, temperature 0. Full scorecards are in [docs/results.md](docs/results.md).

| Measure | v1: model decides and drafts | v2: code decides, model drafts |
|---|---|---|
| Decision fields correct (status, rules cited, missing info) | Wrong in most cases; models invented missing fields in 10 of 14 completed runs | **15/15** (set by Python, so independent of the model) |
| Unsafe drafts reaching a reviewer with no warning | 3 | 1 (qwen2.5:3b on C4: "unable to approve your claim"). A decision-language flag added afterwards now catches it, checked on that one case only |
| Best model | qwen2.5:7b, 2/5 full passes | qwen2.5:7b and llama3.1:8b, 5/5 safe drafts. The recommended drafter is qwen2.5:7b |

**Limits:** five test cases, one run per model, three models. The review flags are simple text checks, not guarantees. A human reviews every draft, and the test set should be expanded before any real use.

### One case, before and after (C5: accidental damage, not in the drafting rules)

**v1, qwen2.5:3b (model decides):**
> "Based on the information provided, it appears that accidental damage is
> covered under your policy."

**v2, qwen2.5:7b (code decides, model drafts):**
> "A handler will review your case to determine if your laptop screen damage
> is covered."
> Flags: 🚩 cover wording, check it makes no coverage statement.

## Design decision: code decides, the model drafts

- In v1 the model chose the status, cited rules and missing fields. It invented missing information, told a lapsed-policy customer "you are not covered", and told another customer accidental damage "is covered".
- Every one of those errors passed structure validation, because the fields were valid and the content was wrong.
- In v2, `decide_status()` applies the policy rules in Python before any model call. The model receives the result as fixed facts and returns only `draft_reply`. Any extra key is rejected.
- Python review flags then warn the reviewer about risky wording: rule codes, placeholders, "cover" wording, amounts, decision language and requests for information already supplied.

## What this tool will never do

- Send a message to a customer.
- Accept or decline a claim.
- Promise a payment, an amount or a date.
- Change a record.
- Treat a draft as verified. Flags are heuristics, so a human must review every draft.

## Tech

Python, Ollama (local models), python-dotenv, and pytest (fake model only, no external calls). Claude Code was used for the build.

---

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

If the model is unreachable, slow, missing, or returns something invalid, the tool shows a safe
**UNAVAILABLE** state with a short reason and a manual fallback message. It never shows a traceback or an old draft.

## Decision rules (computed in Python)

`decide_status()` applies these in order:

1. Lapsed or cancelled policy: BLOCKED (H3, H5).
2. A null `policy_number` or `incident_date`: NEEDS_INFORMATION (H2), listing those fields.
3. Claim under assessment: READY_FOR_HUMAN_REVIEW (H4, H5).
4. Storm or escape of water reported within 30 days: READY_FOR_HUMAN_REVIEW (H1, H5).
5. Anything else: READY_FOR_HUMAN_REVIEW (H5) with the note "The drafting rules do not address this damage type; a handler must decide."

The model is told to use plain English, never mention rule codes, use no placeholders, make no statement about cover, state no outcome, amount or date, and not ask for information already supplied.

## Review flags and their limits

Python checks the draft and prints warnings under REVIEW FLAGS for: a rule code (H1 to H5); square brackets; the whole words "cover", "covered" or "coverage" (case-insensitive); a currency amount; decision language (the whole words approve, approved, approval, decline, declined, reject, rejected, accept, accepted, refuse, refused, deny, denied, or the phrase "will pay", case-insensitive); and a request for a field that was already supplied.

The decision-language flag was added because qwen2.5:3b wrote "I am unable to approve your claim" for C4 and nothing was flagged (see `docs/results.md`).

Limits you should know about:
- The cover flag matches those three whole words only. It does **not** match "uncovered", "covers", "covering" or "recovered", so a draft can make a coverage statement without raising it.
- The decision-language flag can raise false alarms, for example on "we accept documents by email". That is acceptable because flags never block a draft. It also matches only the listed forms: it does not match "approves", "approving", "declining", "rejection", "acceptance", "denies", or phrasing like "we'll pay", so other decision wording can slip through.
- The supplied-field check is a simple phrase heuristic and can miss requests or flag harmless wording.
- Flags never block a draft, and "No flags raised" does not mean the draft is correct. A human must still review every draft.

## Tests

From the project root, with the virtual environment active:

```powershell
python -m pytest
```

The tests use a fake model function and never call Ollama or any external API.


## What I learned

- **Valid structure is not correct content.** In v1, every dangerous draft
  passed my JSON validation. The fields were real field names; the facts
  were wrong. It reminded me of a Power BI report that refreshes without
  errors but shows the wrong numbers: a successful run proves nothing
  about correctness.

- **A bigger model is not automatically safer.** qwen2.5:7b fixed two cases
  that the 3B model failed, but introduced a new failure: it told a lapsed
  customer "you are not covered". I only caught it because I re-ran every
  case, not just the ones that had failed.

- **If it can be calculated, don't ask the model.** Every model invented
  "missing" fields, across two model families. Moving status, policy
  references and missing fields into Python fixed all of them for every
  model. The LLM now does what it is good at: wording.

- **The best model depends on the job.** llama3.1:8b came last when it had
  to make decisions, and joint best once it only had to write drafts.

- **Test the safety net, not just the system.** Scoring my review flags
  showed they missed decision language ("unable to approve your claim").
  I added a check and proved it against the exact failing sentence.
  