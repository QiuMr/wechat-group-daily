#!/usr/bin/env python3
"""Collect yesterday's groups and build the daily report. Long-running."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from typing import Any

from common import (
    SKILL,
    config_groups,
    config_problems,
    data_home,
    date_dir,
    date_label,
    expected_groups,
    log_path,
    load_config,
    now_text,
    relative_prefix,
    resolve_date,
    status_path,
    summary_path,
    write_json,
)

PROBE = SKILL / "scripts" / "probe_groups.py"
PREPARE = SKILL / "scripts" / "prepare_workspace.py"
REPORT = SKILL / "scripts" / "report.py"


def log_line(handle: Any, message: str = "") -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    text = f"[{stamp}] {message}" if message else ""
    print(text, flush=True)
    handle.write(text + "\n")
    handle.flush()


def wechat_running() -> bool:
    try:
        output = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=20,
        ).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return True
    return "weixin.exe" in output or "wechat.exe" in output


def dependency_missing() -> list[str]:
    missing = []
    for name, label in (("wxauto4", "wxauto4"), ("openpyxl", "openpyxl")):
        if importlib.util.find_spec(name) is None:
            missing.append(label)
    return missing


def write_status(value: date_cls, payload: dict[str, Any], home: Path) -> None:
    write_json(status_path(value, home), payload)


def build_brief(data: dict[str, Any], prefix: str) -> list[str]:
    stats = data["stats"]
    lines = [
        f"{prefix}{stats['groups']} 个群，共 {stats['leads']} 条线索："
        f"{stats['claimed']} 条有人扣 1，{stats['unclaimed']} 条没人扣 1。"
    ]
    for insight in data.get("insights") or []:
        if insight not in lines:
            lines.append(str(insight))
        if len(lines) >= 2:
            break
    if stats.get("unclaimed") or stats.get("orphans") or stats.get("review"):
        lines.append(
            f"需要先看：{stats.get('unclaimed', 0)} 条线索没人扣 1、"
            f"{stats.get('orphans', 0)} 条领取对不上线索、"
            f"{stats.get('review', 0)} 项数据需核对。"
        )
    coverage = str(data.get("meta", {}).get("scope_visible") or "")
    if coverage:
        lines.append(coverage + "。")
    saved = sum(int(group.get("image_saved") or 0) for group in data.get("groups") or [])
    total = sum(int(group.get("image_count") or 0) for group in data.get("groups") or [])
    if total and saved < total:
        lines.append(f"图片共 {total} 张，已保存 {saved} 张；未保存的只能看到占位。")
    return lines


def build_summary(
    data: dict[str, Any],
    value: date_cls,
    paths: dict[str, Path],
    warnings: list[str],
) -> dict[str, Any]:
    stats = data["stats"]
    groups = []
    for group in data.get("groups") or []:
        groups.append(
            {
                "key": group.get("key"),
                "label": group.get("label"),
                "status": group.get("status"),
                "status_label": group.get("status_label"),
                "status_note": group.get("status_note"),
                "leads": group.get("leads"),
                "claimed": group.get("claimed"),
                "unclaimed": group.get("unclaimed"),
                "claims": group.get("claims"),
                "coverage": f"{group.get('coverage_from')}–{group.get('coverage_to')}",
                "image_count": group.get("image_count"),
                "image_saved": group.get("image_saved"),
            }
        )
    partial = bool(warnings) or any(
        group.get("status") != "complete" for group in data.get("groups") or []
    )
    return {
        "state": "ready",
        "date": value.isoformat(),
        "date_label": date_label(value),
        "prefix": relative_prefix(value),
        "partial": partial,
        "stats": stats,
        "brief_lines": build_brief(data, relative_prefix(value)),
        "groups": groups,
        "attention": data.get("attention") or [],
        "insights": data.get("insights") or [],
        "warnings": warnings,
        "coverage_note": data.get("meta", {}).get("scope_visible", ""),
        "files": {
            "report": str(paths["report"].resolve()),
            "xlsx": str(paths["xlsx"].resolve()),
            "facts": str(paths["facts"].resolve()),
            "summary": str(paths["summary"].resolve()),
            "log": str(paths["log"].resolve()),
            "probe": str(paths["probe"].resolve()),
        },
        "generated_at": now_text(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="回捞微信群并生成日报")
    parser.add_argument("--date", default="yesterday", help="yesterday / today / 2026-10-28")
    parser.add_argument("--force", action="store_true", help="已有报告也重新采集")
    parser.add_argument("--config", default=str(SKILL / "config" / "groups.json"))
    parser.add_argument("--home", default="", help="数据目录；默认 ~/.rent-lead/wechat-group-daily")
    args = parser.parse_args()

    try:
        value = resolve_date(args.date)
    except ValueError as exc:
        print(json.dumps({"state": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    if value > date_cls.today():
        print(json.dumps({"state": "failed", "error": "不能采集未来日期"}, ensure_ascii=False))
        return 2

    home = Path(args.home).expanduser().resolve() if args.home else data_home()
    folder = date_dir(value, home)
    folder.mkdir(parents=True, exist_ok=True)
    paths = {
        "probe": folder / "probe",
        "report": folder / "report" / "report.html",
        "xlsx": folder / "report" / f"群日报_{value.isoformat()}.xlsx",
        "facts": folder / "report" / "facts.json",
        "summary": summary_path(value, home),
        "log": log_path(value, home),
    }
    runner_log = log_path(value, home)
    status_file = status_path(value, home)

    existing = None
    try:
        existing = json.loads(paths["summary"].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if existing and existing.get("state") == "ready" and not args.force:
        print(json.dumps(existing, ensure_ascii=False))
        return 0

    started = now_text()
    write_status(
        value,
        {
            "state": "running",
            "date": value.isoformat(),
            "stage": "preflight",
            "pid": os.getpid(),
            "started_at": started,
            "log": str(runner_log),
            "message": "正在检查环境",
        },
        home,
    )

    with runner_log.open("a", encoding="utf-8") as log:
        log_line(log, f"群日报开始：{date_label(value)}")
        log_line(log, f"数据目录：{folder.resolve()}")

        config_path = Path(args.config).expanduser().resolve()
        try:
            config = load_config(config_path)
        except ValueError as exc:
            return fail(value, home, status_file, runner_log, "config_invalid", str(exc), log)
        problems = config_problems(config)
        groups = config_groups(config)
        if not groups:
            return fail(value, home, status_file, runner_log, "config_invalid", "还没有配置群", log)
        placeholders = [item for item in problems if "占位" in item or "缺少 name" in item]
        if placeholders:
            return fail(
                value,
                home,
                status_file,
                runner_log,
                "config_invalid",
                "群配置还有占位内容：" + "；".join(placeholders),
                log,
            )
        warnings = list(problems)
        expected = expected_groups(config)
        if expected and len(groups) < expected:
            warnings.append(f"当前只配置 {len(groups)} 个群，预期 {expected} 个")
        if warnings:
            log_line(log, "配置提醒：" + "；".join(warnings))

        missing = dependency_missing()
        if missing:
            message = "缺少依赖：" + "、".join(missing) + "；先运行 setup_silent.bat"
            return fail(value, home, status_file, runner_log, "dependency_missing", message, log)

        if not wechat_running():
            message = "没有检测到微信进程；先登录微信并保持窗口打开"
            return fail(value, home, status_file, runner_log, "wechat_not_running", message, log)

        groups_file = folder / "groups.txt"
        groups_file.write_text(
            "\n".join(str(group["name"]).strip() for group in groups) + "\n",
            encoding="utf-8",
        )

        since_text = f"{value.isoformat()} 00:00"
        if value == date_cls.today():
            until_text = datetime.now().strftime("%Y-%m-%d %H:%M")
        else:
            until_text = f"{(value + timedelta(days=1)).isoformat()} 00:00"

        probe_cfg = config.get("probe") or {}
        command = [
            sys.executable,
            str(PROBE),
            "--groups-file",
            str(groups_file),
            "--since",
            since_text,
            "--until",
            until_text,
            "--out",
            str(paths["probe"]),
        ]
        for key, flag in (
            ("max_rounds", "--max-rounds"),
            ("wheel_steps", "--wheel-steps"),
            ("stall_limit", "--stall-limit"),
            ("bottom_steps", "--bottom-steps"),
        ):
            if probe_cfg.get(key):
                command.extend([flag, str(probe_cfg[key])])
        if probe_cfg.get("save_raw"):
            command.append("--save-raw")

        write_status(
            value,
            {
                "state": "running",
                "date": value.isoformat(),
                "stage": "collecting",
                "pid": os.getpid(),
                "started_at": started,
                "log": str(runner_log),
                "message": f"正在回捞 {len(groups)} 个群，约 30–90 分钟（活跃小群更久）",
                "progress": {"groups": len(groups), "done": 0},
            },
            home,
        )
        log_line(log, f"开始回捞 {len(groups)} 个群：{since_text} ~ {until_text}")
        log_line(log, "只读采集：会切群和滚动，不会发消息；期间不要操作微信。")

        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=str(SKILL),
            )
        except OSError as exc:
            return fail(value, home, status_file, runner_log, "probe_failed", f"启动采集失败：{exc}", log)

        done_groups = 0
        assert process.stdout is not None
        for line in process.stdout:
            log_line(log, line.rstrip("\n"))
            if line.startswith("[") and "/" in line.split("] ", 1)[0]:
                done_groups += 1
                write_status(
                    value,
                    {
                        "state": "running",
                        "date": value.isoformat(),
                        "stage": "collecting",
                        "pid": os.getpid(),
                        "started_at": started,
                        "log": str(runner_log),
                        "message": f"正在回捞：已开始第 {min(done_groups, len(groups))}/{len(groups)} 个群",
                        "progress": {"groups": len(groups), "done": min(done_groups, len(groups))},
                    },
                    home,
                )
        code = process.wait()
        if code == 3:
            return fail(
                value, home, status_file, runner_log, "wechat_not_ready",
                "挂不上微信；确认已登录、版本 4.1.8.x、窗口没有最小化", log,
            )
        if not (paths["probe"] / "report.json").exists():
            return fail(
                value, home, status_file, runner_log, "probe_failed",
                f"采集没有产出结果（退出码 {code}）；看日志确认原因", log,
            )
        if code != 0:
            warnings.append(f"采集退出码 {code}，已用现有结果生成工作台")

        write_status(
            value,
            {
                "state": "running",
                "date": value.isoformat(),
                "stage": "preparing",
                "pid": os.getpid(),
                "started_at": started,
                "log": str(runner_log),
                "message": "正在切分消息、整理图片索引",
            },
            home,
        )
        log_line(log, "采集完成，开始生成豆包工作台。")

        prepare_command = [
            sys.executable,
            str(PREPARE),
            "--probe",
            str(paths["probe"]),
            "--config",
            str(config_path),
            "--date",
            value.isoformat(),
            "--out",
            str(folder / "work"),
        ]
        if args.force:
            prepare_command.append("--force")
        result = subprocess.run(
            prepare_command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(SKILL),
        )
        if result.returncode != 0 or not (folder / "work" / "index.json").exists():
            detail = (result.stderr or result.stdout or "").strip()[-800:]
            return fail(
                value, home, status_file, runner_log, "prepare_failed",
                f"生成工作台失败：{detail or '未知错误'}", log,
            )
        work_index = json.loads((folder / "work" / "index.json").read_text(encoding="utf-8"))
        log_line(log, (result.stdout or "").strip()[:500])

        group_summaries = [
            {
                "key": group["key"],
                "label": f"{group['short']} · {group['display_name']}",
                "messages": group["message_count"],
                "images": group["image_count"],
                "slices": len(group["slices"]),
            }
            for group in work_index.get("groups") or []
        ]
        write_status(
            value,
            {
                "state": "ready_for_review",
                "date": value.isoformat(),
                "stage": "review",
                "pid": os.getpid(),
                "started_at": started,
                "finished_at": now_text(),
                "message": f"采集完成，已切成 {work_index.get('total_slices', 0)} 片，等豆包整理",
                "workdir": str((folder / "work").resolve()),
                "groups": group_summaries,
                "total_slices": work_index.get("total_slices", 0),
                "total_messages": work_index.get("total_messages", 0),
                "total_images": work_index.get("total_images", 0),
                "warnings": warnings,
                "log": str(runner_log),
                "next": "读工作台 README 和每个群的 digest.md，按 slices 顺序整理",
            },
            home,
        )
        log_line(
            log,
            f"工作台完成：{work_index.get('total_messages', 0)} 条消息，"
            f"{work_index.get('total_images', 0)} 张图片，{work_index.get('total_slices', 0)} 片",
        )
        print(json.dumps(work_index, ensure_ascii=False))
        return 0


def fail(
    value: date_cls,
    home: Path | None,
    status_file: Path,
    runner_log: Path,
    code: str,
    message: str,
    log: Any,
) -> int:
    log_line(log, f"失败[{code}]：{message}")
    payload = {
        "state": "failed",
        "date": value.isoformat(),
        "stage": "failed",
        "error_code": code,
        "error": message,
        "log": str(runner_log),
        "finished_at": now_text(),
    }
    try:
        payload["log_tail"] = "\n".join(
            runner_log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
        )
    except OSError:
        pass
    write_json(status_file, payload)
    print(json.dumps(payload, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
