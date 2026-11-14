"""python-oracledb driver mode (thin / thick), chosen once per process.

Thin mode needs nothing installed but cannot log in users whose password only has the old 10G
verifier (DPY-3015) and lacks a few other legacy features. Thick mode loads Oracle Instant Client
and supports them. The mode is process-wide and must be fixed before the first connection, so it is
decided lazily by the first Oracle connection (or at startup) and never changes afterwards.

Environment:
  DBMON_ORACLE_MODE            auto (default) | thick | thin
                                 auto  -> thick if Instant Client can be loaded, otherwise thin
                                 thick -> thick; if Instant Client cannot be loaded, stay thin and
                                          report why in connection errors
                                 thin  -> never load Instant Client
  DBMON_ORACLE_CLIENT_LIB_DIR  directory of Instant Client (optional on Linux when it is in the
                               system library path; required on macOS/Windows unless on the
                               default search path).
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

import oracledb

log = logging.getLogger(__name__)

_lock = threading.Lock()
_decided = False
_thick_error: Optional[str] = None


def ensure_mode() -> None:
    """Decide thin/thick once. Safe to call many times and from many threads."""
    global _decided, _thick_error
    if _decided:
        return
    with _lock:
        if _decided:
            return
        mode = os.environ.get("DBMON_ORACLE_MODE", "auto").strip().lower() or "auto"
        if mode not in ("auto", "thick", "thin"):
            log.warning("DBMON_ORACLE_MODE=%r no es válido (auto|thick|thin); se usa auto", mode)
            mode = "auto"
        if mode != "thin":
            lib_dir = os.environ.get("DBMON_ORACLE_CLIENT_LIB_DIR") or None
            try:
                oracledb.init_oracle_client(lib_dir=lib_dir)
                log.info("Oracle: modo thick (Instant Client %s)",
                         ".".join(map(str, oracledb.clientversion())))
            except Exception as e:  # DPI-1047 (library not found) and friends
                _thick_error = str(e).strip().splitlines()[0] if str(e).strip() else repr(e)
                level = logging.WARNING if mode == "thick" else logging.INFO
                log.log(level, "Oracle: modo thin (Instant Client no disponible: %s)", _thick_error)
        else:
            log.info("Oracle: modo thin (DBMON_ORACLE_MODE=thin)")
        _decided = True


def is_thick() -> bool:
    return not oracledb.is_thin_mode()


def thick_error() -> Optional[str]:
    """Why Instant Client could not be loaded (None if it loaded or was never tried)."""
    return _thick_error


def mode_label() -> str:
    return "thick" if is_thick() else "thin"
