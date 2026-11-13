"""Fernet encryption for stored passwords."""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet

_cache: dict[tuple[str, str], Fernet] = {}


def data_dir() -> Path:
    path = Path(os.environ.get("DBMON_DATA_DIR", "./data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_key(directory: Path) -> bytes:
    env_key = os.environ.get("DBMON_SECRET_KEY", "").strip()
    if env_key:
        return env_key.encode()
    key_file = directory / "secret.key"
    if key_file.exists():
        return key_file.read_bytes().strip()
    key = Fernet.generate_key()
    # Create with 0600 so the key is never world-readable, even briefly.
    fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def _fernet() -> Fernet:
    directory = data_dir()
    cache_key = (os.environ.get("DBMON_SECRET_KEY", ""), str(directory.resolve()))
    if cache_key not in _cache:
        _cache[cache_key] = Fernet(_load_key(directory))
    return _cache[cache_key]


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
