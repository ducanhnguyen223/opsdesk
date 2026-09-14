# OpsDesk Backend

FastAPI backend for a fictional logistics operations team. It turns a delayed-shipment
notice into verified facts, affected orders, authorized procedure citations and a
human-reviewed internal ticket. It never sends customer messages or changes a carrier.

## What this demonstrates

- Tenant and role isolation derived from server sessions.
- Evidence-first workflow with stale-data checks and transactional, idempotent approval.
- Versioned procedures, exact citations, PDF text extraction and operator draft history.
- Strict OpenAI Responses adapter with bounded output, usage ledger, cost ceiling and exact cache.
- 60 frozen workflow cases plus reproducible retrieval and semantic-cache experiments.

Current offline evaluation: 60/60 workflow cases, zero false authorizations. On 28
synthetic Vietnamese retrieval queries, BM25 top-1 was 64.3%, dense 71.4% and hybrid
78.6%. Semantic cache remains disabled because answer-changing negations scored above
valid paraphrases.

## Run

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

Open `http://127.0.0.1:8767/docs`. The server binds to loopback and seeds fictional
data only when launched through `app.py`.

Minimal API flow:

1. `POST /api/demo/login` with `{"actor_id":"A-operator"}`.
2. `POST /api/analyses` with `{"message":"Lô SHP-1042 chậm hai ngày."}`.
3. Review the response and `POST /api/analyses/{id}/approve` with an
   `Idempotency-Key` and `{"proposal_revision":1,"confirmed":true}`.

Run the complete offline backend check:

```sh
.venv/bin/python verify.py
```

Optional embedding experiments use a separate environment and do not affect the API:

```sh
python3 -m venv .venv-embeddings
.venv-embeddings/bin/python -m pip install -r requirements-embeddings.txt
.venv-embeddings/bin/python evaluate_retrieval.py
.venv-embeddings/bin/python evaluate_semantic_cache.py --offline
```

Raw results are in [`artifacts/`](artifacts/). Architecture and trust boundaries are
documented in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Limits

The repository is backend-only and uses synthetic data. Default analysis is deterministic
and offline; the OpenAI transport has mocked protocol tests but no claimed live-model result.
Demo login is not production authentication, so do not expose this server publicly.

MIT licensed. No affiliation with Samsung or any real logistics provider.
