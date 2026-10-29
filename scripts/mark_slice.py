#!/usr/bin/env python3
"""Mark one workspace slice as reviewed and update progress.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import now_text, read_json, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="标记工作台切片已完成")
    parser.add_argument("--work", required=True)
    parser.add_argument("--group", required=True)
    parser.add_argument("--slice", required=True, help="part-001")
    args = parser.parse_args()

    work = Path(args.work).expanduser().resolve()
    folder = work / str(args.group)
    progress_path = folder / "progress.json"
    progress = read_json(progress_path, {}) or {}
    if not progress:
        print(json.dumps({"ok": False, "error": f"读不到 {progress_path}"}, ensure_ascii=False))
        return 1
    slice_id = str(args.slice)
    if not slice_id.startswith("part-"):
        slice_id = f"part-{int(slice_id):03d}"
    total = int(progress.get("total_slices") or 0)
    done = [str(item) for item in progress.get("done_slices") or []]
    if slice_id not in done:
        done.append(slice_id)
    done = sorted(set(done))
    progress.update(
        {
            "done_slices": done,
            "status": "done" if total and len(done) >= total else "reviewing",
            "updated_at": now_text(),
        }
    )
    write_json(progress_path, progress)
    print(
        json.dumps(
            {
                "ok": True,
                "group": args.group,
                "slice": slice_id,
                "done": len(done),
                "total": total,
                "status": progress["status"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
