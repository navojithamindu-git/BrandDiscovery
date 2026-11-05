"""Small encrypted file vault for the POC.

Production deployments should replace this with a managed secret store. Tokens are
never returned by API responses or written to the application database.
"""
import json
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class TokenVaultError(RuntimeError):
    pass


class TokenVault:
    def __init__(self, key: str | None, path: str):
        if not key:
            raise TokenVaultError("META_TOKEN_ENCRYPTION_KEY is required for OAuth token storage")
        try:
            self.cipher = Fernet(key.encode())
        except Exception as exc:
            raise TokenVaultError("META_TOKEN_ENCRYPTION_KEY must be a valid Fernet key") from exc
        self.path = Path(path)

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.cipher.decrypt(self.path.read_bytes()).decode())
        except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
            raise TokenVaultError("encrypted token vault cannot be opened") from exc

    def _write(self, values: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_bytes(self.cipher.encrypt(json.dumps(values, separators=(",", ":")).encode()))
        temporary.replace(self.path)

    def get(self, ref: str) -> str | None:
        return self._read().get(ref)

    def set(self, ref: str, token: str) -> None:
        values = self._read()
        values[ref] = token
        self._write(values)

    def delete(self, ref: str) -> None:
        values = self._read()
        values.pop(ref, None)
        self._write(values)

