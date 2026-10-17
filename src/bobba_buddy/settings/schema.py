"""What the settings window shows: pages, boxes and fields, as plain data.

The window builds itself from :data:`PAGES`. Every config key is either shown here or
listed in :data:`HIDDEN`; a test checks that, so a new config key can't be forgotten.

Attributes:
    PAGES (tuple[Page, ...]): The sidebar pages, in order (About is added by the window).
    HIDDEN (frozenset[tuple[str, str]]): Config keys deliberately left out of the window.
    PAGE_ICONS (dict[str, str]): Tabler icon name per sidebar page, About included.
    BOX_ICONS (dict[str, str]): Tabler icon name per box title. The PNGs are made by
        ``scripts/make_icons.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import EFFORT_LEVELS, HABBO_BUBBLE_LIMIT


@dataclass(frozen=True)
class Field:
    """One setting.

    Attributes:
        section: Config section, ``""`` for the top level or ``"commands.q"``.
        key: Key inside the section.
        label: Short name shown in the window.
        kind: ``toggle``, ``number``, ``text``, ``names``, ``choice`` or ``chips``.
        help: One line of grey help text under the label.
        min: Lowest allowed number, or ``None``.
        max: Highest allowed number, or ``None``.
        step: How much ``+`` and ``-`` change a number.
        decimals: Decimals shown for a number; 0 means a whole number.
        unit: Shown after a number, e.g. ``s`` or ``$``.
        options: Choices for ``choice`` and ``chips``.
        restart: The change only applies after the extension restarts.
    """

    section: str
    key: str
    label: str
    kind: str
    help: str = ""
    min: float | None = None
    max: float | None = None
    step: float = 1
    decimals: int = 0
    unit: str = ""
    options: tuple[str, ...] = ()
    restart: bool = False

    @property
    def id(self) -> tuple[str, str]:
        """``(section, key)``, the way :mod:`.configfile` addresses values."""
        return (self.section, self.key)


@dataclass(frozen=True)
class Box:
    """A boxed group of fields with a yellow header.

    Attributes:
        title: Header text.
        fields: The fields, top to bottom.
        note: Optional grey line under the header.
    """

    title: str
    fields: tuple[Field, ...]
    note: str = ""


@dataclass(frozen=True)
class Page:
    """A sidebar page.

    Attributes:
        name: Sidebar label.
        boxes: The boxes, top to bottom.
    """

    name: str
    boxes: tuple[Box, ...]


def _seconds(section: str, key: str, label: str, help: str = "", **kw) -> Field:
    """A number of seconds, 0 or more unless ``min`` says otherwise."""
    kw.setdefault("min", 0)
    return Field(section, key, label, "number", help, unit="s", **kw)


def _command(name: str, title: str, *, web_search: bool, roast: bool = False) -> Box:
    """The box for one chat command."""
    s = f"commands.{name}"
    fields = [
        Field(s, "enabled", "Enabled", "toggle"),
        Field(s, "prefix", "Prefix", "text", "What people type, one word"),
        _seconds(s, "cooldown_s", "Cooldown", "Per person, between uses"),
    ]
    if roast:
        fields += [
            _seconds(s, "target_cooldown_s", "Same target again after"),
            Field(s, "opt_out", "Never roast", "names", "Names, separated by commas"),
        ]
    if web_search:
        fields.append(Field(s, "web_search", "Web search", "toggle", "Each search costs $0.01"))
    fields += [
        Field(s, "max_chars", "Answer length", "number", "Characters", min=20, step=10),
        Field(s, "max_bubbles", "Most bubbles", "number", "Per reply", min=1, max=5),
    ]
    return Box(title, tuple(fields))


PAGES: tuple[Page, ...] = (
    Page(
        "Whitelist",
        (
            Box(
                "Who can use the bot",
                (
                    Field(
                        "",
                        "whitelist",
                        "Allowed names",
                        "names",
                        "Habbo names, separated by commas",
                    ),
                ),
                note="You (the logged-in account) are always allowed.",
            ),
        ),
    ),
    Page(
        "Commands",
        (
            _command("q", "/q · ask Claude", web_search=True),
            _command("roast", "/roast", web_search=False, roast=True),
            _command("suggest", "/suggest", web_search=True),
            _command("joke", "/joke", web_search=False),
        ),
    ),
    Page(
        "Avatar",
        (
            Box(
                "Autopilot",
                (
                    Field("avatar", "autopilot", "Walk, sit and dance on its own", "toggle"),
                    _seconds("avatar", "autopilot_min_s", "Shortest wait", min=5, step=5),
                    _seconds("avatar", "autopilot_max_s", "Longest wait", min=5, step=5),
                    Field(
                        "avatar",
                        "walk_radius",
                        "Walk radius",
                        "number",
                        "Tiles; 0 = anywhere, never the door",
                        min=0,
                    ),
                    Field("avatar", "wave_on_walk", "Wave after walking", "toggle"),
                    Field("avatar", "hc_dances", "Habbo Club dances", "toggle", "Dances 2-4"),
                    Field(
                        "avatar",
                        "hc_expressions",
                        "Habbo Club expressions",
                        "toggle",
                        "Blow kiss, laugh, jump",
                    ),
                ),
            ),
            Box(
                "How often each action",
                tuple(
                    Field("avatar", f"{name}_weight", label, "number", min=0)
                    for name, label in (
                        ("walk", "Walk"),
                        ("sit", "Sit or stand"),
                        ("dance", "Dance"),
                        ("expression", "Wave, thumbs up"),
                        ("sign", "Hold up a sign"),
                        ("look", "Look around"),
                    )
                ),
                note="Relative chances; 0 turns an action off.",
            ),
            Box(
                "Copy nearby players",
                (
                    Field("avatar", "mimic", "Copy players next to you", "toggle"),
                    Field(
                        "avatar",
                        "mimic_radius",
                        "Distance",
                        "number",
                        "Tiles; 1 = the 8 tiles around you",
                        min=1,
                        max=3,
                    ),
                    Field("avatar", "mimic_dance", "Dances", "toggle"),
                    Field("avatar", "mimic_expressions", "Expressions", "toggle"),
                    Field("avatar", "mimic_signs", "Signs", "toggle"),
                    Field("avatar", "mimic_posture", "Sitting and standing", "toggle"),
                    _seconds("avatar", "mimic_pause_s", "Autopilot pause after copying", step=5),
                ),
            ),
            Box(
                "Anti-idle",
                (
                    Field("avatar", "anti_idle", "Never fall asleep (Zzz)", "toggle"),
                    Field(
                        "avatar",
                        "wake_actions",
                        "Wake up by",
                        "chips",
                        "Tried in this order",
                        options=("wave", "walk", "look"),
                    ),
                ),
            ),
            Box(
                "Timing",
                (
                    _seconds("avatar", "owner_pause_s", "Pause after you act yourself", step=5),
                    _seconds(
                        "avatar",
                        "action_gap_s",
                        "Gap between actions",
                        min=0.5,
                        step=0.5,
                        decimals=1,
                    ),
                ),
            ),
        ),
    ),
    Page(
        "Room",
        (
            Box(
                "Rejoin after a restart",
                (
                    Field("room", "rejoin_on_start", "Re-enter the last room", "toggle"),
                    _seconds("room", "rejoin_delay_s", "Wait first", "Skipped if a room loads"),
                ),
            ),
            Box(
                "Go back after leaving",
                (
                    Field(
                        "room",
                        "return_on_exit",
                        "Return from the hotel view",
                        "toggle",
                        "Back to the last room",
                    ),
                    _seconds("room", "return_delay_s", "Wait first", "Longer on each retry"),
                    Field(
                        "room",
                        "return_max_attempts",
                        "Tries in a row",
                        "number",
                        "Gives up after kicks, bans or doorbells",
                        min=1,
                    ),
                ),
            ),
        ),
    ),
    Page(
        "Claude",
        (
            Box(
                "Model",
                (
                    Field("claude", "model", "Model", "text"),
                    Field("claude", "effort", "Thinking effort", "choice", options=EFFORT_LEVELS),
                    Field(
                        "claude",
                        "max_tokens",
                        "Max tokens",
                        "number",
                        "Thinking, search and the answer",
                        min=256,
                        step=256,
                    ),
                    _seconds("claude", "timeout_s", "Timeout", min=1, step=5),
                    Field("claude", "max_retries", "Retries", "number", min=0),
                    Field(
                        "claude",
                        "web_search_max_uses",
                        "Searches per /q",
                        "number",
                        "Each search costs $0.01",
                        min=1,
                    ),
                ),
            ),
            Box(
                "/q memory",
                (
                    Field(
                        "memory",
                        "max_turns",
                        "Remembered turns",
                        "number",
                        "Per person; 0 = no memory",
                        min=0,
                    ),
                    _seconds("memory", "ttl_s", "Forget after", "Of silence", step=60),
                ),
            ),
        ),
    ),
    Page(
        "Budget",
        (
            Box(
                "Spending",
                (
                    Field(
                        "limits",
                        "daily_budget_usd",
                        "Daily budget",
                        "number",
                        "0 = no cap; resets at midnight",
                        min=0,
                        step=0.25,
                        decimals=2,
                        unit="$",
                    ),
                    Field(
                        "limits",
                        "max_input_chars",
                        "Longest question",
                        "number",
                        "Characters",
                        min=1,
                        step=50,
                    ),
                ),
            ),
            Box(
                "Load",
                (
                    Field(
                        "limits",
                        "workers",
                        "Claude calls at once",
                        "number",
                        min=1,
                        restart=True,
                    ),
                    Field(
                        "limits",
                        "max_pending",
                        "Queue size",
                        "number",
                        "Beyond this people get 'busy'",
                        min=1,
                        restart=True,
                    ),
                ),
                note="These apply after the extension restarts.",
            ),
        ),
    ),
    Page(
        "Advanced",
        (
            Box(
                "Chat",
                (
                    Field(
                        "chat",
                        "bubble_limit",
                        "Bubble length",
                        "number",
                        f"Characters, 20-{HABBO_BUBBLE_LIMIT}",
                        min=20,
                        max=HABBO_BUBBLE_LIMIT,
                        step=5,
                    ),
                    _seconds(
                        "chat", "send_interval_s", "Gap between bubbles", step=0.5, decimals=1
                    ),
                    _seconds(
                        "chat", "flood_margin_s", "Extra wait after a mute", step=0.5, decimals=1
                    ),
                    Field(
                        "chat",
                        "bubble_style",
                        "Bubble style",
                        "number",
                        "-1 = your own style",
                        min=-1,
                    ),
                ),
            ),
            Box(
                "Packet safety",
                (
                    Field(
                        "chat",
                        "require_seen_packets",
                        "Chat: only modes you've used",
                        "toggle",
                        "Off = always reply, G-Earth decides",
                    ),
                    Field(
                        "avatar",
                        "require_seen_packets",
                        "Avatar: only actions you've done",
                        "toggle",
                        "Off = always act, G-Earth decides",
                    ),
                ),
            ),
            Box(
                "Pricing (USD)",
                (
                    Field(
                        "pricing",
                        "input_per_mtok",
                        "Input, per million tokens",
                        "number",
                        min=0,
                        step=0.05,
                        decimals=2,
                        unit="$",
                    ),
                    Field(
                        "pricing",
                        "output_per_mtok",
                        "Output, per million tokens",
                        "number",
                        min=0,
                        step=0.05,
                        decimals=2,
                        unit="$",
                    ),
                    Field(
                        "pricing",
                        "per_search",
                        "Per web search",
                        "number",
                        min=0,
                        step=0.01,
                        decimals=2,
                        unit="$",
                    ),
                ),
                note="Used for the daily budget.",
            ),
            Box(
                "Room loading",
                (
                    Field(
                        "room",
                        "room_reload_packet",
                        "Reload packet",
                        "text",
                        "Sent on connect if G-Earth knows it",
                    ),
                ),
            ),
        ),
    ),
)

HIDDEN: frozenset[tuple[str, str]] = frozenset(
    [
        (f"commands.{n}", k)
        for n in ("q", "suggest", "joke")
        for k in ("target_cooldown_s", "opt_out")
    ]
    + [("commands.roast", "web_search"), ("commands.joke", "web_search")]
)

PAGE_ICONS: dict[str, str] = {
    "Whitelist": "users",
    "Commands": "message-circle",
    "Avatar": "walk",
    "Room": "door",
    "Claude": "sparkles",
    "Budget": "coin",
    "Advanced": "adjustments",
    "About": "info-circle",
}

BOX_ICONS: dict[str, str] = {
    "Who can use the bot": "users",
    "/q · ask Claude": "message-question",
    "/roast": "flame",
    "/suggest": "bulb",
    "/joke": "mood-happy",
    "Autopilot": "route",
    "How often each action": "dice",
    "Copy nearby players": "copy",
    "Anti-idle": "zzz",
    "Timing": "clock",
    "Rejoin after a restart": "refresh",
    "Go back after leaving": "arrow-back-up",
    "Model": "sparkles",
    "/q memory": "brain",
    "Spending": "coin",
    "Load": "gauge",
    "Chat": "messages",
    "Packet safety": "shield-check",
    "Pricing (USD)": "receipt",
    "Room loading": "home",
    "About Bobba Buddy": "info-circle",
}


def all_fields() -> list[Field]:
    """Every field on every page, in window order.

    Returns:
        The fields.
    """
    return [f for page in PAGES for box in page.boxes for f in box.fields]
