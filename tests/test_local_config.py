from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.local_config import load_local_config


class LoadLocalConfigTests(unittest.TestCase):
    def test_loads_export_lines_and_replaces_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "local_config.sh"
            config_path.write_text(
                "\n".join(
                    [
                        "# comment",
                        "export TELEGRAM_BOT_TOKEN='abc123'",
                        "PATH=/custom/bin:$PATH",
                        "EMPTY_VALUE=",
                    ]
                ),
                encoding="utf-8",
            )

            env = {"PATH": "/usr/bin"}
            result = load_local_config(config_path, env)

            self.assertIs(result, env)
            self.assertEqual(env["TELEGRAM_BOT_TOKEN"], "abc123")
            self.assertEqual(env["PATH"], "/custom/bin:/usr/bin")
            self.assertEqual(env["EMPTY_VALUE"], "")

    def test_missing_file_is_a_noop(self) -> None:
        env = {"PATH": "/usr/bin"}
        result = load_local_config(Path("/tmp/does-not-exist"), env)
        self.assertEqual(result["PATH"], "/usr/bin")


if __name__ == "__main__":
    unittest.main()
