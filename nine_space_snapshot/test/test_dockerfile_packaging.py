"""Regression test for Finding 1 (M2B reviewer findings):

The Docker image build previously only `COPY`'d ``main.py``,
``log_config.json`` and ``run.sh`` into ``/app`` / ``/``, even though
``main.py`` imports ``background``, which in turn imports ``channel_state``,
``live_probe`` and ``recording_query`` at module load time. A container
built from that Dockerfile would fail immediately on startup with
``ModuleNotFoundError``.

This test never runs Docker. It:

1. Parses the Dockerfile's ``COPY`` instructions to find every ``*.py``
   source file it declares will be copied into the image.
2. Statically walks ``main.py``'s local (same-directory) imports,
   transitively, to compute the actual set of ``*.py`` runtime modules the
   app needs to start.
3. Asserts the Dockerfile copies at least that full set (Acceptance 1/4).
4. Copies *only* the Dockerfile-declared Python sources into an isolated
   temporary "/app-like" directory and successfully imports ``main`` from
   there, proving the declared COPY set is sufficient on its own
   (Acceptance 5).
5. Asserts the Dockerfile does not copy test/credential/cache directories
   or other unrelated repository content (Acceptance 3).

If a real, usable local Docker daemon is available, an optional extra smoke
test also builds the image and imports ``main`` inside the running
container-equivalent Python (Acceptance 7); it is skipped (not failed) when
Docker is unavailable, per AGENTS.md instructions not to install tooling or
expand scope just to exercise it.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1]
DOCKERFILE = ADDON_DIR / "Dockerfile"
DOCKERFILE_TEXT = DOCKERFILE.read_text(encoding="utf-8")

# Matches: COPY <src>.py /app/<src>.py  (also tolerates /app/<anything>.py)
_COPY_PY_RE = re.compile(r"(?m)^\s*COPY\s+(\S+\.py)\s+(\S+)\s*$")


def _dockerfile_copied_py_sources() -> set[str]:
    """Every distinct top-level ``*.py`` filename the Dockerfile copies."""
    return {match.group(1) for match in _COPY_PY_RE.finditer(DOCKERFILE_TEXT)}


def _local_imports(py_file: Path) -> set[str]:
    """Names imported by ``py_file`` that correspond to sibling ``*.py``
    files in the app directory (i.e. first-party runtime modules, not
    third-party packages like fastapi/uvicorn)."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module.split(".")[0])
    return {name for name in names if (ADDON_DIR / f"{name}.py").is_file()}


def _required_runtime_modules() -> set[str]:
    """Transitive closure of first-party modules ``main.py`` needs to run,
    computed purely by static import analysis (no execution)."""
    required: set[str] = {"main"}
    pending = ["main"]
    while pending:
        current = pending.pop()
        for dep in _local_imports(ADDON_DIR / f"{current}.py"):
            if dep not in required:
                required.add(dep)
                pending.append(dep)
    return required


class DockerfilePackagingTests(unittest.TestCase):
    def test_dockerfile_copies_every_runtime_module_main_needs(self) -> None:
        required = _required_runtime_modules()
        copied = {Path(src).stem for src in _dockerfile_copied_py_sources()}
        missing = {f"{name}.py" for name in required} - {
            f"{name}.py" for name in copied
        }
        self.assertFalse(
            missing,
            f"Dockerfile does not COPY these runtime modules main.py needs: {sorted(missing)}",
        )

    def test_dockerfile_does_not_copy_unrelated_repository_content(self) -> None:
        copied_sources = _dockerfile_copied_py_sources()
        for src in copied_sources:
            self.assertFalse(src.startswith("test/"), f"must not COPY tests: {src}")
        self.assertNotIn("config.yaml", DOCKERFILE_TEXT)
        self.assertNotRegex(DOCKERFILE_TEXT, r"COPY\s+test[/ ]")
        self.assertNotRegex(DOCKERFILE_TEXT, r"COPY\s+\.\s")

    def test_declared_copy_set_is_sufficient_to_import_main_in_isolation(self) -> None:
        """Recreate an "/app-like" directory containing *only* the Python
        sources the Dockerfile declares, then import ``main`` from it. This
        proves the Dockerfile's COPY list is both necessary (previous test)
        and sufficient (this test) -- a container built from it would not
        hit ModuleNotFoundError on startup."""
        copied_py = sorted(_dockerfile_copied_py_sources())
        self.assertTrue(copied_py, "Dockerfile does not declare any *.py COPY")

        with tempfile.TemporaryDirectory(prefix="app-like-") as tmp:
            app_dir = Path(tmp)
            for rel_src in copied_py:
                source_path = ADDON_DIR / rel_src
                self.assertTrue(source_path.is_file(), f"declared COPY source missing: {rel_src}")
                shutil.copy2(source_path, app_dir / source_path.name)

            script = (
                "import sys; "
                f"sys.path.insert(0, {str(app_dir)!r}); "
                "import main; "
                "assert main.app is not None"
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=app_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"importing main from an isolated Dockerfile-declared /app failed:\n"
                f"stdout={result.stdout}\nstderr={result.stderr}",
            )


class DockerBuildSmokeTest(unittest.TestCase):
    """Optional, best-effort local Docker build+import smoke test.

    Skipped (not failed) whenever a usable local Docker daemon is not
    available -- this repository's AGENTS.md forbids installing tooling or
    expanding scope just to exercise this, and no site/NVR is required or
    contacted by this test either way.
    """

    def test_optional_docker_build_and_import_smoke(self) -> None:
        docker = shutil.which("docker")
        if not docker:
            self.skipTest("docker CLI not available locally")
        info = subprocess.run([docker, "info"], capture_output=True, text=True, timeout=10)
        if info.returncode != 0:
            self.skipTest("local Docker daemon not usable")

        tag = "9space-snapshot-packaging-smoke:test"
        build = subprocess.run(
            [docker, "build", "-t", tag, str(ADDON_DIR)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        self.assertEqual(build.returncode, 0, build.stderr)
        try:
            run = subprocess.run(
                [
                    docker,
                    "run",
                    "--rm",
                    "--entrypoint",
                    "/opt/venv/bin/python3",
                    tag,
                    "-c",
                    "import sys; sys.path.insert(0, '/app'); import main; assert main.app",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
        finally:
            subprocess.run([docker, "image", "rm", "-f", tag], capture_output=True, text=True, timeout=60)


if __name__ == "__main__":
    unittest.main()
