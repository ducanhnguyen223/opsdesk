# OpsDesk Agent Gym — prototype

This experiment turns OpsDesk's synthetic cases and read-only MCP tools into short tool-use episodes. It is separate from the product workflow: proposals are checked, never approved or executed, and each episode uses a temporary SQLite database.

The agent can call `list_cases`, `get_case` and `search_procedures`, then submit a structured proposal. It must include the exact procedure excerpt, version and chunk ID returned by the tool. The procedure tool exposes ranked matches and a separate complete list of currently applicable procedures, so a lower-ranked conflict is not hidden. The verifier checks that the agent observed the target case and current policy, identified active orders, cited the right procedure, and abstained when policy is missing or conflicting. A cross-tenant probe or forbidden action cannot be offset by a correct final answer. Final episode results expose component checks for schema, evidence, decision/action, affected-order precision/recall, citation validity/completeness, abstention, safety attempts and tool-call counts.

`experiments/agent_runner.py` now runs an async `agent.act(observation, history)` adapter. Each action/response is passed to the same environment and its reward/observation retained with the raw adapter output. Malformed responses are preserved and counted as invalid actions; an adapter exception returns only its type, not its message. The result includes the initial task/tool-schema observation and the sum of wall time spent inside `agent.act` only—not environment execution, token usage, or hardware resource use. Results and raw outputs stay in memory; nothing is written to disk. A live model adapter and its model/revision are still unselected.

The pilot has five development episodes and six held-out episodes. The held-out split includes Beta-specific policy IDs/actions and conflict documents plus an Alpha case that contains an expired-policy distractor. An environment instance is bound to one split, and split metadata is returned outside the observation. All records and rewards are synthetic. Trajectories are returned in memory for inspection; this prototype does not persist or train on them.

Run the focused check from this directory:

```sh
.venv/bin/python -m unittest tests.test_agent_gym -v
```

`summarize_results()` aggregates component checks overall, by split and by scenario family, including adapter latency from the runner. This is a task-wiring pilot, not a statistically useful benchmark, Gymnasium-compatible package, RL run, or evidence of model quality. The source is local and the gold labels are visible to a repository reader, so the split is not a secure hidden test. Eleven episodes are far too few for training claims. Next gate: grow independently authored cases and freeze a larger held-out suite before connecting a model.

## Verification — 2026-09-27

- Focused Agent Gym suite: 17 tests passed, including four runner regressions for a valid trajectory, an unsafe action, malformed output and an adapter exception.
- `.venv/bin/python verify.py`: dependency check passed; full OpsDesk suite passed (106 tests); triage examples passed; deterministic evaluator passed 60/60 synthetic cases with zero false authorizations; offline demo smoke passed.
- The verification script rewrites `artifacts/evaluation_offline.json`; that generated change was restored because this experiment did not intend to update the checked-in artifact.
- No external model/API calls or training ran. The 60-case result is a deterministic product-fixture baseline, not Agent Gym or LLM quality. Runner latency tests use only a scripted adapter and are not model latency measurements.
