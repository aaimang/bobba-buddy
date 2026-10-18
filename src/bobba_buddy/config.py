"""Typed configuration loaded from TOML, with validation and mtime-based hot reload.

Each ``config.toml`` section maps to a frozen dataclass below. :func:`parse_config`
rejects unknown keys and wrong types, so a typo fails loudly instead of being ignored.
:class:`ConfigStore` re-reads the file when it changes.

Attributes:
    COMMAND_NAMES (tuple[str, ...]): Internal command names, in help order.
    EFFORT_LEVELS (tuple[str, ...]): Allowed ``[claude] effort`` values.
    HABBO_BUBBLE_LIMIT (int): Habbo's maximum characters per chat bubble.
    COMMAND_DEFAULTS (dict[str, CommandConfig]): Per-command defaults, before
        ``[commands.<name>]`` overrides.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

COMMAND_NAMES = ("q", "roast", "suggest", "joke")
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
HABBO_BUBBLE_LIMIT = 100


class ConfigError(ValueError):
    """Raised when the config file is missing, malformed, or fails validation."""


@dataclass(frozen=True)
class ClaudeConfig:
    """``[claude]``: model and request settings.

    Attributes:
        model: Claude model id.
        effort: Thinking effort: ``low``, ``medium``, ``high``, ``xhigh`` or ``max``.
        max_tokens: Output cap per request, covering thinking, search and the answer.
        timeout_s: Per-request timeout in seconds.
        max_retries: SDK retries for retryable errors.
        web_search_max_uses: Most web searches per ``/q``.
    """

    model: str = "claude-haiku-5-5"
    effort: str = "low"
    max_tokens: int = 4096
    timeout_s: float = 45.0
    max_retries: int = 2
    web_search_max_uses: int = 2


@dataclass(frozen=True)
class LimitsConfig:
    """``[limits]``: concurrency and spending.

    Attributes:
        workers: Claude calls running at once (read at startup only).
        max_pending: Queued plus running requests before people get "busy" (startup only).
        daily_budget_usd: Daily spend cap; 0 disables it.
        max_input_chars: Longest accepted command argument.
    """

    workers: int = 2
    max_pending: int = 5
    daily_budget_usd: float = 1.0  # 0 disables the cap
    max_input_chars: int = 300


@dataclass(frozen=True)
class ChatConfig:
    """``[chat]``: how replies are sent.

    Attributes:
        bubble_limit: Maximum characters per bubble (20 to 100).
        send_interval_s: Gap between bubbles, to avoid flood mutes.
        flood_margin_s: Extra wait after a flood or moderator mute.
        bubble_style: Bubble style for replies; -1 reuses the owner's own.
        require_seen_packets: Only use a reply mode after seeing the owner use it.
            ``False`` sends anyway and lets G-Earth decide.
    """

    bubble_limit: int = HABBO_BUBBLE_LIMIT
    send_interval_s: float = 2.0
    flood_margin_s: float = 2.0
    bubble_style: int = -1  # -1 = reuse the owner's own last chat style
    # G-Earth (without Developer mode) drops extension packets of a type it hasn't seen
    # since it was opened. False: always send, and let G-Earth decide. True: only send a
    # reply mode after seeing you use it (shouts then fall back to chat).
    require_seen_packets: bool = False


@dataclass(frozen=True)
class MemoryConfig:
    """``[memory]``: ``/q`` follow-up memory.

    Attributes:
        max_turns: Question/answer pairs kept per user; 0 disables memory.
        ttl_s: Seconds without a question before a user's memory is forgotten.
    """

    max_turns: int = 6
    ttl_s: float = 600.0


@dataclass(frozen=True)
class PricingConfig:
    """``[pricing]``: prices used for the cost estimate and budget, in USD.

    Attributes:
        input_per_mtok: Price per million input tokens.
        output_per_mtok: Price per million output tokens.
        per_search: Price per web search.
    """

    input_per_mtok: float = 0.10
    output_per_mtok: float = 0.50
    per_search: float = 0.01


@dataclass(frozen=True)
class RoomConfig:
    """``[room]``: loading rooms and staying in them.

    Attributes:
        room_reload_packet: Packet sent on connect to reload the room, only if G-Earth
            knows the name (``GetHeightMap`` doesn't exist in the current client).
        rejoin_on_start: After a bot restart, re-enter the last room seen.
        rejoin_delay_s: Wait before rejoining; skipped if a room loads by itself.
        return_on_exit: Go back to the last room after ending up on the hotel view.
        return_delay_s: Wait before going back (multiplied by the attempt number).
        return_max_attempts: Tries in a row before giving up.
    """

    room_reload_packet: str = "GetHeightMap"
    # After the bot (re)starts, re-enter the last room it saw you in, so it gets the user
    # list and floor map without you reloading the room. Skipped if a room loads by itself
    # within rejoin_delay_s.
    rejoin_on_start: bool = False
    rejoin_delay_s: float = 3.0
    # If you end up outside a room (hotel view), go back to the last room after
    # return_delay_s. Gives up after return_max_attempts tries in a row (kick, ban,
    # doorbell or password rooms), waiting a little longer before each retry.
    return_on_exit: bool = False
    return_delay_s: float = 5.0
    return_max_attempts: int = 3


@dataclass(frozen=True)
class AvatarConfig:
    """``[avatar]``: autopilot, copying and anti-idle.

    Attributes:
        autopilot: Enable random actions.
        autopilot_min_s: Shortest wait between autopilot actions.
        autopilot_max_s: Longest wait between autopilot actions.
        walk_radius: Furthest walk target from the owner, in tiles; 0 = anywhere.
        walk_weight: Relative chance of walking.
        sit_weight: Relative chance of sitting down or standing up.
        dance_weight: Relative chance of starting or stopping a dance.
        expression_weight: Relative chance of an expression.
        hc_expressions: Allow the Habbo Club expressions (blow kiss, laugh, jump).
        wave_on_walk: Wave after each autopilot walk.
        sign_weight: Relative chance of holding up a random sign.
        look_weight: Relative chance of turning to look somewhere.
        hc_dances: Allow Habbo Club dances 2-4; otherwise they're sent as dance 1.
        mimic: Enable copying nearby players.
        mimic_radius: How close a player must be to be copied (1 to 3 tiles).
        mimic_dance: Copy dances.
        mimic_expressions: Copy expressions.
        mimic_signs: Copy signs.
        mimic_posture: Copy sitting and standing.
        mimic_pause_s: Autopilot pause after copying someone.
        anti_idle: Block the client's idle report and wake up when marked asleep.
        wake_actions: Wake-up actions to try, in order: ``wave``, ``walk``, ``look``.
        owner_pause_s: Autopilot pause after the owner acts by hand.
        action_gap_s: Minimum time between any two avatar actions.
        require_seen_packets: Only send an action after seeing the owner do it.
            ``False`` sends anyway and lets G-Earth decide.
    """

    # Autopilot: random walk / sit / dance every autopilot_min_s..autopilot_max_s seconds.
    autopilot: bool = True
    autopilot_min_s: float = 30.0
    autopilot_max_s: float = 90.0
    walk_radius: int = 5  # tiles from the current position; 0 = anywhere in the room
    walk_weight: int = 5
    sit_weight: int = 2
    dance_weight: int = 2
    expression_weight: int = 2  # wave / thumbs up (+ blow kiss, laugh, jump with HC)
    hc_expressions: bool = False  # blow kiss, laugh and jump need Habbo Club
    wave_on_walk: bool = True  # wave after each autopilot walk
    sign_weight: int = 1  # hold up a random sign 0-17
    look_weight: int = 2  # turn to face a random direction
    hc_dances: bool = False  # dances 2-4 need Habbo Club; without it they're sent as dance 1
    # Mimic: copy players standing within mimic_radius tiles.
    mimic: bool = True
    mimic_radius: int = 1
    mimic_dance: bool = True
    mimic_expressions: bool = True
    mimic_signs: bool = True
    mimic_posture: bool = True
    mimic_pause_s: float = 30.0  # autopilot waits this long after copying someone
    # Anti-idle: block the client's "I'm idle" packet and wake up if the server says asleep.
    anti_idle: bool = True
    wake_actions: tuple[str, ...] = ("wave", "walk", "look")  # first one G-Earth allows wins
    # Shared
    owner_pause_s: float = 60.0  # autopilot waits this long after you move or act yourself
    action_gap_s: float = 1.5  # minimum time between any two avatar actions
    # False: always send and let G-Earth decide. True: only send an action after seeing you
    # do it once yourself (useful without Developer mode, to avoid G-Earth's log warnings).
    require_seen_packets: bool = False


@dataclass(frozen=True)
class UpdatesConfig:
    """``[updates]``: checking GitHub for a newer release.

    Attributes:
        check: Ask GitHub for the latest release once a day (sends your IP address
            to GitHub). The settings window shows a banner when there's a newer one.
    """

    check: bool = True


@dataclass(frozen=True)
class CommandConfig:
    """``[commands.<name>]``: settings for one chat command.

    Attributes:
        enabled: Whether the command responds at all.
        prefix: What users type, e.g. ``/q``; one word, unique among enabled commands.
        cooldown_s: Per-user cooldown.
        web_search: Allow web search for this command.
        max_chars: Length limit written into the prompt.
        max_bubbles: Hard cap on bubbles per reply (1 to 5).
        target_cooldown_s: ``/roast`` only: how long before the same person can be
            roasted again.
        opt_out: ``/roast`` only: lowercased names that are never roasted.
    """

    enabled: bool = True
    prefix: str = ""
    cooldown_s: float = 15.0
    web_search: bool = False
    max_chars: int = 250
    max_bubbles: int = 3
    target_cooldown_s: float = 60.0  # /roast only
    opt_out: tuple[str, ...] = ()  # /roast only; lowercased


COMMAND_DEFAULTS: dict[str, CommandConfig] = {
    "q": CommandConfig(prefix="/q", web_search=True, max_chars=250, max_bubbles=3),
    "roast": CommandConfig(prefix="/roast", max_chars=150, max_bubbles=2),
    "suggest": CommandConfig(prefix="/suggest", max_chars=250, max_bubbles=3),
    "joke": CommandConfig(prefix="/joke", max_chars=200, max_bubbles=2),
}


@dataclass(frozen=True)
class Config:
    """The whole configuration.

    Attributes:
        whitelist: Lowercased names allowed to run commands (the owner always is).
        claude: ``[claude]`` settings.
        limits: ``[limits]`` settings.
        chat: ``[chat]`` settings.
        memory: ``[memory]`` settings.
        pricing: ``[pricing]`` settings.
        room: ``[room]`` settings.
        avatar: ``[avatar]`` settings.
        updates: ``[updates]`` settings.
        commands: ``[commands.<name>]`` settings, keyed by internal command name.
    """

    whitelist: frozenset[str] = frozenset()  # lowercased names
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    pricing: PricingConfig = field(default_factory=PricingConfig)
    room: RoomConfig = field(default_factory=RoomConfig)
    avatar: AvatarConfig = field(default_factory=AvatarConfig)
    updates: UpdatesConfig = field(default_factory=UpdatesConfig)
    commands: Mapping[str, CommandConfig] = field(default_factory=lambda: dict(COMMAND_DEFAULTS))


def _build(cls: type, data: Any, section: str, base: Any = None) -> Any:
    """Build a frozen dataclass from a TOML table, rejecting unknown keys and wrong types.

    Ints are accepted for float fields, and lists of names become lowercased tuples.

    Args:
        cls: The dataclass to build.
        data: The TOML table.
        section: Section name, for error messages.
        base: Defaults to start from; ``cls()`` if ``None``.

    Returns:
        ``base`` with the table's values applied.

    Raises:
        ConfigError: If the table isn't a table, has unknown keys, or has wrong types.
    """
    if not isinstance(data, dict):
        raise ConfigError(f"[{section}] must be a table")
    base = base if base is not None else cls()
    fields = {f.name: f for f in dataclasses.fields(cls)}
    unknown = set(data) - set(fields)
    if unknown:
        raise ConfigError(f"[{section}] unknown key(s): {', '.join(sorted(unknown))}")
    values: dict[str, Any] = {}
    for name, value in data.items():
        expected = type(getattr(base, name))
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        elif expected is tuple and isinstance(value, list):
            if not all(isinstance(v, str) for v in value):
                raise ConfigError(f"[{section}] {name} must be a list of names")
            value = tuple(v.strip().lower() for v in value if v.strip())
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ConfigError(
                f"[{section}] {name} must be {expected.__name__}, got {type(value).__name__}"
            )
        values[name] = value
    return dataclasses.replace(base, **values)


def parse_config(data: dict[str, Any]) -> Config:
    """Validate a parsed TOML document and return a Config.

    Args:
        data: The document, as returned by ``tomllib.load``.

    Returns:
        The validated configuration; missing keys take their defaults.

    Raises:
        ConfigError: On unknown keys, wrong types, or values out of range.
    """
    allowed = {
        "whitelist",
        "claude",
        "limits",
        "chat",
        "memory",
        "pricing",
        "room",
        "avatar",
        "updates",
        "commands",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"unknown top-level key(s): {', '.join(sorted(unknown))}")

    raw_whitelist = data.get("whitelist", [])
    if not isinstance(raw_whitelist, list) or not all(isinstance(n, str) for n in raw_whitelist):
        raise ConfigError("whitelist must be a list of Habbo names")
    whitelist = frozenset(n.strip().lower() for n in raw_whitelist if n.strip())

    raw_commands = data.get("commands", {})
    if not isinstance(raw_commands, dict):
        raise ConfigError("[commands] must be a table")
    unknown_cmds = set(raw_commands) - set(COMMAND_NAMES)
    if unknown_cmds:
        raise ConfigError(f"unknown command(s): {', '.join(sorted(unknown_cmds))}")
    commands = {
        name: _build(
            CommandConfig, raw_commands.get(name, {}), f"commands.{name}", COMMAND_DEFAULTS[name]
        )
        for name in COMMAND_NAMES
    }

    config = Config(
        whitelist=whitelist,
        claude=_build(ClaudeConfig, data.get("claude", {}), "claude"),
        limits=_build(LimitsConfig, data.get("limits", {}), "limits"),
        chat=_build(ChatConfig, data.get("chat", {}), "chat"),
        memory=_build(MemoryConfig, data.get("memory", {}), "memory"),
        pricing=_build(PricingConfig, data.get("pricing", {}), "pricing"),
        room=_build(RoomConfig, data.get("room", {}), "room"),
        avatar=_build(AvatarConfig, data.get("avatar", {}), "avatar"),
        updates=_build(UpdatesConfig, data.get("updates", {}), "updates"),
        commands=commands,
    )
    _validate(config)
    return config


def _validate(config: Config) -> None:
    """Check value ranges and cross-field rules (such as unique prefixes).

    Args:
        config: The configuration to check.

    Raises:
        ConfigError: Describing the first invalid value.
    """
    c, lim, chat, mem = config.claude, config.limits, config.chat, config.memory
    if c.effort not in EFFORT_LEVELS:
        raise ConfigError(f"[claude] effort must be one of {', '.join(EFFORT_LEVELS)}")
    if not c.model.strip():
        raise ConfigError("[claude] model must not be empty")
    if c.max_tokens < 256:
        raise ConfigError("[claude] max_tokens must be >= 256")
    if c.timeout_s <= 0 or c.max_retries < 0 or c.web_search_max_uses < 1:
        raise ConfigError("[claude] timeout_s > 0, max_retries >= 0, web_search_max_uses >= 1")
    if lim.workers < 1 or lim.max_pending < 1:
        raise ConfigError("[limits] workers and max_pending must be >= 1")
    if lim.daily_budget_usd < 0:
        raise ConfigError("[limits] daily_budget_usd must be >= 0 (0 disables the cap)")
    if lim.max_input_chars < 1:
        raise ConfigError("[limits] max_input_chars must be >= 1")
    if not 20 <= chat.bubble_limit <= HABBO_BUBBLE_LIMIT:
        raise ConfigError(f"[chat] bubble_limit must be between 20 and {HABBO_BUBBLE_LIMIT}")
    if chat.send_interval_s < 0 or chat.flood_margin_s < 0:
        raise ConfigError("[chat] send_interval_s and flood_margin_s must be >= 0")
    if mem.max_turns < 0 or mem.ttl_s < 0:
        raise ConfigError("[memory] max_turns and ttl_s must be >= 0")

    if config.room.rejoin_delay_s < 0 or config.room.return_delay_s < 0:
        raise ConfigError("[room] rejoin_delay_s and return_delay_s must be >= 0")
    if config.room.return_max_attempts < 1:
        raise ConfigError("[room] return_max_attempts must be >= 1")
    av = config.avatar
    if av.autopilot_min_s < 5 or av.autopilot_max_s < av.autopilot_min_s:
        raise ConfigError("[avatar] autopilot_min_s >= 5 and autopilot_max_s >= autopilot_min_s")
    if av.walk_radius < 0 or not 1 <= av.mimic_radius <= 3:
        raise ConfigError("[avatar] walk_radius >= 0 and mimic_radius between 1 and 3")
    weights = (
        av.walk_weight,
        av.sit_weight,
        av.dance_weight,
        av.expression_weight,
        av.sign_weight,
        av.look_weight,
    )
    if min(weights) < 0:
        raise ConfigError("[avatar] weights must be >= 0")
    if av.action_gap_s < 0.5 or av.mimic_pause_s < 0 or av.owner_pause_s < 0:
        raise ConfigError("[avatar] action_gap_s >= 0.5 and pauses >= 0")
    unknown_wake = set(av.wake_actions) - {"wave", "walk", "look"}
    if unknown_wake:
        raise ConfigError(f"[avatar] unknown wake_actions: {', '.join(sorted(unknown_wake))}")

    prefixes: dict[str, str] = {}
    for name, cmd in config.commands.items():
        prefix = cmd.prefix.strip().lower()
        if not prefix or " " in prefix:
            raise ConfigError(f"[commands.{name}] prefix must be one word, like '/{name}'")
        if cmd.enabled and prefix in prefixes:
            raise ConfigError(
                f"[commands.{name}] prefix '{prefix}' is already used by {prefixes[prefix]}"
            )
        prefixes[prefix] = name
        if cmd.max_chars < 20 or not 1 <= cmd.max_bubbles <= 5:
            raise ConfigError(f"[commands.{name}] max_chars >= 20 and max_bubbles 1-5")
        if cmd.cooldown_s < 0 or cmd.target_cooldown_s < 0:
            raise ConfigError(f"[commands.{name}] cooldowns must be >= 0")


def load_config(path: Path) -> Config:
    """Read and validate a config file.

    Args:
        path: Path to ``config.toml``.

    Returns:
        The validated configuration.

    Raises:
        ConfigError: If the file is missing, isn't valid TOML, or fails validation.
    """
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name}: {exc}") from exc
    return parse_config(data)


class ConfigStore:
    """Holds the current Config and reloads it when the file's mtime changes.

    A bad edit keeps the last good config and reports the error through ``on_error``,
    so a typo never takes the bot down. Settings that size thread pools (workers,
    max_pending) only take effect on restart.

    Attributes:
        path: The config file being watched.
        on_reload: Called with the new config after a successful reload.
        on_error: Called with the error message after a failed reload.
    """

    def __init__(
        self,
        path: Path,
        *,
        check_interval_s: float = 2.0,
        on_reload: Callable[[Config], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Load the config file.

        Args:
            path: Path to ``config.toml``.
            check_interval_s: Minimum seconds between file checks.
            on_reload: Called with the new config after a successful reload.
            on_error: Called with the error message after a failed reload.
            clock: Monotonic clock in seconds; injectable for tests.

        Raises:
            ConfigError: If the initial load fails.
        """
        self.path = path
        self._lock = threading.Lock()
        self._config = load_config(path)
        self._mtime = self._stat()
        self._check_interval_s = check_interval_s
        self._next_check = clock() + check_interval_s
        self._clock = clock
        self.on_reload = on_reload
        self.on_error = on_error

    def _stat(self) -> float | None:
        """The config file's modification time.

        Returns:
            ``st_mtime``, or ``None`` if the file can't be read.
        """
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    def get(self) -> Config:
        """The current config, reloading it first if the file changed.

        Checks the file at most every ``check_interval_s`` seconds. Callbacks run
        outside the lock.

        Returns:
            The newest valid configuration.
        """
        error: str | None = None
        reloaded = False
        with self._lock:
            now = self._clock()
            if now < self._next_check:
                return self._config
            self._next_check = now + self._check_interval_s
            mtime = self._stat()
            if mtime is None or mtime == self._mtime:
                return self._config
            self._mtime = mtime
            try:
                self._config = load_config(self.path)
                reloaded = True
            except ConfigError as exc:
                error = str(exc)
            config = self._config

        # Callbacks run outside the lock so they may call get() themselves.
        if error is not None:
            log.error("config reload failed, keeping previous config: %s", error)
            if self.on_error:
                self.on_error(error)
        elif reloaded:
            log.info("config reloaded (%d whitelisted)", len(config.whitelist))
            if self.on_reload:
                self.on_reload(config)
        return config

    def reload_now(self) -> Config:
        """Check the file right away instead of waiting for ``check_interval_s``.

        Used after the settings window saves, so a change applies immediately.

        Returns:
            The newest valid configuration.
        """
        with self._lock:
            self._next_check = float("-inf")
        return self.get()
