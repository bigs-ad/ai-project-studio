from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "studio.py"


class StudioContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name)
        self.run_cli(
            "init",
            str(self.project),
            "--profile",
            "game",
            "--name",
            "Test Game",
            "--owner",
            "Jiang",
            "--idea",
            "A card-driven creative generator",
        )
        self.spec = self.project / "studio/specs/WORK_ITEM_TEMPLATE.md"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_cli(self, *args: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            text=True,
            capture_output=True,
            check=False,
        )
        if ok and result.returncode != 0:
            self.fail(f"CLI failed: {' '.join(args)}\n{result.stdout}\n{result.stderr}")
        if not ok and result.returncode == 0:
            self.fail(f"CLI unexpectedly succeeded: {' '.join(args)}\n{result.stdout}")
        return result

    def add_item(self, title: str) -> str:
        result = self.run_cli(
            "work",
            "add",
            str(self.project),
            "--title",
            title,
            "--kind",
            "experiment",
            "--summary",
            "Verify a bounded player outcome",
            "--spec",
            str(self.spec),
        )
        return result.stdout.split(":", 1)[0].split()[-1]

    def move(self, item_id: str, state: str, *extra: str, ok: bool = True):
        return self.run_cli(
            "work", "move", str(self.project), item_id, state, *extra, ok=ok
        )

    def test_valid_contract_supports_full_work_lifecycle(self) -> None:
        item_id = self.add_item("Playable slice")
        self.move(item_id, "proposed", "--by", "Codex")
        self.move(item_id, "approved", "--approved-by", "Jiang")
        self.move(item_id, "in_progress", "--by", "Codex")
        self.move(
            item_id,
            "review",
            "--by",
            "Codex",
            "--evidence",
            "build: /tmp/game; recording: /tmp/game.mp4",
        )
        self.move(
            item_id,
            "done",
            "--approved-by",
            "Jiang",
            "--evidence",
            "accepted recording: /tmp/game.mp4",
        )
        self.run_cli("validate", str(self.project))

    def test_proposal_rejects_spec_without_contract(self) -> None:
        invalid_spec = self.project / "studio/specs/invalid.md"
        invalid_spec.write_text("# Incomplete spec\n", encoding="utf-8")
        result = self.run_cli(
            "work",
            "add",
            str(self.project),
            "--title",
            "Incomplete",
            "--kind",
            "chore",
            "--summary",
            "Missing contract",
            "--spec",
            str(invalid_spec),
        )
        item_id = result.stdout.split(":", 1)[0].split()[-1]
        rejected = self.move(item_id, "proposed", "--by", "Codex", ok=False)
        self.assertIn("deliverable-contract", rejected.stderr)

    def test_only_one_item_can_be_in_flight(self) -> None:
        first = self.add_item("First slice")
        self.move(first, "proposed", "--by", "Codex")
        self.move(first, "approved", "--approved-by", "Jiang")
        self.move(first, "in_progress", "--by", "Codex")

        second = self.add_item("Second slice")
        self.move(second, "proposed", "--by", "Codex")
        self.move(second, "approved", "--approved-by", "Jiang")
        rejected = self.move(second, "in_progress", "--by", "Codex", ok=False)
        self.assertIn("Finish or pause the current in-flight work item", rejected.stderr)

    def test_initial_idea_and_owner_are_persisted(self) -> None:
        project_text = (self.project / "studio/PROJECT.md").read_text(encoding="utf-8")
        project_state = json.loads(
            (self.project / "studio/project.json").read_text(encoding="utf-8")
        )
        self.assertIn("A card-driven creative generator", project_text)
        self.assertIn("不是已经批准的项目目标", project_text)
        self.assertEqual(project_state["project"]["owner"], "Jiang")

    def test_owner_must_approve_and_accept_work(self) -> None:
        item_id = self.add_item("Owner-controlled slice")
        self.move(item_id, "proposed", "--by", "Codex")
        rejected = self.move(
            item_id, "approved", "--approved-by", "Codex", ok=False
        )
        self.assertIn("must match the project owner", rejected.stderr)
        self.move(item_id, "approved", "--approved-by", "Jiang")
        self.move(item_id, "in_progress", "--by", "Codex")
        self.move(
            item_id,
            "review",
            "--by",
            "Codex",
            "--evidence",
            "recording: /tmp/game.mp4",
        )
        rejected = self.move(
            item_id,
            "done",
            "--approved-by",
            "Codex",
            "--evidence",
            "producer accepted",
            ok=False,
        )
        self.assertIn("must match the project owner", rejected.stderr)

    def test_brief_exposes_execution_boundary_and_ambiguity(self) -> None:
        first = self.add_item("First proposal")
        self.move(first, "proposed", "--by", "Codex")
        proposed = self.run_cli(
            "brief", str(self.project), "--item", first, "--json"
        )
        proposed_data = json.loads(proposed.stdout)
        self.assertFalse(proposed_data["focus_item"]["execution_allowed"])
        self.assertFalse(
            proposed_data["focus_item"]["subagent_state_changes_allowed"]
        )
        self.assertIn("studio/PROJECT.md", proposed_data["required_reads"])
        self.assertNotIn("studio/STATUS.md", proposed_data["required_reads"])
        self.assertIn(
            proposed_data["focus_item"]["spec"], proposed_data["required_reads"]
        )

        self.move(first, "approved", "--approved-by", "Jiang")
        second = self.add_item("Second proposal")
        self.move(second, "proposed", "--by", "Codex")
        self.move(second, "approved", "--approved-by", "Jiang")
        ambiguous = self.run_cli("brief", str(self.project), "--json")
        ambiguous_data = json.loads(ambiguous.stdout)
        self.assertIsNone(ambiguous_data["focus_item"])
        self.assertEqual(ambiguous_data["focus_candidates"], [first, second])

    def test_checkpoint_survives_process_boundary_and_appears_in_brief(self) -> None:
        item_id = self.add_item("Recoverable work")
        self.move(item_id, "proposed", "--by", "Codex")
        self.move(item_id, "approved", "--approved-by", "Jiang")
        self.move(item_id, "in_progress", "--by", "Codex")
        self.run_cli(
            "work",
            "checkpoint",
            str(self.project),
            item_id,
            "--summary",
            "Input flow implemented",
            "--next",
            "Run the card draw playtest",
            "--blocker",
            "Needs target-device build",
            "--by",
            "Codex",
        )
        recovered = self.run_cli(
            "brief", str(self.project), "--item", item_id, "--json"
        )
        checkpoint = json.loads(recovered.stdout)["focus_item"]["checkpoint"]
        self.assertEqual(checkpoint["summary"], "Input flow implemented")
        self.assertEqual(checkpoint["next_action"], "Run the card draw playtest")
        self.assertEqual(checkpoint["blockers"], ["Needs target-device build"])

    def test_repair_refreshes_managed_block_and_preserves_user_content(self) -> None:
        agents = self.project / "AGENTS.md"
        original = agents.read_text(encoding="utf-8")
        stale = "# User Rules\n\nKeep this.\n\n" + original.replace(
            "## AI Project Studio\n", "## AI Project Studio\n\n- stale rule\n", 1
        )
        agents.write_text(stale, encoding="utf-8")
        status = self.project / "studio/STATUS.md"
        status.write_text("stale\n", encoding="utf-8")

        self.run_cli("repair", str(self.project))
        repaired = agents.read_text(encoding="utf-8")
        self.assertIn("# User Rules", repaired)
        self.assertIn("Keep this.", repaired)
        self.assertNotIn("- stale rule", repaired)
        self.run_cli("validate", str(self.project))
        self.run_cli("repair", str(self.project))

    def test_repair_refuses_duplicate_markers_without_modifying_file(self) -> None:
        agents = self.project / "AGENTS.md"
        corrupted = agents.read_text(encoding="utf-8") + "\n<!-- ai-project-studio:start -->\n"
        agents.write_text(corrupted, encoding="utf-8")
        rejected = self.run_cli("repair", str(self.project), ok=False)
        self.assertIn("fix these state errors first", rejected.stderr)
        self.assertEqual(agents.read_text(encoding="utf-8"), corrupted)

    def test_legacy_project_can_set_owner_once(self) -> None:
        project_path = self.project / "studio/project.json"
        project_state = json.loads(project_path.read_text(encoding="utf-8"))
        del project_state["project"]["owner"]
        project_path.write_text(
            json.dumps(project_state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        warning = self.run_cli("validate", str(self.project))
        self.assertIn("Legacy project has no enforced user owner", warning.stdout)
        self.run_cli(
            "project", "set-owner", str(self.project), "--owner", "Legacy Owner"
        )
        self.run_cli("validate", str(self.project))
        rejected = self.run_cli(
            "project",
            "set-owner",
            str(self.project),
            "--owner",
            "Replacement",
            ok=False,
        )
        self.assertIn("changing ownership requires a migration", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
