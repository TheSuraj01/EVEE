"""Checks on values that came from speech or the cloud model, before anything uses them.

A web address proposed by the model is opened only if it is plainly an https site name:
never a file, a script, an IP address, a port, a login part or a private-only name. (A
public name can still point anywhere, and a site can redirect; certificates and https on
port 443 are what remain between it and a local service.)
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

#: Long enough for any address worth saying aloud.
MAX_URL_CHARS = 300

#: One DNS label: letters, digits and inner hyphens.
_LABEL = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)")
#: An alphabetic top-level domain, so an IP address never passes.
_TLD = re.compile(r"[a-z]{2,63}")
#: Names that only mean something on a private network.
_PRIVATE_TLDS = frozenset(
    {
        "local",
        "localhost",
        "localdomain",
        "internal",
        "lan",
        "home",
        "corp",
        "intranet",
        "test",
        "invalid",
        "example",
        "arpa",
        "onion",
        "svc",
    }
)
#: What may follow the host: RFC 3986 path, query and fragment characters. No spaces,
#: quotes, backslashes or angle brackets.
_REST = re.compile(r"[A-Za-z0-9\-._~/?#\[\]@!$&'()*+,;=%:]*")


def web_address(text: str) -> str | None:
    """'youtube.com' or 'https://www.youtube.com/results?search_query=x' -> the https
    address to open; anything else -> None. http is upgraded to https. The host must be an
    ASCII public name: an internationalised one (xn--) could imitate another site."""
    value = text.strip()
    if not value or len(value) > MAX_URL_CHARS or any(ch.isspace() for ch in value):
        return None
    if "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        port = parts.port  # a malformed port raises
    except ValueError:
        return None
    if parts.scheme.lower() not in {"https", "http"} or port is not None:
        return None
    host = (parts.hostname or "").rstrip(".")
    if parts.netloc.lower().rstrip(".") != host:  # a login part ("user@") or a port
        return None
    labels = host.split(".")
    if (
        len(labels) < 2
        or not all(_LABEL.fullmatch(label) for label in labels)
        or not _TLD.fullmatch(labels[-1])
        or labels[-1] in _PRIVATE_TLDS
        or any(label.startswith("xn--") for label in labels)
    ):
        return None
    rest = value.split("://", 1)[1][len(parts.netloc) :]
    if not _REST.fullmatch(rest):
        return None
    return urlunsplit(("https", host, parts.path or "/", parts.query, parts.fragment))
