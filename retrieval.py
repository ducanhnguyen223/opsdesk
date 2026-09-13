"""Scoped lexical retrieval; no model, network or third-party dependencies."""
import hashlib
import math
import re
import unicodedata
from collections import Counter
from datetime import datetime


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def tokens(text):
    normalized = unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
    # Keep negations and digits: removing them changes business meaning.
    return re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)


def authorized_documents(documents, *, tenant_id, role, policy_scope, as_of):
    """Filter before tokenizing/ranking so foreign data cannot affect scores."""
    return sorted((d for d in documents
        if d["tenant_id"] == tenant_id and d["policy_scope"] == policy_scope
        and role in d["allowed_roles"] and instant(d["effective_from"]) <= as_of
        and (not d["effective_to"] or as_of < instant(d["effective_to"]))), key=lambda d: d["id"])


def chunks(document, size=800, overlap=120):
    if not 0 <= overlap < size:
        raise ValueError("Chunk size must exceed overlap")
    text = document["text"]
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n", start + size // 2, end),
                           text.rfind(" ", start + size // 2, end))
            if boundary > start:
                end = boundary
        quote = text[start:end]
        if quote.strip():
            yield {"document_id": document["id"], "version": document["version"],
                "chunk_id": f"{document['id']}:v{document['version']}:{digest}:{start}-{end}",
                "quote": quote, "start": start, "end": end, "kind": document["kind"]}
        if end == len(text):
            break
        start = max(start + 1, end - overlap)


def rank_chunks(query, scoped_documents):
    """BM25 baseline. Input must already be authorized; scores are not confidence."""
    candidates = [chunk for doc in scoped_documents for chunk in chunks(doc)]
    counts = [Counter(tokens(c["quote"])) for c in candidates]
    if not counts:
        return []
    average = sum(sum(c.values()) for c in counts) / len(counts) or 1
    frequencies = Counter(term for count in counts for term in count)
    query_terms = set(tokens(query))
    for chunk, count in zip(candidates, counts):
        length, score = sum(count.values()), 0.0
        for term in query_terms:
            tf = count[term]
            if tf:
                df = frequencies[term]
                idf = math.log(1 + (len(counts) - df + 0.5) / (df + 0.5))
                score += idf * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * length / average))
        chunk["score"] = score
    return sorted(candidates, key=lambda c: (-c["score"], c["document_id"], c["start"]))


def search_procedures(query, documents, *, tenant_id, role, policy_scope, as_of, limit=6):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 8000:
        raise ValueError("Query must contain 1–8000 characters")
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Limit must be 1–20")
    scoped = authorized_documents(documents, tenant_id=tenant_id, role=role,
                                  policy_scope=policy_scope, as_of=as_of)
    return [c for c in rank_chunks(query, scoped) if c["kind"] == "procedure" and c["score"] > 0][:limit]


def policy_citations(query, scoped_documents):
    """One ranked excerpt per applicable procedure, including conflicting ones.

    Metadata identifies applicable policy independently of relevance. Do not hide a
    conflicting policy just because its vocabulary ranks below another document.
    """
    best = {}
    for chunk in rank_chunks(query, scoped_documents):
        if chunk["kind"] == "procedure":
            best.setdefault(chunk["document_id"], chunk)
    return [{key: best[doc_id][key] for key in ("document_id", "version", "chunk_id", "quote")}
            for doc_id in sorted(best)]


def validate_citations(citations, scoped_documents):
    allowed = {c["chunk_id"]: c for d in scoped_documents if d["kind"] == "procedure" for c in chunks(d)}
    for citation in citations:
        source = allowed.get(citation["chunk_id"])
        if source is None or any(citation[key] != source[key]
                                 for key in ("document_id", "version", "quote")):
            raise ValueError("Citation is not an exact authorized source excerpt")
