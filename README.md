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
- ask a model for a draft reply, cited policy rules, missing fields and a review status;
- check the structure of the answer and show it for human review.

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

Output has four sections: SUPPLIED FACTS, SUPPLIED POLICY, MODEL USED, and DRAFT RESULT FOR HUMAN REVIEW.

Review statuses: `READY_FOR_HUMAN_REVIEW`, `NEEDS_INFORMATION`, `BLOCKED`, or `UNAVAILABLE` (tool-generated when no valid draft exists).

## Tests

From the project root, with the virtual environment active:

```powershell
python -m pytest
```

The tests use a fake model function and never call Ollama or any external API.
