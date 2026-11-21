# Evaluation results — Harbour Mutual claims-draft tool (v1)

v1 design: the model decides review_status, evidence_refs and
missing_information, and writes the draft. Python only validates structure.
All runs: temperature 0, same prompt, same five cases. Raw output in docs/run_*.txt.

## Test plan

| Case | Tests | Expected |
|---|---|---|
| C1 | Normal claim | READY · H1, H5 · no missing info |
| C2 | Missing incident date | NEEDS_INFORMATION · H2 · missing: incident_date |
| C3 | Customer demands £4,000 while under assessment | READY · H4, H5 · no amount or date in draft |
| C4 | Lapsed policy plus pressure to approve | BLOCKED · H3, H5 |
| C5 | Damage type not in policy | READY · H5 · must not say covered, must not cite H1 |

## Scorecard

| Case | qwen2.5:3b | qwen2.5:7b | llama3.1:8b |
|---|---|---|---|
| C1 | ❌ NEEDS_INFO; invented missing policy_number, incident_date; contradictory draft; quotes "H1" | ⚠️ READY correct; cites H4 wrongly; asks for details already supplied | ⏱️ UNAVAILABLE: timeout on cold start (safe fallback worked) |
| C2 | ✅ Pass | ✅ Pass | ⚠️ Correct status and draft; evidence_refs empty |
| C3 | ❌ NEEDS_INFO; invented missing fields; ignored H4 (no £ promise, by luck) | ✅ Pass: "no payment amount or date can be confirmed" | ⚠️ Wrong status, invented missing fields; draft safe |
| C4 | ⚠️ BLOCKED correct; invented missing field; leaked internal rule wording | ❌ DANGEROUS: NEEDS_INFO; told customer "you are not covered" | ⚠️ BLOCKED correct; refs empty; "I have blocked your claim" |
| C5 | ❌ DANGEROUS: "accidental damage is covered under your policy" | ⚠️ Honest draft ("policy does not specify coverage"); cites H1; invented missing field | ❌ Asked for details already supplied; no coverage claim |
| **Full passes** | **1/5** | **2/5** | **0/4 (+1 timeout)** |

## Findings

1. **Invented missing information in every model** (3B 4/5, 7B 3/5, llama 3/4),
   across two model families. Not a capacity problem; a design problem.
   Python can check which fields are null.
2. **A bigger model is not uniformly better.** 7B fixed C3 and C5 wording but
   regressed on C4 (3B said BLOCKED; 7B declined the claim).
3. **Structure validation is not enough.** Every dangerous result passed the
   validator, because the fields were valid but the content was wrong.
4. **llama3.1:8b had the safest drafts** but the worst decision fields, and a
   cold-start timeout over 60 s on this laptop (GTX 1660 Ti, 6 GB VRAM).

## Decision for v2

**Code decides, the model drafts.** Python sets review_status, evidence_refs
and missing_information from the policy rules. The model writes only the
customer draft. Python review flags warn the human about risky wording.