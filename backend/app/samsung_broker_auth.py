from __future__ import annotations

import os
import secrets
from pathlib import Path


_DEFAULT_KEY_PATH = "/data/samsung-login-broker.key"
_MIN_PERSISTED_KEY_LENGTH = 32


def broker_key_path() -> Path:
    return Path(os.getenv("SAMSUNG_LOGIN_BROKER_KEY_PATH", _DEFAULT_KEY_PATH))


def _validated_persisted(value: str, *, source: str) -> str:
    key = value.strip()
    if len(key) < _MIN_PERSISTED_KEY_LENGTH:
        raise RuntimeError(f"Samsung login-broker key fra {source} er ugyldig")
    return key


def broker_key() -> str:
    """Return one shared broker secret, persisted safely when env is absent.

    Existing deployments can continue to provide any non-empty
    SAMSUNG_LOGIN_BROKER_KEY unchanged for backward compatibility. Otherwise
    both the mobile API and isolated login broker share the same strong random
    key through their existing persistent /data volume. O_EXCL makes first-start
    generation safe even when both containers start concurrently.
    """
    configured = os.getenv("SAMSUNG_LOGIN_BROKER_KEY", "").strip()
    if configured:
        return configured

    path = broker_key_path()
    try:
        return _validated_persisted(path.read_text("utf-8"), source=str(path))
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RuntimeError(f"Samsung login-broker key kunne ikke læses: {path}") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_urlsafe(48)

    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another container won the first-start race. Use its key.
        try:
            return _validated_persisted(path.read_text("utf-8"), source=str(path))
        except OSError as exc:
            raise RuntimeError(f"Samsung login-broker key kunne ikke læses: {path}") from exc
    except OSError as exc:
        raise RuntimeError(f"Samsung login-broker key kunne ikke oprettes: {path}") from exc

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(generated + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, 0o600)
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise

    return generated
