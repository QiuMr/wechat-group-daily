#!/usr/bin/env python3
"""Open a generated daily report locally."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from common import data_home, date_dir, date_label, read_json, resolve_date, summary_path


def emit(payload: dict, code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


def latest_report(home: Path, *, workspace: bool = False) -> Path | None:
    root = home / "reports"
    if not root.exists():
        return None
    marker = ("work" / "index.json") if workspace else ("report" / "report.html")
    candidates = sorted(
        (path for path in root.iterdir() if (path / marker).exists()),
        key=lambda path: path.name,
        reverse=True,
    )
    return candidates[0] if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser(description="打开群日报")
    parser.add_argument("--date", default="yesterday", help="yesterday / today / 2026-10-28")
    parser.add_argument("--latest", action="store_true", help="打开最近一份报告")
    parser.add_argument("--targets", action="store_true", help="打开目标进度看板")
    parser.add_argument("--home", default="", help="数据目录")
    parser.add_argument("--no-open", action="store_true", help="只返回路径，不打开浏览器")
    args = parser.parse_args()

    home = Path(args.home).expanduser().resolve() if args.home else data_home()
    if args.latest:
        folder = latest_report(home)
        if folder is None:
            work_folder = latest_report(home, workspace=True)
            if work_folder is None:
                return emit({"state": "not_started", "message": "还没有任何群日报"}, 1)
            return emit(
                {
                    "state": "ready_for_review",
                    "date": work_folder.name,
                    "message": "有工作台，但报告还没生成",
                    "workdir": str((work_folder / "work").resolve()),
                    "next": "先读工作台并运行 finalize_agent.py",
                },
                1,
            )
        date_value = folder.name
        summary = read_json(folder / "summary.json", {}) or {}
        report = Path(
            summary.get("files", {}).get("report") or folder / "report" / "report.html"
        )
    else:
        try:
            value = resolve_date(args.date)
        except ValueError as exc:
            return emit({"state": "failed", "error_code": "bad_date", "error": str(exc)}, 2)
        folder = date_dir(value, home)
        summary = read_json(summary_path(value, home), {}) or {}
        report = Path(
            summary.get("files", {}).get("report") or folder / "report" / "report.html"
        )
        date_value = value.isoformat()
        if not report.exists():
            work = folder / "work" / "index.json"
            if work.exists():
                return emit(
                    {
                        "state": "ready_for_review",
                        "date": date_value,
                        "date_label": date_label(value),
                        "message": "工作台已经准备好，但豆包还没整理完",
                        "workdir": str((folder / "work").resolve()),
                        "next": "先读工作台并运行 finalize_agent.py",
                    },
                    1,
                )
            return emit(
                {
                    "state": "not_started",
                    "date": date_value,
                    "date_label": date_label(value),
                    "message": "报告还没有生成",
                    "next": "先运行 start_daily.py",
                },
                1,
            )

    xlsx = summary.get("files", {}).get("xlsx") or str(
        (folder / "report" / f"群日报_{date_value}.xlsx").resolve()
    )
    target = folder / "report" / "目标进度.html"
    if args.targets:
        if not target.exists():
            return emit(
                {
                    "state": "not_ready",
                    "date": date_value,
                    "message": "目标进度看板还没有生成",
                    "next": "先配置 config/targets.json，再运行 targets.py 或 finalize_agent.py",
                },
                1,
            )
        report = target
    payload = {
        "state": "opened" if not args.no_open else "ready",
        "date": date_value,
        "report": str(report.resolve()),
        "targets": str(target.resolve()) if target.exists() else "",
        "xlsx": xlsx if Path(xlsx).exists() else "",
        "summary": str((folder / "summary.json").resolve()),
    }
    if not args.no_open:
        os.startfile(report)  # noqa: S606 - local report opened by user request
    emit(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
