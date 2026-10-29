#!/usr/bin/env python3
"""Validate Doubao's lead/claim/image JSONL without judging semantics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from common import date_label, read_json, resolve_date


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    if not path.exists():
        return rows, [f"{path.name} 不存在"]
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except ValueError as exc:
                errors.append(f"{path.name} 第 {line_no} 行不是合法 JSON：{exc}")
                continue
            if not isinstance(value, dict):
                errors.append(f"{path.name} 第 {line_no} 行不是对象")
                continue
            rows.append(value)
    return rows, errors


def load_group(work: Path, group: dict[str, Any]) -> dict[str, Any]:
    key = str(group["key"])
    folder = work / key
    raw_path = work / "raw" / f"messages_{key}.jsonl"
    raw_rows, raw_errors = load_jsonl(raw_path)
    raw_by_uid = {str(row.get("uid")): row for row in raw_rows}
    leads, lead_errors = load_jsonl(folder / "leads.jsonl")
    claims, claim_errors = load_jsonl(folder / "claims.jsonl")
    images, image_errors = load_jsonl(folder / "images.jsonl")
    progress = read_json(folder / "progress.json", {}) or {}
    return {
        "group": group,
        "folder": folder,
        "raw_by_uid": raw_by_uid,
        "raw_count": len(raw_rows),
        "leads": leads,
        "claims": claims,
        "images": images,
        "progress": progress,
        "errors": raw_errors + lead_errors + claim_errors + image_errors,
        "warnings": [],
    }


def check_lead(
    row: dict[str, Any],
    group_key: str,
    raw_by_uid: dict[str, dict[str, Any]],
    seen_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    lead_id = str(row.get("lead_id") or "")
    if not lead_id:
        errors.append("leads.jsonl 有一条缺少 lead_id")
    elif lead_id in seen_ids:
        errors.append(f"线索编号重复：{lead_id}")
    else:
        seen_ids.add(lead_id)
    if str(row.get("group_key") or "") != group_key:
        errors.append(f"{lead_id} 的 group_key 不是 {group_key}")
    contact = row.get("contact")
    if not isinstance(contact, dict):
        errors.append(f"{lead_id} 缺少 contact 对象")
    elif not str(contact.get("value") or "").strip():
        errors.append(f"{lead_id} 的 contact.value 为空")
    if row.get("contacts"):
        errors.append(f"{lead_id} 用了 contacts 数组；一条线索只能有一个联系方式")
    for field in ("source_message_uids", "evidence_uids"):
        values = row.get(field)
        if field == "source_message_uids" and not values:
            errors.append(f"{lead_id} 缺少 source_message_uids")
        if values is not None and not isinstance(values, list):
            errors.append(f"{lead_id} 的 {field} 不是数组")
            continue
        for uid in values or []:
            if str(uid) not in raw_by_uid:
                errors.append(f"{lead_id} 引用了不存在的消息：{uid}")
    if str(row.get("status") or "") not in {"claimed", "unclaimed", "uncertain"}:
        errors.append(f"{lead_id} 的 status 必须是 claimed/unclaimed/uncertain")
    if row.get("source_type") and str(row["source_type"]) not in {"text", "image", "mixed"}:
        errors.append(f"{lead_id} 的 source_type 不合法")
    return errors


def check_claim(
    row: dict[str, Any],
    group_key: str,
    raw_by_uid: dict[str, dict[str, Any]],
    lead_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    claim_id = str(row.get("claim_id") or "")
    if not claim_id:
        errors.append("claims.jsonl 有一条缺少 claim_id")
    if str(row.get("group_key") or "") != group_key:
        errors.append(f"{claim_id} 的 group_key 不是 {group_key}")
    uid = str(row.get("message_uid") or "")
    if uid not in raw_by_uid:
        errors.append(f"{claim_id} 引用了不存在的消息：{uid}")
    is_claim = row.get("is_claim")
    if not isinstance(is_claim, bool):
        errors.append(f"{claim_id} 的 is_claim 必须是 true/false")
    target = row.get("target_lead_id")
    if target and str(target) not in lead_ids:
        errors.append(f"{claim_id} 指向不存在的线索：{target}")
    status = str(row.get("status") or "")
    if is_claim:
        if status not in {"matched", "orphan", "uncertain"}:
            errors.append(f"{claim_id} 的 status 必须是 matched/orphan/uncertain")
        if status == "matched" and not target:
            errors.append(f"{claim_id} 是 matched 但没有 target_lead_id")
        if status == "orphan" and target:
            errors.append(f"{claim_id} 是 orphan 但仍有 target_lead_id")
    return errors


def check_image(
    row: dict[str, Any],
    group_key: str,
    raw_by_uid: dict[str, dict[str, Any]],
    lead_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    uid = str(row.get("message_uid") or "")
    raw = raw_by_uid.get(uid)
    if raw is None:
        errors.append(f"images.jsonl 引用了不存在的消息：{uid}")
        return errors
    if raw.get("msg_type") != "图片":
        errors.append(f"{uid} 不是图片消息")
    classification = str(row.get("classification") or "")
    if classification not in {"lead", "attachment", "noise", "uncertain"}:
        errors.append(f"{uid} 的 classification 必须是 lead/attachment/noise/uncertain")
    lead_refs = row.get("lead_ids") or []
    if not isinstance(lead_refs, list):
        errors.append(f"{uid} 的 lead_ids 不是数组")
        lead_refs = []
    for lead_id in lead_refs:
        if str(lead_id) not in lead_ids:
            errors.append(f"{uid} 指向不存在的线索：{lead_id}")
    if classification == "lead" and not lead_refs:
        errors.append(f"{uid} 标成 lead，但没有 lead_ids")
    if classification == "noise" and lead_refs:
        errors.append(f"{uid} 标成 noise，却引用了线索")
    return errors


def check_group(work: Path, group: dict[str, Any]) -> dict[str, Any]:
    data = load_group(work, group)
    key = str(group["key"])
    raw_by_uid = data["raw_by_uid"]
    errors = list(data["errors"])
    warnings: list[str] = []
    lead_ids = {str(row.get("lead_id")) for row in data["leads"] if row.get("lead_id")}
    seen_lead_ids: set[str] = set()
    for row in data["leads"]:
        errors.extend(check_lead(row, key, raw_by_uid, seen_lead_ids))
    claim_ids: set[str] = set()
    for row in data["claims"]:
        claim_id = str(row.get("claim_id") or "")
        if claim_id in claim_ids:
            errors.append(f"扣 1 记录重复：{claim_id}")
        claim_ids.add(claim_id)
        errors.extend(check_claim(row, key, raw_by_uid, lead_ids))

    raw_images = {
        uid for uid, row in raw_by_uid.items() if row.get("msg_type") == "图片"
    }
    checked_images = set()
    for row in data["images"]:
        uid = str(row.get("message_uid") or "")
        checked_images.add(uid)
        errors.extend(check_image(row, key, raw_by_uid, lead_ids))
    missing_images = raw_images - checked_images
    for uid in sorted(missing_images):
        errors.append(f"{uid} 是图片，但 images.jsonl 没有判断")

    total_slices = int(data["progress"].get("total_slices") or 0)
    done_slices = data["progress"].get("done_slices") or []
    if total_slices and len(set(map(str, done_slices))) < total_slices:
        warnings.append(f"{key} 还有 {total_slices - len(set(map(str, done_slices)))} 片没标记完成")

    if not data["leads"]:
        warnings.append(f"{key} 还没有线索")
    if not data["claims"]:
        warnings.append(f"{key} 还没有扣 1 记录")

    return {
        "group": key,
        "label": f"{group.get('short')} · {group.get('display_name')}",
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "messages": data["raw_count"],
            "leads": len(data["leads"]),
            "claims": len(data["claims"]),
            "images": len(data["images"]),
            "raw_images": len(raw_images),
        },
        "progress": {
            "total_slices": total_slices,
            "done_slices": len(set(map(str, done_slices))),
            "status": data["progress"].get("status"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="校验豆包整理的 leads/claims/images")
    parser.add_argument("--work", required=True)
    parser.add_argument("--group", default="", help="只校验某个群 key；不传校验全部")
    args = parser.parse_args()

    work = Path(args.work).expanduser().resolve()
    index = read_json(work / "index.json", {}) or {}
    if not index:
        print(json.dumps({"ok": False, "errors": [f"读不到工作台：{work / 'index.json'}"]}, ensure_ascii=False, indent=2))
        return 1
    groups = index.get("groups") or []
    if args.group:
        groups = [item for item in groups if str(item.get("key")) == args.group]
    results = [check_group(work, group) for group in groups]
    errors = [f"{item['group']}: {error}" for item in results for error in item["errors"]]
    warnings = [f"{item['group']}: {warning}" for item in results for warning in item["warnings"]]
    payload = {
        "ok": not errors,
        "date": index.get("date"),
        "date_label": index.get("date_label") or date_label(resolve_date(index.get("date"))),
        "groups": results,
        "errors": errors,
        "warnings": warnings,
        "next": "修正 JSONL 后重新校验；全部通过后运行 finalize_agent.py",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
