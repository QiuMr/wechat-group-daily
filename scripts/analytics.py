#!/usr/bin/env python3
"""Deduplicate customers and aggregate publisher / claimer performance."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

PHONE_RE = re.compile(r"^\+?\d[\d\s-]{6,}$")
UNKNOWN_PUBLISHER = "未知发布人"


def contact_key(contact: dict[str, Any]) -> str:
    value = str(
        contact.get("display") or contact.get("value") or contact.get("masked") or ""
    ).strip()
    digits = re.sub(r"\D", "", value)
    if contact.get("type") == "phone" or (PHONE_RE.match(value) and len(digits) >= 10):
        if len(digits) == 13 and digits.startswith("86"):
            digits = digits[2:]
        return "phone:" + digits
    return "wechat:" + re.sub(r"\s+", "", value).lower()


def _lead_time(lead: dict[str, Any]) -> str:
    return str(lead.get("time") or "9999")


def _sorted_occurrences(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda lead: (_lead_time(lead), str(lead.get("group_key") or ""), str(lead.get("id") or "")),
    )


def _claim_rows_for(
    lead: dict[str, Any],
    claims_by_lead: dict[tuple[str, str], list[dict[str, Any]]] | None,
) -> list[dict[str, Any]]:
    if not claims_by_lead:
        return []
    key = (str(lead.get("group_key") or ""), str(lead.get("agent_id") or lead.get("id") or ""))
    return [row for row in claims_by_lead.get(key, []) if row.get("is_claim")]


def build_customers(
    leads: list[dict[str, Any]],
    claims_by_lead: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    order: list[str] = []
    for lead in leads:
        contacts = lead.get("contacts") or []
        if not contacts:
            continue
        key = contact_key(contacts[0])
        if key not in grouped:
            order.append(key)
        grouped[key].append(lead)

    customers: list[dict[str, Any]] = []
    for key in order:
        occurrences = _sorted_occurrences(grouped[key])
        primary = occurrences[0]
        contact = (primary.get("contacts") or [{}])[0]
        claimer_map: dict[str, dict[str, Any]] = {}
        claim_rows = 0
        message_uids: set[str] = set()
        for lead in occurrences:
            rows = _claim_rows_for(lead, claims_by_lead)
            claim_rows += len(rows)
            for row in rows:
                message_uids.add(str(row.get("message_uid") or ""))
            sender_messages: dict[str, set[str]] = defaultdict(set)
            for row in rows:
                sender_messages[str(row.get("sender") or "")].add(str(row.get("message_uid") or ""))
            for claimer in lead.get("claimers") or []:
                name = str(claimer.get("name") or "")
                if not name:
                    continue
                entry = claimer_map.get(name)
                if entry is None:
                    entry = {
                        "name": name,
                        "time": claimer.get("time") or lead.get("time"),
                        "time_label": claimer.get("time_label") or "-",
                        "lead_id": lead.get("id"),
                        "group_label": lead.get("group_label") or lead.get("group"),
                        "claim_messages": set(),
                    }
                    claimer_map[name] = entry
                entry["claim_messages"].update(sender_messages.get(name, set()))
        claimers = sorted(
            claimer_map.values(),
            key=lambda item: (str(item.get("time") or "9999"), item["name"]),
        )
        for index, claimer in enumerate(claimers, start=1):
            claimer["order"] = index
            messages = claimer.pop("claim_messages", set())
            claimer["claim_messages"] = len(messages) or 1
        publishers = [str(lead.get("publisher") or UNKNOWN_PUBLISHER) for lead in occurrences]
        customers.append(
            {
                "id": "",
                "contact": {
                    "type": str(contact.get("type") or ""),
                    "display": str(contact.get("display") or contact.get("masked") or ""),
                },
                "time": primary.get("time"),
                "time_label": primary.get("time_label") or "-",
                "publisher": publishers[0],
                "group_label": primary.get("group_label") or primary.get("group") or "",
                "group_short": primary.get("group_short") or "",
                "lead_id": primary.get("id"),
                "lead_ids": [lead.get("id") for lead in occurrences],
                "occurrences": len(occurrences),
                "duplicate_count": len(occurrences) - 1,
                "other_publishers": list(dict.fromkeys(publishers[1:])),
                "claimers": claimers,
                "claim_rows": claim_rows,
                "claim_messages": len(message_uids),
                "status": "claimed" if claimers else "unclaimed",
                "note": "",
            }
        )

    customers.sort(
        key=lambda item: (
            str(item.get("time") or "9999"),
            str(item.get("group_label") or ""),
            str(item.get("lead_id") or ""),
        )
    )
    for index, customer in enumerate(customers, start=1):
        customer["id"] = f"C{index:02d}"
        notes = []
        if customer["duplicate_count"]:
            notes.append(f"重复发布 {customer['duplicate_count']} 次，合并到首发")
        if len(customer["claimers"]) > 1:
            notes.append(f"{len(customer['claimers'])} 人领取，按 {len(customer['claimers'])} 次领取计")
        customer["note"] = "；".join(notes)
    return customers


def build_people(
    customers: list[dict[str, Any]],
    orphan_claimers: Counter | dict[str, int] | None = None,
    claimer_message_uids: dict[str, set[str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    publishers: dict[str, dict[str, Any]] = {}
    claimers: dict[str, dict[str, Any]] = {}
    total_relations = sum(len(customer["claimers"]) for customer in customers)

    for customer in customers:
        publisher_name = customer["publisher"]
        publisher = publishers.setdefault(
            publisher_name,
            {
                "name": publisher_name,
                "customers": 0,
                "claimed": 0,
                "unclaimed": 0,
                "claim_relations": 0,
                "claimers": Counter(),
                "duplicates": 0,
            },
        )
        publisher["customers"] += 1
        publisher["claimed"] += 1 if customer["status"] == "claimed" else 0
        publisher["unclaimed"] += 1 if customer["status"] == "unclaimed" else 0
        publisher["claim_relations"] += len(customer["claimers"])
        for claimer in customer["claimers"]:
            publisher["claimers"][claimer["name"]] += 1
            entry = claimers.setdefault(
                claimer["name"],
                {
                    "name": claimer["name"],
                    "customers": 0,
                    "claimed": 0,
                    "claim_relations": 0,
                    "claim_messages": 0,
                    "publishers": Counter(),
                    "self_claims": 0,
                    "orphan_claims": 0,
                },
            )
            entry["customers"] += 1
            entry["claimed"] += 1
            entry["claim_relations"] += 1
            entry["claim_messages"] += int(claimer.get("claim_messages") or 0)
            entry["publishers"][publisher_name] += 1
            if claimer["name"] == publisher_name:
                entry["self_claims"] += 1
        for other in customer.get("other_publishers") or []:
            duplicate = publishers.setdefault(
                other,
                {
                    "name": other,
                    "customers": 0,
                    "claimed": 0,
                    "unclaimed": 0,
                    "claim_relations": 0,
                    "claimers": Counter(),
                    "duplicates": 0,
                },
            )
            duplicate["duplicates"] += 1

    for name, count in (orphan_claimers or {}).items():
        entry = claimers.setdefault(
            name,
            {
                "name": name,
                "customers": 0,
                "claimed": 0,
                "claim_relations": 0,
                "claim_messages": 0,
                "publishers": Counter(),
                "self_claims": 0,
                "orphan_claims": 0,
            },
        )
        entry["orphan_claims"] += int(count)

    for name, uids in (claimer_message_uids or {}).items():
        entry = claimers.get(name)
        if entry is not None:
            entry["claim_messages"] = len(uids)

    publisher_rows = []
    for publisher in publishers.values():
        top_claimers = [
            {"name": name, "count": count}
            for name, count in publisher["claimers"].most_common(3)
        ]
        publisher_rows.append(
            {
                "name": publisher["name"],
                "customers": publisher["customers"],
                "claimed": publisher["claimed"],
                "unclaimed": publisher["unclaimed"],
                "claim_relations": publisher["claim_relations"],
                "claim_share": round(publisher["claimed"] * 100 / publisher["customers"])
                if publisher["customers"]
                else 0,
                "top_claimers": top_claimers,
                "duplicates": publisher["duplicates"],
            }
        )
    publisher_rows.sort(key=lambda row: (-row["customers"], -row["claimed"], row["name"]))

    claimer_rows = []
    for claimer in claimers.values():
        top_publishers = [
            {"name": name, "count": count}
            for name, count in claimer["publishers"].most_common(3)
        ]
        preference = None
        if top_publishers:
            top = top_publishers[0]
            share = round(top["count"] * 100 / claimer["customers"]) if claimer["customers"] else 0
            preference = {
                "publisher": top["name"],
                "count": top["count"],
                "share": share,
                "text": f"{share}% 来自 {top['name']}",
            }
        claimer_rows.append(
            {
                "name": claimer["name"],
                "customers": claimer["customers"],
                "claim_relations": claimer["claim_relations"],
                "claim_messages": claimer["claim_messages"],
                "claim_share": round(claimer["claim_relations"] * 100 / total_relations)
                if total_relations
                else 0,
                "top_publishers": top_publishers,
                "preference": preference,
                "self_claims": claimer["self_claims"],
                "orphan_claims": claimer["orphan_claims"],
            }
        )
    claimer_rows.sort(key=lambda row: (-row["customers"], -row["claim_messages"], row["name"]))
    return publisher_rows, claimer_rows


def build_affinities(customers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs: Counter[tuple[str, str]] = Counter()
    publisher_claimed: Counter[str] = Counter()
    publisher_total: Counter[str] = Counter()
    claimer_total: Counter[str] = Counter()
    for customer in customers:
        publisher = customer["publisher"]
        publisher_total[publisher] += 1
        if customer["claimers"]:
            publisher_claimed[publisher] += 1
        for claimer in customer["claimers"]:
            pairs[(publisher, claimer["name"])] += 1
            claimer_total[claimer["name"]] += 1

    rows = []
    for (publisher, claimer), count in pairs.items():
        rows.append(
            {
                "publisher": publisher,
                "claimer": claimer,
                "customers": count,
                "share_of_publisher_claimed": round(count * 100 / publisher_claimed[publisher])
                if publisher_claimed[publisher]
                else 0,
                "share_of_publisher_total": round(count * 100 / publisher_total[publisher])
                if publisher_total[publisher]
                else 0,
                "share_of_claimer": round(count * 100 / claimer_total[claimer])
                if claimer_total[claimer]
                else 0,
                "text": f"{claimer} 领了 {publisher} 的 {count} 个客户"
                f"（占 {publisher} 已领 {round(count * 100 / publisher_claimed[publisher]) if publisher_claimed[publisher] else 0}%）",
            }
        )
    rows.sort(key=lambda row: (-row["customers"], row["publisher"], row["claimer"]))
    return rows


def build_analytics(
    leads: list[dict[str, Any]],
    claims_by_lead: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
    orphan_claimers: Counter | dict[str, int] | None = None,
) -> dict[str, Any]:
    customers = build_customers(leads, claims_by_lead)
    claimer_message_uids: dict[str, set[str]] = defaultdict(set)
    for rows in (claims_by_lead or {}).values():
        for row in rows:
            name = str(row.get("sender") or "")
            if name:
                claimer_message_uids[name].add(str(row.get("message_uid") or ""))
    publishers, claimers = build_people(customers, orphan_claimers, claimer_message_uids)
    affinities = build_affinities(customers)
    claimed = sum(1 for customer in customers if customer["status"] == "claimed")
    unclaimed = len(customers) - claimed
    return {
        "customers": customers,
        "publishers": publishers,
        "claimers": claimers,
        "affinities": affinities,
        "unique_customers": len(customers),
        "claimed_customers": claimed,
        "unclaimed_customers": unclaimed,
        "claim_relations": sum(len(customer["claimers"]) for customer in customers),
        "multi_claimer_customers": sum(
            1 for customer in customers if len(customer["claimers"]) >= 2
        ),
        "duplicate_posts": sum(customer["duplicate_count"] for customer in customers),
        "publisher_count": len(publishers),
        "claimer_count": len(claimers),
    }
