#!/usr/bin/env python3

import os
import json
import re
from pathlib import Path

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def load_recipient_groups(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}

    data = json.loads(path.read_text(encoding="utf-8"))
    groups = data.get("groups", {})
    if not isinstance(groups, dict):
        raise SystemExit(f"Invalid groups object in {path}")

    normalized: dict[str, list[str]] = {}
    for name, emails in groups.items():
        if not isinstance(name, str) or not isinstance(emails, list):
            continue
        cleaned = sorted(
            {
                email.strip().lower()
                for email in emails
                if isinstance(email, str) and EMAIL_PATTERN.fullmatch(email.strip().lower())
            }
        )
        if cleaned:
            normalized[name] = cleaned
    return normalized

def resolve_emails(subscription: dict, groups: dict[str, list[str]]) -> list[str]:
    direct_emails = sorted(
        {
            email.strip().lower()
            for email in subscription.get("emails", [])
            if isinstance(email, str) and EMAIL_PATTERN.fullmatch(email.strip().lower())
        }
    )

    group_names: list[str] = []
    group_name = subscription.get("recipient_group")
    if isinstance(group_name, str) and group_name.strip():
        group_names.append(group_name.strip())

    extra_group_names = subscription.get("recipient_groups", [])
    if isinstance(extra_group_names, list):
        group_names.extend(
            name.strip()
            for name in extra_group_names
            if isinstance(name, str) and name.strip()
        )

    group_emails: set[str] = set()
    for name in group_names:
        group_emails.update(groups.get(name, []))

    resolved = set(direct_emails) | group_emails
    
    # Global recipients from environment variable (comma-separated)
    global_recipients = os.environ.get("GLOBAL_RECIPIENTS", "")
    if global_recipients:
        for email in global_recipients.split(","):
            email = email.strip().lower()
            if EMAIL_PATTERN.fullmatch(email):
                resolved.add(email)

    return sorted(resolved)
