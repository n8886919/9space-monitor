"""Regression checks for the fail-closed integration deployment layout."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = (ROOT / "DEPLOY.md").read_text()
DOMAIN = "nvr_monitor"


def write_manifest(directory: Path, domain: str = DOMAIN) -> None:
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps({"domain": domain}))


def find_nvr_monitor_manifests(custom_components: Path) -> list[Path]:
    """Mirror DEPLOY.md's first-level manifest gate without mutating its input."""
    return [
        child / "manifest.json"
        for child in custom_components.iterdir()
        if child.is_dir()
        and (child / "manifest.json").is_file()
        and json.loads((child / "manifest.json").read_text()).get("domain") == DOMAIN
    ]


def canonical_layout_is_safe(custom_components: Path) -> bool:
    expected = custom_components / DOMAIN / "manifest.json"
    return find_nvr_monitor_manifests(custom_components) == [expected]


class DeployLayoutSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.config = Path(self.tempdir.name) / "config"
        self.components = self.config / "custom_components"
        self.components.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_canonical_only_layout_passes(self) -> None:
        write_manifest(self.components / DOMAIN)
        self.assertTrue(canonical_layout_is_safe(self.components))

    def test_dot_leading_staging_fails(self) -> None:
        write_manifest(self.components / DOMAIN)
        write_manifest(self.components / ".nvr_monitor.deploy")
        self.assertFalse(canonical_layout_is_safe(self.components))

    def test_old_and_backup_same_domain_directories_fail(self) -> None:
        for sibling in ("nvr_monitor.old", "nvr_monitor.bak_20260802"):
            with self.subTest(sibling=sibling), tempfile.TemporaryDirectory() as tempdir:
                components = Path(tempdir) / "custom_components"
                components.mkdir()
                write_manifest(components / DOMAIN)
                write_manifest(components / sibling)
                self.assertFalse(canonical_layout_is_safe(components))

    def test_deployment_artifact_outside_custom_components_passes(self) -> None:
        write_manifest(self.components / DOMAIN)
        artifact = (
            self.config
            / "9space_deploy/nvr-monitor.ABC123/transaction.DEF456/integration_replaced"
        )
        write_manifest(artifact)
        self.assertTrue(canonical_layout_is_safe(self.components))

    def test_document_has_fail_closed_external_artifacts_only(self) -> None:
        self.assertIn("verify_nvr_monitor_layout", DEPLOY)
        self.assertIn("test \"$count\" -eq 1", DEPLOY)
        self.assertIn("/config/9space_deploy/nvr-monitor.", DEPLOY)
        for forbidden in (".nvr_monitor*", "nvr_monitor.old*", "nvr_monitor.bak*"):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, DEPLOY)
        self.assertNotIn("$INTEGRATION_REMOTE_DIR.new", DEPLOY)
        self.assertNotIn("$INTEGRATION_REMOTE_DIR.old", DEPLOY)
        self.assertNotIn("integration_predeploy", DEPLOY)
        self.assertGreaterEqual(DEPLOY.count("DEPLOY_LAYOUT_HELPER_BEGIN"), 4)
        self.assertIn("cleanup() { rm -f", DEPLOY)
        self.assertNotIn('rm -rf "$TXN_DIR"', DEPLOY)

    def test_document_keeps_storage_read_only(self) -> None:
        self.assertIn("只在確定要執行 UI Reconfigure 前建立一次", DEPLOY)
        self.assertEqual(
            1,
            DEPLOY.count("cp -a /config/.storage/core.config_entries"),
        )
        self.assertIn("不得編輯、不得直接覆寫", DEPLOY)
        self.assertNotIn("core.config_entries\n  mv", DEPLOY)

    def test_layout_deployment_shell_blocks_parse_in_bash(self) -> None:
        blocks = re.findall(r"```bash\n(.*?)```", DEPLOY, flags=re.DOTALL)
        layout_blocks = [block for block in blocks if "verify_nvr_monitor_layout" in block]
        self.assertGreaterEqual(len(layout_blocks), 3)
        for block in layout_blocks:
            with self.subTest(block=block[:60]):
                result = subprocess.run(
                    ["bash", "-n"], input=block, text=True, capture_output=True
                )
                self.assertEqual(0, result.returncode, result.stderr)

    def test_production_helper_executes_layout_and_malformed_json_gates(self) -> None:
        helper = re.search(
            r"# DEPLOY_LAYOUT_HELPER_BEGIN\n(.*?)# DEPLOY_LAYOUT_HELPER_END",
            DEPLOY,
            re.DOTALL,
        )
        self.assertIsNotNone(helper)
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            helper_path = root / "helper.sh"
            helper_path.write_text(helper.group(1))
            fake_bin = root / "bin"
            fake_bin.mkdir()
            jq = fake_bin / "jq"
            jq.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "try:\n data=json.load(open(sys.argv[-1]))\nexcept Exception: raise SystemExit(1)\n"
                "expr=' '.join(sys.argv)\n"
                "ok=isinstance(data,dict)\n"
                "if 'domain == \\\"nvr_monitor\\\"' in expr: ok &= data.get('domain') == 'nvr_monitor'\n"
                "if '.version == $v' in expr:\n i=sys.argv.index('--arg'); ok &= data.get('version') == sys.argv[i+2]\n"
                "raise SystemExit(0 if ok else 1)\n"
            )
            jq.chmod(0o755)
            components = root / "custom_components"
            write_manifest(components / DOMAIN)
            script = (
                f"source {helper_path}\nCUSTOM_COMPONENTS={components}\n"
                "verify_nvr_monitor_layout\n"
            )
            env = {**__import__("os").environ, "PATH": f"{fake_bin}:{__import__('os').environ['PATH']}"}
            self.assertEqual(0, subprocess.run(["bash", "-c", script,], env=env).returncode)
            write_manifest(components / ".nvr_monitor.stage")
            self.assertNotEqual(0, subprocess.run(["bash", "-c", script], env=env).returncode)
            (components / ".nvr_monitor.stage/manifest.json").write_text("{")
            self.assertNotEqual(0, subprocess.run(["bash", "-c", script], env=env).returncode)

    def test_production_helper_transaction_and_log_matrix(self) -> None:
        """Exercise the extracted transaction driver with only temp directories/fakes."""
        helper = re.search(r"# DEPLOY_LAYOUT_HELPER_BEGIN\n(.*?)# DEPLOY_LAYOUT_HELPER_END", DEPLOY, re.DOTALL).group(1)
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir); helper_path = root / "helper.sh"; helper_path.write_text(helper)
            fake = root / "bin"; fake.mkdir()
            (fake / "jq").write_text("#!/usr/bin/env python3\nimport json,sys\n\ntry: d=json.load(open(sys.argv[-1]))\nexcept: raise SystemExit(1)\ne=' '.join(sys.argv); ok=isinstance(d,dict)\nif '.version == $v' in e: ok &= d.get('version')==sys.argv[sys.argv.index('--arg')+2]\nraise SystemExit(0 if ok else 1)\n")
            (fake / "ha").write_text("#!/bin/bash\nif [ \"$1 $2\" = 'core check' ]; then echo check >>\"$HA_CHECKS\"; if [ -n \"${HA_FAIL_ONCE:-}\" ] && [ ! -e \"$HA_FAIL_ONCE\" ]; then touch \"$HA_FAIL_ONCE\"; exit 1; fi; fi\nif [ \"$1 $2\" = 'core logs' ]; then printf '%s\\n' \"$HA_LOGS\"; exit \"${HA_LOG_STATUS:-0}\"; fi\nexit 0\n")
            for f in fake.iterdir(): f.chmod(0o755)
            cc=root/"cc"; art=root/"art"; art.mkdir(); canonical=cc/DOMAIN
            def component(path, version="0.2.2"):
                write_manifest(path); (path/"manifest.json").write_text(json.dumps({"domain":DOMAIN,"version":version})); (path/"__init__.py").touch()
            component(canonical, "0.2.1"); component(root/"rollback_source", "0.2.1"); component(root/"candidate", "0.2.2")
            checks=root/"checks"; env={**__import__('os').environ,"PATH":f"{fake}:{__import__('os').environ['PATH']}","HA_CHECKS":str(checks)}
            base=f"source {helper_path}; CUSTOM_COMPONENTS={cc}; CANONICAL={canonical}; ARTIFACT_DIR={art}; EXPECTED_VERSION=0.2.2; "
            # Success then a separate rollback transaction: no collision/nesting.
            command=base+f"begin_transaction; cp -a {root}/candidate/. $STAGE; swap_verified_stage; ROLLBACK_SOURCE=$REPLACED; begin_transaction; EXPECTED_VERSION=0.2.1; cp -a $ROLLBACK_SOURCE/. $STAGE; swap_verified_stage"
            self.assertEqual(0, subprocess.run(["bash","-c",command],env=env).returncode)
            self.assertGreaterEqual(len(list(art.glob("transaction.*"))),2)
            # Invalid rollback version fails before canonical is moved.
            bad=base+f"begin_transaction; cp -a {root}/rollback_source/. $STAGE; EXPECTED_VERSION=9; ! swap_verified_stage; test -d $CANONICAL"
            self.assertEqual(0, subprocess.run(["bash","-c",bad],env=env).returncode)
            # core-check failure restores the original canonical.
            fail_once=root/"fail_once"; fail=base+f"begin_transaction; cp -a {root}/candidate/. $STAGE; export HA_FAIL_ONCE={fail_once}; ! swap_verified_stage; grep -F '\"version\": \"0.2.1\"' $CANONICAL/manifest.json; grep -F '\"version\": \"0.2.2\"' $FAILED/manifest.json; test $(wc -l < $HA_CHECKS) -ge 2"
            self.assertEqual(0, subprocess.run(["bash","-c",fail],env=env).returncode)
            # A forced device mismatch fails before moving the canonical directory.
            mismatch=base+"begin_transaction; cp -a $CANONICAL/. $STAGE; stat(){ [ \"$3\" = \"$STAGE\" ] && echo 1 || echo 2; }; ! swap_verified_stage; jq -e '.version == \"0.2.1\"' $CANONICAL/manifest.json"
            self.assertEqual(0, subprocess.run(["bash","-c",mismatch],env=env).returncode)
            # Directly execute production log filter semantics from DEPLOY.md.
            cases = [
                {
                    "name": "no_ansi_normal_timestamp",
                    "logs": "2026-01-02 00:00:00 new\nTraceback",
                    "status": 0,
                    "expect_nonzero": False,
                    "expect_traceback": True,
                },
                {
                    "name": "ansi_prefixed_marker_timestamp",
                    "logs": "\x1b[32m2026-01-02 00:00:00 new\x1b[0m\nTraceback",
                    "status": 0,
                    "expect_nonzero": False,
                    "expect_traceback": True,
                },
                {
                    "name": "ansi_prefixed_multiline_traceback",
                    "logs": (
                        "\x1b[36m2026-01-02 00:00:00 new\x1b[0m\n"
                        "\x1b[31mTraceback (most recent call last):\x1b[0m\n"
                        "  File \"x.py\", line 1, in <module>"
                    ),
                    "status": 0,
                    "expect_nonzero": False,
                    "expect_traceback": True,
                },
                {
                    "name": "only_old_timestamp_fails_closed",
                    "logs": "2026-01-01 00:00:00 old\nTraceback",
                    "status": 0,
                    "expect_nonzero": True,
                    "expect_traceback": False,
                },
                {
                    "name": "no_timestamp_fails_closed",
                    "logs": "Traceback without timestamp",
                    "status": 0,
                    "expect_nonzero": True,
                    "expect_traceback": False,
                },
                {
                    "name": "ha_core_logs_partial_output_then_exit7_propagates",
                    "logs": "2026-01-02 00:00:00 partial\nTraceback",
                    "status": 7,
                    "expect_nonzero": True,
                    "expect_traceback": False,
                },
            ]
            for case in cases:
                with self.subTest(case=case["name"]):
                    output = root / f"log_{case['name']}"
                    cmd = (
                        base
                        + f"set -euo pipefail; "
                        + f"filter_logs_after_marker '2026-01-02 00:00:00' {output}; "
                        + f"grep -iE 'traceback' {output} || true"
                    )
                    result = subprocess.run(
                        ["bash", "-c", cmd],
                        env={
                            **env,
                            "HA_LOGS": case["logs"],
                            "HA_LOG_STATUS": str(case["status"]),
                        },
                    )
                    if case["expect_nonzero"]:
                        self.assertNotEqual(0, result.returncode)
                    else:
                        self.assertEqual(0, result.returncode)
                    if case["expect_traceback"]:
                        self.assertIn("Traceback", output.read_text())

    def test_read_only_paths_do_not_require_backups(self) -> None:
        self.assertIn(
            "唯讀 preflight、smoke 與 observation 不建立備份",
            DEPLOY,
        )
        self.assertNotIn("共用備份（所有路徑都必須先執行）", DEPLOY)
        self.assertNotIn("若 config-entry backup 任一步驟失敗", DEPLOY)

    def test_rollbacks_are_scoped_to_the_mutated_component(self) -> None:
        self.assertIn("ADDON_REMOTE_DIR.old", DEPLOY)
        self.assertIn('ROLLBACK_SOURCE="$REPLACED"', DEPLOY)
        self.assertNotIn("integration_predeploy", DEPLOY)
        self.assertNotIn('"$BACKUP/addon"', DEPLOY)


if __name__ == "__main__":
    unittest.main()
