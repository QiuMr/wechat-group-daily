#!/usr/bin/env python3
"""Start the daily collection in the background and return immediately."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date as date_cls
from pathlib import Path

from common import (
    SKILL,
    config_groups,
    config_problems,
    data_home,
    date_dir,
    date_label,
    log_path,
    load_config,
    now_text,
    pid_alive,
    read_json,
    resolve_date,
    status_path,
    summary_path,
)
from run_daily import dependency_missing, wechat_running

RUNNER = SKILL / "scripts" / "run_daily.py"


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="后台启动微信群日报")
    parser.add_argument("--date", default="yesterday", help="yesterday / today / 2026-10-28")
    parser.add_argument("--force", action="store_true", help="已有报告也重新采集")
    parser.add_argument("--config", default=str(SKILL / "config" / "groups.json"))
    parser.add_argument("--home", default="", help="数据目录")
    args = parser.parse_args()

    try:
        value = resolve_date(args.date)
    except ValueError as exc:
        emit({"state": "failed", "error_code": "bad_date", "error": str(exc)})
        return 2
    if value > date_cls.today():
        emit({"state": "failed", "error_code": "bad_date", "error": "不能采集未来日期"})
        return 2

    home = Path(args.home).expanduser().resolve() if args.home else data_home()
    folder = date_dir(value, home)
    status_file = status_path(value, home)
    current = read_json(status_file, {}) or {}
    summary = read_json(summary_path(value, home), {}) or {}

    if not args.force and summary.get("state") == "ready":
        emit(
            {
                "state": "ready",
                "date": value.isoformat(),
                "message": "当天报告已经有了，直接打开即可",
                "report": summary.get("files", {}).get("report"),
                "summary": str(summary_path(value, home)),
            }
        )
        return 0

    if current.get("state") in {"starting", "running"} and pid_alive(current.get("pid")):
        emit(
            {
                "state": "running",
                "date": value.isoformat(),
                "pid": current.get("pid"),
                "message": current.get("message") or "采集正在进行",
                "log": current.get("log"),
            }
        )
        return 0

    work_index_path = folder / "work" / "index.json"
    if not args.force and work_index_path.exists():
        work = read_json(work_index_path, {}) or {}
        emit(
            {
                "state": current.get("state") if current.get("state") in {"ready_for_review", "reviewing"} else "ready_for_review",
                "date": value.isoformat(),
                "message": "工作台已经准备好，等豆包继续整理",
                "workdir": str((folder / "work").resolve()),
                "total_slices": work.get("total_slices", 0),
                "total_messages": work.get("total_messages", 0),
                "total_images": work.get("total_images", 0),
                "next": "读工作台 README 和 group digest，继续按 slices 整理",
            }
        )
        return 0

    try:
        config = load_config(Path(args.config).expanduser().resolve())
    except ValueError as exc:
        emit({"state": "failed", "error_code": "config_invalid", "error": str(exc)})
        return 1
    if not config_groups(config):
        emit({"state": "failed", "error_code": "config_invalid", "error": "config/groups.json 还没有配置群"})
        return 1
    placeholders = [
        item
        for item in config_problems(config)
        if "占位" in item or "缺少 name" in item
    ]
    if placeholders:
        emit(
            {
                "state": "failed",
                "error_code": "config_invalid",
                "error": "群配置还有占位内容：" + "；".join(placeholders),
                "hint": "打开 config/groups.json 填完整群名",
            }
        )
        return 1
    missing = dependency_missing()
    if missing:
        emit(
            {
                "state": "failed",
                "error_code": "dependency_missing",
                "error": "缺少依赖：" + "、".join(missing),
                "hint": "先运行 setup.bat",
            }
        )
        return 1
    if not wechat_running():
        emit(
            {
                "state": "failed",
                "error_code": "wechat_not_running",
                "error": "没有检测到微信进程",
                "hint": "先登录微信，保持主窗口打开，再启动日报",
            }
        )
        return 1

    folder.mkdir(parents=True, exist_ok=True)
    bootstrap = folder / "bootstrap.log"
    command = [
        sys.executable,
        str(RUNNER),
        "--date",
        value.isoformat(),
        "--config",
        str(Path(args.config).expanduser().resolve()),
        "--home",
        str(home),
    ]
    if args.force:
        command.append("--force")

    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    if os.name == "nt" and Path(sys.executable).name.lower() == "python.exe":
        flags |= 0x08000000  # CREATE_NO_WINDOW

    handle = bootstrap.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            cwd=str(SKILL),
            env=env,
            close_fds=True,
            creationflags=flags,
        )
    except OSError as exc:
        handle.close()
        emit({"state": "failed", "error_code": "launch_failed", "error": f"启动失败：{exc}"})
        return 1
    handle.close()

    payload = {
        "state": "starting",
        "date": value.isoformat(),
        "date_label": date_label(value),
        "pid": process.pid,
        "started_at": now_text(),
        "stage": "starting",
        "message": "已开始后台采集，约 15–25 分钟；期间不要操作微信。",
        "eta_minutes": "15-25",
        "log": str(log_path(value, home)),
        "bootstrap_log": str(bootstrap),
    }
    emit(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
