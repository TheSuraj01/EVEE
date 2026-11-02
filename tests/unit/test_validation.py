"""The web-address check: what the cloud model proposes is opened only if it is plainly a
public https site."""

from __future__ import annotations

import pytest

from voice_agent.safety.validation import web_address


@pytest.mark.parametrize(
    ("proposed", "opened"),
    [
        ("https://www.youtube.com", "https://www.youtube.com/"),
        ("youtube.com", "https://youtube.com/"),
        ("http://example.org/page", "https://example.org/page"),  # upgraded, never plain http
        ("https://WWW.GitHub.com/", "https://www.github.com/"),
        (
            "https://www.youtube.com/results?search_query=lofi+music",
            "https://www.youtube.com/results?search_query=lofi+music",
        ),
        ("https://en.wikipedia.org/wiki/Python_(programming_language)#History", None),
    ],
)
def test_public_https_sites_open(proposed: str, opened: str | None) -> None:
    expected = opened or proposed
    assert web_address(proposed) == expected


@pytest.mark.parametrize(
    "proposed",
    [
        "",
        "file:///C:/Windows/System32",
        "javascript:alert(1)",
        "data:text/html,<script>x</script>",
        "ms-settings:privacy",
        "https://localhost/admin",
        "https://127.0.0.1",
        "https://192.168.1.1/login",
        "https://[::1]/",
        "https://router.lan",
        "https://printer.local",
        "https://router.home.arpa",
        "https://somewhere.onion",
        "https://www.example",
        "https://user:pass@example.com",
        "https://example.com@evil.com",
        "https://example.com:8080",
        "https://xn--e1afmkfd.xn--p1ai",  # an internationalised name can imitate another site
        "https://exa mple.com",
        'https://example.com/"onmouseover',
        "https://example.com/<script>",
        "https://example.com\\..\\",
        "--remote-debugging-port=9222",
        "https://-bad-.com",
        "https://" + "a" * 300 + ".com",
        "com",
    ],
)
def test_anything_else_is_refused(proposed: str) -> None:
    assert web_address(proposed) is None
