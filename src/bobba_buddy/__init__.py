"""G-Earth extension that answers whitelisted Habbo chat commands with Claude.

The package runs as a G-Earth extension for the Habbo AIR client. It answers chat
commands (``/q``, ``/roast``, ``/suggest``, ``/joke``) through the Claude API and
gives the logged-in avatar its own behaviour: copying nearby players, a random
autopilot, anti-idle, and returning to the last room.

Start it with ``python -m bobba_buddy -p 9092`` (see :mod:`bobba_buddy.__main__`).

Attributes:
    __version__ (str): The package version, also shown to G-Earth as the extension
        version.
"""

__version__ = "1.5.0"
