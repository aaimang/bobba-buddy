"""Single import point for g_python.

g-python 0.1.6 has ``x is not 'NULL'`` comparisons that make Python 3.12+ print
SyntaxWarnings when the module is first compiled. They're harmless, so this module
silences them and re-exports the four classes the bot needs. Import g_python
classes from here, never from ``g_python`` directly.

Attributes:
    Extension: G-Python's extension class (connects to G-Earth, registers intercepts).
    Direction: ``Direction.TO_CLIENT`` (incoming) / ``Direction.TO_SERVER`` (outgoing).
    HMessage: An intercepted packet plus its direction and ``is_blocked`` flag.
    HPacket: A Habbo packet: build one by name, or read fields from one.
"""

import warnings

with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)
    from g_python.gextension import Extension
    from g_python.hdirection import Direction
    from g_python.hmessage import HMessage
    from g_python.hpacket import HPacket

__all__ = ["Direction", "Extension", "HMessage", "HPacket"]
