import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "daemon"))

from aitd import context, generate, llm, render, store, suggest, why  # noqa: E402


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


class WhyTest(unittest.TestCase):
    SCREEN = ("% ls\nREADME.md\n"
              "% git psuh origin main\n"
              "git: 'psuh' is not a git command. See 'git --help'.\n\n"
              "The most similar command is\n\tpush\n"
              "% why\n")
    RAW = ("SUMMARY: You typed psuh instead of push.\n"
           "FIX: git push origin main :: Push with the right spelling\n"
           "TIP: Run git config --global help.autocorrect 10 to fix typos.")

    def setUp(self):
        os.environ["NO_COLOR"] = "1"

    def test_cuts_the_output_out_of_the_screen(self):
        out = why.output_of("git psuh origin main", self.SCREEN)
        self.assertTrue(out.startswith("git: 'psuh' is not a git command"))
        self.assertIn("\tpush", out)
        self.assertNotIn("README", out)
        self.assertNotIn("% why", out)

    def test_output_is_capped(self):
        screen = "% make\n" + "\n".join(f"line {i}" for i in range(500)) + "\n% why"
        out = why.output_of("make", screen)
        self.assertEqual(len(out.splitlines()), why.MAX_LINES)
        self.assertTrue(out.endswith("line 499"))

    def test_status_meanings(self):
        self.assertIn("not found", why.status_meaning("127"))
        self.assertIn("Ctrl+C", why.status_meaning("130"))
        self.assertIn("piped", why.status_meaning("?"))

    def _ask(self, payload, raw):
        sent = {}

        async def fake(task, system, user, max_tokens=600):
            sent["user"] = user
            return raw, "test-model"
        real, llm.complete = llm.complete, fake
        try:
            return asyncio.run(why.why(payload, "/tmp", 60)), sent["user"]
        finally:
            llm.complete = real

    def test_answer_and_fix_for_the_prompt(self):
        sep = why.FIELD_SEP
        reply, user = self._ask(sep.join(["1", "git psuh origin main", "screen", self.SCREEN]),
                                self.RAW)
        text, fix = reply.split(why.FIX_SEP)
        self.assertEqual(fix, "git push origin main")
        self.assertIn("You typed psuh instead of push.", text)
        self.assertIn("git push origin main   Push with the right spelling", text)
        self.assertIn("1, error", user)
        self.assertNotIn("README", user)              # only the failed command's output

    def test_risky_fix_is_not_put_on_the_prompt(self):
        raw = "SUMMARY: The folder is in the way.\nFIX: rm -r build :: Delete the old build"
        reply, _ = self._ask(why.FIELD_SEP.join(["1", "mkdir build", "none", ""]), raw)
        self.assertNotIn(why.FIX_SEP, reply)
        self.assertIn("rm -r build", reply)           # still shown, to copy by hand

    def test_secrets_in_output_are_redacted(self):
        out = "error: bad token ghp_abcdefghijklmnopqrstuvwxyz123456"
        _, user = self._ask(why.FIELD_SEP.join(["?", "deploy", "pipe", out]), self.RAW)
        self.assertNotIn("ghp_abc", user)

    def test_needs_a_command(self):
        reply, _ = asyncio.run(why.why("0", "/tmp")), None
        self.assertTrue(reply.startswith("ERR:"))


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
