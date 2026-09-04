# Codebase Improvements Backlog

Findings from the classification-split work. Each item below is a candidate for
its own GitHub issue. Items already fixed in the split branch are marked DONE for
traceability; the rest are open.

Severity: **S** security, **C** correctness, **M** maintainability, **X** split follow-up.

---

## DONE (fixed in the classification-split branch)

### [S] #1 - Production RDS was the default DB host
`config/settings.py` defaulted `DB_HOST` to the production RDS endpoint, so any
process with an unset `DB_HOST` would silently connect to prod. Changed the
default to `localhost`; `DB_HOST` must now be set explicitly in each environment.
This was the whole reason the local classifier compose had to pin `DB_HOST: db`
as a guardrail.

---

## OPEN

### [X] #6 - How does the classifier run in production?
`fill_missing_intents` is a one-shot command. In the split, something has to run
it on a schedule to drain rows whose `strategic_intent` is still null (the Lambda
now only ingests). Decide the mechanism: an ECS scheduled task, a cron on the
GPU/CPU box, or an EventBridge-triggered container. This is a design decision, not
a code change yet.

### [C] #7 - LLM arbitration swallows all errors
`ml_inference_service.py._get_llm_strategic_intent` catches every exception and
returns `Neutral`/`0.0`. That is exactly how the decommissioned-Groq-model failure
hid for months - a dead model looked like a lake of low-confidence Neutral
predictions. The `check_groq` preflight now catches a fully dead key/model up
front, but the inference path should still (a) log the real error at ERROR level
and (b) distinguish "LLM failed" from a genuine Neutral (e.g. a distinct
`prediction_source` value) so silent degradation is visible.

### [C] #8 - `map_to_canonical_intent` drops Neutral/Unknown to NULL
`dashboard/utils.py` returns `None` for `neutral`/`unknown` because they are not in
`intent_mapping`. A correctly-Neutral article is then stored with a NULL intent,
indistinguishable from an unprocessed row. `fill_missing_intents` was patched with
an `ml_processed_at` marker so those rows settle, but the other pipeline runners
(`run_pipeline`, `run_full_pipeline`, `Full_pipeline_run`) hit the same mapping and
have no such guard. Decide how Neutral is represented globally.

### [M] #9 - No tests
There is no test suite (`dashboard/tests.py` does not exist). At minimum, add unit
tests for the two things the split touched: `map_to_canonical_intent` (including
the Neutral -> None case) and the `fill_missing_intents` queryset filter
(unprocessed vs processed-Neutral).

### [M] #10 - Duplicate modules and dead commands
Two ingestion modules exist - `mediacloud_ingestion.py` and
`mediacloud_ingestion_service.py` (the Lambda uses `_service`). There are also
three pipeline orchestrators (`run_pipeline`, `run_full_pipeline`,
`Full_pipeline_run`) that all do ingest+ML in one shot, plus one-off data-fixers
(`fast_redo`, `fix_mappings`, `fix_tone_data`, `reset_other_intents_to_null`).
`views.py` is ~2,300 lines. Consolidate to one canonical ingestion path, delete the
dead runners, and split `views.py`.

### [M] #11 - Inconsistent dependency pinning
`requirements.txt` mixes `==` and `>=` with no lockfile or hashes, so the Lambda
and classifier images can drift to different resolved versions. There are also 3
open Dependabot PRs, including a **Django 4.2 -> 5.2 major bump** that needs a test
pass before merge. Adopt a single pinned lockfile (or `pip-compile`) shared by both
images. (The split's `requirements-lambda.txt` is already pinned to resolved
versions as a first step.)

---

## Not doing here (tracked elsewhere)

- **MediaCloud API key rotation** - the key leaked in git history. Rotation is
  deferred until deployment; it must be rotated and scrubbed from history then.
