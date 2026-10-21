"""End-to-end tests with Docker (Online mode): run with `ADVIEWER_E2E=1 pytest tests/e2e`.
They start containers, so they are skipped unless asked for and Docker is reachable."""
import os
import shutil
import subprocess

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    reason = ""
    if os.environ.get("ADVIEWER_E2E") != "1":
        reason = "end-to-end tests need ADVIEWER_E2E=1"
    elif not shutil.which("docker") or subprocess.run(["docker", "info"], capture_output=True, check=False).returncode:
        reason = "end-to-end tests need a running Docker"
    if reason:
        for item in items:
            if "/e2e/" in str(item.fspath).replace("\\", "/"):
                item.add_marker(pytest.mark.skip(reason=reason))
