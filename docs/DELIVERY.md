# Complete project + web app acceptance

Active user goal: **hoàn thành dự án và web app ở mức hoàn chỉnh**.
Do not mark this goal complete on backend tests alone. This is an evolving evidence ledger,
not a reduced substitute for the full plan in `../../../docs/plans/2026-09-10-samsung-sds-ai-portfolio-plan.md`.

## Current evidence (2026-09-12)

- [x] Offline business APIs, tenant scoping, server sessions, transactional approval.
- [x] Twenty development scenarios execute through HTTP; database concurrency/rollback tests.
- [x] Source chunking, accent-insensitive BM25, exact citations, pre-retrieval scope filtering.
- [x] Official OpenAI Responses transport and strict JSON schema covered by mocked HTTP tests.
- [x] Web workspace APIs: session/logout, own history, tenant tickets, scoped document search,
  text/Markdown ingestion, version history and concurrent-edit protection. 57 tests pass;
  expanded real HTTP smoke covers history, ticket listing and document creation/search.
- [x] Queue-first logistics workflow with 36 persisted synthetic cases across two tenants,
  108 related order records, carrier notices, fictional customers/routes, stable SLA deadlines,
  ownership, notes, controlled lifecycle, approved-ticket association and append-only event history
  through the API. Initial scenario histories are explicitly marked synthetic.
- [x] 60 tests pass, including case lifecycle, restart persistence, tenant/viewer boundaries,
  stale revision rejection, invalid transitions and mandatory approved ticket before resolution.
- [x] Browser-observed workflow on 2026-09-12: login, filter SHP-9000, open EX-9000, claim,
  deterministic analysis with A-STANDARD-V2 citation, approve ticket, link ticket, resolve with note.
  DOM confirmed ticket/event/status persistence within the workflow. Desktop 1440 px and mobile
  390 px checked with no document horizontal overflow; complete visual/accessibility audit remains.
- [ ] Live OpenAI generation and drafting end-to-end, with explicit model/cost authorization.
- [x] Local operator-edited case drafts: recipient label, subject/body, immutable versions through
  the API, author/time, case audit event, tenant/role checks, analysis/shipment binding, stale evidence
  and concurrent-version rejection. No send endpoint. 62 Python tests pass plus
  `node tests/test_web_state.js` for unsaved-change protection.
- [x] Browser verified draft v1 and edited-subject v2 in EX-9006, then reopened after page/session
  changes: exact edited subject/body and both audit entries persisted. Attempting to leave with
  an unsaved title showed the expected warning; discard restored the saved draft.
- [ ] Document ingestion and management; ranked keyword/hybrid comparison with actual embeddings.
- [x] PDF text extraction API and frontend import control implemented using pinned pypdf.
  Real parser subprocess + API tests verify extraction, preview without database mutation,
  explicit save/search, permissions, blank/encrypted/malformed/oversized text, timeout and busy
  responses. PDF picker browser interaction remains to be verified separately.
  Parser capped at 3 MiB, 30 pages, 10,000 characters, 5 CPU seconds / 8 wall seconds;
  decoder limits on macOS/Linux, additional 512 MiB address-space limit on Linux only.
  No OCR; no original PDF retention; page markers are preserved in extracted text.
- [x] Full `verify.py` rerun after PDF integration: 66 Python tests, JavaScript syntax,
  unsaved-draft check, dependency compatibility and real HTTP demo all pass.
- [ ] Timeout/retry policy, usage/cost ledger, per-user limits and enforced spend ceiling.
- [ ] Exact cache and measured semantic-cache experiment with stale/permission isolation checks.
- [ ] Complete web UI using the real backend: identity, analysis, citations, review, tickets/history,
  document management and usage/errors. No hardcoded success responses.
- [ ] Real authentication/deployment configuration for any public demo; no public demo-login bypass.
- [x] 60 frozen synthetic evaluation cases: 40 development/20 held-out, with every scenario
  family confined to one split. `evaluate.py` uses a fresh database per case and writes raw
  results to `artifacts/evaluation_offline.json`. Current deterministic result: 60/60 exact
  gold checks, zero false authorizations, p50 2.972 ms / p95 3.881 ms on this local run.
  This is an offline fixture baseline, not live-model quality or production performance.
- [ ] Browser end-to-end verification, responsive/keyboard/error states, offline/live distinction.
- [ ] CI, deployment instructions, architecture documentation, demo video, secret/license checks.
- [x] Local verification entrypoint `verify.py` passed dependency compatibility, 62 Python tests,
  JavaScript syntax/unsaved-state checks, and real localhost HTTP smoke. README updated to current
  functionality; architecture/trust-boundary documentation added.
- [x] GitHub Actions workflow authored with read-only token permissions, disabled credential
  persistence and SHA-pinned actions. Hosted CI is NOT verified; repository publication is pending.
- [ ] Backend repository and working web demo delivery; verify both from the delivered artifacts.

Related follow-ups remain ordered after the product: CV/profile presentation and reversible
workspace cleanup. They must not be claimed done merely because the application is ready.

## Next implementation

Verified 2026-09-14: the offline **Sử dụng AI** page was exercised in the real browser
and correctly showed no GPT calls or fabricated token/cost values. The PDF control opened
the native file chooser, but Codex safety prevents driving its own app's chooser; picker
selection remains unverified rather than being claimed as passed. Added the 60-case frozen
evaluation dataset, executable evaluator, raw per-case results and split/family guards.
Full verify.py now passes 77 Python tests, JS checks, 60/60 offline eval and HTTP smoke.

Handoff 2026-09-14: authenticated `/api/usage` and the Sử dụng AI view are implemented; scoped summaries
exclude other actors/tenants and show unknown usage explicitly. 74 Python tests plus
full verify.py passed. The demo restarted on 127.0.0.1:8767 with its existing DB and
health confirmed offline. Usage UI and PDF file picker browser checks remain pending.

Verified 2026-09-12: durable SQLite provider-call ledger with atomic global/per-actor
request reservations, retained failed/crashed reservations and reported token usage.
Live transport construction requires a ledger. Mock-only tests verify concurrent quota
contention, restart persistence, rejected-response usage, unknown usage and omission of
prompt/key/content from the ledger. 72 Python tests plus complete offline verify pass.
This is NOT a dollar spending ceiling; live configuration, cost rates and usage UI remain.

Verified 2026-09-12: provider structured output now includes a Vietnamese draft;
backend bounds its length, rejects missing/foreign shipment IDs and control characters,
persists it with an explicit unverified-wording warning and never sends it. Three new
regression checks pass (69 Python tests total); full verify.py including HTTP smoke passes.
This does not establish live LLM quality or semantic grounding of generated prose.

1. Finish provider output contract for grounded summaries/drafts and usage metadata; connect
   live mode only behind explicit configuration and spending controls.
2. Verify the PDF file picker in the browser; extend the queue-first web application with the
   live provider and usage/error visibility. Editable persisted drafts and PDF extraction are implemented.
3. Continue the complete plan through reliability, web UI, evaluation and delivery.

No paid API request has been made. API key, model and authorized budget have not been supplied
for this project. This does not block offline development or UI work.
