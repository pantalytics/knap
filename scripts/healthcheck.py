"""Container liveness probe. Exit 0 when the app is answering.

A separate file rather than a one-liner in the Dockerfile's HEALTHCHECK, because
the distinction it draws is easy to get backwards and worth reading.

The MCP endpoint answers a bare GET with a 4xx. That is the app working, not the
app failing: it means the server is up, routing, and correctly refusing a request
that is not a valid MCP call. ``urlopen`` raises ``HTTPError`` on a 4xx, so the
obvious probe (``sys.exit(0) if urlopen(...) else 1``) marks a perfectly healthy
container unhealthy forever, which on a rolling deploy means every release rolls
itself back. squirrel-mcp's Dockerfile carries exactly that bug; its comment says
a 4xx is fine and its code disagrees.

So: an HTTP response of any status is healthy, and only a refused connection, a
timeout or a malformed reply is not.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request

TIMEOUT_S = 3


def probe(url: str) -> bool:
    request = urllib.request.Request(url, method="GET")
    try:
        urllib.request.urlopen(request, timeout=TIMEOUT_S)
        return True
    except urllib.error.HTTPError:
        return True  # the app answered, which is all this asks
    except Exception:
        return False


def main() -> int:
    host = os.getenv("KNAP_MCP_HOST", "127.0.0.1")
    # 0.0.0.0 is a bind address, not a destination: probe loopback instead.
    if host in ("0.0.0.0", "::", ""):  # noqa: S104
        host = "127.0.0.1"
    port = os.getenv("KNAP_MCP_PORT", "8000")
    return 0 if probe(f"http://{host}:{port}/mcp") else 1


if __name__ == "__main__":
    raise SystemExit(main())
