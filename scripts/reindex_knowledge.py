#!/usr/bin/env python3
import argparse
import os
import sys


def _project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser(description="Reindex knowledge-priors into ChromaDB.")
    parser.add_argument("--query", help="Optional query to run after reindex.")
    parser.add_argument("--top-k", type=int, default=5, help="Top-k query results when --query is provided.")
    args = parser.parse_args()

    root = _project_root()
    src_dir = os.path.join(root, "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    try:
        import rag
    except ModuleNotFoundError as e:
        print(f"Missing dependency: {e}. Install requirements first.")
        return 1

    result = rag.init_knowledge()
    print(result)

    if args.query:
        print("---- QUERY RESULT ----")
        print(rag.query_knowledge(args.query, k=args.top_k))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
