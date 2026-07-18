import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "oaw_research.py"
SPEC = importlib.util.spec_from_file_location("oaw_research", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HandoffPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.research_dir = Path(self.tmp.name) / "Research" / "topic"
        self.research_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def write_prompt(self, body: str) -> None:
        heading = "## Deep research prompt\n\n"
        if heading in body:
            local, prompt = body.split(heading, 1)
            body = local + heading + "```text\n" + prompt.rstrip() + "\n```\n"
        (self.research_dir / "Prompt.md").write_text(
            "---\ntype: research\nid: OAW-LOCAL-ID\n---\n\n"
            "# Prompt - Topic\n\n"
            "## Running research sessions\n\n"
            "- ChatGPT:\n\n" + body,
            encoding="utf-8",
        )

    def handoff(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "handoff", str(self.research_dir), "ChatGPT"],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_provider_prompt_text_matches_handoff_range(self):
        self.write_prompt(
            "Local-only link: [[Projects/Private/Index|OAW-index]]\n\n"
            "## Deep research prompt\n\n"
            "Research the topic for Fable operating instructions.\n"
        )

        prompt = MODULE.provider_prompt_text(self.research_dir)

        self.assertEqual(
            prompt,
            "Research the topic for Fable operating instructions.\n",
        )

    def test_handoff_allows_plain_downstream_artifact_names(self):
        self.write_prompt(
            "## Deep research prompt\n\n"
            "Research public evidence.\n\n"
            "Downstream artifacts:\n"
            "- Fable operating instructions\n"
            "- Workflow approval checklist\n"
        )

        proc = self.handoff()

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "Copy to ChatGPT:\n\n"
            "Research public evidence.\n\n"
            "Downstream artifacts:\n"
            "- Fable operating instructions\n"
            "- Workflow approval checklist\n",
        )

    def test_handoff_refuses_every_vault_only_token_class(self):
        self.write_prompt(
            "## Deep research prompt\n\n"
            "Use [[Projects/Fable/Index|Fable]].\n"
            "Resolve obs:OAW-TSK-example.\n"
            "Read Agents/Tasks/example.md.\n"
            "Consumers:\n"
            "- FAB-REF-operating-instructions\n"
            "- AGT-TSK-example OAW-TSK-example CDX-RES-example SR-TSK-example PMX-FW-1\n"
        )

        proc = self.handoff()

        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertIn("Refusing unsafe provider handoff", proc.stderr)
        self.assertIn("Obsidian wikilink", proc.stderr)
        self.assertIn("obs: reference", proc.stderr)
        self.assertIn("vault path", proc.stderr)
        self.assertIn("local Consumers heading", proc.stderr)
        self.assertIn("internal durable ID", proc.stderr)
        self.assertIn("Move local metadata before '## Deep research prompt'", proc.stderr)

    def test_detector_catches_malformed_wikilink_and_respects_boundaries(self):
        findings = MODULE.unsafe_handoff_findings(
            "See [[unfinished local link\n"
            "Consumers should compare public evidence.\n"
            "The agents coordinate around OAW concepts.\n"
            "Use Fable operating instructions.\n"
            "Public URL: https://example.com/Projects/demo\n"
        )

        self.assertEqual(
            [(line, label) for line, label, _ in findings],
            [(1, "Obsidian wikilink")],
        )

    def test_detector_catches_markdown_wrapped_consumers_labels(self):
        for line in (
            "Consumers:",
            "### Consumers:",
            "**Consumers:**",
            "- Consumers:",
            "> Consumers:",
            "> - **Consumers:**",
        ):
            with self.subTest(line=line):
                findings = MODULE.unsafe_handoff_findings(line + "\n")
                self.assertEqual(
                    [(line_number, label) for line_number, label, _ in findings],
                    [(1, "local Consumers heading")],
                )


class IntakeSourceLabelTests(unittest.TestCase):
    def test_intake_requires_oaw_running_result_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            research_dir = Path(tmp) / "Research" / "topic"
            research_dir.mkdir(parents=True)
            report = Path(tmp) / "report.md"
            report.write_text("# Report\n", encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "intake",
                    str(research_dir),
                    "ChatGPT",
                    "--file",
                    str(report),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Register the provider first with 'oaw research start'", proc.stderr)
            self.assertFalse((research_dir / "Raw - ChatGPT.md").exists())

    def test_intake_preserves_exact_existing_oaw_source_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            research_dir = Path(tmp) / "Research" / "topic"
            research_dir.mkdir(parents=True)
            result = research_dir / "Results - chatgpt pro.md"
            result.write_text(
                "---\ntype: research-result\nsource: chatgpt pro\n"
                "url: https://example.com/run\nstatus: running\n---\n\n"
                "# Results - chatgpt pro\n",
                encoding="utf-8",
            )
            report = Path(tmp) / "report.md"
            report.write_text(
                "# Report\n\n## Full source list\n\n- https://example.com\n",
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "intake",
                    str(research_dir),
                    "chatgpt pro",
                    "--file",
                    str(report),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("status: done", result.read_text(encoding="utf-8"))
            self.assertTrue((research_dir / "Raw - chatgpt pro.md").is_file())
            self.assertFalse((research_dir / "Results - Chatgpt pro.md").exists())


if __name__ == "__main__":
    unittest.main()
