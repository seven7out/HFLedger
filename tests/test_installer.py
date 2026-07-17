import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest

from core import store
from install.generate import configure_install
from tests.helpers import ROOT


INSTALLER = os.path.join(ROOT, "install", "ledger-install")


class InstallerTests(unittest.TestCase):
    def test_installer_creates_config_packs_and_inactive_schedules(self):
        with tempfile.TemporaryDirectory(prefix="ledger-installer-tests-") as temporary:
            home = os.path.join(temporary, "ledger-home")
            result = subprocess.run([
                INSTALLER, home, "--project", "Fictional orchard tools",
                "--repo", "orchard,example/orchard,stage,main",
                "--local-root", "notes=%s" % temporary,
                "--runtime", "generic", "--runtime", "claude-code",
                "--schedule", "both", "--hour", "6", "--minute", "15",
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertFalse(output["schedules"]["activated"])
            config = store.load_config(home)
            self.assertTrue(config["automation"]["sources"]["github"]["enabled"])
            self.assertTrue(config["automation"]["sources"]["localFiles"]["enabled"])
            self.assertTrue(os.path.exists(os.path.join(home, "generated", "packs", "generic", "AGENTS.md")))
            launchd = output["schedules"]["files"][0]
            with open(launchd, "rb") as handle:
                plist = plistlib.load(handle)
            self.assertEqual(plist["StartCalendarInterval"], {"Hour": 6, "Minute": 15})
            self.assertTrue(os.path.samefile(
                plist["ProgramArguments"][0], os.path.abspath(sys.executable)))
            self.assertEqual(plist["ProgramArguments"][-1], "collect")
            self.assertLess(len(plist["EnvironmentVariables"]["PATH"]), 500)
            service = next(path for path in output["schedules"]["files"] if path.endswith(".service"))
            with open(service, encoding="utf-8") as handle:
                self.assertIn(" collect", handle.read())

    def test_invalid_installer_input_creates_nothing(self):
        with tempfile.TemporaryDirectory(prefix="ledger-installer-invalid-") as temporary:
            home = os.path.join(temporary, "ledger-home")
            with self.assertRaisesRegex(ValueError, "repository"):
                configure_install(home, "Fictional orchard", repositories=["bad"])
            self.assertFalse(os.path.exists(home))

    def test_installer_refuses_initialized_directory(self):
        with tempfile.TemporaryDirectory(prefix="ledger-installer-existing-") as home:
            store.initialize(home, "Fictional orchard")
            result = subprocess.run([
                INSTALLER, home, "--project", "Replacement orchard",
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("refusing to overwrite", result.stderr)


if __name__ == "__main__":
    unittest.main()
