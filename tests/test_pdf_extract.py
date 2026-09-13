import base64
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from app import create_app
from backend import Problem
from pdf_extract import extract_pdf


def sample_pdf(text='Verify carrier ETA before notifying the customer.', *, blank=False, encrypted=False, pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=595, height=842)
        if not blank:
            font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(('BT /F1 12 Tf 50 750 Td ('+text+') Tj ET').encode('ascii'))
            page[NameObject('/Contents')] = writer._add_object(stream)
    if encrypted:
        writer.encrypt('test-only-password')
    output = io.BytesIO();writer.write(output)
    return base64.b64encode(output.getvalue()).decode('ascii')


class PdfExtractionTest(unittest.TestCase):
    def test_real_worker_extracts_text_and_tracks_pages(self):
        result = extract_pdf(sample_pdf())
        self.assertIn('Verify carrier ETA',result['text'])
        self.assertIn('[Trang 1]',result['text'])
        self.assertEqual(result['page_count'],1)
        self.assertEqual(len(result['sha256']),64)
        self.assertEqual(result['empty_pages'],[])

    def test_rejects_blank_encrypted_malformed_and_oversized(self):
        for encoded in [sample_pdf(blank=True),sample_pdf(encrypted=True),sample_pdf(pages=31),
                        'not base64!',base64.b64encode(b'not pdf').decode(),sample_pdf(text='a'*11000)]:
            with self.subTest(encoded_length=len(encoded)),self.assertRaises(Problem) as error:
                extract_pdf(encoded)
            self.assertEqual(error.exception.status,422)

    def test_timeout_and_busy_are_explicit(self):
        with patch('pdf_extract.subprocess.run',side_effect=subprocess.TimeoutExpired('worker',8)):
            with self.assertRaises(Problem) as error:
                extract_pdf(sample_pdf())
            self.assertEqual(error.exception.detail,'PDF extraction timed out')
        with patch('pdf_extract._workers.acquire',return_value=False):
            with self.assertRaises(Problem) as error:
                extract_pdf(sample_pdf())
            self.assertEqual(error.exception.status,429)

    def test_api_preview_is_authorized_and_does_not_save(self):
        with tempfile.TemporaryDirectory() as tmp,TestClient(create_app(Path(tmp)/'db.sqlite3',demo_login=True)) as client:
            payload={'content_base64':sample_pdf()}
            self.assertEqual(client.post('/api/documents/extract-pdf',json=payload).status_code,401)
            client.post('/api/demo/login',json={'actor_id':'A-viewer'})
            self.assertEqual(client.post('/api/documents/extract-pdf',json=payload).status_code,403)
            client.post('/api/demo/login',json={'actor_id':'A-operator'})
            before=client.get('/api/documents').json()
            response=client.post('/api/documents/extract-pdf',json=payload)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(client.get('/api/documents').json(),before)
            doc=client.post('/api/documents',json={'title':'Imported PDF procedure','text':response.json()['text'],
                'policy_scope':'no_policy','action':'contact_carrier','allowed_roles':['operator'],
                'effective_from':'2026-01-01T00:00:00Z','expected_version':0})
            self.assertEqual(doc.status_code,201)
            found=client.get('/api/documents/search',params={'q':'carrier ETA','shipment_id':'SHP-1045'}).json()
            self.assertEqual(found[0]['document_id'],doc.json()['id'])


if __name__ == '__main__':
    unittest.main()
