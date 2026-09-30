from __future__ import annotations

from pathlib import Path
import unittest


class WorkflowNotificationTests(unittest.TestCase):
    def test_workflows_rely_on_github_notifications(self) -> None:
        """Publication and validation do not require a separate email provider."""
        repository = Path(__file__).parents[1]
        publication = (repository / ".github/workflows/publish-leaderboard.yml").read_text()
        validation = (repository / ".github/workflows/validate.yml").read_text()

        self.assertNotIn("RESEND_API_KEY", publication)
        self.assertNotIn("notify-publication", publication)
        self.assertNotIn("RESEND_API_KEY", validation)
        self.assertNotIn("notify-validation-failure", validation)
