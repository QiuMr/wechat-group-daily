#!/usr/bin/env python3
"""Merge Doubao's structured decisions into facts.json, HTML and Excel."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter
from datetime import date as date_cls, datetime
from pathlib import Path
from typing import Any

import report as render_kit
from analytics import build_analytics
from check_group import check_group, load_jsonl
from targets import build_for_date
from common import (
    SKILL,
    data_home,
    date_label,
    expected_groups,
    load_config,
    log_path,
    now_text,
    relative_prefix,
    resolve_date,
    status_path,
    summary_path,
    write_json,
)
from run_daily import build_summary

PHONE_RE = re.compile(r"^\+?\d[\d\s-]{6,}$")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def hm(value: datetime | None) -> str:
    return value.strftime("%H:%M") if value else "-"


def raw_time(row: dict[str, Any]) -> str:
    return str(row.get("resolved") or "")


def raw_text(row: dict[str, Any]) -> str:
    if row.get("msg_type") == "图片":
        return "[图片]"
    return str(row.get("content") or "")


def contact_kind(value: str, declared: str = "") -> str:
    if declared in {"phone", "wechat", "other"}:
        return declared
    digits = re.sub(r"\D", "", value)
    if PHONE_RE.match(value) and len(digits) >= 10:
        return "phone"
    return "wechat"


def copy_image(raw: dict[str, Any], out: Path, group_key: str) -> tuple[str, str] | None:
    raw_path = str(raw.get("image_abs") or "").strip()
    if not raw_path:
        return None
    source = Path(raw_path)
    if not source.is_file() or source.stat().st_size == 0:
        return None
    images_dir = out / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    uid_name = str(raw.get("uid") or source.stem).replace(":", "_").replace("\\", "_").replace("/", "_")
    dest = images_dir / f"{group_key}_{uid_name}_{source.name}"
    if not dest.exists():
        shutil.copy2(source, dest)
    return f"images/{dest.name}", str(dest.resolve())


def normalize_followups(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, dict):
            result.append(
                {
                    "sender": str(item.get("sender") or ""),
                    "time_label": str(item.get("time_label") or item.get("time") or ""),
                    "text": str(item.get("text") or ""),
                }
            )
        elif str(item).strip():
            result.append({"sender": "", "time_label": "", "text": str(item)})
    return result


def claim_view(claim: dict[str, Any], raw_by_uid: dict[str, dict[str, Any]]) -> dict[str, Any]:
    raw = raw_by_uid.get(str(claim.get("message_uid") or ""), {})
    value = parse_dt(str(claim.get("time") or raw.get("resolved") or ""))
    return {
        "claim_id": str(claim.get("claim_id") or ""),
        "message_uid": str(claim.get("message_uid") or ""),
        "sender": str(claim.get("sender") or raw.get("sender") or ""),
        "time": value.isoformat() if value else "",
        "time_label": hm(value),
        "seq": int(raw.get("seq") or 0),
        "target_lead_id": str(claim.get("target_lead_id") or ""),
        "status": str(claim.get("status") or ""),
        "reason": str(claim.get("reason") or ""),
        "confidence": claim.get("confidence"),
        "is_claim": bool(claim.get("is_claim")),
    }


def build_lead(
    agent: dict[str, Any],
    group: dict[str, Any],
    raw_by_uid: dict[str, dict[str, Any]],
    claims: list[dict[str, Any]],
    out: Path,
    low_confidence: float,
    linked_image_uids: list[str] | None = None,
) -> dict[str, Any] | None:
    group_key = str(group["key"])
    agent_id = str(agent.get("lead_id") or "")
    source_uids = [str(item) for item in agent.get("source_message_uids") or []]
    evidence_uids = [str(item) for item in agent.get("evidence_uids") or []]
    uids = list(dict.fromkeys(source_uids + evidence_uids + list(linked_image_uids or [])))
    source_rows = [raw_by_uid[uid] for uid in uids if uid in raw_by_uid]
    if not source_rows:
        return None
    source_rows.sort(key=lambda row: int(row.get("seq") or 0))
    first = source_rows[0]
    value = parse_dt(str(agent.get("time") or first.get("resolved") or ""))
    contact = agent.get("contact") if isinstance(agent.get("contact"), dict) else {}
    contact_value = str(contact.get("value") or "").strip()
    if not contact_value:
        return None
    publisher = str(agent.get("publisher") or first.get("sender") or "")
    image_rows = [row for row in source_rows if row.get("msg_type") == "图片"]
    images: list[str] = []
    image_abs: list[str] = []
    for row in image_rows:
        copied = copy_image(row, out, group_key)
        if copied:
            images.append(copied[0])
            image_abs.append(copied[1])
    confidence = agent.get("confidence")
    try:
        confidence_value = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence_value = None
    group_leads = [item for item in claims if item["target_lead_id"] == agent_id and item["is_claim"]]
    group_leads.sort(key=lambda item: (item["time"], item["seq"], item["message_uid"]))
    claimers = []
    seen_claimers: set[str] = set()
    for item in group_leads:
        key = item["sender"] or item["message_uid"]
        if key in seen_claimers:
            continue
        seen_claimers.add(key)
        claimers.append(
            {
                "name": item["sender"],
                "time": item["time"] or None,
                "time_label": item["time_label"],
                "order": len(claimers) + 1,
            }
        )
    note_parts = []
    agent_note = str(agent.get("note") or "").strip()
    if agent_note:
        note_parts.append(agent_note)
    source_type = str(agent.get("source_type") or ("image" if image_rows else "text"))
    if source_type == "image":
        note_parts.append("图片线索")
    if confidence_value is not None and confidence_value < low_confidence:
        note_parts.append("AI 置信度较低，建议核对")
    if str(agent.get("status") or "") == "uncertain":
        note_parts.append("AI 标记为不确定")
    if image_rows and len(images) < len(image_rows):
        note_parts.append(f"有 {len(image_rows) - len(images)} 张图片未落盘")
    evidence: list[dict[str, Any]] = []
    for row in source_rows:
        absolute = ""
        relative = ""
        if row.get("msg_type") == "图片":
            copied = copy_image(row, out, group_key)
            if copied:
                relative, absolute = copied
        evidence.append(
            {
                "seq": int(row.get("seq") or 0),
                "time": row.get("resolved"),
                "time_label": row.get("time_label") or "-",
                "sender": row.get("sender") or "",
                "text": raw_text(row),
                "kind": "image" if row.get("msg_type") == "图片" else (
                    "system" if row.get("is_system") else "contact"
                ),
                **({"image": relative, "image_abs": absolute} if relative else {}),
            }
        )
    for item in group_leads:
        raw = raw_by_uid.get(item["message_uid"], {})
        evidence.append(
            {
                "seq": item["seq"],
                "time": item["time"] or None,
                "time_label": item["time_label"],
                "sender": item["sender"],
                "text": str(raw.get("content") or "领取"),
                "kind": "claim",
            }
        )
    evidence.sort(key=lambda row: (str(row.get("time") or ""), int(row.get("seq") or 0)))
    time_label = hm(value)
    return {
        "group": group["name"],
        "publisher_set": [publisher],
        "contacts": [
            {
                "type": contact_kind(contact_value, str(contact.get("type") or "")),
                "display": contact_value,
            }
        ],
        "images": images,
        "image_abs": image_abs,
        "image_count": len(image_rows),
        "image_saved": len(images),
        "claimers": claimers,
        "evidence": evidence,
        "followups": normalize_followups(agent.get("followups")),
        "seq_start": int(first.get("seq") or 0),
        "seq_end": int(source_rows[-1].get("seq") or 0),
        "time": value.isoformat() if value else None,
        "time_label": time_label,
        "publisher": publisher,
        "contact_count": 1,
        "status": "claimed" if claimers else "unclaimed",
        "claim_count": len(claimers),
        "first_claimer": claimers[0]["name"] if claimers else "",
        "claim_time_label": claimers[0]["time_label"] if claimers else "",
        "kind": "lead",
        "group_key": group_key,
        "group_short": group["short"],
        "group_display": group["display_name"],
        "group_label": f"{group['short']} · {group['display_name']}",
        "note": "；".join(dict.fromkeys(note_parts)),
        "id": "",
        "agent_id": agent_id,
        "agent_status": str(agent.get("status") or ""),
        "agent_confidence": confidence_value,
        "agent_reason": str(agent.get("reason") or ""),
        "source_type": source_type,
    }


def build_orphan(
    claim: dict[str, Any],
    group: dict[str, Any],
    raw_by_uid: dict[str, dict[str, Any]],
    raw_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    raw = raw_by_uid.get(claim["message_uid"], {})
    previous = [
        row
        for row in raw_rows
        if int(row.get("seq") or 0) <= int(raw.get("seq") or 0)
    ]
    previous = previous[-3:-1] if len(previous) >= 3 else previous[: max(0, len(previous) - 1)]
    evidence = [
        {
            "seq": int(row.get("seq") or 0),
            "time": row.get("resolved"),
            "time_label": row.get("time_label") or "-",
            "sender": row.get("sender") or "",
            "text": raw_text(row),
            "kind": "system" if row.get("is_system") else "context",
        }
        for row in previous
    ]
    evidence.append(
        {
            "seq": int(raw.get("seq") or 0),
            "time": raw.get("resolved"),
            "time_label": raw.get("time_label") or "-",
            "sender": claim["sender"],
            "text": str(raw.get("content") or "领取"),
            "kind": "claim",
        }
    )
    return {
        "id": "",
        "kind": "orphan",
        "group": group["name"],
        "time": claim["time"] or raw.get("resolved"),
        "time_label": claim["time_label"],
        "sender": claim["sender"],
        "status": "orphan",
        "note": claim["reason"] or "前面没有可见线索，或这条领取指向的线索不明确。",
        "evidence": evidence,
        "group_key": group["key"],
        "group_short": group["short"],
        "group_display": group["display_name"],
        "group_label": f"{group['short']} · {group['display_name']}",
        "claim_id": claim["claim_id"],
        "agent_confidence": claim["confidence"],
    }


def build_group_data(
    group: dict[str, Any],
    leads: list[dict[str, Any]],
    orphans: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    image_index: list[dict[str, Any]],
    image_decisions: list[dict[str, Any]],
    raw_rows: list[dict[str, Any]],
    low_confidence: float,
) -> dict[str, Any]:
    probe = group.get("probe_group") or {}
    error = str(probe.get("error") or "")
    if "连续多轮没有新增" in error:
        error = ""
    if not raw_rows:
        error = error or "未采集：目标日期没有消息"
    gaps = int(probe.get("gaps") or 0)
    reached_since = bool(probe.get("reached_since"))
    image_saved = sum(1 for item in image_index if item.get("saved"))
    low = [
        lead for lead in leads
        if lead.get("agent_confidence") is not None and lead["agent_confidence"] < low_confidence
    ]
    uncertain_images = [
        item for item in image_decisions if str(item.get("classification") or "") == "uncertain"
    ]
    reasons = []
    if error:
        reasons.append(error)
    if gaps:
        reasons.append(f"{gaps} 处合并低置信")
    if not reached_since:
        reasons.append(f"只覆盖 {group.get('coverage_from')} 以后")
    if len(image_index) > image_saved:
        reasons.append(f"{len(image_index) - image_saved} 张图片未落盘")
    if low:
        reasons.append(f"{len(low)} 条线索置信度低")
    if uncertain_images:
        reasons.append(f"{len(uncertain_images)} 张图片需核对")
    if not raw_rows:
        reasons.append("没有消息")
    if error or gaps or len(image_index) > image_saved or low or uncertain_images or not raw_rows:
        status = "review"
        status_label = "需核对"
    elif not reached_since:
        status = "partial"
        status_label = "部分"
    else:
        status = "complete"
        status_label = "完整"
    claimed = sum(1 for lead in leads if lead["status"] == "claimed")
    unclaimed = len(leads) - claimed
    claim_rows = [claim for claim in claims if claim["is_claim"]]
    claimers = set()
    for lead in leads:
        for claimer in lead["claimers"]:
            claimers.add(claimer["name"])
    for orphan in orphans:
        claimers.add(orphan["sender"])
    return {
        "key": group["key"],
        "name": group["name"],
        "display_name": group["display_name"],
        "label": f"{group['short']} · {group['display_name']}",
        "short": group["short"],
        "members": int(probe.get("members") or 0),
        "leads": len(leads),
        "claimed": claimed,
        "unclaimed": unclaimed,
        "claimers": len(claimers),
        "claims": len(claim_rows),
        "claim_messages": len({claim["message_uid"] for claim in claim_rows}),
        "orphans": len(orphans),
        "unclaimed_contacts": unclaimed,
        "coverage_from": group.get("coverage_from") or "-",
        "coverage_to": group.get("coverage_to") or "-",
        "earliest": probe.get("earliest"),
        "latest": probe.get("latest"),
        "merged_count": int(probe.get("merged_count") or 0),
        "in_range_count": int(probe.get("in_range_count") or 0),
        "gaps": gaps,
        "reached_since": reached_since,
        "image_count": len(image_index),
        "image_saved": image_saved,
        "low_confidence_count": len(low),
        "unknown_count": int(group.get("unknown_count") or 0),
        "status": status,
        "status_label": status_label,
        "status_note": "；".join(dict.fromkeys(reasons)) or "合并无缺口，豆包已整理",
        "bar": round(claimed * 100 / len(leads)) if leads else 0,
        "error": error,
    }


def build_attention(
    leads: list[dict[str, Any]],
    orphans: list[dict[str, Any]],
    issue_groups: list[dict[str, Any]],
    low_leads: list[dict[str, Any]],
    customers: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    attention = []
    if customers is not None:
        unclaimed = [customer for customer in customers if customer["status"] == "unclaimed"]
    else:
        unclaimed = [lead for lead in leads if lead["status"] == "unclaimed"]
    if unclaimed:
        groups = {item["group_label"] for item in unclaimed}
        times = "、".join(item["time_label"] for item in unclaimed[:3])
        if len(groups) == 1:
            title = f"{unclaimed[0]['group_short']} · {len(unclaimed)} 个客户没人领"
        else:
            title = f"{len(groups)} 个群 · {len(unclaimed)} 个客户没人领"
        attention.append(
            {
                "kind": "unclaimed",
                "title": title,
                "detail": f"{times}；可能私聊接了，或连续丢客只回了一个“1”，群里看不到。",
                "action": "看明细",
                "target": "unclaimed",
            }
        )
    if orphans:
        first = orphans[0]
        title = (
            f"{first['group_short']} · {first['time_label']} 有一条领取找不到线索"
            if len(orphans) == 1
            else f"{len(orphans)} 条领取找不到对应线索"
        )
        attention.append(
            {
                "kind": "orphan",
                "title": title,
                "detail": "可能是本机可见起点之前的线索，或语义判断不明确；先人工确认。",
                "action": "看证据",
                "target": first["id"],
            }
        )
    if issue_groups or low_leads:
        parts = []
        if issue_groups:
            parts.append(f"{len(issue_groups)} 个群数据需核对")
        if low_leads:
            parts.append(f"{len(low_leads)} 条线索置信度低")
        attention.append(
            {
                "kind": "review",
                "title": "；".join(parts),
                "detail": "覆盖不完整、图片未落盘或 AI 置信度低；详见数据说明。",
                "action": "看数据说明",
                "target": "notes",
            }
        )
    if not attention:
        attention.append(
            {
                "kind": "good",
                "title": "这次没有需要处理的异常",
                "detail": "所有群都有覆盖，领取都能对上；仍建议抽查原文。",
                "action": "看明细",
                "target": "detail",
            }
        )
    return attention


def build_insights(
    leads: list[dict[str, Any]],
    orphans: list[dict[str, Any]],
    issue_groups: list[dict[str, Any]],
    analytics: dict[str, Any] | None = None,
) -> list[str]:
    counter = Counter()
    for lead in leads:
        for claimer in lead["claimers"]:
            counter[claimer["name"]] += 1
    for orphan in orphans:
        counter[orphan["sender"]] += 1
    if analytics:
        unique = analytics["unique_customers"]
        claimed = analytics["claimed_customers"]
        unclaimed = analytics["unclaimed_customers"]
        relations = analytics["claim_relations"]
        messages = analytics.get("claim_messages", relations)
        multi = analytics["multi_claimer_customers"]
        insights = [
            f"去重后 {unique} 个客户：{claimed} 个有人领，{unclaimed} 个没人领；"
            f"共 {relations} 次领取（{messages} 条领取消息）"
            + (f"，{multi} 个客户被 2 人以上领取。" if multi else "。")
        ]
    elif counter:
        top = counter.most_common(3)
        insights = [
            "领取最多的是 " + "、".join(f"{name} {count} 个客户" for name, count in top) + "。"
        ]
    else:
        insights = ["这一天还没有人领取。"]

    top_claimer_name = ""
    top_preference_publisher = ""
    if analytics and analytics.get("claimers") and analytics["claim_relations"]:
        top_claimer = analytics["claimers"][0]
        top_claimer_name = top_claimer["name"]
        preference = top_claimer.get("preference")
        line = f"领得最多的是 {top_claimer['name']}（{top_claimer['customers']} 个客户"
        if top_claimer.get("claim_share"):
            line += f"，占全部领取 {top_claimer['claim_share']}%"
        line += "）"
        if preference and preference["count"] >= 2:
            top_preference_publisher = preference["publisher"]
            line += (
                f"；其中 {preference['count']} 个来自 {preference['publisher']}"
                f"（占其领取 {preference['share']}%）。"
            )
        else:
            line += "。"
        insights.append(line)
    if analytics:
        strong = [
            item for item in analytics.get("affinities") or []
            if item["customers"] >= 2 and item["share_of_claimer"] >= 50
        ]
        if strong:
            best = max(strong, key=lambda item: (item["share_of_claimer"], item["customers"]))
            if (best["claimer"], best["publisher"]) != (top_claimer_name, top_preference_publisher):
                insights.append(
                    f"偏好最明显的是 {best['claimer']}：领了 {best['publisher']} 的 "
                    f"{best['customers']} 个客户，占其领取 {best['share_of_claimer']}%。"
                )
    if analytics and analytics.get("publishers"):
        top_publisher = analytics["publishers"][0]
        insights.append(
            f"丢客最多的是 {top_publisher['name']}（{top_publisher['customers']} 个客户），"
            f"其中 {top_publisher['claimed']} 个被领取（{top_publisher['claim_share']}%）。"
        )
    if analytics and analytics.get("duplicate_posts"):
        insights.append(
            f"有 {analytics['duplicate_posts']} 次重复发布，已合并到同一客户，按首次发布人统计。"
        )

    unclaimed_leads = [lead for lead in leads if lead["status"] == "unclaimed"]
    if not analytics:
        if unclaimed_leads:
            insights.append(f"{len(unclaimed_leads)} 条线索没人扣 1；可能私聊接了，或连续丢客只回了一个“1”。")
        else:
            insights.append("所有线索在群里都有人领取。")
    elif analytics["unclaimed_customers"]:
        insights.append(
            f"{analytics['unclaimed_customers']} 个客户群里没人领；可能私聊接了，或连续丢客只回了一个“1”。"
        )
    image_leads = [lead for lead in leads if lead.get("source_type") == "image"]
    if image_leads:
        insights.append(f"其中 {len(image_leads)} 条线索来自图片，豆包已经逐张看过。")
    followups = sum(len(lead.get("followups") or []) for lead in leads)
    if followups:
        insights.append(f"群内可见 {followups} 条后续动作；仍以群内消息为准。")
    else:
        insights.append("群里没有看到“已加/拉群/带看”的后续动作；只能确认谁领取，不能确认谁接上了。")
    if issue_groups:
        insights.append(
            f"{len(issue_groups)} 个群数据需核对：" + "、".join(group["short"] for group in issue_groups) + "。"
        )
    return insights


def build_notes(
    groups: list[dict[str, Any]],
    date_value: date_cls,
    source: str,
) -> list[dict[str, str]]:
    visible = "；".join(
        f"{group['short']} {group['coverage_from']}–{group['coverage_to']}"
        for group in groups
        if group["coverage_from"] != "-"
    )
    missing = [group for group in groups if not group["reached_since"]]
    return [
        {"label": "统计范围", "value": f"{date_value.isoformat()} 00:00–24:00"},
        {"label": "本机实际可见", "value": visible or "本机没有读到群消息"},
        {
            "label": "为什么不是完整一天",
            "value": (
                "采集电脑在 "
                + "、".join(f"{group['short']} {group['coverage_from']}" for group in missing)
                + " 才可见；登录前的群历史读不到。"
                if missing
                else "本机可见范围覆盖目标起点；但仍以实际可见消息为准。"
            ),
        },
        {"label": "只统计群内", "value": "只统计群里可见的领取动作；私聊、其他群的动作看不到。"},
        {
            "label": "线索口径",
            "value": "一个联系方式算一条线索；一条消息或一张图里有多个联系方式会拆成多条；任何人发的都算。",
        },
        {
            "label": "扣 1 口径",
            "value": "由豆包按语义判断：1、我要、接了、私聊，以及其他表示领取的话；不按固定关键词硬切。",
        },
        {
            "label": "批量领取",
            "value": "同一个人连续发 2–4 条线索算一批；后面一个“1”默认领整批，同批每条都记领取；两个人各扣 1，两个人都记到整批；只有明确“只要一个/引用某条”才只领一条。",
        },
        {
            "label": "客户去重",
            "value": "按联系方式去重：同一个号码或微信号多次出现只算一个客户，归到最早一次发布；其他发布记“重复发布”，不重复计数。",
        },
        {
            "label": "领取量口径",
            "value": "一个客户被一个客服领取算 1 次；同一个客户被两个客服领取算 2 次；一个人扣一个“1”领走一批 3 个客户，算 3 次领取、1 条领取消息。",
        },
        {
            "label": "丢客与偏好",
            "value": "谁发线索谁就是丢客员；客服领取占比按“领取关系”算；“B 常领 C 的客户”看 B 领取的客户里有多少来自 C。",
        },
        {
            "label": "图片口径",
            "value": "豆包逐张看图，判断是线索、附件、噪音还是看不准；图里的联系方式会读出来；看不清的进需核对。",
        },
        {
            "label": "数据质量",
            "value": "；".join(
                f"{group['short']} 合并 {group['merged_count']} 条"
                + (f"，{group['gaps']} 处低置信" if group["gaps"] else "，无缺口")
                + (f"，图片 {group['image_saved']}/{group['image_count']} 张已落盘" if group["image_count"] else "")
                + (f"，{group['error']}" if group["error"] else "")
                for group in groups
            ),
        },
        {
            "label": "证据回填",
            "value": "页面里的原文和图片由程序按豆包写的 message_uid 从采集数据回填，避免编造证据。",
        },
        {
            "label": "发布人提示",
            "value": "config/groups.json 里的发布人只是提示，不再限制谁可以发线索。",
        },
        {
            "label": "数据来源",
            "value": f"{source}；豆包整理，Python 校验和渲染；数据只留本机。",
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="把豆包工作台合并成群日报")
    parser.add_argument("--work", required=True)
    parser.add_argument("--out", required=True, help="报告输出目录")
    parser.add_argument("--config", default=str(SKILL / "config" / "groups.json"))
    parser.add_argument("--targets-config", default=str(SKILL / "config" / "targets.json"))
    parser.add_argument("--home", default="")
    parser.add_argument("--date", default="")
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()

    work = Path(args.work).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    index = json.loads((work / "index.json").read_text(encoding="utf-8"))
    date_value = resolve_date(args.date or index.get("date"))
    if args.home:
        home = Path(args.home).expanduser().resolve()
    elif work.parent.parent.name == "reports":
        home = work.parents[2]
    else:
        home = data_home()
    config = load_config(Path(args.config))
    low_confidence = float(config.get("confidence_min") or 0.6)

    groups = index.get("groups") or []
    checked = [check_group(work, group) for group in groups]
    errors = [error for item in checked for error in item["errors"]]
    warnings = [warning for item in checked for warning in item["warnings"]]
    if errors:
        print(json.dumps({"state": "failed", "error_code": "agent_output_invalid", "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    incomplete = []
    for group in groups:
        progress = json.loads((work / group["key"] / "progress.json").read_text(encoding="utf-8"))
        total = int(progress.get("total_slices") or 0)
        done = set(map(str, progress.get("done_slices") or []))
        if total and len(done) < total:
            incomplete.append(f"{group['short']} 还差 {total - len(done)} 片")
    if incomplete and not args.allow_incomplete:
        print(
            json.dumps(
                {
                    "state": "failed",
                    "error_code": "workspace_incomplete",
                    "error": "还有切片没整理完：" + "；".join(incomplete),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    all_leads: list[dict[str, Any]] = []
    all_orphans: list[dict[str, Any]] = []
    all_claims: list[dict[str, Any]] = []
    claims_by_lead: dict[tuple[str, str], list[dict[str, Any]]] = {}
    group_data: list[dict[str, Any]] = []
    raw_by_group: dict[str, list[dict[str, Any]]] = {}
    for group in groups:
        key = str(group["key"])
        folder = work / key
        raw_rows, _ = load_jsonl(work / "raw" / f"messages_{key}.jsonl")
        raw_by_uid = {str(row.get("uid")): row for row in raw_rows}
        agent_leads, _ = load_jsonl(folder / "leads.jsonl")
        agent_claims, _ = load_jsonl(folder / "claims.jsonl")
        agent_images, _ = load_jsonl(folder / "images.jsonl")
        image_index = json.loads((folder / "image_index.json").read_text(encoding="utf-8"))
        raw_by_group[key] = raw_rows
        claims = [claim_view(item, raw_by_uid) for item in agent_claims]
        all_claims.extend(claims)
        claims_by_target: dict[str, list[dict[str, Any]]] = {}
        for claim in claims:
            if claim["is_claim"] and claim["target_lead_id"]:
                claims_by_target.setdefault(claim["target_lead_id"], []).append(claim)
                claims_by_lead.setdefault((key, claim["target_lead_id"]), []).append(claim)
        image_links: dict[str, list[str]] = {}
        for image in agent_images:
            for lead_id in image.get("lead_ids") or []:
                image_links.setdefault(str(lead_id), []).append(str(image.get("message_uid") or ""))
        group_leads = []
        for agent in agent_leads:
            lead = build_lead(
                agent,
                group,
                raw_by_uid,
                claims_by_target.get(str(agent.get("lead_id")), []),
                out,
                low_confidence,
                image_links.get(str(agent.get("lead_id")), []),
            )
            if lead:
                group_leads.append(lead)
        group_orphans = [
            build_orphan(claim, group, raw_by_uid, raw_rows)
            for claim in claims
            if claim["is_claim"] and not claim["target_lead_id"]
        ]
        all_leads.extend(group_leads)
        all_orphans.extend(group_orphans)
        group_data.append(
            build_group_data(
                group,
                group_leads,
                group_orphans,
                claims,
                image_index,
                agent_images,
                raw_rows,
                low_confidence,
            )
        )

    all_leads.sort(key=lambda lead: (lead.get("time") or "9999", lead["group_key"], lead["agent_id"]))
    for index_no, lead in enumerate(all_leads, start=1):
        lead["id"] = f"L{index_no:02d}"
    all_orphans.sort(key=lambda orphan: (orphan.get("time") or "9999", orphan["group_key"]))
    for index_no, orphan in enumerate(all_orphans, start=1):
        orphan["id"] = f"X{index_no:02d}"

    orphan_claimers = Counter(orphan["sender"] for orphan in all_orphans if orphan["sender"])
    analytics = build_analytics(all_leads, claims_by_lead, orphan_claimers)
    customers = analytics["customers"]
    publishers = analytics["publishers"]
    claimers = analytics["claimers"]
    lead_by_id = {lead["id"]: lead for lead in all_leads}
    for customer in customers:
        if not customer["duplicate_count"]:
            continue
        for position, lead_id in enumerate(customer["lead_ids"]):
            lead = lead_by_id.get(lead_id)
            if lead is None:
                continue
            suffix = (
                f"同一客户共发布 {customer['occurrences']} 次，已合并到 {customer['id']}"
                if position == 0
                else f"同一客户重复发布，已合并到 {customer['id']}"
            )
            lead["note"] = "；".join(part for part in (lead.get("note"), suffix) if part)

    issue_groups = [group for group in group_data if group["status"] != "complete"]
    low_leads = [
        lead for lead in all_leads
        if lead.get("agent_confidence") is not None and lead["agent_confidence"] < low_confidence
    ]
    claim_rows = [claim for claim in all_claims if claim["is_claim"]]
    claims_count = len(claim_rows)
    claim_message_count = len({claim["message_uid"] for claim in claim_rows})
    analytics["claim_messages"] = claim_message_count
    messages_by_claimer: dict[str, set[str]] = {}
    for claim in claim_rows:
        messages_by_claimer.setdefault(claim["sender"], set()).add(claim["message_uid"])
    for claimer in claimers:
        if claimer["name"] in messages_by_claimer:
            claimer["claim_messages"] = len(messages_by_claimer[claimer["name"]])
    review_items = len(all_orphans) + len(issue_groups) + len(low_leads)
    expected = expected_groups(config)
    if expected and len(groups) < expected:
        warnings.append(f"当前只配置 {len(groups)} 个群，预期 {expected} 个")
    for group in group_data:
        if group["status"] != "complete" and group["status_note"]:
            warnings.append(f"{group['short']}：{group['status_note']}")
    for lead in low_leads:
        warnings.append(f"{lead['id']} 置信度低：{lead['agent_reason'] or '建议人工核对'}")
    if index.get("unconfigured_groups"):
        warnings.append("有群不在配置里：" + "、".join(index["unconfigured_groups"]))
    warnings = list(dict.fromkeys(warnings))

    source_name = Path(index.get("probe") or "").name
    scope_parts = [
        f"{group['short']} {group['coverage_from']}–{group['coverage_to']}"
        for group in group_data
        if group["coverage_from"] != "-"
    ]
    scope_visible = (
        "本机实际可见：" + "、".join(scope_parts)
        if any(not group["reached_since"] for group in group_data)
        else "本机可见范围覆盖目标起点"
    )
    meta = {
        "title": f"{date_value.month} 月 {date_value.day} 日 · 群日报",
        "date": date_value.isoformat(),
        "date_label": date_label(date_value),
        "generated_at": now_text().replace("T", " ")[:16],
        "hero_prefix": relative_prefix(date_value),
        "brand_sub": f"{len(group_data)} 个群 · 豆包整理",
        "badge": "内部日报",
        "eyebrow": f"{date_label(date_value)} · 群日报",
        "scope_target": f"{date_value.isoformat()} 00:00–24:00",
        "scope_visible": scope_visible,
        "groups_foot": (
            f"{len(group_data)} 个群 · {len(all_leads)} 条线索去重后 "
            f"{analytics['unique_customers']} 个客户"
        ),
        "claims_foot": (
            f"{analytics['claim_relations']} 次领取 · {claim_message_count} 条领取消息"
            + (
                f" · {analytics['multi_claimer_customers']} 个客户多人领"
                if analytics["multi_claimer_customers"]
                else ""
            )
        ),
        "unclaimed_foot": (
            f"{analytics['unclaimed_customers']} 个客户没人领"
            + (
                f" · {analytics['duplicate_posts']} 次重复发布已合并"
                if analytics["duplicate_posts"]
                else ""
            )
        ),
        "review_foot": f"{len(all_orphans)} 条无对应线索 · {review_items} 项需核对",
        "groups_title": f"{len(group_data)} 个群",
        "insights_title": "",
        "disclaimer": f"本次共 {len(group_data)} 个群；只统计群内可见的领取；私聊领取、其他群动作看不到。",
        "footer_source": f"生成于 {now_text().replace('T', ' ')[:16]} · 豆包整理 · Python 渲染",
        "footer_note": "只读采集 · 不发送消息 · 数据只留本机",
        "export_name": f"群日报_{date_value.isoformat()}.xlsx",
        "lede": (
            f"共 {len(group_data)} 个群，{analytics['unique_customers']} 个客户"
            f"（{len(all_leads)} 条线索去重后）；"
            f"{analytics['claim_relations']} 次领取、{claim_message_count} 条领取消息"
            + (
                f"，{analytics['multi_claimer_customers']} 个客户被多人领取"
                if analytics["multi_claimer_customers"]
                else ""
            )
            + f"；{len(all_orphans)} 条领取没有对应线索。先看下面几件事，明细点开再看。"
        ),
    }
    attention = build_attention(all_leads, all_orphans, issue_groups, low_leads, customers)
    insights = build_insights(all_leads, all_orphans, issue_groups, analytics)
    meta["insights_title"] = f"豆包看到的 {len(insights)} 件事"
    data = {
        "meta": meta,
        "stats": {
            "groups": len(group_data),
            "leads": len(all_leads),
            "customers": analytics["unique_customers"],
            "unique_customers": analytics["unique_customers"],
            "claimed": analytics["claimed_customers"],
            "unclaimed": analytics["unclaimed_customers"],
            "claim_relations": analytics["claim_relations"],
            "claim_messages": claim_message_count,
            "claims": claims_count,
            "multi_claimer_customers": analytics["multi_claimer_customers"],
            "duplicate_posts": analytics["duplicate_posts"],
            "publisher_count": analytics["publisher_count"],
            "claimer_count": analytics["claimer_count"],
            "orphans": len(all_orphans),
            "review": review_items,
            "unclaimed_contacts": analytics["unclaimed_customers"],
        },
        "groups": group_data,
        "leads": all_leads,
        "orphans": all_orphans,
        "customers": customers,
        "publishers": publishers,
        "claimers": claimers,
        "affinities": analytics["affinities"],
        "attention": attention,
        "insights": insights,
        "notes": build_notes(group_data, date_value, source_name),
    }

    out.mkdir(parents=True, exist_ok=True)
    (out / "facts.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    render_kit.build_xlsx(data, out / meta["export_name"])
    render_kit.build_html(data, render_kit.DEFAULT_TEMPLATE, out / "report.html")
    try:
        target_result = build_for_date(
            date_value,
            home,
            config_path=Path(args.targets_config),
            out_dir=out,
            current_facts=out / "facts.json",
        )
    except (OSError, ValueError) as exc:
        target_result = {"state": "failed", "error": str(exc)}

    paths = {
        "report": out / "report.html",
        "xlsx": out / meta["export_name"],
        "facts": out / "facts.json",
        "summary": summary_path(date_value, home),
        "log": log_path(date_value, home),
        "probe": Path(index.get("probe") or ""),
    }
    summary = build_summary(data, date_value, paths, warnings)
    summary["targets"] = target_result
    if target_result.get("html"):
        summary["files"]["targets"] = target_result["html"]
        summary["files"]["targets_xlsx"] = target_result["xlsx"]
    summary["next"] = "打开 report，按需查看证据和导出 Excel"
    write_json(paths["summary"], summary)
    write_json(
        status_path(date_value, home),
        {
            "state": "ready",
            "date": date_value.isoformat(),
            "stage": "done",
            "finished_at": now_text(),
            "partial": summary["partial"],
            "message": "豆包整理完成，报告已生成",
            "report": str(paths["report"]),
            "xlsx": str(paths["xlsx"]),
            "targets": target_result.get("html", ""),
            "targets_xlsx": target_result.get("xlsx", ""),
            "summary": str(paths["summary"]),
            "log": str(paths["log"]),
            "warnings": warnings,
        },
    )
    index["status"] = "ready"
    index["finished_at"] = now_text()
    write_json(work / "index.json", index)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
