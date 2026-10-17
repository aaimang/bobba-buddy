"""The exception guard and bad-packet reporting."""

import logging
from functools import partial

from conftest import packet

from bobba_buddy.gpython import Direction, HMessage
from bobba_buddy.guard import (
    configure_dump_dir,
    guarded,
    hex_dump,
    report_bad_packet,
    reset_reports,
)
from bobba_buddy.protocol import ProtocolError


def test_protocol_error_is_reported_once_with_dump(caplog):
    reset_reports()

    def on_thing(msg):
        raise ProtocolError("truncated string")

    handler = guarded(on_thing)
    msg = HMessage(packet(1, "hi"), Direction.TO_CLIENT, 0)
    with caplog.at_level(logging.DEBUG, logger="bobba_buddy.guard"):
        handler(msg)
        handler(msg)
    levels = [r.levelname for r in caplog.records]
    assert levels == ["WARNING", "DEBUG"]
    assert "on_thing packet didn't match" in caplog.records[0].getMessage()
    assert "| " in caplog.records[0].getMessage()


def test_other_errors_keep_traceback(caplog):
    def boom(_msg):
        raise RuntimeError("bug")

    with caplog.at_level(logging.ERROR, logger="bobba_buddy.guard"):
        guarded(boom)(None)
    assert caplog.records[0].exc_info is not None


def test_partial_handlers_get_a_name(caplog):
    def handle(kind, msg):
        raise ProtocolError("x")

    reset_reports()
    with caplog.at_level(logging.WARNING, logger="bobba_buddy.guard"):
        guarded(partial(handle, "chat"))(HMessage(packet(1), Direction.TO_CLIENT, 0))
    assert caplog.records[0].getMessage().startswith("handle packet")


def test_hex_dump():
    assert hex_dump(b"\x00\x01AB") == "00 01 41 42 | ..AB"
    assert "(+2 more bytes)" in hex_dump(b"abcd", limit=2)


def test_first_bad_packet_is_saved_whole(tmp_path, caplog):
    reset_reports()
    configure_dump_dir(tmp_path)
    try:
        pkt = packet(*range(200))  # 806 bytes, longer than the log excerpt
        with caplog.at_level(logging.WARNING, logger="bobba_buddy.guard"):
            report_bad_packet("Users", pkt, "entry 3: odd tail")
            report_bad_packet("Users", pkt, "entry 3: odd tail")
        [saved] = list(tmp_path.glob("Users-*.hex"))
        text = saved.read_text().splitlines()
        assert text[0] == "# Users: entry 3: odd tail"
        assert bytes.fromhex(" ".join(text[2:])) == bytes(pkt.bytearray)
        assert f"saved={saved}" in caplog.records[0].getMessage()
    finally:
        configure_dump_dir(None)
