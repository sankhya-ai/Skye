"""Where credentials live: a 0600 file next to the data, or the macOS Keychain.

The server (and tests, and a hosted install) keep secrets in 0600 JSON files
under the data directory, as they always have. The macOS app sets
``TURA_CODE_SECRETS=keychain``: credentials then live in the login Keychain,
one item per secret, and the files hold none. Moving to the Keychain migrates
whatever a file still holds, and ``settings.json`` records the move
(``"secrets": "keychain"``) so every root on that data directory — the app,
and ``make run`` beside it — reads the Keychain from then on.

Callers see one small interface — ``get``, ``set``, ``delete`` — and a
``inline`` flag: an inline vault means "keep it in your own file, as before".
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

SECRETS_ENV = "TURA_CODE_SECRETS"


class Vault:
    """Secrets kept by the caller in its own 0600 file."""

    inline = True
    name = "file"

    def get(self, key: str) -> str:
        return ""

    def set(self, key: str, value: str) -> None:
        return None

    def delete(self, key: str) -> None:
        return None


class KeychainVault(Vault):
    """One login-Keychain item per secret, under a service named for the data directory."""

    inline = False
    name = "keychain"

    def __init__(self, root: Path) -> None:
        digest = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:8]
        self.service = f"root ({digest})"

    def get(self, key: str) -> str:
        import keyring

        try:
            return keyring.get_password(self.service, key) or ""
        except Exception as error:  # noqa: BLE001 - a locked or missing Keychain reads as "no secret"
            logger.warning("Keychain read of %s failed: %s", key, error)
            return ""

    def set(self, key: str, value: str) -> None:
        import keyring

        if not value:
            self.delete(key)
            return
        keyring.set_password(self.service, key, value)

    def delete(self, key: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(self.service, key)
        except PasswordDeleteError:
            pass


MARKER = "secrets"


def moved_to_keychain(root: Path) -> bool:
    """Whether this data directory's secrets already live in the Keychain."""
    try:
        return json.loads((root / "settings.json").read_text("utf-8")).get(MARKER) == "keychain"
    except (OSError, ValueError, AttributeError):
        return False


def for_root(root: Path) -> Vault:
    """The vault for a data directory: the Keychain when asked for (or moved there) and available."""
    if os.environ.get(SECRETS_ENV, "").strip().lower() != "keychain" and not moved_to_keychain(root):
        return Vault()
    try:
        import keyring  # noqa: F401
    except ImportError:
        logger.warning("%s=keychain, but the keyring package is missing: keeping secrets in files", SECRETS_ENV)
        return Vault()
    return KeychainVault(root)
