"""
Secrets stored in the database, encrypted with MATE_SECRET_KEY.

The columns that hold a secret MATE has to read back (a Slack bot token and
signing secret, a trigger's signing secret, an agent's model API key) use the
EncryptedText column type. It encrypts a value on its way into the database and
decrypts it on its way out, so the code that reads and writes those columns
sees plain text and does not change.

MATE_SECRET_KEY is a Fernet key, separate from SECRET_KEY so the session secret
can be rotated without losing the stored secrets (documents/design/workspaces.md,
D7). Without it values are stored in the clear, as before 1.6, and the auth server
warns at startup. Values written before the key was set are encrypted by
encrypt_stored_secrets() when the auth server next starts.

A stored value is either plain text or "enc:v1:" followed by a Fernet token, so
both can sit in one column while an installation moves over. A value that cannot
be decrypted (no key, or another key) reads as None and logs an error: a webhook
signature then fails to verify instead of being checked against the wrong secret.
"""

import json
import logging
import os
from functools import lru_cache
from typing import Dict, Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import String, Text, cast, select, update
from sqlalchemy.types import TypeDecorator

logger = logging.getLogger(__name__)

ENV_VAR = "MATE_SECRET_KEY"
PREFIX = "enc:v1:"


@lru_cache(maxsize=4)
def _fernet_for(key: str) -> Fernet:
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as e:
        raise RuntimeError(
            f"{ENV_VAR} is not a valid Fernet key (32 url-safe base64-encoded bytes). "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        ) from e


def _fernet() -> Optional[Fernet]:
    key = os.getenv("MATE_SECRET_KEY", "").strip()
    return _fernet_for(key) if key else None


def key_configured() -> bool:
    """Whether MATE_SECRET_KEY is set. Raises RuntimeError when it is set but invalid."""
    return _fernet() is not None


def is_encrypted(value: Optional[str]) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(value: Optional[str]) -> Optional[str]:
    """The stored form of a secret: encrypted with a key, unchanged without one."""
    if not value or is_encrypted(value):
        return value
    fernet = _fernet()
    if fernet is None:
        return value
    return PREFIX + fernet.encrypt(value.encode()).decode()


def decrypt(value: Optional[str]) -> Optional[str]:
    """The secret behind a stored value; None when it cannot be decrypted."""
    if not is_encrypted(value):
        return value
    fernet = _fernet()
    if fernet is None:
        logger.error("A stored secret is encrypted but %s is not set; it reads as empty", ENV_VAR)
        return None
    try:
        return fernet.decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        logger.error("A stored secret does not decrypt with %s (another key?); it reads as empty",
                     ENV_VAR)
        return None


class EncryptedText(TypeDecorator):
    """A text column whose values are encrypted at rest with MATE_SECRET_KEY."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt(value)

    def process_result_value(self, value, dialect):
        return decrypt(value)


def _secret_columns():
    from .models import AgentConfig, AgentTrigger, ChannelIntegration
    return [
        (ChannelIntegration, ChannelIntegration.bot_token),
        (ChannelIntegration, ChannelIntegration.signing_secret),
        (AgentTrigger, AgentTrigger.signing_secret),
        (AgentConfig, AgentConfig.model_api_key),
    ]


def _stored_in_the_clear(column):
    # cast() reads the stored form, skipping the column type's decryption
    stored = cast(column, String)
    return stored, (column.isnot(None), stored != "", ~stored.like(f"{PREFIX}%"))


def _snapshots_in_the_clear(session):
    """Agent versions whose snapshot holds a model_api_key stored in the clear."""
    from .models import AgentConfigVersion
    for version in session.query(AgentConfigVersion).filter(
            AgentConfigVersion.config_snapshot.like("%model_api_key%")):
        try:
            key = json.loads(version.config_snapshot).get("model_api_key")
        except (ValueError, AttributeError):
            continue
        if key and not is_encrypted(key):
            yield version


def encrypt_stored_secrets(session) -> Dict[str, int]:
    """Encrypt the secrets still stored in the clear; a no-op without MATE_SECRET_KEY.

    Returns the number of values encrypted per column. Safe to run on every start
    and from several processes: a value that is already encrypted is skipped.
    """
    if not key_configured():
        return {}
    counts: Dict[str, int] = {}
    for model, column in _secret_columns():
        stored, in_the_clear = _stored_in_the_clear(column)
        rows = session.execute(select(model.id, stored).where(*in_the_clear)).all()
        for row_id, value in rows:
            session.execute(update(model).where(model.id == row_id).values({column.key: value}))
        session.commit()
        if rows:
            counts[f"{model.__tablename__}.{column.key}"] = len(rows)
    versions = list(_snapshots_in_the_clear(session))
    for version in versions:
        version.set_snapshot(version.get_snapshot())
    session.commit()
    if versions:
        counts["agent_config_versions.config_snapshot"] = len(versions)
    return counts


def count_unencrypted_secrets(session) -> int:
    """How many secrets are stored in the clear, for the startup warning."""
    total = 0
    for model, column in _secret_columns():
        _, in_the_clear = _stored_in_the_clear(column)
        total += session.query(model.id).filter(*in_the_clear).count()
    return total + sum(1 for _ in _snapshots_in_the_clear(session))
