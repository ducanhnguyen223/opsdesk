# OpsDesk v0.1 — contract and baseline

Status: G2 implemented; G3 in progress on 2026-09-11. Scoped lexical retrieval and source chunks work; OpenAI transport has mocked tests only. Live AI evaluation is not implemented.

## Scope

An independent portfolio application for a fictional logistics operator investigating one delayed shipment. All fixtures are synthetic. No affiliation with Samsung, real carrier connections, real customer emails or imported ReverseProxy data.

The operator pastes a notice, reviews verified shipment/order facts and relevant procedures, then explicitly approves creation of an internal ticket. Viewers can analyze but cannot approve. The application never changes shipment routing or sends customer messages.

## Data and time

`fixtures/baseline.json` contains two tenants, four actors, twelve shipments, twenty-four orders, eight documents and twenty hand-authored development scenarios. The fixed evaluation time is `2026-09-10T12:00:00Z`. IDs, names and policies are invented for testing and are not industry advice.

An order is affected only if it belongs to the requested shipment and tenant, is active, and the shipment is delayed. Cancelled orders are excluded. Dates and status come from the business store; model prose is never an authority for changing them. A notice inconsistent with the store requires clarification.

Document validity uses a half-open interval: `effective_from <= as_of < effective_to`; a null end means no scheduled expiry. Scope must match tenant, customer policy scope and allowed role before retrieval. An untrusted note cannot act as a procedure. A higher version number alone does not resolve overlapping contradictory policies without declared supersession. These fixtures intentionally include that conflict.

## Identity and authorization

For local demo, fixed actors are selected through a clearly marked demo login bound to a server-side session. Demo login must be disabled on any publicly accessible deployment until real authentication exists. Never accept a tenant/role from an analysis body as authority.

Server-side checks apply to shipment lookup, order lookup, document retrieval, proposal access, approval and ticket retrieval. Return the same generic 404 for missing and foreign-tenant resource IDs; no foreign facts reach the model or cache. Viewer approval returns 403. No authentication returns 401.

## Endpoints

| Endpoint | Request | Success | Errors |
|---|---|---|---|
| `POST /api/analyses` | `{message: string}` (1–8000 characters) | 200 analysis below | 401, 404, 422 |
| `GET /api/shipments/{id}` | Session identity | 200 tenant-scoped shipment | 401, 404 |
| `GET /api/shipments/{id}/orders` | Session identity | 200 scoped orders | 401, 404 |
| `GET /api/analyses/{id}` | Session identity | 200 scoped saved analysis | 401, 404 |
| `POST /api/analyses/{id}/approve` | `{proposal_revision: int, confirmed: true}`, `Idempotency-Key` header | 201 first ticket, 200 replay | 401, 403, 404, 409, 422 |
| `GET /api/tickets/{id}` | Session identity | 200 scoped ticket | 401, 404 |

Reject unknown request fields. Provider/database outages affecting API infrastructure may return 503. A caught provider failure during an otherwise valid analysis returns a typed `dependency_unavailable` result, never a fabricated ready proposal. Fault injection fields in fixtures are test-harness settings, not public endpoint parameters.

## Analysis response

Required keys:

```json
{
  "analysis_id": "server-generated",
  "revision": 1,
  "status": "ready_for_review",
  "clarification_questions": [],
  "verified_facts": [],
  "affected_orders": [],
  "recommended_actions": [],
  "citations": [],
  "draft_message": null,
  "warnings": [],
  "trace_id": "server-generated"
}
```

Status is one of `needs_clarification`, `insufficient_evidence`, `ready_for_review`, `dependency_unavailable`. Only `ready_for_review` may be approved. Non-ready results have no executable recommended action and no send-ready draft.

- Fact: `{field, value, source_type, source_id, source_revision}`. Database facts reference the row revision.
- Citation: `{document_id, version, chunk_id, quote}`. Quote must occur in the cited authorized source; citation existence is checked outside the model.
- Recommended action: `{action, reason, citation_ids}`; action is from the controlled policy vocabulary, never executable model code.
- Draft is a string for human review. Do not promise compensation or a new guaranteed delivery date without evidence.
- Persist the proposal's shipment/order revisions, applicable knowledge version fingerprint, identity and authorization revision separately from model output. Keep sensitive trace content out of normal logs.

## Approval and consistency

Approval is a separate authenticated endpoint, not a tool exposed to the analysis model. Recheck current role, proposal ownership, status, shipment/order and document revisions in a transaction. Any changed evidence requires a new analysis (409). Viewer role cannot approve even if the proposal was created while the actor had operator rights.

Use a database unique constraint for `(tenant_id, idempotency_key)`, store a canonical payload hash and ticket result atomically. Same key/same payload returns the original result; a changed payload returns 409. Also permit at most one ticket per analysis revision, including requests using different idempotency keys. Replays still require current access rights. Concurrent requests must not produce duplicate tickets.

## Tool and model boundary

Read-only tools: `get_shipment(id)`, `list_affected_orders(shipment_id)`, `search_procedures(query, authorized_scope)`. Their implementation adds identity scope server-side. No free-form SQL, filesystem tool or arbitrary URL fetch.

G2 uses a clearly marked fake provider and policy-scope/validity filtering to test workflow behavior. Ranked keyword search and embeddings/LLM comparisons belong to G3. User text and retrieved content are untrusted data. They cannot override scope, execute actions or grant permissions. A provider fallback must not weaken these rules.

## Baseline evaluation contract

Each scenario has a stable ID, actor, message, expected subset, category and explanation. Fixtures with `operation` exercise approval after `setup_case`; unspecified operation means analyze. Every case runs with a fresh database snapshot. `tickets_created` is the final row-count difference for that case, not a count of HTTP calls. `http_sequence` describes ordered requests. C18–C20 include first creation in their count.

Expected results assert facts, status, scope, citations and effects, not exact prose. C12 must remain safe with the injected note included in retrieval context, not just absent from retrieval. Dependency faults must be injected at adapter boundaries. G2 must additionally test simultaneous approvals, revoked roles, changed document/order versions and duplicate creation with different keys.

`test_fixtures.py` validates fixture references, scopes, dates, policy expectations and coverage only. `test_backend.py` executes all twenty development scenarios through the HTTP API plus thirteen regression checks. Seven retrieval and four mocked OpenAI tests bring the current total to 49. Passing the offline adapter does not establish model quality or a safety certification.

The twenty cases are development data. Later holdout cases must be independently authored, frozen before evaluation and kept separate by scenario family. Synthetic gold labels need human review before being used for CV performance claims.

## G1 exit / G2 entry

- Contract, synthetic fixtures and fixture consistency checker exist.
- Checker passes using Python standard library, without network or credentials.
- G2 completed: business storage, demo session, deterministic analysis and transactional approval. 36 tests and a real HTTP smoke pass. Next is G3 retrieval and live-provider integration; no cache yet.

## G2 implementation decisions

- FastAPI/Pydantic schemas, synchronous SQLite operations in synchronous endpoint handlers; no ORM/agent framework.
- SQLite stores scoped business snapshots plus constrained ticket/idempotency tables. A write transaction rechecks evidence and commits ticket/key together. PostgreSQL remains a later migration, not a current capability.
- Saved analyses belong to their creating actor. Tickets are readable within their tenant. Removed or altered evidence invalidates approval, including replay, with 409.
- Opaque session cookie (HttpOnly, SameSite=Strict), server-side hashed token, one-hour expiry and authorization revision. Demo mode binds loopback only and rejects foreign Host/Origin; it must not be publicly proxied.
- Minimal parser recognizes one shipment ID and selected contradictory phrases. It is not an LLM. Procedure metadata selects applicable sources; G3 now adds chunk-level lexical ranking.
- A fresh database seeds once; restarting never overwrites changed records. Synthetic test databases and live-smoke databases are temporary.

## G3 progress

`retrieval.py` provides Unicode/accent-normalized tokenization without dropping negations,
overlapping source-preserving chunks, BM25 ranking after authorization, and exact citation
validation. Policy conflicts are checked on the complete applicable policy set, not top-k.
Empty procedures cannot authorize tickets. Evidence changed during provider execution
causes 409 before the response is saved. Vector/hybrid and ingestion remain pending.

The OpenAI adapter uses the official Responses API with a strict action/citation schema,
explicit model/key constructor, no automatic environment-key discovery, no redirects or
automatic retry. Tests use only a mock HTTP transport. It is not enabled in the demo CLI;
live verification, model drafting, usage and spend controls are unfinished.

## Web workspace API (implemented; UI pending)

- `GET /api/me`: safe identity fields plus configured provider mode; no session token/hash.
- `POST /api/logout` with `{}`: revoke server session and remove cookie.
- `GET /api/analyses?limit=20&before=<cursor>`: newest-first own history; rows whose
  source access was revoked are omitted. Cursor advances over scanned rows, so a
  page may be short/empty while a next cursor still exists.
- `GET /api/tickets?limit=20&before=<cursor>`: newest-first tenant ticket summaries.
- `GET /api/documents?limit=20&after=<id>` and `GET /api/documents/{id}`: only documents
  allowed for the current role, including expired ones for management; search separately
  enforces validity. Page limits are capped at 100.
- `GET /api/documents/search?q=...&shipment_id=...&limit=6`: BM25 after shipment-derived
  policy/tenant scope, role and validity checks. Empty query is 422; limit is capped at 20.
- `POST /api/documents`: operator-only JSON text/Markdown ingestion; server assigns tenant,
  document ID and version. Requires title, text (up to 10,000 characters), scope, controlled
  action, allowed roles, timezone-aware validity dates and `expected_version: 0`.
- `PUT /api/documents/{id}`: same fields with current expected version. One transaction
  retains old/new snapshots and replaces current document. Stale update returns 409.
- `GET /api/documents/{id}/versions`: current and historical role checks both apply.

57 tests and the expanded real HTTP demo pass. Eight new tests cover workspace permissions,
pagination, logout revocation, document lifecycle/stale approval and concurrent updates.
No document deletion or shipment mutation endpoints. Public login, frontend,
PDF ingestion and live API spend controls remain pending.
