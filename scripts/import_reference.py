"""Import manually researched reference records (the standardized workbook or a CSV) as a
versioned evaluation reference dataset. The file itself is not modified.

    uv run python scripts/import_reference.py "resources/Sarina Water Source.xlsx" --cities Toronto
    uv run python scripts/import_reference.py data.csv --cities "Madrid,Houston" --dry-run

Prints the dataset version, the mapping and every import issue (fraction percentages,
placeholders, duplicates, unmapped columns, ...).
"""

import argparse
import sys
from pathlib import Path
from water_source_research.db.session import get_engine
from water_source_research.evaluation.reference import import_reference
from water_source_research.evaluation.store import EvaluationStore

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--cities", default=None,
                        help="comma-separated city names (default: every city in the file)")
    parser.add_argument("--sheet", default="Standardization")
    parser.add_argument("--name", default=None, help="dataset name (default: file name)")
    parser.add_argument("--dry-run", action="store_true", help="report only, store nothing")
    args = parser.parse_args()
    cities = [c.strip() for c in args.cities.split(",")] if args.cities else None
    ds = import_reference(args.path, cities, args.sheet, args.name)
    print(f"dataset {ds.version}  ({ds.filename}, sheet {ds.sheet}, sha256 "
          f"{ds.file_sha256[:12]}, policy {ds.policy_version})")
    for city in ds.cities:
        print(f"  {city.city}, {city.country}: {len(city.records)} record(s) "
              + ", ".join(f"{r.water_source} [{r.utility}]" for r in city.records))
    print(f"\n{len(ds.issues)} issue(s):")
    for i in ds.issues:
        where = f"row {i.row} " if i.row else ""
        print(f"  {i.code:<26} {where}{i.field_key or i.column or ''}: {i.detail}")
    if args.dry_run:
        return 0
    created = EvaluationStore(get_engine()).save_dataset(ds)
    print(f"\n{'stored new' if created else 'updated existing'} dataset {ds.version}")
    return 0 if ds.cities else 1

if __name__ == "__main__":
    sys.exit(main())
