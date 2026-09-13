"""Compare scoped BM25, dense and hybrid retrieval on frozen synthetic data."""
import argparse
import hashlib
import json
import platform
import statistics
import time
from pathlib import Path

from retrieval import authorized_documents, instant, rank_chunks

MODEL = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
MODEL_FILE = "onnx/model_O4.onnx"


def unique_ids(chunks):
    return list(dict.fromkeys(c["document_id"] for c in chunks if c["kind"] == "procedure"))


def rrf(*rankings, k=60):
    scores = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, 1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1 / (k + rank)
    return sorted(scores, key=lambda doc_id: (-scores[doc_id], doc_id))


def metrics(rows, name):
    ranks = []
    recalls = []
    for row in rows:
        ranking, relevant = row[name], row["relevant"]
        positions = [ranking.index(doc_id) + 1 for doc_id in relevant if doc_id in ranking]
        ranks.append(min(positions, default=0))
        recalls.append(sum(doc_id in ranking[:3] for doc_id in relevant) / len(relevant))
    return {
        "top_1_accuracy": sum(rank == 1 for rank in ranks) / len(rows),
        "recall_at_3": statistics.mean(recalls),
        "mrr": statistics.mean(1 / rank if rank else 0 for rank in ranks),
    }


def load_model(cache_dir, offline):
    try:
        from fastembed import TextEmbedding
        from fastembed.common.model_description import ModelSource, PoolingType
    except ImportError as error:
        raise SystemExit("Install requirements-embeddings.txt in a separate environment") from error
    TextEmbedding.add_custom_model(model=MODEL, pooling=PoolingType.MEAN,
        normalization=True, sources=ModelSource(hf=MODEL), dim=384,
        model_file=MODEL_FILE, license="MIT", size_in_gb=0.24)
    return TextEmbedding(model_name=MODEL, cache_dir=str(cache_dir),
        local_files_only=offline, revision=MODEL_REVISION)


def evaluate(fixture_path, output_path, cache_dir, offline=False):
    raw = fixture_path.read_bytes()
    fixture = json.loads(raw)
    as_of = instant(fixture["as_of"])
    scoped = authorized_documents(fixture["documents"], tenant_id="A", role="operator",
                                  policy_scope="standard", as_of=as_of)
    candidates = [doc for doc in scoped if doc["kind"] == "procedure"]
    allowed = {doc["id"] for doc in candidates}

    started = time.perf_counter()
    model = load_model(cache_dir, offline)
    passages = [f"passage: {doc['text']}" for doc in candidates]
    passage_vectors = list(model.embed(passages))
    index_seconds = time.perf_counter() - started

    rows = []
    bm25_ms, dense_ms = [], []
    for item in fixture["queries"]:
        before = time.perf_counter()
        bm25 = unique_ids(rank_chunks(item["query"], scoped))
        bm25_ms.append((time.perf_counter() - before) * 1000)

        before = time.perf_counter()
        query_vector = next(model.embed([f"query: {item['query']}"]))
        dense = [doc_id for _, doc_id in sorted(
            ((float(query_vector @ vector), doc["id"])
             for doc, vector in zip(candidates, passage_vectors)), reverse=True)]
        dense_ms.append((time.perf_counter() - before) * 1000)
        hybrid = rrf(bm25, dense)
        if not set(bm25 + dense + hybrid) <= allowed:
            raise RuntimeError("Unauthorized document reached a ranking")
        rows.append({"id": item["id"], "query": item["query"], "relevant": item["relevant"],
                     "bm25": bm25[:5], "dense": dense[:5], "hybrid": hybrid[:5]})

    report = {
        "kind": "offline synthetic retrieval benchmark",
        "dataset": fixture["snapshot"],
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "model": MODEL,
        "model_revision": MODEL_REVISION,
        "model_file": MODEL_FILE,
        "runtime": {"python": platform.python_version(), "platform": platform.platform(),
                    "index_seconds": round(index_seconds, 3),
                    "bm25_query_p50_ms": round(statistics.median(bm25_ms), 3),
                    "dense_query_p50_ms": round(statistics.median(dense_ms), 3)},
        "query_count": len(rows),
        "authorized_document_count": len(allowed),
        "forbidden_rankings": 0,
        "metrics": {name: metrics(rows, name) for name in ("bm25", "dense", "hybrid")},
        "results": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"metrics": report["metrics"], "output": str(output_path)}, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=Path("fixtures/retrieval_evaluation.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/retrieval_evaluation.json"))
    parser.add_argument("--cache-dir", type=Path,
                        default=Path.home() / ".cache" / "opsdesk" / "fastembed")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    evaluate(args.fixture, args.output, args.cache_dir, args.offline)
