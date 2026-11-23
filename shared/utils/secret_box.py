"""
Secrets stored in the database, encrypted with MATE_SECRET_KEY.

The columns that hold a secret MATE has to read back (a Slack bot token and
signing secret, a trigger's signing secret, an agent's model API key, a workspace's
provider key) use the
EncryptedText column type. It encrypts a value on its way into the database and
decrypts it on its way out, so the code that reads and writes those columns
sees plain text and does not change. The JSON columns that hold secrets among other
settings (an agent's MCP servers and tools, where a trigger and an alert rule send) use
EncryptedSecrets, which does it for those values only (config_secrets.py). A version's
snapshot is stored the same way.

MATE_SECRET_KEY is a Fernet key, separate from SECRET_KEY so the session secret
can be rotated without losing the stored secrets (documents/design/workspaces.md,
D7). Without it values are stored in the clear, as before 1.6, and the auth server
warns at startup; in production (MATE_ENV=production) it refuses to start, unless
MATE_ALLOW_INSECURE_DEFAULTS is set. Values written before the key was set are encrypted by
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


def check_key_at_startup(enforce: bool) -> None:
    """Refuse to start without MATE_SECRET_KEY when enforce is set (production, without
    MATE_ALLOW_INSECURE_DEFAULTS); otherwise warn."""
    if key_configured():
        return
    if enforce:
        raise RuntimeError(
            f"{ENV_VAR} is not set. Slack tokens, signing secrets, model keys and the keys of "
            f"MCP servers and webhooks would be stored in the clear. Set {ENV_VAR} (see docs/dev/deployment.md), or set "
            "MATE_ALLOW_INSECURE_DEFAULTS=true to override.")
    logger.warning(
        "%s not set: Slack tokens, signing secrets, agent model keys and the keys of MCP "
        "servers and webhooks are stored in the clear. Production refuses to start without it. See docs/dev/deployment.md.", ENV_VAR)


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


class EncryptedSecrets(TypeDecorator):
    """A JSON text column whose secrets are encrypted at rest (config_secrets.py)."""

    impl = Text
    cache_ok = True

    def __init__(self, field: str) -> None:
        super().__init__()
        self.field = field

    def process_bind_param(self, value, dialect):
        from .config_secrets import encrypt_secrets
        return encrypt_secrets(self.field, value) if isinstance(value, str) else value

    def process_result_value(self, value, dialect):
        from .config_secrets import decrypt_secrets
        return decrypt_secrets(value)


def encrypt_snapshot(snapshot: dict) -> dict:
    """An agent version's snapshot as stored: its secrets encrypted."""
    from .config_secrets import FIELDS, encrypt_secrets
    stored = dict(snapshot)
    if stored.get("model_api_key"):
        stored["model_api_key"] = encrypt(stored["model_api_key"])
    for field in FIELDS:
        if isinstance(stored.get(field), str):
            stored[field] = encrypt_secrets(field, stored[field])
    return stored


def decrypt_snapshot(stored: dict) -> dict:
    from .config_secrets import FIELDS, decrypt_secrets
    snapshot = dict(stored)
    if snapshot.get("model_api_key"):
        snapshot["model_api_key"] = decrypt(snapshot["model_api_key"])
    for field in FIELDS:
        if isinstance(snapshot.get(field), str):
            snapshot[field] = decrypt_secrets(snapshot[field])
    return snapshot


def _secret_columns():
    from .models import AgentConfig, AgentTrigger, ChannelIntegration, WorkspaceProviderKey
    return [
        (ChannelIntegration, ChannelIntegration.bot_token),
        (ChannelIntegration, ChannelIntegration.signing_secret),
        (AgentTrigger, AgentTrigger.signing_secret),
        (AgentConfig, AgentConfig.model_api_key),
        (WorkspaceProviderKey, WorkspaceProviderKey.api_key),
    ]


def _stored_in_the_clear(column):
    # cast() reads the stored form, skipping the column type's decryption
    stored = cast(column, String)
    return stored, (column.isnot(None), stored != "", ~stored.like(f"{PREFIX}%"))


def _json_columns():
    from .models import AgentConfig, AgentTrigger, AlertRule
    return [
        (AgentConfig, AgentConfig.mcp_servers_config),
        (AgentConfig, AgentConfig.tool_config),
        (AgentTrigger, AgentTrigger.output_config),
        (AlertRule, AlertRule.destination_config),
    ]


def _json_in_the_clear(session, model, column):
    """(id, stored text) of the rows with a secret stored in the clear."""
    from .config_secrets import has_secret_in_the_clear
    stored = cast(column, String)
    for row_id, value in session.execute(select(model.id, stored).where(column.isnot(None))).all():
        if has_secret_in_the_clear(column.key, value):
            yield row_id, value


def _snapshots_in_the_clear(session):
    """Agent versions whose snapshot holds a secret stored in the clear."""
    from .config_secrets import FIELDS, has_secret_in_the_clear
    from .models import AgentConfigVersion
    for version in session.query(AgentConfigVersion):
        try:
            stored = json.loads(version.config_snapshot)
            key = stored.get("model_api_key")
        except (ValueError, AttributeError):
            continue
        if (key and not is_encrypted(key)) or any(
                has_secret_in_the_clear(field, stored.get(field)) for field in FIELDS):
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
    for model, column in _json_columns():
        rows = list(_json_in_the_clear(session, model, column))
        # Not an edit: a row keeps the time of its last one
        kept = {"updated_at": model.updated_at} if hasattr(model, "updated_at") else {}
        for row_id, value in rows:
            session.execute(update(model).where(model.id == row_id).values({column.key: value, **kept}))
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
    for model, column in _json_columns():
        total += sum(1 for _ in _json_in_the_clear(session, model, column))
    return total + sum(1 for _ in _snapshots_in_the_clear(session))
