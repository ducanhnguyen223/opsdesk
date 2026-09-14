# OpsDesk

A late shipment rarely lives in one record. The carrier has an update, several orders
may be affected, and the response depends on the service policy that was valid at that
moment. OpsDesk brings those pieces into one review flow before anyone takes action.

An operator submits the carrier notice. The service finds the shipment and active orders,
retrieves the procedures that operator is allowed to use, and prepares a sourced proposal.
Nothing is sent automatically: approval creates an internal ticket only after the evidence
is checked again.

## Design notes

- Tenant, role and document validity are applied before retrieval.
- Shipment state comes from the database; generated text cannot create facts or approve work.
- Approvals are transactional and idempotent. Changed evidence makes an old proposal unusable.

The sample contains two isolated companies, versioned procedures, conflicting policies and
hostile document text. All names and operational records are synthetic.

## Run

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

To use a Responses-compatible model, copy `.env.example`, fill it in, then start with:

```sh
.venv/bin/python app.py --provider-config .env
```

API documentation is available at `http://127.0.0.1:8767/docs`. A minimal flow is:

1. `POST /api/demo/login` with `{"actor_id":"A-operator"}`
2. `POST /api/analyses` with `{"message":"Lô SHP-1042 chậm hai ngày."}`
3. Review the result, then approve it with an `Idempotency-Key`

Run the offline checks with:

```sh
.venv/bin/python verify.py
```

## Evaluation

The frozen workflow set currently passes 60/60 cases with no cross-scope authorization.
On 28 Vietnamese retrieval queries, top-1 accuracy was 64.3% for BM25, 71.4% for dense
retrieval and 78.6% for reciprocal-rank fusion. A separate cache experiment rejected
semantic reuse because negated requests scored above valid paraphrases; exact caching remains.

Raw results are kept in [`artifacts/`](artifacts/), with the model revision and individual
rankings. Architecture and trust boundaries are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

The default provider is deterministic and offline. The remote model adapter has protocol
tests, but this repository does not claim a live model run. Demo login is for localhost only.

MIT licensed.
