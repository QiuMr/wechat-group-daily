#!/usr/bin/env python3
"""Turn raw probe output into a readable review workspace for Doubao.

This script is mechanical only: it filters the target day, sorts messages,
slices them, resolves image paths and writes empty ledgers. It never decides
what is a lead or a claim.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date as date_cls, datetime, time, timedelta
from pathlib import Path
from typing import Any

from common import (
    SKILL,
    date_label,
    load_config,
    now_text,
    resolve_date,
    write_json,
)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def hm(value: datetime | None) -> str:
    return value.strftime("%H:%M") if value else "时间未知"


def message_type(record: dict[str, Any]) -> str:
    class_name = str(record.get("class") or "")
    content = str(record.get("content") or "").strip()
    if record.get("is_system"):
        return "系统"
    if "Image" in class_name or content in {"图片", "[图片]"}:
        return "图片"
    if "Quote" in class_name:
        return "引用"
    if "File" in class_name:
        return "文件"
    if "Voice" in class_name:
        return "语音"
    if "Video" in class_name:
        return "视频"
    return "文本"


def one_line(text: str, limit: int = 300) -> str:
    value = str(text or "").replace("\r", " ").replace("\n", " / ").replace("|", "｜")
    if len(value) > limit:
        value = value[:limit] + "…"
    return value


def load_probe_records(probe: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(probe.glob("messages_*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    records.append(json.loads(line))
    return records


def target_window(value: date_cls, records: list[dict[str, Any]]) -> tuple[datetime, datetime]:
    tz = None
    for record in records:
        parsed = parse_dt(record.get("resolved"))
        if parsed is not None:
            tz = parsed.tzinfo
            break
    start = datetime.combine(value, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def image_source(probe: Path, record: dict[str, Any]) -> Path | None:
    relative = str(record.get("image_path") or "")
    if not relative:
        return None
    path = probe / relative
    return path if path.exists() and path.stat().st_size > 0 else None


def copy_image(probe: Path, work: Path, record: dict[str, Any], key: str) -> tuple[str, str, bool]:
    source = image_source(probe, record)
    if source is None:
        return "", "", False
    images_dir = work / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    dest = images_dir / f"{key}_{source.name}"
    if not dest.exists():
        shutil.copy2(source, dest)
    return str(dest.resolve()), f"images/{dest.name}", True


def write_slice(
    path: Path,
    index: int,
    total: int,
    records: list[dict[str, Any]],
    overlap: int,
    label: str,
) -> dict[str, Any]:
    first = records[0]
    last = records[-1]
    lines = [
        f"# {label} · 切片 {index:03d}/{total:03d}",
        f"消息：{first['uid']}–{last['uid']}（{len(records)} 条，与上一片重叠 {overlap} 条）",
        f"时间：{first.get('time_label') or '时间未知'}–{last.get('time_label') or '时间未知'}",
        "",
        "格式：[消息uid] 时间 | 发送人 | 类型 | 内容",
        "图片消息下一行给出本机绝对路径；证据只需要写消息 uid，程序会回填原文。",
        "---",
    ]
    for record in records:
        lines.append(
            f"[{record['uid']}] {record.get('time_label') or '时间未知'} | "
            f"{record.get('sender') or '未知'} | {record.get('msg_type') or '文本'} | "
            f"{one_line(record.get('content') or '')}"
        )
        if record.get("image_abs"):
            lines.append(f"    图片：{record['image_abs']}")
        elif record.get("msg_type") == "图片":
            lines.append("    图片：未落盘（只能看到占位）")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "id": f"part-{index:03d}",
        "file": f"slices/part-{index:03d}.md",
        "count": len(records),
        "overlap": overlap,
        "from_uid": first["uid"],
        "to_uid": last["uid"],
        "from_time": first.get("time_label") or "",
        "to_time": last.get("time_label") or "",
    }


def build_group(
    work: Path,
    key: str,
    cfg: dict[str, Any],
    records: list[dict[str, Any]],
    probe_group: dict[str, Any],
    probe: Path,
    date_value: date_cls,
    slice_size: int,
    overlap: int,
) -> dict[str, Any]:
    group_dir = work / key
    group_dir.mkdir(parents=True, exist_ok=True)
    (group_dir / "slices").mkdir(exist_ok=True)
    (group_dir / "notes").mkdir(exist_ok=True)
    raw_dir = work / "raw"
    raw_dir.mkdir(exist_ok=True)

    normalized: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: int(item.get("seq") or 0)):
        if record.get("is_time"):
            continue
        uid = f"{key}:{int(record.get('seq') or 0):04d}"
        parsed = parse_dt(record.get("resolved"))
        image_abs, image_work, image_saved = ("", "", False)
        if message_type(record) == "图片":
            image_abs, image_work, image_saved = copy_image(probe, work, record, key)
        item = {
            "uid": uid,
            "group_key": key,
            "group": cfg["name"],
            "seq": int(record.get("seq") or 0),
            "class": str(record.get("class") or ""),
            "sender": str(record.get("sender") or ""),
            "content": str(record.get("content") or ""),
            "msg_type": message_type(record),
            "resolved": record.get("resolved"),
            "time_label": hm(parsed),
            "is_system": bool(record.get("is_system")),
            "image_abs": image_abs,
            "image_work": image_work,
            "image_saved": image_saved,
        }
        if parsed is None:
            unknown.append(item)
        else:
            normalized.append(item)

    raw_path = raw_dir / f"messages_{key}.jsonl"
    with raw_path.open("w", encoding="utf-8") as handle:
        for item in normalized:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    if unknown:
        unknown_path = raw_dir / f"unknown_{key}.jsonl"
        with unknown_path.open("w", encoding="utf-8") as handle:
            for item in unknown:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    slices: list[dict[str, Any]] = []
    if normalized:
        step = max(1, slice_size - overlap)
        start = 0
        index = 1
        while start < len(normalized):
            end = min(start + slice_size, len(normalized))
            chunk = normalized[start:end]
            overlap_count = min(overlap, len(chunk)) if start > 0 else 0
            slices.append(
                write_slice(
                    group_dir / "slices" / f"part-{index:03d}.md",
                    index,
                    (len(normalized) + step - 1) // step,
                    chunk,
                    overlap_count,
                    f"{cfg.get('short') or key} · {cfg.get('display_name') or cfg['name']}",
                )
            )
            if end >= len(normalized):
                break
            start += step
            index += 1

    image_records = [item for item in normalized if item["msg_type"] == "图片"]
    image_index = []
    for position, item in enumerate(normalized):
        if item["msg_type"] != "图片":
            continue
        before = normalized[max(0, position - 2):position]
        after = normalized[position + 1:position + 3]
        image_index.append(
            {
                "uid": item["uid"],
                "time": item.get("resolved"),
                "time_label": item["time_label"],
                "sender": item["sender"],
                "image_abs": item["image_abs"],
                "image_work": item["image_work"],
                "saved": item["image_saved"],
                "context_before": [
                    {
                        "uid": row["uid"],
                        "time_label": row["time_label"],
                        "sender": row["sender"],
                        "text": one_line(row["content"], 160),
                    }
                    for row in before
                ],
                "context_after": [
                    {
                        "uid": row["uid"],
                        "time_label": row["time_label"],
                        "sender": row["sender"],
                        "text": one_line(row["content"], 160),
                    }
                    for row in after
                ],
            }
        )
    write_json(group_dir / "image_index.json", image_index)

    for name in ("leads.jsonl", "claims.jsonl", "images.jsonl"):
        target = group_dir / name
        if not target.exists():
            target.write_text("", encoding="utf-8")
    questions = group_dir / "open-questions.md"
    if not questions.exists():
        questions.write_text(
            "# 拿不准的问题\n\n"
            "格式：时间 / 消息 uid / 问题 / 初步判断\n\n",
            encoding="utf-8",
        )
    progress_path = group_dir / "progress.json"
    if not progress_path.exists():
        progress = {
            "group_key": key,
            "status": "pending" if slices else "done",
            "total_slices": len(slices),
            "done_slices": [],
            "updated_at": now_text(),
        }
        write_json(progress_path, progress)

    earliest = min((item["resolved"] for item in normalized if item.get("resolved")), default=None)
    latest = max((item["resolved"] for item in normalized if item.get("resolved")), default=None)
    probe_error = str(probe_group.get("error") or "")
    if "连续多轮没有新增" in probe_error:
        probe_error = ""
    if not probe_group:
        probe_error = "未采集：群名不在本次 probe 结果里"
    digest = [
        f"# {cfg.get('short') or key} · {cfg.get('display_name') or cfg['name']}",
        "",
        f"- 微信完整群名：{cfg['name']}",
        f"- 配置发布人（仅提示，不限制谁可以发线索）：{'、'.join(cfg.get('publishers') or []) or '未配置'}",
        f"- 目标日期：{date_value.isoformat()} 00:00–24:00",
        f"- 本机可见：{(earliest or '无')[:16]} – {(latest or '无')[:16]}",
        f"- 目标日期消息：{len(normalized)} 条；图片：{len(image_records)} 张；未知时间：{len(unknown)} 条",
        f"- 切片：{len(slices)} 片，每片约 {slice_size} 条，重叠 {overlap} 条",
        f"- 采集状态：{probe_error or '正常'}",
        "",
        "## 整理口径（必须遵守）",
        "",
        "1. 一个联系方式算一条线索；一条消息/一张图里有多个联系方式，要拆成多条。",
        "2. 任何人发的都算线索，不只看上面列出的发布人。",
        "3. 同一个人连续发 2-4 条线索算一批；后面一个“1”默认领整批，不是只领最近一条；两个人各扣 1，就两个人都记到整批。",
        "4. 扣 1 的范围由你判断：1、我要、接了、私聊，以及其他语义上表示领取的话。",
        "5. 每张图片都要打开看，判断是线索、附件、噪音还是看不准；图片里的联系方式要读出来。",
        "6. 线索写 `leads.jsonl`，扣 1 写 `claims.jsonl`，图片判断写 `images.jsonl`；一行一个 JSON。",
        "7. 证据只写 `message_uid`，不要自己复制大段原文；页面原文由程序从原始消息回填。",
        "8. 每读完一片，更新 `progress.json`，并在 `notes/` 写这一片的笔记。",
        "",
        "## 文件",
        "",
        "- `slices/part-XXX.md`：原始消息切片",
        "- `image_index.json`：所有图片的 uid、绝对路径和前后文",
        "- `notes/part-XXX.md`：你的阅读笔记",
        "- `open-questions.md`：拿不准的问题",
        "- `raw/messages_*.jsonl`：程序用的原始消息，不要改",
    ]
    (group_dir / "digest.md").write_text("\n".join(digest) + "\n", encoding="utf-8")

    probe_summary = {
        "error": probe_group.get("error") or "",
        "merged_count": int(probe_group.get("merged_count") or 0),
        "in_range_count": int(probe_group.get("in_range_count") or 0),
        "gaps": len(probe_group.get("gaps") or []),
        "reached_since": bool(probe_group.get("reached_since")),
        "earliest": probe_group.get("earliest"),
        "latest": probe_group.get("latest"),
        "visible_from": probe_group.get("visible_from"),
        "visible_to": probe_group.get("visible_to"),
        "members": int((probe_group.get("chat_info") or {}).get("group_member_count") or 0),
        "coverage_note": probe_group.get("coverage_note") or "",
    }
    group_index = {
        "key": key,
        "name": cfg["name"],
        "short": cfg.get("short") or key,
        "display_name": cfg.get("display_name") or cfg["name"],
        "publishers": cfg.get("publishers") or [],
        "message_count": len(normalized),
        "image_count": len(image_records),
        "unknown_count": len(unknown),
        "coverage_from": hm(parse_dt(earliest)),
        "coverage_to": hm(parse_dt(latest)),
        "slices": slices,
        "raw_file": f"raw/messages_{key}.jsonl",
        "image_index_file": "image_index.json",
        "probe_group": probe_summary,
    }
    write_json(group_dir / "index.json", group_index)
    return group_index


def main() -> int:
    parser = argparse.ArgumentParser(description="把 probe 输出切成豆包工作台")
    parser.add_argument("--probe", required=True)
    parser.add_argument("--config", default=str(SKILL / "config" / "groups.json"))
    parser.add_argument("--date", required=True, help="2026-10-28 / yesterday / today")
    parser.add_argument("--out", required=True, help="工作台目录")
    parser.add_argument("--slice-size", type=int, default=40)
    parser.add_argument("--overlap", type=int, default=5)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    probe = Path(args.probe).expanduser().resolve()
    work = Path(args.out).expanduser().resolve()
    value = resolve_date(args.date)
    if work.exists() and (work / "index.json").exists() and not args.force:
        print(json.dumps(json.loads((work / "index.json").read_text(encoding="utf-8")), ensure_ascii=False))
        return 0
    if work.exists() and args.force:
        backup = work.parent / f"{work.name}_backup_{datetime.now():%Y%m%d_%H%M%S}"
        work.rename(backup)

    config = load_config(Path(args.config))
    groups = [item for item in config.get("groups") or [] if item.get("name")]
    probe_report = json.loads((probe / "report.json").read_text(encoding="utf-8"))
    probe_by_name = {item["group"]: item for item in probe_report.get("groups") or []}
    records = load_probe_records(probe)
    start, end = target_window(value, records)

    by_group: dict[str, list[dict[str, Any]]] = {}
    unconfigured: set[str] = set()
    configured_names = {str(item["name"]): item for item in groups}
    for record in records:
        name = str(record.get("group") or "")
        parsed = parse_dt(record.get("resolved"))
        if parsed is None or not (start <= parsed < end):
            continue
        if name not in configured_names:
            unconfigured.add(name)
            continue
        by_group.setdefault(str(configured_names[name]["key"]), []).append(record)

    work.mkdir(parents=True, exist_ok=True)
    group_indexes = []
    for cfg in groups:
        key = str(cfg.get("key") or f"g{len(group_indexes) + 1}")
        group_indexes.append(
            build_group(
                work,
                key,
                cfg,
                by_group.get(key, []),
                probe_by_name.get(str(cfg["name"]), {}),
                probe,
                value,
                max(5, args.slice_size),
                max(0, min(args.overlap, args.slice_size - 1)),
            )
        )

    index = {
        "schema_version": 1,
        "date": value.isoformat(),
        "date_label": date_label(value),
        "created_at": now_text(),
        "probe": str(probe),
        "workdir": str(work),
        "groups": group_indexes,
        "total_messages": sum(item["message_count"] for item in group_indexes),
        "total_images": sum(item["image_count"] for item in group_indexes),
        "total_slices": sum(len(item["slices"]) for item in group_indexes),
        "unconfigured_groups": sorted(unconfigured),
    }
    write_json(work / "index.json", index)

    readme = [
        "# 豆包工作台",
        "",
        "先读每个群的 `digest.md`，再按 `slices/part-XXX.md` 顺序读。",
        "每片读完写 `notes/`，并把线索/扣1/图片判断追加到对应 JSONL。",
        "全部群完成后，运行 `python scripts/check_group.py --work <这里>`。",
        "校验通过后运行 `python scripts/finalize_agent.py --work <这里> --out <报告目录>`。",
        "",
        "不要改 `raw/` 和 `slices/`；不要用程序规则替自己判断线索和扣 1。",
    ]
    (work / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    print(json.dumps(index, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
