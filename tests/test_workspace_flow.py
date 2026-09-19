import os
import sys
import tempfile
import unittest
from pathlib import Path

db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
db_file.close()
os.environ["DATABASE_URL"] = f"sqlite:///{db_file.name}"
os.environ["SECRET_KEY"] = "test-secret-key-for-workspace-flow-tests"

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import main  # noqa: E402
import models  # noqa: E402
import security  # noqa: E402
import workspace as workspace_utils  # noqa: E402
from database import SessionLocal  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class WorkspaceFlowTests(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self.client = TestClient(main.app)
        self.created_users = []

    def tearDown(self):
        for user in self.created_users:
            row = self.db.query(models.User).filter(models.User.id == user.id).first()
            if row:
                self.db.delete(row)
        self.db.commit()
        self.db.close()

    def make_user(self, email, **attrs):
        user = models.User(email=email, name=attrs.pop("name", "Test User"), provider="email", **attrs)
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        self.created_users.append(user)
        return user

    def cookies_for(self, user):
        return {"access_token": security.create_access_token(user.id)}

    def test_new_authenticated_user_goes_to_role_onboarding(self):
        user = self.make_user("new-onboarding@example.com")

        response = self.client.get("/workspace", cookies=self.cookies_for(user), follow_redirects=False)

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/onboarding/role")

    def test_candidate_dashboard_renders_workspace_components(self):
        user = self.make_user(
            "candidate-dashboard@example.com",
            primary_role="candidate",
            active_workspace="candidate",
            available_roles=["candidate"],
            workspace="APPLICANT",
            onboarding_completed=True,
        )

        response = self.client.get("/candidate/dashboard", cookies=self.cookies_for(user))

        self.assertEqual(response.status_code, 200)
        self.assertIn("Aptura Career Passport", response.text)
        self.assertIn("workspace-switcher", response.text)
        self.assertIn("Candidate workspace", response.text)

    def test_onboarding_progress_persists_in_workspace_preferences(self):
        user = self.make_user(
            "progress@example.com",
            primary_role="student",
            active_workspace="student",
            available_roles=["student"],
            workspace="STUDENT",
            onboarding_completed=True,
        )

        workspace_utils.save_onboarding_progress(
            self.db,
            user,
            "student",
            completed_steps=["Target career", "Skills", "Not a real step"],
            is_skipped=False,
        )
        self.db.refresh(user)
        progress = workspace_utils.onboarding_progress(user, "student")

        self.assertEqual(progress["completed_steps"], ["Target career", "Skills"])
        self.assertFalse(progress["is_skipped"])

    def test_screening_history_requires_recruiter_workspace(self):
        candidate = self.make_user(
            "candidate-api@example.com",
            primary_role="candidate",
            active_workspace="candidate",
            available_roles=["candidate"],
            workspace="APPLICANT",
            onboarding_completed=True,
        )
        recruiter = self.make_user(
            "recruiter-api@example.com",
            primary_role="recruiter",
            active_workspace="recruiter",
            available_roles=["recruiter"],
            workspace="RECRUITER",
            onboarding_completed=True,
        )

        blocked = self.client.get("/api/history", cookies=self.cookies_for(candidate))
        allowed = self.client.get("/api/history", cookies=self.cookies_for(recruiter))

        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(allowed.status_code, 200)


if __name__ == "__main__":
    unittest.main()
