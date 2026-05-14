from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.recipient_groups import load_recipient_groups, resolve_emails


class RecipientGroupTests(unittest.TestCase):
    def test_load_recipient_groups_normalizes_and_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "groups.json"
            path.write_text(
                '{"groups":{"team":[" Alice@example.com ","bad","bob@example.com"],"empty":[]}}',
                encoding="utf-8",
            )

            groups = load_recipient_groups(path)

            self.assertEqual(groups, {"team": ["alice@example.com", "bob@example.com"]})

    def test_resolve_emails_merges_direct_group_and_global(self) -> None:
        subscription = {
            "emails": ["direct@example.com", "Direct@example.com", "bad"],
            "recipient_group": "team",
            "recipient_groups": ["ops"],
        }
        groups = {
            "team": ["alice@example.com"],
            "ops": ["bob@example.com"],
        }

        with patch.dict(os.environ, {"GLOBAL_RECIPIENTS": "global@example.com,invalid"}, clear=False):
            resolved = resolve_emails(subscription, groups)

        self.assertEqual(
            resolved,
            ["alice@example.com", "bob@example.com", "direct@example.com", "global@example.com"],
        )


if __name__ == "__main__":
    unittest.main()
