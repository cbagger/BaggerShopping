import struct

from app.grpc_web import (
    build_sync_items_add_request,
    build_sync_items_quantity_request,
    parse_grpc_web_response,
)


def test_sync_items_frame_contains_list_and_name():
    data = build_sync_items_add_request(
        "1060197a2b61e7875e38fc3955cf1897843",
        "ChatGPT-test-123",
        1786359260070,
    )

    assert data[0] == 0
    message_len = int.from_bytes(data[1:5], "big")
    assert message_len == len(data) - 5
    assert b"1060197a2b61e7875e38fc3955cf1897843" in data
    assert b"ChatGPT-test-123" in data


def test_quantity_update_contains_observed_float_and_unit():
    data = build_sync_items_quantity_request(
        "1060197a2b61e7875e38fc3955cf1897843",
        "019fefe0-3bc6-7852-a396-6cdfbdbaf7a6",
        "Gulerødder",
        False,
        4.0,
        "stk",
        1786359260070,
    )

    # Captured Samsung payload: item field 4 is protobuf fixed32 float and
    # item field 5 is the unit string.
    assert struct.pack("<f", 4.0) in data
    assert b"stk" in data
    assert b"Guler\xc3\xb8dder" in data
    assert b"019fefe03bc67852a3966cdfbdbaf7a6" in data


def test_parse_grpc_web_trailer():
    msg = b"abc"
    message_frame = b"\x00" + len(msg).to_bytes(4, "big") + msg
    trailer = b"grpc-status: 0\r\n"
    trailer_frame = b"\x80" + len(trailer).to_bytes(4, "big") + trailer

    parsed = parse_grpc_web_response(message_frame + trailer_frame)

    assert parsed.message == b"abc"
    assert parsed.grpc_status == 0
