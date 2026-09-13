"""Real API scenario: queue -> claim -> analyse -> approve -> resolve, plus boundaries."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from fastapi.testclient import TestClient
from app import create_app


class CasesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "cases.sqlite3"
        self.clock = lambda: datetime(2026, 9, 12, 8, tzinfo=timezone.utc)
        self.app = create_app(self.path, demo_login=True, clock=self.clock)
        self.client = TestClient(self.app)
        self.client.post('/api/demo/login', json={'actor_id': 'A-operator'})

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def change(self, action, value='', note='', revision=None):
        if revision is None:
            revision = self.client.get('/api/cases/EX-9000').json()['revision']
        return self.client.post('/api/cases/EX-9000/events', json={
            'action': action, 'value': value, 'note': note, 'expected_revision': revision})

    def test_complete_workflow_and_restart(self):
        queue = self.client.get('/api/cases').json()
        self.assertEqual(queue['metrics'], {'active':15, 'overdue':3, 'unassigned':6, 'resolved':3})
        self.assertTrue(all(c['tenant_id']=='A' for c in queue['items']))
        self.assertEqual(self.change('claim').json()['status'], 'investigating')
        self.assertEqual(self.change('transition', 'resolved', 'Done').status_code,409)
        case = self.client.get('/api/cases/EX-9000').json()
        analysis = self.client.post('/api/analyses', json={'message':case['notice']['body']}).json()
        self.assertEqual(analysis['status'], 'ready_for_review')
        ticket = self.client.post('/api/analyses/'+analysis['analysis_id']+'/approve',
            json={'proposal_revision':1,'confirmed':True}, headers={'Idempotency-Key':'case-test'}).json()
        self.assertEqual(self.change('attach_ticket', ticket['ticket_id']).status_code,200)
        self.assertEqual(self.change('transition','waiting_carrier','Đã ghi nhận yêu cầu xác nhận ETA.').status_code,200)
        result = self.change('transition','resolved','Đã nhận xác nhận lịch giao mới trong kịch bản demo.').json()
        self.assertEqual(result['status'],'resolved')
        with TestClient(create_app(self.path,demo_login=True,clock=self.clock)) as restarted:
            restarted.post('/api/demo/login',json={'actor_id':'A-operator'})
            self.assertEqual(restarted.get('/api/cases/EX-9000').json()['events'],result['events'])
            self.assertEqual(restarted.get('/api/cases?status=all').json()['metrics']['resolved'],4)

    def test_tenant_role_validation_and_stale_writes(self):
        self.assertEqual(self.client.get('/api/cases/EX-9100').status_code,404)
        self.assertEqual(self.change('claim', revision=1).status_code,200)
        self.assertEqual(self.change('note',note='stale',revision=1).status_code,409)
        self.assertEqual(self.change('transition','new','Invalid').status_code,422)
        self.assertEqual(self.change('note',note='  ').status_code,422)
        self.assertEqual(self.change('attach_ticket','missing').status_code,404)
        self.client.post('/api/demo/login',json={'actor_id':'A-viewer'})
        self.assertEqual(self.change('note',note='Not permitted').status_code,403)
        self.client.post('/api/logout',json={})
        self.assertEqual(self.client.get('/api/cases').status_code,401)

    def test_filter_and_immutable_seed(self):
        initial = self.client.get('/api/cases/EX-9000').json()
        self.change('note',note='Ghi chú có thật của người vận hành.')
        create_app(self.path,demo_login=True,clock=lambda:datetime(2026,10,1,tzinfo=timezone.utc))
        latest = self.client.get('/api/cases/EX-9000').json()
        self.assertEqual(latest['due_at'],initial['due_at'])
        self.assertEqual(latest['revision'],2)
        self.assertEqual(len(self.client.get('/api/cases?q=SHP-9000').json()['items']),1)
        self.assertEqual(len(self.client.get('/api/cases?status=resolved').json()['items']),3)

    def draft_payload(self, shipment='SHP-9000'):
        analysis = self.client.post('/api/analyses',json={'message':shipment+' chậm 48 giờ.'}).json()
        return {'expected_version':0,'analysis_id':analysis['analysis_id'],
                'recipient_label':'An Việt Manufacturing','subject':'Cập nhật lịch giao',
                'body':'Kính gửi anh/chị, chúng tôi đang kiểm tra lịch giao mới và sẽ cập nhật sau khi xác minh.'}

    def test_drafts_versioned_persistent_and_audited(self):
        payload = self.draft_payload()
        draft = self.client.put('/api/cases/EX-9000/draft',json=payload)
        self.assertEqual(draft.status_code,200)
        self.assertEqual(draft.json()['version'],1)
        self.assertEqual(draft.json()['status'],'local_draft')
        self.assertEqual(self.client.put('/api/cases/EX-9000/draft',json=payload).status_code,409)
        payload.update(expected_version=1,body='Nội dung sửa tay: chưa xác nhận ETA, không cam kết giao ngay.')
        self.assertEqual(self.client.put('/api/cases/EX-9000/draft',json=payload).json()['version'],2)
        drafts = self.client.get('/api/cases/EX-9000/drafts').json()
        self.assertEqual([d['version'] for d in drafts],[2,1])
        self.assertNotEqual(drafts[0]['body'],drafts[1]['body'])
        self.assertEqual(self.client.get('/api/cases/EX-9000/drafts?before=2').json()[0]['version'],1)
        case = self.client.get('/api/cases/EX-9000').json()
        self.assertEqual(case['events'][-1]['action'],'draft_saved')
        self.assertEqual(case['ticket_ids'],[])
        with TestClient(create_app(self.path,demo_login=True,clock=self.clock)) as restarted:
            restarted.post('/api/demo/login',json={'actor_id':'A-operator'})
            self.assertEqual(restarted.get('/api/cases/EX-9000/drafts').json(),drafts)

    def test_drafts_boundaries_and_stale_evidence(self):
        from backend import encode, record
        payload = self.draft_payload()
        self.assertEqual(self.client.put('/api/cases/EX-9100/draft',json=payload).status_code,404)
        self.assertEqual(self.client.put('/api/cases/EX-9001/draft',json=payload).status_code,404)
        self.assertEqual(self.client.put('/api/cases/EX-9000/draft',json=payload|{'body':' '}).status_code,422)
        self.assertEqual(self.client.put('/api/cases/EX-9000/draft',json=payload).status_code,200)
        self.client.post('/api/demo/login',json={'actor_id':'A-viewer'})
        self.assertEqual(len(self.client.get('/api/cases/EX-9000/drafts').json()),1)
        self.assertEqual(self.client.put('/api/cases/EX-9000/draft',json=payload|{'expected_version':1}).status_code,403)
        with self.app.state.store.connect(write=True) as db:
            doc = record(db,'documents','A-STANDARD-V2','A')
            doc['allowed_roles']=['operator']
            db.execute("UPDATE records SET body=? WHERE kind='documents' AND id=?",(encode(doc),doc['id']))
        self.assertEqual(self.client.get('/api/cases/EX-9000/drafts').json(),[])
        self.client.post('/api/demo/login',json={'actor_id':'A-operator'})
        self.assertEqual(self.client.put('/api/cases/EX-9000/draft',json=payload|{'expected_version':1}).status_code,409)
        self.client.post('/api/demo/login',json={'actor_id':'B-operator'})
        self.assertEqual(self.client.get('/api/cases/EX-9000/drafts').status_code,404)


if __name__ == '__main__':
    unittest.main()
