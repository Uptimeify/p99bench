#!/usr/bin/env python3
"""Generate every published artifact from results/.

Generated artifacts are never hand-edited -- CI runs `--check` and fails the
PR if a committed file differs from what results/ would produce.

The actual rendering lives in tools/writers.py (RESULTS.md, the per-provider
pages, and the machine-readable export); this module is just the CLI that
loads results/, builds every artifact, and either writes them or checks them
against the working tree. The variance doctrine (median AND worst, never a
mean; time variance vs host variance kept separate) lives in
tools/aggregate.py -- see that module's docstring for the reasoning.

Usage:
    python3 tools/render.py            # write every artifact
    python3 tools/render.py --check    # verify artifacts are up to date (CI)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import aggregate
import writers

ROOT = Path(__file__).resolve().parent.parent


def build_all(runs: list[dict]) -> dict[Path, str]:
    """Every artifact this tool publishes, keyed by the path it belongs at.

    Keeping the return type a dict from the start means adding artifacts is
    inert: the CLI below already knows how to write or check N artifacts, so
    each new one only touches this function.
    """
    rows = writers.index_rows(runs)
    artifacts = {
        ROOT / "RESULTS.md": writers.write_index_md(rows),
        writers.DATA_DIR / "index.json": writers.write_index_json(rows),
        writers.DATA_DIR / "index.csv": writers.write_index_csv(rows),
    }
    artifacts.update(writers.provider_pages(runs))
    return artifacts


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate every published artifact from results/."
    )
    ap.add_argument(
        "--check", action="store_true",
        help="verify the committed artifacts match what results/ would generate; "
             "exit 1 and name each stale file. This is what CI runs.",
    )
    args = ap.parse_args()

    runs = aggregate.load_all()
    artifacts = build_all(runs)   # {Path: str}

    if args.check:
        stale = [p for p, body in artifacts.items()
                 if not p.exists() or p.read_text() != body]
        for p in stale:
            print(f"stale: {p.relative_to(ROOT)}", file=sys.stderr)
        if stale:
            print("run: python3 tools/render.py", file=sys.stderr)
            return 1
        return 0

    for p, body in artifacts.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        print(f"wrote {p.relative_to(ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
