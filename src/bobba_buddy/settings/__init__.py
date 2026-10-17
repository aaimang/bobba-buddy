"""The settings window that G-Earth's ▶ button opens.

:class:`~bobba_buddy.settings.host.SettingsHost` runs it on the main thread;
:mod:`~bobba_buddy.settings.window` draws it with Tkinter; :mod:`.configfile` reads and
writes ``config.toml`` (keeping comments); :mod:`.schema` lists what it shows.

Preview it without G-Earth::

    uv run python -m bobba_buddy.settings --project-dir .
"""

from .host import BotBridge, SettingsHost

__all__ = ["BotBridge", "SettingsHost"]
