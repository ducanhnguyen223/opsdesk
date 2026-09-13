"""Measure whether semantic cache reuse is safe enough to enable."""
import argparse
import hashlib
import json
from pathlib import Path

from evaluate_retrieval import MODEL, MODEL_FILE, MODEL_REVISION, load_model


def scopes(fixture, pair):
    base = fixture["default_scope"]
    return base | pair.get("left_scope", {}), base | pair.get("right_scope", {})


def select_threshold(rows):
    development = [row for row in rows if row["split"] == "development" and row["scope_match"]]
    candidates = sorted({row["similarity"] for row in development} | {1.000001})
    safe = []
    for threshold in candidates:
        false_hits = sum(not row["same_answer"] and row["similarity"] >= threshold
                         for row in development)
        true_hits = sum(row["same_answer"] and row["similarity"] >= threshold
                        for row in development)
        if false_hits == 0:
            safe.append((true_hits, -threshold, threshold))
    return max(safe)[2]


def split_metrics(rows, split, threshold):
    selected = [row for row in rows if row["split"] == split]
    positives = sum(row["same_answer"] and row["scope_match"] for row in selected)
    negatives = sum(not row["same_answer"] and row["scope_match"] for row in selected)
    true_hits = sum(row["same_answer"] and row["scope_match"]
                    and row["similarity"] >= threshold for row in selected)
    false_hits = sum(not row["same_answer"] and row["scope_match"]
                     and row["similarity"] >= threshold for row in selected)
    return {"pairs": len(selected), "scope_blocked": sum(not row["scope_match"] for row in selected),
            "true_hits": true_hits, "false_hits": false_hits,
            "true_hit_rate": true_hits / positives if positives else 0,
            "false_hit_rate": false_hits / negatives if negatives else 0}


def evaluate(fixture_path, output_path, cache_dir, offline=False):
    raw = fixture_path.read_bytes()
    fixture = json.loads(raw)
    model = load_model(cache_dir, offline)
    texts = list(dict.fromkeys(text for pair in fixture["pairs"]
                              for text in (pair["left"], pair["right"])))
    vectors = dict(zip(texts, model.embed([f"query: {text}" for text in texts])))
    rows = []
    for pair in fixture["pairs"]:
        left_scope, right_scope = scopes(fixture, pair)
        rows.append({**pair, "scope_match": left_scope == right_scope,
                     "similarity": round(float(vectors[pair["left"]] @ vectors[pair["right"]]), 8)})
    threshold = select_threshold(rows)
    metrics = {split: split_metrics(rows, split, threshold) for split in ("development", "holdout")}
    acceptance = fixture["acceptance"]
    enable = (metrics["holdout"]["false_hits"] <= acceptance["max_holdout_false_hits"]
              and metrics["holdout"]["true_hit_rate"] >= acceptance["min_holdout_true_hit_rate"])
    report = {"kind":"offline synthetic semantic-cache experiment",
              "dataset":fixture["snapshot"], "dataset_sha256":hashlib.sha256(raw).hexdigest(),
              "model":MODEL, "model_revision":MODEL_REVISION, "model_file":MODEL_FILE,
              "threshold_selected_on":"development", "threshold":threshold,
              "acceptance":acceptance, "metrics":metrics, "recommend_enable":enable,
              "results":rows}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"threshold":threshold, "metrics":metrics,
                      "recommend_enable":enable, "output":str(output_path)}, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=Path("fixtures/semantic_cache_evaluation.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/semantic_cache_evaluation.json"))
    parser.add_argument("--cache-dir", type=Path,
                        default=Path.home() / ".cache" / "opsdesk" / "fastembed")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    evaluate(args.fixture, args.output, args.cache_dir, args.offline)
