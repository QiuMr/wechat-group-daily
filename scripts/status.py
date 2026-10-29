#!/usr/bin/env python3
"""Print the current daily-report status as JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import (
    date_dir,
    date_label,
    log_path,
    pid_alive,
    read_json,
    resolve_date,
    status_path,
    summary_path,
    tail_text,
)


def emit(payload: dict, code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


def workspace_payload(folder: Path, value) -> dict | None:
    work = folder / "work"
    index = read_json(work / "index.json", {}) or {}
    if not index:
        return None
    groups = []
    total = 0
    done_total = 0
    next_group = None
    next_slice = None
    for group in index.get("groups") or []:
        progress = read_json(work / group["key"] / "progress.json", {}) or {}
        total_slices = int(progress.get("total_slices") or len(group.get("slices") or []))
        done = sorted(set(map(str, progress.get("done_slices") or [])))
        total += total_slices
        done_total += len(done)
        groups.append(
            {
                "key": group["key"],
                "label": f"{group['short']} · {group['display_name']}",
                "messages": group.get("message_count"),
                "images": group.get("image_count"),
                "total_slices": total_slices,
                "done_slices": len(done),
                "status": progress.get("status") or "pending",
            }
        )
        if next_group is None:
            for item in group.get("slices") or []:
                if item["id"] not in done:
                    next_group = group["key"]
                    next_slice = item["id"]
                    break
    if total and done_total >= total:
        state = "reviewing"
        message = "切片已经全部读完，等生成报告"
        next_text = "运行 finalize_agent.py 生成 HTML、Excel 和摘要"
    elif done_total:
        state = "reviewing"
        message = f"豆包正在整理：已读 {done_total}/{total} 片"
        next_text = f"继续读 {next_group}/slices/{next_slice}.md，读完后标记完成"
    else:
        state = "ready_for_review"
        message = "工作台已准备好，等豆包开始整理"
        next_text = "先读工作台 README 和每个群的 digest.md，再按 slices 顺序整理"
    if total == 0:
        next_text = "没有可整理的消息，直接运行 finalize_agent.py 生成空报告"
    return {
        "state": state,
        "date": index.get("date") or value.isoformat(),
        "date_label": index.get("date_label") or date_label(value),
        "message": message,
        "workdir": str(work.resolve()),
        "total_messages": index.get("total_messages", 0),
        "total_images": index.get("total_images", 0),
        "total_slices": total,
        "done_slices": done_total,
        "groups": groups,
        "next_group": next_group,
        "next_slice": next_slice,
        "next": next_text,
    }


def failed_from(status: dict) -> dict:
    log = status.get("log") or ""
    return {
        "state": "failed",
        "date": status.get("date"),
        "error_code": status.get("error_code") or "process_exited",
        "error": status.get("error") or "采集进程已退出，但没有生成报告",
        "log": log,
        "log_tail": status.get("log_tail") or (tail_text(Path(log), 15) if log else ""),
        "next": "看日志确认原因；修好后重新运行 start_daily.py",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="查看群日报状态")
    parser.add_argument("--date", default="yesterday", help="yesterday / today / 2026-10-28")
    parser.add_argument("--home", default="", help="数据目录")
    args = parser.parse_args()

    try:
        value = resolve_date(args.date)
    except ValueError as exc:
        return emit({"state": "failed", "error_code": "bad_date", "error": str(exc)}, 2)

    home = Path(args.home).expanduser().resolve() if args.home else None
    folder = date_dir(value, home)
    status = read_json(status_path(value, home), {}) or {}
    summary = read_json(summary_path(value, home), {}) or {}
    report = summary.get("files", {}).get("report") or str((folder / "report" / "report.html").resolve())
    log = status.get("log") or str(log_path(value, home))
    work_payload = workspace_payload(folder, value)

    if status:
        state = str(status.get("state") or "unknown")
        if state in {"starting", "running"}:
            if pid_alive(status.get("pid")):
                return emit(
                    {
                        "state": "running",
                        "date": value.isoformat(),
                        "date_label": date_label(value),
                        "stage": status.get("stage"),
                        "message": status.get("message") or "正在采集",
                        "progress": status.get("progress"),
                        "started_at": status.get("started_at"),
                        "log": log,
                        "report": report if Path(report).exists() else "",
                        "next": "等待 15–25 分钟后再查；期间不要操作微信",
                    }
                )
            if summary.get("state") == "ready":
                return emit(summary)
            if work_payload:
                return emit(work_payload)
            return emit(failed_from(status), 1)
        if state in {"ready_for_review", "reviewing"}:
            if summary.get("state") == "ready":
                return emit(summary)
            if work_payload:
                return emit(work_payload)
            return emit(failed_from(status), 1)
        if state == "ready":
            if summary.get("state") != "ready":
                summary = {
                    "state": "ready",
                    "date": value.isoformat(),
                    "date_label": date_label(value),
                    "files": {
                        "report": status.get("report") or report,
                        "xlsx": status.get("xlsx"),
                        "summary": status.get("summary") or str(summary_path(value, home)),
                    },
                    "warnings": status.get("warnings") or [],
                }
            report_file = Path(summary.get("files", {}).get("report") or report)
            if not report_file.exists():
                return emit(
                    {
                        "state": "failed",
                        "date": value.isoformat(),
                        "error_code": "report_missing",
                        "error": "报告记录是 ready，但 report.html 不在了",
                        "report": str(report_file),
                        "next": "重新运行 start_daily.py --force",
                    },
                    1,
                )
            summary.setdefault("next", "读取 summary.json，然后打开 report")
            return emit(summary)
        if state == "failed":
            return emit(failed_from(status), 1)

    if summary.get("state") == "ready":
        report_file = Path(summary.get("files", {}).get("report") or report)
        if not report_file.exists():
            return emit(
                {
                    "state": "failed",
                    "date": value.isoformat(),
                    "error_code": "report_missing",
                    "error": "摘要说报告已生成，但 report.html 不在了",
                    "report": str(report_file),
                    "next": "重新运行 start_daily.py --force",
                },
                1,
            )
        summary.setdefault("next", "读取 summary.json，然后打开 report")
        return emit(summary)
    if work_payload:
        return emit(work_payload)
    if Path(report).exists():
        return emit(
            {
                "state": "ready",
                "date": value.isoformat(),
                "date_label": date_label(value),
                "files": {"report": report},
                "next": "打开 report",
            }
        )

    return emit(
        {
            "state": "not_started",
            "date": value.isoformat(),
            "date_label": date_label(value),
            "message": "这一天还没有采集过",
            "next": "运行 start_daily.py 开始采集；约 15–25 分钟",
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
