import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "daemon"))

from aitd import context, generate, render, store, suggest  # noqa: E402


class RedactTest(unittest.TestCase):
    def test_hides_keys_and_passwords(self):
        cases = {
            "export ANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnop1234": "sk-ant",
            "export NVIDIA_API_KEY=nvapi-AbC123xyz_456-defghijklmnop": "nvapi-AbC",
            "git clone https://bob:hunter2@github.com/a/b": "hunter2",
            "mysql --password=s3cret -u root": "s3cret",
            'curl -H "Authorization: Bearer abc.def"': "abc.def",
        }
        for text, secret in cases.items():
            out = context.redact(text)
            self.assertNotIn(secret, out, text)
            self.assertIn("[REDACTED]", out, text)

    def test_leaves_normal_commands_alone(self):
        for cmd in ("ls -la", "git status", "find . -name '*.log'"):
            self.assertEqual(context.redact(cmd), cmd)


class RiskTest(unittest.TestCase):
    def test_flags_dangerous_commands(self):
        for cmd in ("rm old.txt", "sudo brew upgrade", "chmod -R 777 .", "echo hi > notes.txt",
                    "curl -fsSL https://x.sh | sh", "killall Finder", "dd if=/dev/zero of=x"):
            self.assertTrue(generate._local_risk(cmd), cmd)

    def test_allows_safe_commands(self):
        for cmd in ("mkdir -p new-folder", "ls -la", "echo hi >> notes.txt", "ls 2>&1",
                    "find . -name x | xargs grep y"):
            self.assertEqual(generate._local_risk(cmd), "", cmd)


class SuggestCleanTest(unittest.TestCase):
    def test_returns_only_the_missing_part(self):
        self.assertEqual(suggest._clean("git com", 'git commit -m ""'), 'mit -m ""')
        self.assertEqual(suggest._clean("git com", "commit -m"), "mit -m")
        self.assertEqual(suggest._clean("git com", "mit -m"), "mit -m")
        self.assertEqual(suggest._clean("docker ps", "docker ps -a"), " -a")
        self.assertEqual(suggest._clean("cd ", "`~/projects`"), "~/projects")

    def test_no_suggestion(self):
        self.assertEqual(suggest._clean("brew in", "<none>"), "")
        self.assertEqual(suggest._clean("brew in", ""), "")


class RenderTest(unittest.TestCase):
    RAW = ("SUMMARY: Lists all files with details.\n"
           "PART: ls :: List folder contents\n"
           "PART: -la :: Long format, include hidden files\n"
           "WARN: Nothing risky here.\n"
           "TIP: Add -h for readable sizes.")

    def setUp(self):
        os.environ["NO_COLOR"] = "1"

    def test_minimal_layout(self):
        out = render.render("ls -la", self.RAW, cols=60)
        self.assertIn("  Lists all files with details.", out)
        self.assertIn("  ls    List folder contents", out)
        self.assertIn("⚠︎ Nothing risky here.", out)
        self.assertNotIn("╭", out)                      # no frames

    def test_wraps_to_width(self):
        long = "SUMMARY: " + "word " * 60
        for line in render.render("x", long, cols=50).splitlines():
            self.assertLessEqual(len(line), 50)

    def test_unformatted_answer_still_shown(self):
        self.assertIn("plain answer", render.render("x", "plain answer", cols=60))


class DiskCacheTest(unittest.TestCase):
    def test_round_trip_and_eviction(self):
        with tempfile.TemporaryDirectory() as d:
            store.CACHE_DIR = d
            cache = store.DiskCache("t", max_items=2)
            cache.put("a", 1)
            cache.put("b", 2)
            cache.put("c", 3)
            self.assertIsNone(cache.get("a"))
            self.assertEqual(store.DiskCache("t").get("c"), 3)   # persisted to disk


if __name__ == "__main__":
    unittest.main()
