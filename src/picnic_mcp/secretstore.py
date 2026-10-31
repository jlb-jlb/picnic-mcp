"""Store one secret in the operating system's own credential store, standard library only.

Windows uses the Credential Manager through ctypes, macOS the Keychain (reads through the
`security` tool, writes through the Security framework so the secret never appears in a process
list), and Linux the desktop keyring through `secret-tool` (the secret goes in on stdin). There is
deliberately no file fallback: when no store is available the call raises SecretStoreError with a
message that says what to install.
"""

import shutil
import subprocess
import sys


class SecretStoreError(Exception):
    pass


def _run(cmd, stdin=None):
    try:
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SecretStoreError(f"could not run {cmd[0]}: {exc}") from exc


class LinuxStore:
    """Secret Service (GNOME Keyring, KWallet) through libsecret's secret-tool."""

    HINT = (
        "no desktop keyring found. Install libsecret's secret-tool (Debian/Ubuntu: apt install "
        "libsecret-tools, Fedora: dnf install libsecret) and a keyring such as gnome-keyring, and run "
        "inside a desktop session. Headless servers and bare WSL have none."
    )

    def _tool(self):
        if not shutil.which("secret-tool"):
            raise SecretStoreError(self.HINT)
        return "secret-tool"

    def get(self, service, account):
        result = _run([self._tool(), "lookup", "service", service, "account", account])
        # Exit code 1 with no output simply means the item does not exist.
        if result.returncode == 0 and result.stdout:
            return result.stdout
        if result.returncode not in (0, 1) or result.stderr.strip():
            raise SecretStoreError(f"secret-tool failed: {result.stderr.strip() or result.returncode}. {self.HINT}")
        return None

    def set(self, service, account, value):
        result = _run(
            [self._tool(), "store", f"--label={service} {account}", "service", service, "account", account],
            stdin=value,
        )
        if result.returncode != 0:
            raise SecretStoreError(f"secret-tool could not store the secret: {result.stderr.strip()}. {self.HINT}")

    def delete(self, service, account):
        _run([self._tool(), "clear", "service", service, "account", account])


class MacStore:
    """Keychain. Reading and deleting use the security tool, writing uses the Security framework."""

    DUPLICATE_ITEM = -25299

    def get(self, service, account):
        result = _run(["security", "find-generic-password", "-s", service, "-a", account, "-w"])
        if result.returncode == 0:
            return result.stdout.rstrip("\n") or None
        if result.returncode == 44:  # errSecItemNotFound
            return None
        raise SecretStoreError(f"Keychain read failed: {result.stderr.strip() or result.returncode}")

    def delete(self, service, account):
        _run(["security", "delete-generic-password", "-s", service, "-a", account])

    def set(self, service, account, value):
        import ctypes

        security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        add = security.SecKeychainAddGenericPassword
        add.restype = ctypes.c_int32
        add.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p,
            ctypes.c_uint32, ctypes.c_char_p, ctypes.c_void_p,
        ]
        svc, acct, secret = service.encode(), account.encode(), value.encode()
        status = add(None, len(svc), svc, len(acct), acct, len(secret), secret, None)
        if status == self.DUPLICATE_ITEM:
            self.delete(service, account)
            status = add(None, len(svc), svc, len(acct), acct, len(secret), secret, None)
        if status != 0:
            raise SecretStoreError(f"Keychain write failed with status {status}")


class WindowsStore:
    """Credential Manager, bound to the signed-in Windows user."""

    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    MAX_BLOB = 2560
    NOT_FOUND = 1168

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class Credential(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        self._ctypes = ctypes
        self._Credential = Credential
        self._api = ctypes.WinDLL("advapi32", use_last_error=True)
        api = self._api
        pointer = ctypes.POINTER(Credential)
        api.CredWriteW.argtypes = [pointer, wintypes.DWORD]
        api.CredWriteW.restype = wintypes.BOOL
        api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(pointer)]
        api.CredReadW.restype = wintypes.BOOL
        api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        api.CredDeleteW.restype = wintypes.BOOL
        api.CredFree.argtypes = [ctypes.c_void_p]
        api.CredFree.restype = None

    @staticmethod
    def _target(service, account):
        return f"{service}:{account}"

    def get(self, service, account):
        ctypes = self._ctypes
        out = ctypes.POINTER(self._Credential)()
        if not self._api.CredReadW(self._target(service, account), self.CRED_TYPE_GENERIC, 0, ctypes.byref(out)):
            code = ctypes.get_last_error()
            if code == self.NOT_FOUND:
                return None
            raise SecretStoreError(f"Credential Manager read failed with error {code}")
        try:
            cred = out.contents
            return ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize).decode("utf-8")
        finally:
            self._api.CredFree(out)

    def set(self, service, account, value):
        ctypes = self._ctypes
        blob = value.encode("utf-8")
        if len(blob) > self.MAX_BLOB:
            raise SecretStoreError("secret is too large for the Credential Manager")
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = self._Credential()
        cred.Type = self.CRED_TYPE_GENERIC
        cred.TargetName = self._target(service, account)
        cred.UserName = account
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        cred.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        if not self._api.CredWriteW(ctypes.byref(cred), 0):
            raise SecretStoreError(f"Credential Manager write failed with error {ctypes.get_last_error()}")

    def delete(self, service, account):
        self._api.CredDeleteW(self._target(service, account), self.CRED_TYPE_GENERIC, 0)


def default_store():
    if sys.platform == "win32":
        return WindowsStore()
    if sys.platform == "darwin":
        return MacStore()
    return LinuxStore()
