"""Anonymous trial admission and one-task DeepSeek credential encryption."""

import os
import re
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

TRIAL_LIMIT = 3
NETWORK_WINDOW = 86400
KEY_TTL = 300
SESSION_KEY_TTL = 7 * 86400


def trial_enabled():
    return os.environ.get("TRAVEL_TRIAL_ENABLED") == "1"


def _cipher():
    try:
        return Fernet(os.environ.get("TRAVEL_TASK_KEY_ENCRYPTION", ""))
    except (ValueError, TypeError):
        raise HTTPException(503, "trial_not_configured") from None


def check_key(key):
    if not key or not re.fullmatch(r"sk-[a-zA-Z0-9_-]{12,160}", key):
        raise HTTPException(422, "invalid_deepseek_key_format")
    return key


def seal_key(scope, key):
    return _cipher().encrypt(f"{scope}:{key}".encode())


@dataclass(frozen=True)
class Admission:
    network: str
    cap: int

    @classmethod
    def from_headers(cls, headers):
        try:
            cap = int(os.environ.get("TRAVEL_TRIAL_GLOBAL_CAP", "0"))
            if not 0 <= cap <= 1000:
                raise ValueError
        except ValueError:
            raise HTTPException(503, "trial_not_configured") from None
        network = headers.get("x-travel-network", "")
        if not re.fullmatch(r"[a-f0-9]{64}", network):
            network = ""
        return cls(network, cap)

    def admit(self, db, owner, task_id, now):
        saved = db.execute(
            "SELECT encrypted_key FROM session_credentials WHERE owner=? AND expires>=?",
            (owner, now),
        ).fetchone()
        if saved:
            key = open_key(owner, saved[0], ttl=SESSION_KEY_TTL)
            token = seal_key(task_id, key)
            db.execute("INSERT INTO task_admission VALUES(?,?,?)", (task_id, token, now + KEY_TTL))
            return
        if not self.network or not self.cap:
            raise HTTPException(403, "own_deepseek_key_required")
        counts = db.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(CASE WHEN owner=? THEN 1 ELSE 0 END),0) "
            "AS session_count, COALESCE(SUM(CASE WHEN network=? AND created>=? "
            "THEN 1 ELSE 0 END),0) AS network_count FROM trial_grants",
            (owner, self.network, now - NETWORK_WINDOW),
        ).fetchone()
        total, session, network = (counts[index] for index in range(3))
        if total >= self.cap or session >= TRIAL_LIMIT or network >= TRIAL_LIMIT:
            raise HTTPException(403, "own_deepseek_key_required")
        db.execute("INSERT INTO trial_grants VALUES(?,?,?,?)", (task_id, owner, self.network, now))
        db.execute("INSERT INTO task_admission VALUES(?,NULL,NULL)", (task_id,))


def open_key(scope, token, *, ttl=KEY_TTL):
    try:
        bound_id, key = _cipher().decrypt(token, ttl=ttl).decode().split(":", 1)
        if bound_id == scope and key:
            return key
    except (InvalidToken, UnicodeError, ValueError, HTTPException):
        pass
    raise HTTPException(409, "task_key_expired")
