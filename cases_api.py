"""Synthetic logistics work queue; all operator changes persist transactionally."""
import json
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field

from backend import Problem, authenticate, encode, record, scoped_rows, owned_analysis, evidence, fingerprint
from workspace_api import Model, check_snapshot_access
from retrieval import instant

STATES = {
    "new": {"investigating"},
    "investigating": {"waiting_carrier", "escalated", "resolved"},
    "waiting_carrier": {"investigating", "escalated", "resolved"},
    "escalated": {"investigating", "waiting_carrier", "resolved"},
    "resolved": {"investigating"},
}


def seed_cases(store, now):
    """Seed once, never reset operator work or refresh deadlines on restart."""
    with store.connect(write=True) as db:
        if db.execute("SELECT 1 FROM metadata WHERE key='case_scenario_v1'").fetchone():
            return
        for tenant, base in (("A", 9000), ("B", 9100)):
            for i in range(18):
                shipment_id = f"SHP-{base+i}"
                scope = ["standard", "premium", "standard", "conflict", "no_policy", "standard"][i % 6]
                # Fictional operational targets, not legal or industry SLA claims.
                deadline = now + timedelta(minutes=[30, -45, 90, 180, 240, 360][i % 6])
                promised = now + timedelta(hours=6 + i)
                shipment = dict(id=shipment_id, tenant_id=tenant, customer_id=f"{tenant}-demo-{i % 6}",
                    policy_scope=scope, status="delayed", revision=1,
                    promised_at=promised.isoformat(), estimated_at=(promised + timedelta(hours=48)).isoformat())
                db.execute("INSERT INTO records VALUES ('shipments',?,?,?)", (shipment_id, tenant, encode(shipment)))
                orders = []
                for j in range(1, 4):
                    order = dict(id=f"ORD-{base+i}-{j}", tenant_id=tenant, shipment_id=shipment_id,
                                 customer_id=shipment["customer_id"], status="cancelled" if j == 3 else "active", revision=1)
                    orders.append(order | {"product": ["Bộ điều khiển công nghiệp", "Cảm biến nhiệt", "Phụ kiện lắp ráp"][j-1],
                                           "quantity": [24, 120, 10][j-1], "value_vnd": [96000000, 36000000, 2000000][j-1]})
                    db.execute("INSERT INTO records VALUES ('orders',?,?,?)", (order["id"], tenant, encode(order)))
                customer = ["An Việt Manufacturing", "Nam Sơn Electronics", "Minh An Retail", "Bắc Hà Components", "Thành Công Supply", "Đông Phong Trading"][i % 6]
                carrier = ["Northline Demo", "Hải Đăng Demo", "Pacific Demo"][i % 3]
                status = ["new", "new", "investigating", "waiting_carrier", "escalated", "resolved"][i % 6]
                created = now - timedelta(hours=i + 1)
                notice = f"{shipment_id}: Lô hàng bị chậm 48 giờ do xe trung chuyển cần bảo dưỡng. ETA mới: {shipment['estimated_at']}. Đề nghị điều phối xác nhận các đơn bị ảnh hưởng và thông báo khách hàng theo quy trình."
                events = [{"at": created.isoformat(), "actor": "scenario-import", "action": "created",
                           "note": "Tiếp nhận thông báo giả lập từ hãng vận chuyển."}]
                if status != "new":
                    events.append({"at": (created + timedelta(minutes=15)).isoformat(), "actor": f"{tenant}-operator",
                        "action": "scenario_history", "note": {
                            "investigating": "Đã nhận hồ sơ và đối chiếu đơn hàng.",
                            "waiting_carrier": "Đã ghi nhận yêu cầu kiểm tra ETA trong kịch bản mẫu; đang chờ phản hồi.",
                            "escalated": "Quy trình chưa rõ; chuyển trưởng ca kiểm tra trong kịch bản mẫu.",
                            "resolved": "Hồ sơ mẫu đã đóng: khách hàng xác nhận lịch giao mới trong kịch bản giả lập."
                        }[status]})
                case = dict(id=f"EX-{base+i}", tenant_id=tenant, shipment_id=shipment_id, revision=1,
                    title="Giao chậm 48 giờ · " + customer, category="transit_delay",
                    customer=customer, customer_tier="Key account" if i % 3 == 0 else "Standard",
                    carrier=carrier, origin=["Hải Phòng", "Bắc Ninh", "Hưng Yên"][i % 3],
                    destination=["Hà Nội", "Thái Nguyên", "Đà Nẵng"][i % 3],
                    priority="critical" if i % 3 == 0 else "high" if i % 3 == 1 else "normal",
                    status=status, owner=None if status == "new" else f"{tenant}-operator",
                    created_at=created.isoformat(), due_at=deadline.isoformat(),
                    notice={"sender": "dispatch@" + ["northline", "haidang", "pacific"][i % 3] + ".example",
                            "subject": "Cập nhật lịch giao · " + shipment_id, "received_at": created.isoformat(), "body": notice},
                    order_items=orders, events=events, ticket_ids=[], synthetic=True)
                db.execute("INSERT INTO records VALUES ('cases',?,?,?)", (case["id"], tenant, encode(case)))
        db.execute("INSERT INTO metadata VALUES ('case_scenario_v1',?)", (now.isoformat(),))


class CaseChange(Model):
    expected_revision: int = Field(ge=1)
    action: Literal["claim", "note", "transition", "attach_ticket"]
    value: str = Field(default="", max_length=128)
    note: str = Field(default="", max_length=4000)


class DraftInput(Model):
    expected_version: int = Field(ge=0)
    analysis_id: str = Field(min_length=1, max_length=64)
    recipient_label: str = Field(min_length=1, max_length=160)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=8000)


class Draft(Model):
    version: int
    analysis_id: str
    recipient_label: str
    subject: str
    body: str
    updated_by: str
    updated_at: str
    status: Literal["local_draft"] = "local_draft"


def save_draft(store, token, case_id, payload, clock):
    with store.connect(write=True) as db:
        actor = authenticate(db, token)
        case = record(db, "cases", case_id, actor["tenant_id"])
        if actor["role"] != "operator":
            raise Problem(403, "Operator role required")
        saved = owned_analysis(db, actor, payload.analysis_id)
        if saved["shipment_id"] != case["shipment_id"]:
            raise Problem(404, "Matching analysis not found")
        if json.loads(saved["body"])["status"] != "ready_for_review":
            raise Problem(409, "Analysis is not ready for a draft")
        check_snapshot_access(db, actor, saved)
        if fingerprint(evidence(db, actor, case["shipment_id"], clock())) != fingerprint(json.loads(saved["evidence"])):
            raise Problem(409, "Evidence changed; analyse again before saving")
        version = db.execute("SELECT COALESCE(MAX(version),0) FROM case_drafts WHERE tenant=? AND case_id=?",
                             (actor["tenant_id"], case_id)).fetchone()[0]
        if version != payload.expected_version:
            raise Problem(409, "Draft version changed; reload before editing")
        draft = payload.model_dump(exclude={"expected_version"}) | dict(version=version+1,
            updated_by=actor["id"], updated_at=clock().isoformat(), status="local_draft")
        db.execute("INSERT INTO case_drafts VALUES (?,?,?,?,?)",
                   (actor["tenant_id"], case_id, version+1, payload.analysis_id, encode(draft)))
        case["revision"] += 1
        case["events"].append({"at": draft["updated_at"], "actor": actor["id"], "action": "draft_saved",
                               "note": f"Đã lưu bản nháp v{version+1}; chưa gửi ra ngoài."})
        db.execute("UPDATE records SET body=? WHERE kind='cases' AND id=? AND tenant=?",
                   (encode(case), case_id, actor["tenant_id"]))
        return draft


def cases_router(store, clock):
    router = APIRouter(prefix="/api/cases")
    with store.connect(write=True) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS case_drafts (
            tenant TEXT NOT NULL, case_id TEXT NOT NULL, version INTEGER NOT NULL,
            analysis_id TEXT NOT NULL REFERENCES analyses(id), body TEXT NOT NULL,
            PRIMARY KEY(tenant,case_id,version))""")

    @router.put("/{case_id}/draft", response_model=Draft)
    def update_draft(case_id: str, payload: DraftInput, request: Request):
        return save_draft(store, request.cookies.get("opsdesk_session"), case_id, payload, clock)

    @router.get("/{case_id}/drafts", response_model=list[Draft])
    def drafts(case_id: str, request: Request, limit: int = Query(20, ge=1, le=100),
               before: int | None = Query(None, ge=1)):
        with store.connect() as db:
            actor = authenticate(db, request.cookies.get("opsdesk_session"))
            record(db, "cases", case_id, actor["tenant_id"])
            rows = db.execute("""SELECT d.body,a.evidence FROM case_drafts d
                JOIN analyses a ON a.id=d.analysis_id WHERE d.tenant=? AND d.case_id=?
                AND (? IS NULL OR d.version<?) ORDER BY d.version DESC LIMIT ?""",
                (actor["tenant_id"], case_id, before, before, limit)).fetchall()
            result = []
            for row in rows:
                try:
                    check_snapshot_access(db, actor, row)
                except Problem as exc:
                    if exc.status == 404:
                        continue
                    raise
                result.append(json.loads(row["body"]))
            return result

    @router.get("")
    def queue(request: Request, status: Literal["all", "active", "new", "investigating", "waiting_carrier", "escalated", "resolved"] = "active",
              q: str = Query(default="", max_length=160), mine: bool = False):
        with store.connect() as db:
            actor = authenticate(db, request.cookies.get("opsdesk_session"))
            cases = scoped_rows(db, "cases", actor["tenant_id"])
            now = clock().isoformat()
            active = [c for c in cases if c["status"] != "resolved"]
            metrics = {"active": len(active), "unassigned": sum(c["owner"] is None for c in active),
                       "overdue": sum(instant(c["due_at"]) < instant(now) for c in active),
                       "resolved": len(cases) - len(active)}
            filtered = [c for c in cases if (status == "all" or (status == "active" and c["status"] != "resolved") or c["status"] == status)
                        and (not mine or c["owner"] == actor["id"])
                        and q.casefold() in (c["id"] + c["shipment_id"] + c["customer"] + c["carrier"]).casefold()]
            filtered.sort(key=lambda c: (c["status"] == "resolved", instant(c["due_at"]) >= instant(now),
                {"critical":0, "high":1, "normal":2}[c["priority"]], instant(c["due_at"]), c["id"]))
            return {"items": [{k: v for k, v in c.items() if k not in {"events", "notice", "order_items"}} for c in filtered],
                    "metrics": metrics, "as_of": now, "synthetic": True}

    @router.get("/{case_id}")
    def detail(case_id: str, request: Request):
        with store.connect() as db:
            actor = authenticate(db, request.cookies.get("opsdesk_session"))
            case = record(db, "cases", case_id, actor["tenant_id"])
            return case | {"shipment": record(db, "shipments", case["shipment_id"], actor["tenant_id"])}

    @router.post("/{case_id}/events")
    def change(case_id: str, payload: CaseChange, request: Request):
        with store.connect(write=True) as db:
            actor = authenticate(db, request.cookies.get("opsdesk_session"))
            case = record(db, "cases", case_id, actor["tenant_id"])
            if actor["role"] != "operator":
                raise Problem(403, "Operator role required")
            if payload.expected_revision != case["revision"]:
                raise Problem(409, "Case revision changed")
            if payload.action == "claim":
                if case["owner"] not in (None, actor["id"]):
                    raise Problem(409, "Case already assigned")
                case["owner"] = actor["id"]
                if case["status"] == "new":
                    case["status"] = "investigating"
            elif payload.action == "note":
                if not payload.note:
                    raise Problem(422, "Note required")
            elif payload.action == "transition":
                if case["owner"] != actor["id"]:
                    raise Problem(409, "Claim this case first")
                if payload.value not in STATES[case["status"]] or not payload.note:
                    raise Problem(422, "Valid next state and reason required")
                if payload.value == "resolved" and not case["ticket_ids"]:
                    raise Problem(409, "An approved ticket is required before resolution")
                case["status"] = payload.value
            else:
                row = db.execute("""SELECT t.id,a.shipment_id FROM tickets t JOIN analyses a ON a.id=t.analysis_id
                    WHERE t.id=? AND t.tenant=? AND a.actor_id=?""",
                    (payload.value, actor["tenant_id"], actor["id"])).fetchone()
                if not row or row["shipment_id"] != case["shipment_id"]:
                    raise Problem(404, "Matching approved ticket not found")
                if row["id"] in case["ticket_ids"]:
                    return case
                case["ticket_ids"].append(row["id"])
            case["revision"] += 1
            case["events"].append({"at": clock().isoformat(), "actor": actor["id"],
                                   "action": payload.action, "value": payload.value, "note": payload.note})
            db.execute("UPDATE records SET body=? WHERE kind='cases' AND id=? AND tenant=?",
                       (encode(case), case_id, actor["tenant_id"]))
            return case

    return router
