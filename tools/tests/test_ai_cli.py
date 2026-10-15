import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from ai_cli import build_headless_command, model_from_env, normalize_provider, run_headless  # noqa: E402


class AiCliTest(unittest.TestCase):
    def test_codex_command_keeps_global_approval_flag_before_exec(self):
        cmd = build_headless_command("codex", "/bin/codex", Path("/repo"), "gpt-5.6-sol", "repair")
        self.assertEqual(cmd[:4], ["/bin/codex", "-a", "never", "exec"])
        self.assertIn("workspace-write", cmd)
        self.assertEqual(cmd[-1], "-")
        self.assertEqual(cmd[cmd.index("--model") + 1], "gpt-5.6-sol")

    def test_empty_model_means_follow_cli_default(self):
        with patch.dict(os.environ, {"AUTO_REPAIR_MODEL": ""}, clear=False):
            self.assertEqual(model_from_env("AUTO_REPAIR_MODEL", "codex"), "")
        cmd = build_headless_command("codex", "codex", Path("/repo"), "", "repair")
        self.assertNotIn("--model", cmd)

    def test_direct_codex_use_defaults_to_56_sol(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(model_from_env("AUTO_REPAIR_MODEL", "codex"), "gpt-5.6-sol")

    def test_unknown_provider_falls_back_to_claude(self):
        self.assertEqual(normalize_provider("other"), "claude")

    @patch("ai_cli.subprocess.run")
    def test_codex_receives_combined_prompt_via_stdin(self, run):
        run.return_value = SimpleNamespace(returncode=0, stdout="done", stderr="")
        code, output = run_headless(
            "codex", "codex", Path("/repo"), "gpt-5.6-sol", "repair",
            "用户任务", "强制边界", 30,
        )
        self.assertEqual((code, output), (0, "done"))
        kwargs = run.call_args.kwargs
        self.assertIn("强制边界", kwargs["input"])
        self.assertIn("用户任务", kwargs["input"])
        self.assertIsNone(kwargs["stdin"])


if __name__ == "__main__":
    unittest.main()
