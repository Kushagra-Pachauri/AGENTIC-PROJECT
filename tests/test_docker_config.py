"""Validation tests for Docker packaging, compose architecture, and security policies."""

from __future__ import annotations

import re
from pathlib import Path

import pytest


class TestDockerPackagingAndSecurity:
    """Test suite verifying Dockerfile hygiene, compose configuration, and sandbox containment."""

    @pytest.fixture
    def root_dir(self) -> Path:
        return Path(__file__).parent.parent

    def test_01_docker_compose_structure_and_services(self, root_dir: Path) -> None:
        """docker-compose.yml must define all 4 application services with explicit ports."""
        compose_file = root_dir / "docker-compose.yml"
        assert compose_file.is_file(), "docker-compose.yml must exist"

        content = compose_file.read_text(encoding="utf-8")
        assert "services:" in content

        # All 4 services present
        for svc in ("repo-planner:", "code-reviewer:", "repoagent-api:", "repoagent-dashboard:"):
            assert svc in content, f"Service {svc} missing from docker-compose.yml"

        # Explicit ports
        for port in ("8001:8001", "8002:8002", "8000:8000", "8501:8501"):
            assert port in content, f"Port mapping {port} missing from docker-compose.yml"

    def test_02_health_checks_present_on_all_services(self, root_dir: Path) -> None:
        """Every service in docker-compose.yml must define a healthcheck."""
        compose_file = root_dir / "docker-compose.yml"
        content = compose_file.read_text(encoding="utf-8")

        # Must have 4 healthcheck blocks
        healthcheck_count = content.count("healthcheck:")
        assert healthcheck_count == 4, f"Expected 4 healthcheck blocks, found {healthcheck_count}"

    def test_03_no_sensitive_host_paths_mounted_in_compose(self, root_dir: Path) -> None:
        """docker-compose.yml must NOT mount .env, docker.sock, or host credential paths."""
        compose_file = root_dir / "docker-compose.yml"
        content = compose_file.read_text(encoding="utf-8")

        forbidden_patterns = [
            r"/var/run/docker\.sock",
            r"\.env(?::|\s|$)",
            r"\.ssh",
            r"\.aws",
            r"docker_engine",
        ]
        for pattern in forbidden_patterns:
            assert not re.search(pattern, content, re.IGNORECASE), (
                f"Prohibited host volume mount pattern '{pattern}' detected in docker-compose.yml"
            )

    def test_04_no_hardcoded_secrets_in_docker_files(self, root_dir: Path) -> None:
        """Dockerfiles and docker-compose.yml must not contain hardcoded credentials."""
        secret_regexes = [
            r"ghp_[a-zA-Z0-9]{20,}",
            r"sk-[a-zA-Z0-9]{20,}",
            r"AIzaSy[a-zA-Z0-9_-]{20,}",
        ]
        for fname in (
            "docker-compose.yml",
            "Dockerfile.api",
            "Dockerfile.dashboard",
            "Dockerfile.agents",
        ):
            fpath = root_dir / fname
            if fpath.is_file():
                txt = fpath.read_text(encoding="utf-8")
                for s_regex in secret_regexes:
                    assert not re.search(s_regex, txt), (
                        f"Potential hardcoded secret detected in {fname}"
                    )

    def test_05_dockerfiles_use_non_root_users(self, root_dir: Path) -> None:
        """All application Dockerfiles must configure a non-root application user."""
        for fname in ("Dockerfile.api", "Dockerfile.dashboard", "Dockerfile.agents"):
            fpath = root_dir / fname
            assert fpath.is_file(), f"{fname} must exist"
            content = fpath.read_text(encoding="utf-8")
            assert "useradd" in content, f"{fname} must create an application user"
            assert "USER appuser" in content, f"{fname} must switch to USER appuser"

    def test_06_langfuse_compose_structure(self, root_dir: Path) -> None:
        """docker-compose.langfuse.yml must define langfuse-server and langfuse-db."""
        compose_file = root_dir / "docker-compose.langfuse.yml"
        assert compose_file.is_file(), "docker-compose.langfuse.yml must exist"
        content = compose_file.read_text(encoding="utf-8")
        assert "langfuse-server:" in content
        assert "langfuse-db:" in content
        assert "3000:3000" in content
        assert "postgres:16-alpine" in content
