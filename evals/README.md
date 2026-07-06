# OpenCloset E2E Eval Harness

`opencloset/evals/` is the product-level behavioral regression harness for OpenCloset.

This is not a generic chatbot benchmark.

It evaluates OpenCloset as a runtime and harness:

- prompt shaping
- tool discipline
- continuity
- read-before-edit behavior
- execution honesty
- final answer quality
- transient window behavior
- recovery from blocked or failing runs

The local model is one variable. The harness is what we are testing.

## CLI

Via the OpenCloset CLI wrapper:

```bash
python opencloset/oc.py eval run --suite e2e_basic
python opencloset/oc.py eval run --scenario debug_existing_bug_no_rewrite
python opencloset/oc.py eval run --suite e2e_basic --provider llamacpp --model qwen3.6-27b
python opencloset/oc.py eval compare --scenario debug_existing_bug_no_rewrite
```

Direct module form:

```bash
cd opencloset
python -m evals run --suite e2e_basic
python -m evals run --scenario transient_window_report --judge --judge-provider openai --judge-model gpt-4.1-mini
python -m evals compare --suite e2e_basic
```

## Outputs

Each scenario run persists a trace artifact under:

- `evals/runs/YYYYMMDD/<scenario-id>/<artifact>.json`
- `evals/runs/YYYYMMDD/<scenario-id>/<artifact>.replay.json`

Each suite run also writes reports under:

- `evals/reports/<timestamp>_summary.md`
- `evals/reports/<timestamp>_trace.json`
- `evals/reports/<timestamp>_failures.md`

## Scenario Shape

Scenario files live in:

- `evals/scenarios/*.yaml`

Key fields:

- `id`
- `title`
- `category`
- `setup` or `setup_state`
- `turns` or `user_prompt`
- `expected_behavior`
- `forbidden_behavior`
- `scoring_rubric`
- `required_observations`
- `max_turns`
- `checks.rules`

Useful placeholders:

- `${repo_root}`
- `${scenario_dir}`
- `${evals_root}`
- `${temp_workspace}` at runtime

## Suites

Suite files live in:

- `evals/suites/*.yaml`

Current starter suite:

- `e2e_basic`
- `coding_requests_ready`

It includes 12 product-style scenarios:

1. `simple_question`
2. `recommendation_games_vibes`
3. `architecture_spec_opencloset`
4. `code_add_small_feature`
5. `debug_existing_bug_no_rewrite`
6. `continue_existing_plan`
7. `research_with_sources`
8. `tool_read_before_edit`
9. `stalled_run_recovery`
10. `user_angry_course_correct`
11. `context_compaction_continuity`
12. `transient_window_report`

Focused coding-readiness suite:

1. `multi_turn_coding_patch`
2. `code_add_small_feature`
3. `coding_request_perf`

## Judge Mode

Judge mode is optional.

When enabled, the harness runs a second evaluator pass through the real OpenCloset runtime and asks the evaluator to score:

- intent capture
- context use
- tool discipline
- completion
- output quality
- runtime stability
- user experience

It also asks for:

- pass/fail style verdict
- failure category
- likely root cause
- recommended patch target
- minimal fix suggestion

## Adding New Scenarios

1. Add a YAML file under `evals/scenarios/`.
2. Prefer temp-workspace isolation for coding scenarios.
3. Encode observable expectations in `checks.rules` where possible.
4. Put softer behavioral expectations in:
   - `expected_behavior`
   - `forbidden_behavior`
   - `required_observations`
5. Add the scenario id to a suite file in `evals/suites/`.
6. If the scenario is non-trivial, add a focused test in `tests/test_evals.py`.

## Current Boundaries

This harness already uses the real OpenCloset runtime path, stores traces, supports suite reports, and can run an optional evaluator pass.

Still intentionally lightweight:

- scenario reset/isolation is temp-workspace based, not full VM/container isolation
- judge mode is runtime-driven JSON scoring, not a separate external orchestration service
- some behavioral failures are still easier to catch via evaluator judgment than hard rules
