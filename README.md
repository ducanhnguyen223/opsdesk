# OpsDesk

An independent portfolio project exploring evidence-based handling of shipment delays with business APIs and retrieval-augmented generation.

**Current state, verified 2026-09-14:** working local operations web app with a
synthetic logistics queue, grounded offline analysis, reviewed tickets and versioned
operator-edited drafts. The OpenAI adapter has mocked protocol tests only. A reproducible
offline retrieval experiment exists; live LLM and public deployment are not complete.

Licensed under the [MIT License](LICENSE). All companies, customers, carriers,
shipments and operational records in the demo are fictional.

- [Specification](docs/spec.md)
- [Synthetic development dataset](fixtures/baseline.json)
- [Full project and web app delivery checklist](docs/DELIVERY.md)
- [A coordinator's shift: scenario and walkthrough](docs/SCENARIO.md)
- [Architecture and operational boundaries](docs/ARCHITECTURE.md)

## Run

Python 3.14 was used for verification. Node.js is also required to check the vanilla
browser JavaScript; there is no frontend package installation or build step.
Install in a project-local environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python verify.py
```

`demo.py` starts a real localhost HTTP server, logs in as a synthetic operator,
analyzes a delayed shipment, creates one ticket and replays approval. It also
checks foreign-tenant access is 404. The process and its temporary database are
removed on exit. No external API is called or customer message sent.

For a persistent local demo:

```sh
.venv/bin/python app.py
```

Open `http://127.0.0.1:8767` and choose **Alpha / Nhân viên vận hành**. Start with
the work queue: search `SHP-9000`, claim the case, analyze it, review citations,
save a draft and approve an internal ticket. No email is sent.
Interactive API documentation: `/docs` (Swagger assets may require internet).
For API-only usage, POST `/api/demo/login` with `{"actor_id":"A-operator"}` first.
Other synthetic actors: `A-viewer`, `B-operator`, `B-viewer`.
The default database is `opsdesk.sqlite3`, next to `app.py`, and is git-ignored.
Stop the server with Ctrl-C. Use `--db /path/to/new.sqlite3` for a separate demo.

## What is verified

- 85 Python tests covering workflow, retrieval, provider contracts, usage/cost allowances, workspace,
  case/draft persistence and PDF extraction. A Node check exercises the unsaved-draft guard.
- Browser-observed case workflow: filter, inspect, claim, analyze, review, approve,
  resolve and inspect audit history. Draft versions v1/v2 survived reopening the page.
- 36 additional synthetic cases and 108 orders across two tenants. Seed data is
  inserted once; restarting never resets operator changes or moves deadlines.
- Session-derived tenant scope, proposal ownership, operator-only approval,
  expired/revoked session handling and strict request/response schemas.
- Evidence snapshots kept outside provider output; current documents, orders,
  shipment and actor permissions rechecked in the approval transaction.
- Database uniqueness, atomic rollback and six concurrent approvals with either
  the same or different keys produce at most one ticket per proposal revision.
- Real HTTP smoke: analysis 200, approval 201, replay 200 with the same ticket,
  foreign shipment 404, history/ticket listing and procedure creation/search.
  This is offline workflow acceptance, not AI quality evidence.
- Workspace APIs: identity/logout, paginated own analysis history, tenant ticket list,
  document list/search, versioned creation/update/history. Concurrent edits use expected
  version checks; old versions are retained atomically. Stored analyses cannot bypass
  later document permission revocation.
- Frozen retrieval benchmark: 28 synthetic Vietnamese queries across 14 authorized procedures.
  With `intfloat/multilingual-e5-small` revision `614241f` (ONNX O4), BM25 top-1 was
  64.3%, dense 71.4% and reciprocal-rank hybrid 78.6%; dense recall@3 (92.9%) remained
  above hybrid (89.3%). No foreign, expired, role-restricted or untrusted document reached
  a ranking. These are small offline fixture results, not production quality claims.

## Limits and decisions

- SQLite uses one local file and serialized writers. PostgreSQL/pgvector migration
  is deferred until the retrieval/deployment work; do not claim it is implemented.
- Business fixtures are stored as JSON rows scoped by `(kind, id, tenant)`;
  approvals/tickets/version history use relational constraints. Procedure editing is
  available to scoped operators. Shipment/order editing and production migrations
  are not implemented. Ingestion currently accepts plain text/Markdown in JSON;
  Frontend document forms accept TXT/Markdown and text-layer PDF preview. PDFs are
  parsed locally in a timed child process, capped at 3 MiB / 30 pages / 10,000 extracted
  characters. Empty/scanned, encrypted and malformed files are rejected explicitly.
  Check text order/tables before saving; no OCR or layout-faithfulness claim is made.
  macOS uses decoder/CPU/time limits; a hard address-space limit is additionally applied
  on Linux. This parser is not a hardened public upload service.
- The deterministic adapter handles shipment IDs and a few explicit contradictory
  phrases, **not general language understanding**. Retrieval now chunks source text
  with exact offsets, ranks by accent-insensitive BM25 after scope/validity filtering,
  and verifies quotes against authorized chunks. Production search remains lexical;
  dense/hybrid retrieval is measured separately and is not enabled by default.
- Every applicable procedure remains represented in policy citations, even when its
  lexical score is zero: policy metadata, not ranking, determines applicability.
  Conflicts are checked before top-k retrieval, never hidden by relevance ranking.
- `openai_provider.py` implements the official Responses API with a strict structured
  response, disabled storage, bounded output and no automatic retry/redirect. It is
  not wired to the CLI. Only mocked protocol tests have run. It selects an action,
  citations and a Vietnamese draft. The backend rejects malformed drafts and wrong
  shipment IDs; this is NOT semantic fact verification. Every generated draft is
  explicitly labeled for human review. The live transport requires a durable usage ledger;
  injected test clients can omit it. Live validation remains pending.
- `usage.py` reserves request slots transactionally before network access, enforcing
  global and per-tenant/actor lifetime request allowances across concurrent calls and
  restarts. Failed and interrupted attempts still consume a slot. Reported input/output
  token counts are recorded even when generated output is rejected; unavailable usage
  stays unknown. No prompt, completion text or key is stored in this ledger.
  By default these are request caps, not a USD spending ceiling. Optional pricing configuration
  reserves a conservative upper cost before network access using an explicit
  configured USD budget and input/output rates. Integer nano-USD accounting avoids float
  drift; reported usage releases only the verified difference and missing usage retains the
  reservation. The live transport refuses to start without this cost-aware ledger. Rates are
  not hard-coded because the model has not been selected. This app-side ceiling is not the
  provider's billing limit; configure a project limit separately. Live CLI mode is still disabled. An authenticated
  usage endpoint and UI show only the current actor's records; offline has no fabricated counts.
  The offline view has API tests and browser validation.
- `evaluate.py` executes 60 frozen synthetic gold cases against a fresh SQLite database
  per case: 40 development and 20 holdout, with scenario families confined to one split.
  The raw report records exact HTTP/status/action/citation/order checks and offline latency.
  Current deterministic baseline: 60/60 cases, zero false authorizations. This is a
  controlled fixture result, NOT live-model quality or a production benchmark.
- Optional `ExactCache` stores only a SHA-256 request digest and already-validated structured
  response. Its key covers model, prompt/schema, complete provider input, tenant, actor, role
  and authorization revision. Changed evidence or permissions miss the cache; an in-flight
  claim blocks duplicate upstream calls and expires after 60 seconds. The default demo does
  not enable this cache. A frozen 24-pair semantic-cache experiment found that answer-changing
  negations scored up to 0.968, above every valid paraphrase. The only development threshold
  with zero false hits produced zero true hits, so semantic caching remains disabled.
- Provider output cannot choose arbitrary actions or citations. The hostile note
  reaches the fake adapter in tests, but this does not establish LLM prompt-injection robustness.
- Tests freeze time at the fixture timestamp; the server checks policy validity
  against current UTC. Cached or stale approvals are not silently reused.
- Demo identity selection is deliberately not real authentication. Sessions expire
  in one hour. Login is off by default in `create_app`; `app.py` explicitly enables
  it and binds only to loopback with proxy headers disabled. Never tunnel or reverse
  proxy this demo to the internet; real authentication/HTTPS is still required.
- Starlette currently emits an HTTPX TestClient deprecation warning. Tests pass;
  dependency versions are pinned. This is not a model/runtime failure.

Fixture-only checking remains dependency-free:

```sh
python3 -m unittest discover -s tests -p test_fixtures.py -v
```

## Verification and delivery

`verify.py` runs dependency compatibility, all Python tests, JavaScript syntax,
the unsaved-draft predicate and a real localhost HTTP smoke test. It does not call
OpenAI or install dependencies, and does not pass API credentials to subprocesses.
Run it with the project's virtual-environment Python, not a shared global environment.

Run only the reproducible offline evaluation with `.venv/bin/python evaluate.py`.
It writes `artifacts/evaluation_offline.json`.

The optional retrieval experiment does not affect the app or default verification:

```sh
python3 -m venv .venv-embeddings
.venv-embeddings/bin/python -m pip install -r requirements-embeddings.txt
.venv-embeddings/bin/python evaluate_retrieval.py
.venv-embeddings/bin/python evaluate_semantic_cache.py --offline
```

It downloads a 240 MB MIT-licensed multilingual E5 ONNX model into
`~/.cache/opsdesk/fastembed` and writes raw rankings and metrics to
`artifacts/retrieval_evaluation.json`. Use `--offline` after the first download.
The semantic-cache command writes every pair, similarity, selected development threshold and
holdout outcome to `artifacts/semantic_cache_evaluation.json`; its current recommendation is false.

The GitHub Actions workflow runs the same command on a clean runner. Published checkpoints
have passing hosted runs; the optional 240 MB embedding benchmark remains an explicit local run.
The complete delivery checklist remains authoritative; this is not a production-ready
or fully evaluated AI system yet.

All companies, shipments, orders and business procedures are fictional. No affiliation with Samsung or real logistics providers is implied. Human review of synthetic gold labels is still required before making portfolio quality claims.

Implementation references: [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/),
[Python SQLite transactions](https://docs.python.org/3/library/sqlite3.html).
The OpenAI adapter follows [official Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs).
