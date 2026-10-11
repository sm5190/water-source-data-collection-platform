"""Procedural-memory background worker: distillation jobs, pending embeddings, lifecycle.

Usage: python scripts/run_procedure_worker.py [--once] [--interval SECONDS]
"""

import argparse
import time
from water_source_research.core.config import get_settings
from water_source_research.db.session import get_engine
from water_source_research.procedures.services import build_procedure_services

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="process one batch and exit")
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--batch", type=int, default=20)
    args = parser.parse_args()

    services = build_procedure_services(get_settings(), get_engine())
    while True:
        results = services.worker.run_once(limit=args.batch)
        embedded = services.worker.embed_pending()
        transitions = services.worker.sweep_lifecycle()
        print(f"distilled={len(results)} embedded={embedded} transitions={len(transitions)}",
              flush=True)
        if args.once:
            return
        time.sleep(args.interval)

if __name__ == "__main__":
    main()
