import configparser
import tempfile
import unittest
from pathlib import Path

from scripts.replay_config import parse_workflow_config, write_replay_config


def workflow(username="account-one", course="123"):
    return f"""        run: |
          cat > config.ini <<'EOF'
          [common]
          username={username}
          password=test-password
          course_list={course}
          jobs=1
          [tiku]
          provider=AI
          key=test-key
          model=test-model
          submit=true
          cover_rate=0.9
          EOF
          python main.py -c config.ini
"""


class ReplayConfigTests(unittest.TestCase):
    def test_account_and_course_are_preserved(self):
        for account, course in (("account-one", "123"), ("account-two", "123"), ("account-one", "456")):
            with self.subTest(account=account, course=course), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "config.ini"
                original = parse_workflow_config(workflow(account, course))
                actual = write_replay_config(workflow(account, course), path)
                self.assertEqual({key: value for key, value in actual["common"].items() if key != "sequential"},
                                 dict(original["common"]))
                self.assertEqual(actual["common"]["sequential"], "true")
                self.assertEqual(actual["tiku"]["key"], "test-key")
                self.assertEqual(actual["tiku"]["cover_rate"], "0.5")
                self.assertEqual(actual["tiku"]["auto_submit"], "true")
                self.assertEqual(actual["tiku"]["ai_retry_times"], "3")
                self.assertEqual(actual["tiku"]["fallback_model"], "LongCat-2.5-Preview")
                self.assertEqual(actual["tiku"]["check_llm_connection"], "false")
                self.assertEqual(actual["tiku"]["submit"], "true")
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                loaded = configparser.ConfigParser()
                loaded.read(path)
                self.assertEqual(loaded["common"]["username"], account)

    def test_missing_config_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_workflow_config("run: python main.py")

    def test_missing_end_marker_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_workflow_config(workflow().replace("          EOF\n", ""))

    def test_empty_course_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_workflow_config(workflow(course=""))


if __name__ == "__main__":
    unittest.main()
