"""Shared handling for errors talking to the Skylight API.

`pyskylight` only raises a `SkylightError` for a response it actually received
and did not like — a 500, a 422, a rejected token. A dropped connection, a DNS
failure, or a request that times out never reaches that translation:
`Skylight._send` raises whatever `aiohttp` raised, untouched. See
`pyskylight/client.py`'s `_send`/`request`.

Both kinds of failure mean the same thing to this integration — "could not
talk to Skylight this time" — and everywhere a bare `SkylightError` was being
caught to decide that, the transport errors need to be caught alongside it.
"""

from __future__ import annotations

import aiohttp
from pyskylight.exceptions import SkylightError

#: Everything that means "the request to Skylight failed", whether or not
#: pyskylight translated it into one of its own exception types. Use this
#: wherever catching `SkylightError` alone would let a connection drop or a
#: timeout slip past the surrounding handling.
SKYLIGHT_CONNECTION_ERRORS: tuple[type[Exception], ...] = (
    SkylightError,
    aiohttp.ClientError,
    TimeoutError,
)


def describe(err: Exception) -> str:
    """Render an error for a log line or a user-facing message.

    `str(TimeoutError())` is empty, which would otherwise produce a message
    like "Error talking to Skylight: " with nothing after the colon. Falling
    back to the exception's class name keeps that readable.
    """
    return str(err) or type(err).__name__
