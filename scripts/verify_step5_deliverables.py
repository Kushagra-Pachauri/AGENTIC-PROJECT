"""Verification script for Step 5 deliverables:
1. Real round-trip for Planner on demo_repo with real model (Ollama qwen2.5-coder:3b)
2. Reviewer output on a passing example
3. Reviewer output on a failing example
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import uvicorn

from repoagent.a2a_client import A2AClient
from repoagent.agents.adk.server import create_planner_app, create_reviewer_app
from repoagent.schemas import Plan, PlannedFile, TestReport


def run_server(app, port: int):
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.run()


def main():
    print("=== STEP 5 DELIVERABLES VERIFICATION ===")
    os.environ["REPOAGENT_PROVIDER"] = "ollama"
    os.environ["REPOAGENT_MODEL"] = "qwen2.5-coder:3b"
    os.environ["OPENAI_BASE_URL"] = "http://localhost:11434/v1"

    # Start Planner on 8001
    planner_app = create_planner_app(port=8001)
    planner_thread = threading.Thread(target=run_server, args=(planner_app, 8001), daemon=True)
    planner_thread.start()

    # Start Reviewer on 8002
    reviewer_app = create_reviewer_app(port=8002)
    reviewer_thread = threading.Thread(target=run_server, args=(reviewer_app, 8002), daemon=True)
    reviewer_thread.start()

    print("Waiting 2 seconds for A2A servers to listen on 8001 and 8002...")
    time.sleep(2)

    planner_client = A2AClient(base_url="http://127.0.0.1:8001")
    reviewer_client = A2AClient(base_url="http://127.0.0.1:8002")

    # 1. Fetch Agent Cards
    print("\n--- 1. Fetching Agent Cards ---")
    planner_card = planner_client.fetch_agent_card()
    print(f"Planner Agent Card fetched: name={planner_card.get('name')}")
    reviewer_card = reviewer_client.fetch_agent_card()
    print(f"Reviewer Agent Card fetched: name={reviewer_card.get('name')}")

    # 2. Real Planner Round-Trip on demo_repo
    print("\n--- 2. Real Planner Round-Trip on demo_repo with local Ollama ---")
    demo_repo = Path("demo_repo").resolve()
    requirement = (
        "Add password-reset functionality allowing users to request a token and reset password"
    )

    t0 = time.time()
    try:
        planner_output = planner_client.request_plan(
            requirement=requirement,
            workspace=demo_repo,
            run_id="deliverable-plan-01",
        )
        dt = time.time() - t0
        print(f"Planner succeeded in {dt:.2f}s!")
        print("\n[PLANNER OUTPUT - REPO MAP]:")
        print(planner_output.repo_map[:400] + ("..." if len(planner_output.repo_map) > 400 else ""))
        print("\n[PLANNER OUTPUT - PLAN]:")
        print(json.dumps(planner_output.plan.model_dump(), indent=2))
    except Exception as exc:
        print(f"Planner failed: {exc}", file=sys.stderr)
        return 1

    # 3. Real Reviewer Round-Trip - Passing Example
    print("\n--- 3. Real Reviewer Round-Trip - Passing Example ---")
    pass_plan = Plan(
        summary="Add request_password_reset and reset_password methods",
        files_to_change=[
            PlannedFile(path="src/notes_app/auth.py", reason="Add token generation"),
            PlannedFile(path="src/notes_app/app.py", reason="Delegate to auth service"),
        ],
        steps=["Generate reset token", "Validate token and change password"],
        risks=["Ensure token expires or is single use"],
        test_strategy="Add unit tests in test_auth.py",
    )
    pass_diff = """diff --git a/src/notes_app/auth.py b/src/notes_app/auth.py
index 1234567..89abcdef 100644
--- a/src/notes_app/auth.py
+++ b/src/notes_app/auth.py
@@ -35,2 +35,12 @@ class AuthService:
+    def request_password_reset(self, username: str) -> str:
+        if username not in self.users:
+            raise ValueError("User not found")
+        import secrets
+        token = secrets.token_urlsafe(16)
+        self.reset_tokens[token] = username
+        return token
+
+    def reset_password(self, token: str, new_password: str) -> bool:
+        username = self.reset_tokens.pop(token, None)
+        if not username:
+            return False
+        self.users[username].password_hash = self._hash_password(new_password)
+        return True
"""
    pass_test = TestReport(
        command="pytest",
        passed=True,
        exit_code=0,
        workspace_hash="sha256:abc123clean",
        failed_tests=[],
        raw_output="14 passed in 0.05s",
    )
    t0 = time.time()
    try:
        pass_review = reviewer_client.request_review(
            plan=pass_plan,
            diff=pass_diff,
            test_report=pass_test,
            run_id="deliverable-rev-pass",
        )
        dt = time.time() - t0
        print(f"Reviewer evaluated passing example in {dt:.2f}s:")
        print(f"Verdict: {pass_review.verdict.value}")
        print(f"Summary: {pass_review.summary}")
        print(f"Findings: {len(pass_review.findings)}")
        for f in pass_review.findings:
            print(f"  - [{f.severity}] {f.file}:{f.line} {f.message}")
    except Exception as exc:
        print(f"Reviewer passing example failed: {exc}", file=sys.stderr)
        return 1

    # 4. Real Reviewer Round-Trip - Failing/Flawed Example
    print("\n--- 4. Real Reviewer Round-Trip - Failing/Flawed Example ---")
    fail_plan = Plan(
        summary="Add request_password_reset and reset_password methods",
        files_to_change=[PlannedFile(path="src/notes_app/auth.py", reason="Add auth token")],
    )
    # Clear flaw: Hardcoded insecure static token "STATIC_TOKEN_123" and empty password check
    fail_diff = """diff --git a/src/notes_app/auth.py b/src/notes_app/auth.py
index 1234567..89abcdef 100644
--- a/src/notes_app/auth.py
+++ b/src/notes_app/auth.py
@@ -35,2 +35,6 @@ class AuthService:
+    def request_password_reset(self, username: str) -> str:
+        # INSECURE: Fixed predictable token
+        return "FIXED_STATIC_TOKEN_FOR_ALL_USERS"
+
+    def reset_password(self, token: str, new_password: str) -> bool:
+        # BUG: Ignores token and always returns True
+        return True
"""
    fail_test = TestReport(
        command="pytest",
        passed=True,  # Even if tests passed, reviewer inspects code quality/security
        exit_code=0,
        workspace_hash="sha256:flawed999",
        failed_tests=[],
        raw_output="12 passed in 0.03s",
    )
    t0 = time.time()
    try:
        fail_review = reviewer_client.request_review(
            plan=fail_plan,
            diff=fail_diff,
            test_report=fail_test,
            run_id="deliverable-rev-fail",
        )
        dt = time.time() - t0
        print(f"Reviewer evaluated flawed example in {dt:.2f}s:")
        print(f"Verdict: {fail_review.verdict.value}")
        print(f"Summary: {fail_review.summary}")
        print(f"Findings: {len(fail_review.findings)}")
        for f in fail_review.findings:
            print(f"  - [{f.severity}] {f.file}:{f.line} {f.message}")
    except Exception as exc:
        print(f"Reviewer failing example failed: {exc}", file=sys.stderr)
        return 1

    print("\n=== ALL STEP 5 DELIVERABLES VERIFIED SUCCESSFULLY ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
