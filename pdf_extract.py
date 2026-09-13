"""Bounded, local text extraction. PDF bytes never become a filesystem path."""
import base64
import binascii
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from threading import BoundedSemaphore

from backend import Problem

MAX_BYTES = 3 * 1024 * 1024
MAX_BASE64 = 4 * ((MAX_BYTES + 2) // 3)
_workers = BoundedSemaphore(2)


def extract_pdf(encoded):
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise Problem(422, "Invalid PDF encoding") from None
    if len(data) > MAX_BYTES:
        raise Problem(413, "PDF exceeds 3 MiB")
    if not data.startswith(b"%PDF-"):
        raise Problem(422, "Not a PDF file")
    if not _workers.acquire(blocking=False):
        raise Problem(429, "PDF extraction busy; retry later")
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker"],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8,
            env={}, check=False)
        if result.returncode:
            raise Problem(422, "PDF could not be read within resource limits")
        output = json.loads(result.stdout)
        if "error" in output:
            raise Problem(422, output["error"])
        return output | {"sha256": hashlib.sha256(data).hexdigest()}
    except subprocess.TimeoutExpired:
        raise Problem(422, "PDF extraction timed out") from None
    except (ValueError, OSError):
        raise Problem(503, "PDF extraction unavailable") from None
    finally:
        _workers.release()


def worker():
    # Local POSIX demo: macOS rejects RLIMIT_DATA; decoder limits apply on both platforms.
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    from pypdf import PdfReader, overwrite_configuration
    overwrite_configuration(maximum_declared_stream_length=4*1024*1024,
        array_based_stream_maximum_output_length=4*1024*1024,
        zlib_maximum_output_length=4*1024*1024, lzw_maximum_output_length=4*1024*1024,
        run_length_maximum_output_length=4*1024*1024, image_maximum_buffer_size=4*1024*1024,
        page_tree_maximum_entries=100, page_tree_maximum_depth=10,
        xform_maximum_invocations_per_extraction=100, jbig2dec_binary=None)
    data = sys.stdin.buffer.read(MAX_BYTES + 1)
    try:
        if len(data) > MAX_BYTES:
            raise ValueError("PDF exceeds 3 MiB")
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported; supply an unencrypted authorized copy")
        if not 1 <= len(reader.pages) <= 30:
            raise ValueError("PDF must have between 1 and 30 pages")
        parts, empty_pages = [], []
        for number, page in enumerate(reader.pages, 1):
            content = page.get_contents()
            if content and len(content.get_data()) > 4 * 1024 * 1024:
                raise ValueError("PDF page content exceeds extraction limits")
            text = (page.extract_text() or "").replace("\x00", "").strip()
            if text:
                parts.append(f"[Trang {number}]\n{text}")
            else:
                empty_pages.append(number)
            if sum(len(p) + 2 for p in parts) > 10000:
                raise ValueError("Extracted text exceeds 10,000 characters; split the document")
        if not parts:
            raise ValueError("No text layer found; OCR is required for scanned documents")
        return {"text": "\n\n".join(parts), "page_count": len(reader.pages),
                "empty_pages": empty_pages,
                "warnings": ["Review extracted text, reading order and tables before saving."] +
                    (["Some pages have no readable text; review them separately or use OCR."] if empty_pages else [])}
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception:
        return {"error": "Malformed or unsupported PDF"}


if __name__ == "__main__":
    print(json.dumps(worker(), ensure_ascii=False))
