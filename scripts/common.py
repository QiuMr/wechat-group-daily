#!/usr/bin/env python3
"""Shared paths, dates and status helpers for the daily group report skill."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from typing import Any

SKILL = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = SKILL / "config" / "groups.json"
DEFAULT_TEMPLATE = SKILL / "assets" / "report_template.html"
WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def data_home() -> Path:
    custom = os.environ.get("WECHAT_DAILY_HOME")
    if custom:
        return Path(custom).expanduser().resolve()
    return (Path.home() / ".rent-lead" / "wechat-group-daily").resolve()


def resolve_date(raw: str | None, now: datetime | None = None) -> date_cls:
    current = now or datetime.now()
    text = (raw or "yesterday").strip().lower()
    if text in {"yesterday", "昨天"}:
        return current.date() - timedelta(days=1)
    if text in {"today", "今天"}:
        return current.date()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"无法识别日期：{raw}（可用 yesterday / today / 2026-10-28）") from exc


def date_label(value: date_cls) -> str:
    return f"{value.year} 年 {value.month} 月 {value.day} 日 · {WEEKDAYS[value.weekday()]}"


def relative_prefix(value: date_cls, now: datetime | None = None) -> str:
    today = (now or datetime.now()).date()
    if value == today - timedelta(days=1):
        return "昨天，"
    if value == today:
        return "今天，"
    return f"{value.month} 月 {value.day} 日，"


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def date_dir(value: date_cls, home: Path | None = None) -> Path:
    return (home or data_home()) / "reports" / value.isoformat()


def status_path(value: date_cls, home: Path | None = None) -> Path:
    return date_dir(value, home) / "status.json"


def summary_path(value: date_cls, home: Path | None = None) -> Path:
    return date_dir(value, home) / "summary.json"


def log_path(value: date_cls, home: Path | None = None) -> Path:
    return date_dir(value, home) / "run.log"


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    target = Path(path) if path else DEFAULT_CONFIG
    data = read_json(target)
    if not isinstance(data, dict):
        raise ValueError(f"读不到配置：{target}")
    return data


def config_groups(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in config.get("groups") or [] if isinstance(item, dict)]


def config_problems(config: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    groups = config_groups(config)
    if not groups:
        problems.append("config/groups.json 还没有配置群")
    for index, group in enumerate(groups, start=1):
        name = str(group.get("name") or "").strip()
        if not name:
            problems.append(f"第 {index} 个群缺少 name")
        if "请替换" in name:
            problems.append(f"第 {index} 个群还是占位群名")
        publishers = list(group.get("publishers") or [])
        if not publishers:
            problems.append(f"{group.get('short') or name or index} 没有配置发布人")
        elif any("请填" in str(item) or "请替换" in str(item) for item in publishers):
            problems.append(f"{group.get('short') or name or index} 发布人还是占位内容")
    return problems


def expected_groups(config: dict[str, Any]) -> int:
    try:
        return int(config.get("expected_groups") or 0)
    except (TypeError, ValueError):
        return 0


def tail_text(path: Path, lines: int = 20) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def file_info(path: Path | str | None) -> str:
    if not path:
        return ""
    value = Path(path)
    return str(value.resolve()) if value.exists() else ""
