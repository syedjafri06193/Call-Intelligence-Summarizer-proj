# Roadmap

What has been done on top of v1, what is still unverified, and what comes next.
Ordered by how much each item teaches and how much it unblocks.

## Done

- **Live extraction.** `GroqExtractor` (and `LLMExtractor` for Anthropic) implement the `ExtractionModel` protocol. Span validation, the hallucination counter, reconciliation, scoring and task rules are unchanged.
- **`--live` flag.** `cis demo --live --provider groq|anthropic`. Plain `cis demo` still runs offline.
- **Offline tests** for both extractors, using fake clients (no key, no network).
- **Ordinal support level.** The model picks `explicit` or `implied`; code maps that to the confidence the task gate needs. No model-emitted numbers.
- **Speaker attribution.** The model reports `participant_id` so tasks can resolve an owner.

## Pending verification

- [x] Rename test files to `test_groq_extractor.py` and `test_llm_extractor.py` so plain `pytest` collects them.
- [x] Re-run `cis demo --live` after the `support` and `participant_id` changes. Result (groq, gpt-oss-120b): tasks now proposed with owners resolved and Friday task due `2026-09-18`; budget contradiction reduced to the real $50k vs $80k; champion found; Metrics score 3; coverage 100%; hallucination rate 0.0% (38/38 quotes). Remaining problems are listed under "Known issues" below.
- [ ] Run the live demo 3 times and compare outputs (temperature 0 does not guarantee identical results).
- [ ] Try the Anthropic path live; so far only Groq has been run against a real API.

## Known issues from the live run

Found on the sample call with groq / gpt-oss-120b. A 0.0% hallucination rate hides all of these.

1. **Duplicate tasks (highest priority).** 6 tasks for 2 real promises. The same sentence is filed as both `commitment` and `next_step`, and each filing words the value differently ("take to Priya", "take to her"), so nothing is recognized as a duplicate. Likely fix: dedupe by shared quote span in `reconcile.py` and/or when proposing tasks. Needs `extract/reconcile.py` to be read first.
2. **Speaker id in the value field.** One champion claim had the value `p_dana`, which created a false champion contradiction. Quick fix: make the `participant_id` description in `build_tool` say "ONLY the id in square brackets... Never put an id in `value`", and add a `SYSTEM_PROMPT` rule that `value` is plain language taken from the quote.
3. **False timeline contradiction.** "Jan" vs "January" vs a shorter wording of the same fact is reported as a contradiction. Reconciliation needs to treat these as one claim.
4. **Missed claim (recall).** The rep's "three hundred percent return" was not extracted, so the "excluded: stated by the rep" behavior did not appear in this run. Measure with the labeled eval set rather than tuning the prompt to this one call.
5. **Merged facts.** One `metric` value held both "40 hours/week" and "$200k ARR lost". Consider telling the model one fact per entry; the protocol allows one claim per field per chunk, so this may need the protocol or reconcile to change.
6. **Open labeling question.** Is a conditional promise ("I'll take it to her myself once we've seen the security review") a task? Decide it in the labeled answer key, not in the prompt.
7. **Run-to-run variance.** Output differs slightly between runs. Run 3 times and compare before drawing conclusions.

## Next: make extraction trustworthy

1. **Fix duplicates in reconciliation.** Needs a look at `extract/reconcile.py` and how it decides two claims are the same.
2. **Labeled eval set.** Hand-label 20 to 30 calls (real or synthetic) with the correct fields. Report precision and recall per field using `eval/extraction_metrics.py`. A 0.0% hallucination rate does not catch misfiled or missing facts.
3. **Real judge.** Implement `choose_level(prompt)` with a live model so scoring no longer depends on `ScriptedJudge`. Keep the existing structural mitigations (no transcript, no prior score).
4. **Consistency runs.** Use `eval/consistency.py` to publish rerun variance per model.
5. **Model comparison.** Same call, several models (gpt-oss-120b, gpt-oss-20b, Claude). Compare hallucination rate, fields found, cost and latency.

## Then: make it usable

6. **Review UI.** A small page where a rep confirms proposed tasks and `[review]` fields. This closes the human-in-the-loop story.
7. **Real ASR.** Whisper or a hosted recognizer with word-level timestamps and per-speaker tracks (grounding needs word timings).
8. **Real CRM connector.** HubSpot free tier or a Salesforce developer sandbox instead of the simulated CRM.
9. **Progress output and rate-limit handling.** Show per-chunk progress; batch chunks to cut API calls.

## Later: impact features

- **Multi-call deal tracking.** Compare call 1 vs call 3 and flag changes (budget, timeline, stakeholders).
- **Coaching insights.** Talk-to-listen ratio, question quality, unanswered objections.
- **Framework builder.** Let a team define its own qualification framework in YAML without code.
- **Multilingual and code-switching.** Urdu/English calls are relevant for South Asia and a genuinely hard problem.
- **Cost and latency dashboard.** Tokens per call, cost per call, time per stage.
- **Prompt-injection suite.** Test and harden against a prospect saying "ignore previous instructions" on the call.

## Housekeeping

- Keep API keys out of the repo: set them in the terminal or a `.env` file that is listed in `.gitignore`. Revoke any key that was ever pasted into a chat or committed.
- Add `anthropic` and `groq` to the optional `requirements-live.txt`, not the core `requirements.txt` (the README promises one runtime dependency).
- Update the README test count if it still says 387 (the suite collected 391 before the new tests).