# OpsDesk — agent entrypoint

- Read `docs/DELIVERY.md`, `docs/SCENARIO.md` and `docs/ARCHITECTURE.md` before changing behavior.
- Verify from this directory with `.venv/bin/python verify.py`.
- Preserve tenant/role/evidence/idempotency checks and existing user data.
- Keep demo loopback-only, synthetic data visibly labeled and offline by default.
- Paid API calls need explicit model/budget authorization and a locally configured key.
- No automatic email, public deployment or unrelated file cleanup.
- Unit tests do not prove live AI quality, full browser QA or complete delivery.
- Verify current code and runtime rather than relying on stale checklist entries.
