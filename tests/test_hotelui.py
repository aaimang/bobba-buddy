"""Hotel windows: packet layouts, throttling, the send thread and the fallback."""

from __future__ import annotations

import time

from conftest import TO_CLIENT, FakeExtension

from bobba_buddy import hotelui
from bobba_buddy.hotelui import (
    HotelNotifier,
    build_bubble,
    build_popup,
    clean,
    escape,
    red,
)


class Clock:
    def __init__(self):
        """Start at 1000 s."""
        self.now = 1000.0

    def __call__(self):
        return self.now


def windows(*packets):
    ext = FakeExtension()
    ext.client_sent.extend(packets)
    return ext.windows()


def test_bubble_layout_with_the_logo():
    assert windows(build_bubble("Settings saved.")) == [
        (
            "NotificationDialog",
            (
                "bobba_buddy",
                {
                    "display": "BUBBLE",
                    "message": "Bobba Buddy: Settings saved.",
                    "image": hotelui.LOGO_URL,
                },
            ),
        )
    ]


def test_popup_keeps_line_breaks_utf8_and_link():
    [(_, (_, params))] = windows(
        build_popup("one\ncafé ✓", title="Hi", image=None, link=("https://x.test", "Go"))
    )
    assert params == {
        "display": "POP_UP",
        "title": "Hi",
        "message": "one\ncafé ✓",
        "linkUrl": "https://x.test",
        "linkTitle": "Go",
    }


def test_escape_and_colours():
    assert escape('<b>"x" & y</b>') == "&lt;b&gt;&quot;x&quot; &amp; y&lt;/b&gt;"
    assert red("no") == f'<font color="{hotelui.RED}">no</font>'


def test_clean_flattens_caps_and_strips_controls():
    assert clean("a\nb\x07c", 50) == "a b c"
    assert clean("a\nb", 50, multiline=True) == "a\nb"
    long = clean("x" * 500, 20)
    assert len(long) == 20 and long.endswith("…")


def make(ext=None, **settings):
    ext = ext or FakeExtension()
    clock = Clock()
    flags = {"in_hotel": True, "config": True, "cooldown": True, "error": True, "room": True}
    flags.update(settings)
    console = []
    notifier = HotelNotifier(
        ext,
        enabled=lambda kind: flags.get(kind, True),
        min_gap_s=lambda: 10.0,
        fallback=console.append,
        clock=clock,
        gap_s=0.0,
    )
    return notifier, ext, clock, console


def test_same_bubble_waits_for_the_gap():
    notifier, ext, clock, _ = make()
    assert notifier.notify("cooldown", "Friend: /q 9s left", key="cd:friend:q")
    assert not notifier.notify("cooldown", "Friend: /q 8s left", key="cd:friend:q")
    assert notifier.notify("cooldown", "Other: /q 9s left", key="cd:other:q")
    clock.now += 10
    assert notifier.notify("cooldown", "Friend: /q 9s left", key="cd:friend:q")
    notifier.flush()
    assert len(ext.windows()) == 3


def test_kinds_and_master_switch():
    notifier, _, _, _ = make(cooldown=False)
    assert not notifier.notify("cooldown", "x")
    assert notifier.notify("error", "x")
    notifier, _, _, _ = make(in_hotel=False)
    assert not notifier.notify("error", "x")


def test_queue_keeps_the_newest():
    notifier, ext, _, _ = make()
    for i in range(hotelui.QUEUE_MAX + 5):
        notifier.send(build_bubble(f"n{i}"))
    notifier.flush()
    sent = ext.windows()
    assert len(sent) == hotelui.QUEUE_MAX
    assert sent[-1][1][1]["message"].endswith(f"n{hotelui.QUEUE_MAX + 4}")


def test_unknown_packet_falls_back_to_the_console_once():
    ext = FakeExtension()
    del ext.packet_infos[TO_CLIENT]["NotificationDialog"]
    notifier, _, _, console = make(ext)
    notifier.notify("error", "one")
    notifier.notify("error", "two")
    notifier.flush()
    assert ext.client_sent == []
    assert len(console) == 1 and "NotificationDialog" in console[0]
    notifier.clear()  # a new session reports again
    notifier.notify("error", "three")
    notifier.flush()
    assert len(console) == 2


def test_send_failure_falls_back():
    class Broken(FakeExtension):
        def send_to_client(self, pkt):
            raise OSError("socket closed")

    notifier, _, _, console = make(Broken())
    notifier.notify("error", "x")
    notifier.flush()
    assert len(console) == 1


def test_thread_sends_and_stops():
    notifier, ext, _, _ = make()
    notifier.start()
    assert notifier._thread.name == "buddy-notify"
    notifier.notify("config", "saved")
    for _ in range(100):
        if ext.client_sent:
            break
        time.sleep(0.01)
    notifier.stop()
    assert len(ext.client_sent) == 1 and notifier._thread is None
