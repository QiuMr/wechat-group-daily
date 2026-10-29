#!/usr/bin/env python3
"""微信多群历史回捞探针（只读）。

用途：在客户电脑上，用已登录的微信，按群名逐个向上滚动，读取历史消息，
合并成一条时间线，统计条数、时间跨度、消息类型和“扣 1”候选。
不会发消息、不会改群、不会加好友。

依赖：Python 3.12 + wxauto4==41.1.7 + 微信 4.1.8.x。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
import traceback
from collections import Counter
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

try:
    from wxauto4 import WeChat
except Exception as exc:  # noqa: BLE001
    print("无法导入 wxauto4。请先运行 setup_silent.bat，或确认当前 Python 能 import wxauto4。")
    print(f"错误：{type(exc).__name__}: {exc}")
    raise SystemExit(2)

WEEKDAY_MAP = {
    "星期一": 0,
    "星期二": 1,
    "星期三": 2,
    "星期四": 3,
    "星期五": 4,
    "星期六": 5,
    "星期日": 6,
    "星期天": 6,
}
CLAIM_TEXTS = {"1", "+1", "1️⃣"}
TIME_CLASS = "Time"


def local_now() -> datetime:
    return datetime.now().astimezone()


def normalize_text(text: Any) -> str:
    return (
        str(text or "")
        .replace("\u00a0", " ")
        .replace("\u3000", " ")
        .replace("：", ":")
        .strip()
    )


def parse_clock(text: str) -> tuple[int, int] | None:
    match = re.search(r"(\d{1,2}):(\d{2})", text)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def apply_period(hour: int, text: str) -> int:
    if ("下午" in text or "晚上" in text or "傍晚" in text) and hour < 12:
        return hour + 12
    if "中午" in text and hour < 11:
        return hour + 12
    if ("凌晨" in text or "上午" in text or "早上" in text) and hour == 12:
        return 0
    return hour


def resolve_time_mark(raw: str, now: datetime) -> datetime | None:
    """把微信时间分隔条解析成绝对时间。

    支持：18:59、昨天 23:50、前天 12:00、星期一 17:18、
    2026年10月26日 17:18、10月26日 17:18、下午 3:45。
    """
    text = normalize_text(raw)
    if not text:
        return None
    clock = parse_clock(text)
    hour, minute = clock if clock else (0, 0)
    hour = apply_period(hour, text)

    def combine(date_value, h: int = hour, m: int = minute) -> datetime:
        return datetime(
            date_value.year, date_value.month, date_value.day, h, m,
            tzinfo=now.tzinfo,
        )

    # 完整日期：2026年10月26日 / 2026-10-26
    match = re.search(r"(\d{4})\s*[年/-]\s*(\d{1,2})\s*[月/-]\s*(\d{1,2})\s*日?", text)
    if match:
        try:
            return combine(datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            return None

    # 月日：10月26日
    match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if match:
        try:
            candidate = combine(datetime(now.year, int(match.group(1)), int(match.group(2))))
            if candidate > now + timedelta(days=1):
                candidate = candidate.replace(year=now.year - 1)
            return candidate
        except ValueError:
            return None

    # 昨天 / 前天 / 今天
    if "前天" in text:
        return combine(now.date() - timedelta(days=2))
    if "昨天" in text:
        return combine(now.date() - timedelta(days=1))
    if "今天" in text:
        return combine(now.date())

    # 星期一 … 星期日；上周一额外减 7 天
    for label, weekday in WEEKDAY_MAP.items():
        if label in text:
            delta = (now.weekday() - weekday) % 7
            date_value = now.date() - timedelta(days=delta)
            candidate = combine(date_value)
            if candidate > now:
                candidate -= timedelta(days=7)
            if "上周" in text:
                candidate -= timedelta(days=7)
            return candidate

    # 只有时间：今天，若还没到就当成昨天
    if clock:
        candidate = combine(now.date())
        if candidate > now:
            candidate -= timedelta(days=1)
        return candidate

    return None


def parse_anchor_arg(raw: str, now: datetime, *, end_of_day: bool = False) -> datetime:
    text = normalize_text(raw).lower()
    if text in {"yesterday", "昨天"}:
        date_value = now.date() - timedelta(days=1)
        return datetime.combine(
            date_value,
            datetime.max.time() if end_of_day else datetime.min.time(),
            tzinfo=now.tzinfo,
        )
    if text in {"today", "今天"}:
        date_value = now.date()
        return datetime.combine(
            date_value,
            datetime.max.time() if end_of_day else datetime.min.time(),
            tzinfo=now.tzinfo,
        )
    try:
        value = datetime.fromisoformat(raw.strip())
    except ValueError as exc:
        raise ValueError(f"无法识别时间：{raw}（示例：yesterday、today、2026-10-27 00:00）") from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=now.tzinfo)
    return value


class ImageSaver:
    """把当前可见的图片消息截成 PNG，按消息 id 去重。"""

    def __init__(self, out_dir: Path) -> None:
        self.folder = out_dir / "images"
        self.cache: dict[str, str] = {}
        self.saved = 0
        self.failed = 0

    def save(self, msg: Any) -> str:
        if type(msg).__name__ != "ImageMessage":
            return ""
        msg_id = str(getattr(msg, "id", "") or "")
        if msg_id and msg_id in self.cache:
            return self.cache[msg_id]
        try:
            rect = msg.control.BoundingRectangle
            if rect.height() <= 0 or rect.width() <= 0:
                self.failed += 1
                return ""
            self.folder.mkdir(parents=True, exist_ok=True)
            name = f"img_{self.saved + 1:04d}.png"
            target = self.folder / name
            msg.control.ScreenShot(str(target))
            if not target.exists() or target.stat().st_size == 0:
                self.failed += 1
                return ""
            self.saved += 1
            relative = f"images/{name}"
            if msg_id:
                self.cache[msg_id] = relative
            return relative
        except Exception:  # noqa: BLE001
            self.failed += 1
            return ""


def message_info(
    msg: Any,
    anchor_text: str | None,
    anchor_dt: datetime | None,
    image_saver: ImageSaver | None = None,
) -> dict[str, Any]:
    class_name = type(msg).__name__
    is_time = TIME_CLASS in class_name
    is_system = "System" in class_name
    image_path = image_saver.save(msg) if image_saver else ""
    return {
        "class": class_name,
        "sender": str(getattr(msg, "sender", "") or ""),
        "content": str(getattr(msg, "content", "") or ""),
        "id": str(getattr(msg, "id", "") or ""),
        "hash": str(getattr(msg, "hash", "") or ""),
        "type": str(getattr(msg, "type", "") or ""),
        "anchor": anchor_text,
        "resolved": anchor_dt.isoformat() if anchor_dt else None,
        "is_time": is_time,
        "is_system": is_system,
        "image_path": image_path,
    }


def read_window(
    wx: WeChat,
    now: datetime,
    image_saver: ImageSaver | None = None,
) -> list[dict[str, Any]]:
    raw_messages = wx.GetAllMessage()
    records: list[dict[str, Any]] = []
    anchor_text: str | None = None
    anchor_dt: datetime | None = None
    for msg in raw_messages:
        class_name = type(msg).__name__
        content = str(getattr(msg, "content", "") or "")
        if TIME_CLASS in class_name:
            parsed = resolve_time_mark(content, now)
            if parsed is not None:
                anchor_text = content
                anchor_dt = parsed
            records.append(message_info(msg, anchor_text, anchor_dt, image_saver))
        else:
            records.append(message_info(msg, anchor_text, anchor_dt, image_saver))
    return records


def signature(record: dict[str, Any], mode: str) -> tuple[str, ...]:
    anchor = str(record.get("anchor") or "")
    class_name = str(record.get("class") or "")
    sender = str(record.get("sender") or "")
    content = str(record.get("content") or "")
    if mode == "strong":
        return (anchor, class_name, sender, content, str(record.get("id") or ""))
    if mode == "context":
        return (anchor, class_name, sender, content)
    return (class_name, sender, content)


def suffix_prefix_overlap(
    window: list[dict[str, Any]],
    accumulated: list[dict[str, Any]],
) -> tuple[int, str]:
    """找“新窗口尾部 = 已合并序列头部”的最长重叠。

    先做这个确定性匹配，再做 SequenceMatcher 兜底；
    比直接找任意内部匹配更不容易把重复的“1”接错。
    """
    max_k = min(len(window), len(accumulated))
    if max_k <= 0:
        return 0, ""
    best_k = 0
    best_mode = ""
    for mode in ("strong", "context", "weak"):
        left = [signature(item, mode) for item in window[-max_k:]]
        right = [signature(item, mode) for item in accumulated[:max_k]]
        for k in range(max_k, 0, -1):
            if left[-k:] == right[:k]:
                if k > best_k:
                    best_k = k
                    best_mode = mode
                break
    return best_k, best_mode


def merge_window(
    accumulated: list[dict[str, Any]],
    window: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """把新窗口拼到已合并序列上。

    微信窗口是连续切片；向上滚时，新窗口的前半段是更早的消息，
    后半段通常和已合并序列的头部重叠。
    """
    if not accumulated:
        return window, {"overlap": 0, "mode": "init", "confidence": "high"}
    if not window:
        return accumulated, {"overlap": 0, "mode": "empty", "confidence": "high"}

    overlap, overlap_mode = suffix_prefix_overlap(window, accumulated)
    if overlap:
        added = window[:-overlap]
        if not added:
            return accumulated, {
                "overlap": overlap,
                "mode": f"suffix:{overlap_mode}",
                "confidence": "high",
            }
        return added + accumulated, {
            "overlap": overlap,
            "mode": f"suffix:{overlap_mode}",
            "confidence": "high" if overlap >= 2 else "low",
        }

    best: tuple[Any, str] | None = None
    for mode in ("strong", "context", "weak"):
        left = [signature(item, mode) for item in window]
        right = [signature(item, mode) for item in accumulated]
        matcher = SequenceMatcher(None, left, right, autojunk=False)
        blocks = [block for block in matcher.get_matching_blocks() if block.size]
        if not blocks:
            continue
        block = max(blocks, key=lambda item: (item.size, -item.b))
        if best is None or block.size > best[0].size:
            best = (block, mode)

    if best is None:
        # 接不上：按“向上滚”的方向假定新窗口更早，拼到前面并标记 gap
        return window + accumulated, {"overlap": 0, "mode": "none", "confidence": "gap"}

    block, mode = best
    added = window[: block.a]
    if not added:
        return accumulated, {"overlap": block.size, "mode": mode, "confidence": "high"}

    # 正常情况重叠应发生在已合并序列的头部（block.b 接近 0）
    confidence = "high" if block.size >= 2 and block.b <= 2 else "low"
    return added + accumulated, {
        "overlap": block.size,
        "mode": mode,
        "confidence": confidence,
        "anchor_at": block.b,
    }


def claim_kind(record: dict[str, Any]) -> str | None:
    content = normalize_text(record.get("content"))
    class_name = str(record.get("class") or "")
    if "Quote" in class_name and content.startswith("1"):
        return "quote"
    if content in CLAIM_TEXTS:
        return "text"
    return None


def assign_times(records: list[dict[str, Any]]) -> None:
    """按整条时间线回填消息时间。

    单次窗口里，时间分隔条之前的消息可能拿不到锚点；
    合并后它们前面通常已经出现了更早的时间分隔条，这里统一补上。
    """
    current_text: str | None = None
    current_dt: datetime | None = None
    for record in records:
        if record.get("is_time"):
            raw_time = record.get("resolved")
            if raw_time:
                current_dt = datetime.fromisoformat(raw_time)
                current_text = str(record.get("content") or "") or None
            continue
        if current_dt is not None:
            record["resolved"] = current_dt.isoformat()
            record["anchor"] = current_text
        # 整条时间线第一个时间分隔条之前的消息，保持 unknown


def safe_name(raw: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", raw)
    return (cleaned[:40] or "group").strip(" .") or "group"


def scroll_to_bottom(wx: WeChat, *, max_steps: int = 600, label: str = "") -> bool:
    """持续向下滚，直到最后一条消息连续多次不变。

    微信会记住每个群上次的滚动位置，固定滚几十下不一定到底；
    对回捞来说，起点不在最新会导致“提前以为到目标时间”。
    """
    last_sig: tuple[str, str, str] | None = None
    stable = 0
    for step in range(1, max_steps + 1):
        try:
            wx.ChatBox.msgbox.WheelDown()
        except Exception:  # noqa: BLE001
            return False
        time.sleep(0.01)
        if step % 10:
            continue
        try:
            messages = wx.GetAllMessage()
        except Exception:  # noqa: BLE001
            continue
        if not messages:
            continue
        last = messages[-1]
        sig = (
            type(last).__name__,
            str(getattr(last, "content", "") or ""),
            str(getattr(last, "id", "") or ""),
        )
        if sig == last_sig:
            stable += 1
        else:
            stable = 0
            last_sig = sig
        if stable >= 3:
            if label:
                print(f"  {label}: 已回到底部（{step} 步）", flush=True)
            return True
    return False


def scan_group(
    wx: WeChat,
    group: str,
    since: datetime,
    until: datetime,
    args: argparse.Namespace,
    image_saver: ImageSaver | None = None,
) -> dict[str, Any]:
    started = time.time()
    result: dict[str, Any] = {
        "group": group,
        "chat_info": {},
        "error": "",
        "rounds": 0,
        "bottom_reached": False,
        "window_sizes": [],
        "merged_count": 0,
        "in_range_count": 0,
        "unknown_time_count": 0,
        "earliest": None,
        "latest": None,
        "visible_from": None,
        "visible_to": None,
        "join_notice_at": None,
        "coverage_ok": False,
        "coverage_note": "",
        "reached_since": False,
        "types_all": {},
        "types_in_range": {},
        "claims": [],
        "gaps": [],
        "elapsed_sec": 0,
    }
    raw_windows: list[dict[str, Any]] = []
    accumulated: list[dict[str, Any]] = []

    try:
        wx.ChatWith(group, exact=True)
        time.sleep(0.8)
        chat_info = wx.ChatInfo()
        result["chat_info"] = chat_info
        if str(chat_info.get("chat_name", "") or "") != group:
            result["error"] = f"切换后群名不匹配：{chat_info.get('chat_name')!r}"
            return result

        # 先确认回到最新位置，避免微信记住上次的滚动位置
        result["bottom_reached"] = scroll_to_bottom(
            wx, max_steps=args.bottom_steps, label=group
        )
        if not result["bottom_reached"]:
            result["error"] = "未确认回到底部，结果可能不完整"
        time.sleep(0.2)

        stall = 0
        last_len = 0
        while result["rounds"] < args.max_rounds:
            now = local_now()
            window = read_window(wx, now, image_saver)
            result["window_sizes"].append(len(window))
            if args.save_raw:
                raw_windows.append({"round": result["rounds"], "messages": window})

            accumulated, meta = merge_window(accumulated, window)
            assign_times(accumulated)
            if meta.get("confidence") in {"gap", "low"}:
                result["gaps"].append({"round": result["rounds"], **meta})

            resolved = [
                datetime.fromisoformat(item["resolved"])
                for item in accumulated
                if item.get("resolved")
            ]
            if resolved:
                earliest = min(resolved)
                latest = max(resolved)
                result["earliest"] = earliest.isoformat()
                result["latest"] = latest.isoformat()
                if earliest <= since:
                    result["reached_since"] = True
                    break

            if len(accumulated) <= last_len:
                stall += 1
            else:
                stall = 0
            last_len = len(accumulated)
            if stall >= args.stall_limit:
                result["error"] = result["error"] or "连续多轮没有新增，可能已到顶或窗口无法继续滚动"
                break

            try:
                steps = args.wheel_steps
                if args.adaptive:
                    if len(window) <= 4:
                        steps = 1
                    elif len(window) <= 8:
                        steps = min(steps, 2)
                    else:
                        steps = min(steps, 3)
                    if result["gaps"] and result["gaps"][-1].get("round") == result["rounds"]:
                        steps = 1
                steps = max(1, min(steps, args.wheel_steps))
                result.setdefault("steps_per_round", []).append(steps)
                for _ in range(steps):
                    wx.ChatBox.msgbox.WheelUp()
                    time.sleep(0.12)
            except Exception as exc:  # noqa: BLE001
                result["error"] = f"向上滚动失败：{type(exc).__name__}: {exc}"
                break
            time.sleep(0.35)
            result["rounds"] += 1
            if result["rounds"] % 10 == 0:
                print(
                    f"  {group}: rounds={result['rounds']} merged={len(accumulated)} "
                    f"steps={steps} earliest={(result['earliest'] or '-')[:19]}",
                    flush=True,
                )

        result["merged_count"] = len(accumulated)
        result["types_all"] = dict(Counter(str(item.get("class")) for item in accumulated))

        in_range: list[dict[str, Any]] = []
        unknown = 0
        for item in accumulated:
            raw_time = item.get("resolved")
            if item.get("is_time"):
                continue
            if not raw_time:
                unknown += 1
                continue
            value = datetime.fromisoformat(raw_time)
            if since <= value < until:
                in_range.append(item)
        result["in_range_count"] = len(in_range)
        result["unknown_time_count"] = unknown
        result["types_in_range"] = dict(Counter(str(item.get("class")) for item in in_range))

        claims = []
        for index, item in enumerate(accumulated):
            kind = claim_kind(item)
            if not kind:
                continue
            value = item.get("resolved")
            if not value:
                continue
            value_dt = datetime.fromisoformat(value)
            if since <= value_dt < until:
                claims.append(
                    {
                        "seq": index,
                        "group": group,
                        "kind": kind,
                        "sender": item.get("sender") or "",
                        "content": item.get("content") or "",
                        "resolved": value,
                        "class": item.get("class") or "",
                    }
                )
        result["claims"] = claims
        result["claim_count"] = len(claims)
        result["claim_text_count"] = sum(1 for item in claims if item["kind"] == "text")
        result["claim_quote_count"] = sum(1 for item in claims if item["kind"] == "quote")

        resolved_all = [
            datetime.fromisoformat(item["resolved"])
            for item in accumulated
            if item.get("resolved")
        ]
        if resolved_all:
            visible_from = min(resolved_all)
            visible_to = max(resolved_all)
            result["visible_from"] = visible_from.isoformat()
            result["visible_to"] = visible_to.isoformat()
            result["coverage_ok"] = visible_from <= since
        result["coverage_note"] = (
            "已覆盖目标区间"
            if result["coverage_ok"]
            else "仅覆盖本机可见起点之后；目标起点更早，无法证明完整"
        )
        for item in accumulated:
            content = str(item.get("content") or "")
            is_self_join = (
                content.startswith("你")
                and ("加入群聊" in content or "扫码" in content)
                and "邀请" not in content
            )
            if item.get("is_system") and is_self_join:
                if item.get("resolved"):
                    result["join_notice_at"] = item["resolved"]
                break

        # 保存合并后的消息和“扣 1”候选，便于人工核对
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_group = safe_name(group)
        messages_path = out_dir / f"messages_{safe_group}.jsonl"
        with messages_path.open("w", encoding="utf-8") as handle:
            for index, item in enumerate(accumulated):
                payload = {"group": group, "seq": index, **item}
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

        claims_path = out_dir / "claims.csv"
        write_header = not claims_path.exists()
        with claims_path.open("a", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["group", "seq", "kind", "sender", "resolved", "content", "class"],
            )
            if write_header:
                writer.writeheader()
            writer.writerows(claims)

        if args.save_raw:
            raw_dir = out_dir / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            raw_path = raw_dir / f"{safe_group}.jsonl"
            with raw_path.open("w", encoding="utf-8") as handle:
                for row in raw_windows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
    finally:
        # 每个群开始时会先回到底部，收尾再滚一次只会多一个卡死点
        result["elapsed_sec"] = round(time.time() - started, 1)
        sizes = result["window_sizes"]
        result["window_min"] = min(sizes) if sizes else 0
        result["window_max"] = max(sizes) if sizes else 0
        result["window_avg"] = round(sum(sizes) / len(sizes), 1) if sizes else 0
        result.pop("window_sizes", None)

    return result


def read_groups(args: argparse.Namespace) -> list[str]:
    groups = [str(item).strip() for item in (args.groups or []) if str(item).strip()]
    if groups:
        return groups
    path = Path(args.groups_file)
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        result.append(line)
    return result


def render_summary(
    results: list[dict[str, Any]],
    since: datetime,
    until: datetime,
    out_dir: Path,
) -> str:
    lines = [
        "微信多群历史回捞探针",
        f"运行时间：{local_now().isoformat(timespec='seconds')}",
        f"目标区间：{since.isoformat(timespec='seconds')} ~ {until.isoformat(timespec='seconds')}",
        f"输出目录：{out_dir.resolve()}",
        "",
    ]
    for result in results:
        lines.append(f"=== {result['group']} ===")
        if result.get("chat_info"):
            lines.append(
                "群信息：{} / {} 人".format(
                    result["chat_info"].get("chat_name", "-"),
                    result["chat_info"].get("group_member_count", "-"),
                )
            )
        lines.append(
            "滚动：{} 轮，窗口条数 min/max/avg = {}/{}/{}".format(
                result.get("rounds", 0),
                result.get("window_min", 0),
                result.get("window_max", 0),
                result.get("window_avg", 0),
            )
        )
        lines.append(
            "合并：{} 条，目标区间内 {} 条，时间未知 {} 条".format(
                result.get("merged_count", 0),
                result.get("in_range_count", 0),
                result.get("unknown_time_count", 0),
            )
        )
        lines.append(
            "时间跨度：{} ~ {}".format(
                (result.get("earliest") or "-")[:19],
                (result.get("latest") or "-")[:19],
            )
        )
        lines.append(f"本机可见起点：{(result.get('visible_from') or '-')[:19]}")
        if result.get("join_notice_at"):
            lines.append(f"本机账号入群提示：{(result.get('join_notice_at') or '-')[:19]}")
        lines.append(f"覆盖结论：{result.get('coverage_note') or '-'}")
        lines.append(f"到达目标起点：{'是' if result.get('reached_since') else '否'}")
        lines.append(f"确认回到底部：{'是' if result.get('bottom_reached') else '否/未确认'}")
        lines.append(f"消息类型：{result.get('types_all', {})}")
        lines.append(
            "扣1候选：{} 条（纯文本 {}，引用 {}）".format(
                result.get("claim_count", 0),
                result.get("claim_text_count", 0),
                result.get("claim_quote_count", 0),
            )
        )
        lines.append(f"缺口/低置信：{len(result.get('gaps', []))} 处")
        lines.append(f"耗时：{result.get('elapsed_sec', 0)} 秒")
        if result.get("error"):
            lines.append(f"错误/提示：{result['error']}")
        lines.append("")
    text = "\n".join(lines)
    (out_dir / "summary.txt").write_text(text, encoding="utf-8")
    (out_dir / "report.json").write_text(
        json.dumps({"since": since.isoformat(), "until": until.isoformat(), "groups": results},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "gaps.json").write_text(
        json.dumps(
            {item["group"]: item.get("gaps", []) for item in results},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description="微信多群历史回捞探针（只读）")
    parser.add_argument("--groups", nargs="*", help="群名；不传则读 groups.txt")
    parser.add_argument("--groups-file", default="groups.txt", help="群名文件，一行一个")
    parser.add_argument("--since", default="yesterday", help="起始时间：yesterday / today / 2026-10-27 00:00")
    parser.add_argument("--until", default="today", help="结束时间：today / 2026-10-28 00:00")
    parser.add_argument("--max-rounds", type=int, default=2000, help="每个群最多向上滚多少轮")
    parser.add_argument("--wheel-steps", type=int, default=3, help="每轮最多滚几次滚轮")
    parser.add_argument(
        "--adaptive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="小窗口自动降为 1 步/轮，减少拼接缺口（默认开）",
    )
    parser.add_argument("--stall-limit", type=int, default=15, help="连续多少轮没有新增就停")
    parser.add_argument("--bottom-steps", type=int, default=600, help="向下滚到最新位置的最大步数")
    parser.add_argument("--save-raw", action="store_true", help="保存每一轮原始窗口，便于排查缺口")
    parser.add_argument("--out", default="", help="输出目录；默认 probe_out_时间戳")
    args = parser.parse_args()

    if not args.out:
        args.out = "probe_out_" + local_now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    now = local_now()
    try:
        since = parse_anchor_arg(args.since, now)
        until = parse_anchor_arg(args.until, now)
    except ValueError as exc:
        print(f"参数错误：{exc}")
        return 2
    if until <= since:
        print("参数错误：--until 必须晚于 --since")
        return 2

    groups = read_groups(args)
    if not groups:
        print("没有群名。请编辑 groups.txt（一行一个群名），或用 --groups 传入群名。")
        return 2

    print(f"目标区间：{since.isoformat(timespec='seconds')} ~ {until.isoformat(timespec='seconds')}")
    print(f"群数量：{len(groups)}；输出：{out_dir.resolve()}")
    print("这是只读探针：会切群、向上滚动，不会发消息。测试期间请不要操作微信。")
    print("")

    try:
        wx = WeChat(ads=False, resize=False)
        print("已挂上微信窗口，开始逐个群回捞。", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"挂微信失败：{type(exc).__name__}: {exc}")
        print("请确认：微信已登录、版本 4.1.8.x、窗口未最小化。")
        return 3

    image_saver = ImageSaver(out_dir)
    results: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        print(f"[{index}/{len(groups)}] {group}", flush=True)
        result = scan_group(wx, group, since, until, args, image_saver)
        results.append(result)
        print(
            f"  -> 合并 {result.get('merged_count', 0)} 条，"
            f"区间内 {result.get('in_range_count', 0)} 条，"
            f"扣1 {result.get('claim_count', 0)} 条，"
            f"到达起点={result.get('reached_since')}，"
            f"耗时 {result.get('elapsed_sec', 0)}s",
            flush=True,
        )
        if result.get("error"):
            print(f"  提示：{result['error']}", flush=True)
        # 每跑完一个群就落一次报告，中断也不丢
        render_summary(results, since, until, out_dir)

    if image_saver.saved or image_saver.failed:
        print(
            f"图片：已落盘 {image_saver.saved} 张，截图失败 {image_saver.failed} 张",
            flush=True,
        )
    text = render_summary(results, since, until, out_dir)
    print("")
    print(text)
    print(f"完成。把整个目录带回来：{out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已中断。已完成群的报告仍在输出目录。")
        raise SystemExit(130)
