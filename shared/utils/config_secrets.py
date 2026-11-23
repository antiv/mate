"""
The secrets inside an agent's mcp_servers_config and tool_config, kept from whoever
reads the config: the dashboard, an export, read_agent's model.

mask() replaces each with STORED_SECRET_SENTINEL, as mask_api_key does for the model's
key; a ${VAR} reference is no secret and stays. What counts:

- an MCP server's env values, and its header values but for a few that name formats;
- a value whose name says so (api_key, token, password, ...), also as --name value,
  --name=value or NAME=value among a command's args, and in an address's query;
- an address's user and password, and a token-like part of its path (some MCP servers
  take their key there);
- a --header's value, and an argument that looks like a token;
- the address of an agent's approval notifications, since a Slack or Discord webhook's
  address is all it takes to post.

restore() puts the stored values back when a masked config is saved: a text is stored
again when it is exactly the stored one's masked form, in the same place. A hidden value
goes only where it went: with its server's or destination's address, command or args
changed, it is refused, and has to be entered again.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

# Where the hidden values beside them go
_DESTINATION = ("url", "command", "args", "host", "endpoint", "base_url")
# Headers that carry no secret
_OPEN_HEADERS = ("accept", "content-type", "user-agent", "mcp-protocol-version")

_SECRET_NAME = re.compile(
    r"api[_-]?key|access[_-]?key|private[_-]?key|secret|token(?!s|iz|_?count|_?budget|_?limit)|passw|pwd"
    r"|passphrase|credential|authorization|bearer", re.I)
# In a command's arguments and an address's query, also a bare or trailing key, pass or auth
_SECRET_ARGUMENT = re.compile(_SECRET_NAME.pattern + r"|(?:^|[_-])(?:key|pass|auth|sig|signature)$", re.I)
_TOKEN = re.compile(r"(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{20,}"
                    r"|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
_HEADER_FLAGS = ("-h", "--header", "--headers")
_ENV_FLAGS = ("-e", "--env")


class HiddenValue(ValueError):
    """A hidden value can't be put back; says which, for whoever is saving."""


def _sentinel() -> str:
    from .utils import STORED_SECRET_SENTINEL
    return STORED_SECRET_SENTINEL


def _open_part(value: str) -> str:
    """What is left of a value without its ${VAR} references."""
    from .utils import ENV_PLACEHOLDER
    return ENV_PLACEHOLDER.sub("", value)


def _mask_value(value: str) -> str:
    """A secret's masked form: "Bearer x" keeps its scheme, a ${VAR} reference stays."""
    scheme = re.match(r"(?:Bearer|Basic|Token|ApiKey)\s+", value, re.I)
    prefix = scheme.group(0) if scheme else ""
    if not _open_part(value[len(prefix):]).strip():
        return value
    return prefix + _sentinel()


def _mask_url(value: str, path_tokens: bool = True) -> str:
    """An address without its user info, the secrets of its query, and token-like path parts."""
    base, _, query = value.partition("?")
    try:
        parts = urlsplit(base)
        userinfo = parts.netloc.rpartition("@")[0]
    except ValueError:
        return _mask_value(value)
    if userinfo:
        user, colon, password = userinfo.partition(":")
        base = base.replace(f"{userinfo}@", f"{user}:{_mask_value(password)}@" if colon
                            else f"{_mask_value(user)}@", 1)
    if path_tokens:
        head, separator, rest = base.partition("://")
        host, slash, path = rest.partition("/")
        path = "/".join(_mask_value(p) if _TOKEN.search(_open_part(p)) else p for p in path.split("/"))
        base = head + separator + host + slash + path
    if not query:
        return base
    masked = []
    for pair in query.split("&"):
        name, equals, text = pair.partition("=")
        if equals and text and (_SECRET_ARGUMENT.search(name) or _TOKEN.search(_open_part(text))):
            text = _mask_value(text)
        masked.append(name + equals + text)
    return f"{base}?{'&'.join(masked)}"


def _mask_header(value: str) -> str:
    """A "Name: value" text."""
    name, colon, text = value.partition(":")
    if not colon:
        return _mask_value(value)
    if name.strip().lower() in _OPEN_HEADERS:
        return value
    space = text[:len(text) - len(text.lstrip())]
    return name + colon + space + _mask_value(text[len(space):])


def _mask_argument(argument: str, before: str) -> str:
    """One of a command's arguments; before is the one before it."""
    flag = before.lower() if before.startswith("-") else ""
    if flag in _HEADER_FLAGS:
        return _mask_header(argument)
    name, equals, value = argument.partition("=")
    if equals and re.fullmatch(r"-{0,2}[\w.-]+", name):
        if name.lower() in _HEADER_FLAGS:
            return name + equals + _mask_header(value)
        if name.lower() in _ENV_FLAGS:
            return name + equals + _mask_argument(value, "")
        if flag in _ENV_FLAGS or _SECRET_ARGUMENT.search(name):
            return name + equals + _mask_value(value)
    if flag and not argument.startswith("-") and _SECRET_ARGUMENT.search(flag):
        return _mask_value(argument)
    if "://" in argument:
        return _mask_url(argument)
    return _mask_value(argument) if _TOKEN.search(_open_part(argument)) else argument


def _masked(node: Any, key: str = "", secret_map: bool = False) -> Any:
    """A copy of a parsed MCP servers config with its secrets masked."""
    key = key.lower()
    if isinstance(node, dict):
        return {k: _masked(v, str(k), secret_map or key in ("env", "headers")) for k, v in node.items()}
    if isinstance(node, list):
        if key == "args":
            return [_mask_argument(a, node[i - 1] if i and isinstance(node[i - 1], str) else "")
                    if isinstance(a, str) else a for i, a in enumerate(node)]
        return [_masked(v, key, secret_map) for v in node]
    if not isinstance(node, str) or not node:
        return node
    if not secret_map and key in ("env", "headers", "args"):
        # The runtimes decode these once more when they are text
        decoded = _parsed(node)
        if isinstance(decoded, (dict, list)):
            masked = _masked(decoded, key)
            return node if masked == decoded else json.dumps(masked)
        return _mask_argument(node, "") if key == "args" else _mask_header(node)
    if secret_map:
        return node if key in _OPEN_HEADERS else _mask_value(node)
    if _SECRET_NAME.search(key):
        return _mask_value(node)
    return _mask_url(node) if "://" in node else node


def _masked_tools(node: Any, key: str = "", secret_map: bool = False) -> Any:
    """A copy of a parsed tool config with its secrets masked. Addresses keep their paths:
    they name documents and catalogues there."""
    key = key.lower()
    if isinstance(node, dict):
        masked = {k: _masked_tools(v, str(k), secret_map or key in ("env", "headers")) for k, v in node.items()}
        if key == "notify" and isinstance(node.get("url"), str):
            masked["url"] = _mask_value(node["url"]) if node["url"] else ""
        return masked
    if isinstance(node, list):
        return [_masked_tools(v, key, secret_map) for v in node]
    if not isinstance(node, str) or not node:
        return node
    if secret_map:
        return node if key in _OPEN_HEADERS else _mask_value(node)
    if _SECRET_NAME.search(key):
        return _mask_value(node)
    return _mask_url(node, path_tokens=False) if "://" in node else node


# The columns that hold secrets, and how each is masked
FIELDS = {"mcp_servers_config": _masked, "tool_config": _masked_tools}


def _parsed(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def mask(field: str, value: Any) -> Any:
    """The field's value, text or parsed, with its secrets masked. Text that isn't JSON
    configures nothing, and stays."""
    parsed = _parsed(value)
    if not isinstance(parsed, (dict, list)):
        return value
    masked = FIELDS[field](parsed)
    if masked == parsed:
        return value
    return json.dumps(masked) if isinstance(value, str) else masked


def _restored(new: Any, old: Any, masked: Any, path: str, left: List[str]) -> Tuple[Any, bool]:
    """(new with the stored values put back, whether any was). left collects the paths of
    the hidden values that can't be, whose text becomes ""."""
    sentinel = _sentinel()
    if isinstance(new, dict):
        olds = old if isinstance(old, dict) else {}
        maskeds = masked if isinstance(masked, dict) else {}
        found, inside, result = False, [], {}
        for key, value in new.items():
            result[key], back = _restored(value, olds.get(key), maskeds.get(key),
                                          f"{path}.{key}" if path else str(key), inside)
            found = found or back
        moved = [k for k in _DESTINATION if new.get(k) != maskeds.get(k)]
        if found and moved:
            # A hidden value must not go to an address it wasn't stored for
            result, _ = _restored(new, None, None, path, inside)
        left.extend(p for p in inside if p not in left)
        return result, found and not moved
    if isinstance(new, list):
        olds = old if isinstance(old, list) and len(old) == len(new) else [None] * len(new)
        maskeds = masked if isinstance(masked, list) and len(masked) == len(new) else [None] * len(new)
        pairs = [_restored(v, o, m, f"{path}[{i}]", left) for i, (v, o, m) in enumerate(zip(new, olds, maskeds))]
        return [v for v, _ in pairs], any(back for _, back in pairs)
    if not isinstance(new, str) or sentinel not in new or new == old:
        return new, False
    if new == masked and isinstance(old, str):
        return old, True
    left.append(path)
    return "", False


def restore(field: str, value: Any, stored: Any) -> Tuple[Any, List[str]]:
    """The value with what mask() hid put back from the stored one, and where that wasn't
    possible: there the value is empty."""
    if _sentinel() not in (value if isinstance(value, str) else json.dumps(value, default=str)):
        return value, []
    new = _parsed(value)
    if not isinstance(new, (dict, list)):
        return (value, []) if value == stored else ("", ["the whole value"])
    old = _parsed(stored)
    masked = FIELDS[field](old) if isinstance(old, (dict, list)) else None
    left: List[str] = []
    result, _ = _restored(new, old, masked, "", left)
    return (json.dumps(result) if isinstance(value, str) else result), left


def restore_config(config: Dict[str, Any], stored: Optional[Dict[str, Any]]) -> List[str]:
    """restore() for each field of an agent config that has secrets, in place. Returns
    where a hidden value was left empty, as "field: path"."""
    left: List[str] = []
    for field in FIELDS:
        if config.get(field):
            config[field], paths = restore(field, config[field], (stored or {}).get(field))
            left.extend(f"{field}: {path}" for path in paths)
    return left


def require_config(config: Dict[str, Any], stored: Optional[Dict[str, Any]]) -> None:
    """restore_config(), or HiddenValue when a value can't be put back."""
    left = restore_config(config, stored)
    if left:
        raise HiddenValue(f"Enter the hidden value again ({', '.join(left)}): it is kept only "
                          "while the address, command and arguments it is sent with stay the same.")
