from app.grpc_web import build_sync_items_add_request, parse_grpc_web_response


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


def test_parse_grpc_web_trailer():
    msg = b"abc"
    message_frame = b"\x00" + len(msg).to_bytes(4, "big") + msg
    trailer = b"grpc-status: 0\r\n"
    trailer_frame = b"\x80" + len(trailer).to_bytes(4, "big") + trailer

    parsed = parse_grpc_web_response(message_frame + trailer_frame)

    assert parsed.message == b"abc"
    assert parsed.grpc_status == 0
