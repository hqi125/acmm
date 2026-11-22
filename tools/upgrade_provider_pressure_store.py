from __future__ import annotations

import argparse
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from acmm.runtime.provider_pressure_migration import (
    provider_pressure_migration_error,
    upgrade_provider_pressure_root,
)
from acmm.runtime.provider_pressure_segment_store import (
    DEFAULT_SEGMENT_POLICY_PRESET,
    SEGMENT_POLICY_PRESETS,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Upgrade immutable legacy Provider Pressure trajectory snapshots into the "
            "bounded segmented store_v2 format. Legacy files are never modified or deleted."
        )
    )
    parser.add_argument("provider_pressure_root", type=Path)
    parser.add_argument(
        "--segment-size",
        choices=tuple(SEGMENT_POLICY_PRESETS),
        default=None,
        help=(
            "Segment-size preset for a new store_v2. "
            f"New stores default to '{DEFAULT_SEGMENT_POLICY_PRESET}'; an existing store always resumes "
            "its persisted policy unless this option explicitly requests the same preset."
        ),
    )
    args = parser.parse_args(argv)
    try:
        result = upgrade_provider_pressure_root(
            args.provider_pressure_root,
            progress=lambda message: print(message, flush=True),
            segment_size=args.segment_size,
        )
    except provider_pressure_migration_error as exc:
        print(f"Provider Pressure upgrade failed: {exc}", file=sys.stderr, flush=True)
        return 2
    print("Provider Pressure upgrade complete:", flush=True)
    for lineage in result.lineages:
        print(f"  legacy:      {lineage.legacy_root}", flush=True)
        print(f"  store_v2:    {lineage.store_root}", flush=True)
        print(f"  snapshots:   {lineage.snapshot_count}", flush=True)
        print(f"  events:      {lineage.event_count}", flush=True)
        print(f"  checkpoints: {lineage.checkpoint_count}", flush=True)
        print(f"  report:      {lineage.report_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
