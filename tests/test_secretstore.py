import os
import sys
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

from picnic_mcp import secretstore


def done(code=0, out="", err=""):
    return SimpleNamespace(returncode=code, stdout=out, stderr=err)


class LinuxStoreTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(secretstore.shutil, "which", return_value="/usr/bin/secret-tool")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_secret_goes_in_on_stdin_never_in_the_arguments(self):
        with mock.patch.object(secretstore, "_run", return_value=done()) as run:
            secretstore.LinuxStore().set("svc", "acct", "s3cret")
        cmd = run.call_args.args[0]
        self.assertNotIn("s3cret", " ".join(cmd))
        self.assertEqual(run.call_args.kwargs["stdin"], "s3cret")

    def test_lookup_returns_the_secret_and_none_when_missing(self):
        store = secretstore.LinuxStore()
        with mock.patch.object(secretstore, "_run", return_value=done(0, "s3cret")):
            self.assertEqual(store.get("svc", "acct"), "s3cret")
        with mock.patch.object(secretstore, "_run", return_value=done(1)):
            self.assertIsNone(store.get("svc", "acct"))

    def test_a_failing_keyring_is_an_error_with_the_hint(self):
        with mock.patch.object(secretstore, "_run", return_value=done(1, "", "Cannot autolaunch D-Bus")):
            with self.assertRaises(secretstore.SecretStoreError) as ctx:
                secretstore.LinuxStore().get("svc", "acct")
        self.assertIn("libsecret-tools", str(ctx.exception))

    def test_missing_secret_tool_says_what_to_install(self):
        with mock.patch.object(secretstore.shutil, "which", return_value=None):
            with self.assertRaises(secretstore.SecretStoreError) as ctx:
                secretstore.LinuxStore().get("svc", "acct")
        self.assertIn("libsecret-tools", str(ctx.exception))


class MacStoreTests(unittest.TestCase):
    def test_read_decodes_the_secret_without_using_the_security_tool(self):
        store = secretstore.MacStore()
        with mock.patch.object(store, "_lookup", return_value=(0, "tök".encode())), \
                mock.patch.object(secretstore, "_run") as run:
            self.assertEqual(store.get("svc", "acct"), "tök")
        run.assert_not_called()  # the tool is not on the item's access list and would prompt

    def test_not_found_is_none_and_other_failures_raise(self):
        store = secretstore.MacStore()
        with mock.patch.object(store, "_lookup", return_value=(store.NOT_FOUND, None)):
            self.assertIsNone(store.get("svc", "acct"))
        with mock.patch.object(store, "_lookup", return_value=(-25293, None)):
            with self.assertRaises(secretstore.SecretStoreError):
                store.get("svc", "acct")

    def test_delete_ignores_a_missing_item_but_reports_other_failures(self):
        store = secretstore.MacStore()
        with mock.patch.object(store, "_lookup", return_value=(store.NOT_FOUND, None)) as lookup:
            store.delete("svc", "acct")
        self.assertTrue(lookup.call_args.kwargs["delete"])
        with mock.patch.object(store, "_lookup", return_value=(-25293, None)):
            with self.assertRaises(secretstore.SecretStoreError):
                store.delete("svc", "acct")


@unittest.skipUnless(sys.platform == "win32", "Windows Credential Manager only")
class WindowsStoreTests(unittest.TestCase):
    """Real round trip against the Credential Manager, under a throwaway name that is cleaned up."""

    def test_roundtrip_overwrite_and_delete(self):
        store = secretstore.WindowsStore()
        service, account = "picnic-mcp-test-" + uuid.uuid4().hex, "token-xx"
        self.addCleanup(store.delete, service, account)
        self.assertIsNone(store.get(service, account))
        store.set(service, account, "first-Ünïcode")
        self.assertEqual(store.get(service, account), "first-Ünïcode")
        store.set(service, account, "second")
        self.assertEqual(store.get(service, account), "second")
        store.delete(service, account)
        self.assertIsNone(store.get(service, account))

    def test_oversized_secret_is_refused(self):
        with self.assertRaises(secretstore.SecretStoreError):
            secretstore.WindowsStore().set("picnic-mcp-test", "big", "x" * 5000)


@unittest.skipUnless(os.environ.get("PICNIC_REAL_STORE") == "1", "set PICNIC_REAL_STORE=1 to use this system's real credential store")
class RealStoreTests(unittest.TestCase):
    """Round trip through whichever store this operating system provides. Runs in CI on all three systems."""

    def test_default_store_roundtrip(self):
        store = secretstore.default_store()
        service, account = "picnic-mcp-test-" + uuid.uuid4().hex, "token-xx"
        self.addCleanup(store.delete, service, account)
        self.assertIsNone(store.get(service, account))
        store.set(service, account, "first-Ünïcode")
        self.assertEqual(store.get(service, account), "first-Ünïcode")
        store.set(service, account, "second")
        self.assertEqual(store.get(service, account), "second")
        store.delete(service, account)
        self.assertIsNone(store.get(service, account))


class DefaultStoreTests(unittest.TestCase):
    def test_picks_a_backend_per_platform(self):
        for platform, cls in (("darwin", "MacStore"), ("linux", "LinuxStore")):
            with mock.patch.object(secretstore.sys, "platform", platform):
                self.assertEqual(type(secretstore.default_store()).__name__, cls)


if __name__ == "__main__":
    unittest.main()
