from app.grpc_web import (
    build_sync_items_checked_request,
    build_sync_items_delete_request,
)


def test_checked_builder_matches_observed_shape():
    body = build_sync_items_checked_request(
        "1060197a2b61e7875e38fc3955cf1897843",
        "019fe69d-b8ac-7824-a51c-ec77ef8b92b9",
        "Bagger-test-8291",
        True,
        1,
    )
    assert body[0] == 0
    payload = body[5:]
    assert b"019fe69db8ac7824a51cec77ef8b92b9" in payload
    assert b"Bagger-test-8291" in payload
    # observed checked field is protobuf field 3 varint = 1
    assert b"\x18\x01" in payload


def test_unchecked_builder_sets_zero():
    body = build_sync_items_checked_request(
        "1060197a2b61e7875e38fc3955cf1897843",
        "019feb4f-5244-7660-a86d-6f5a60c1523b",
        "Chatgpt-test-123",
        False,
        1,
    )
    payload = body[5:]
    assert b"019feb4f52447660a86d6f5a60c1523b" in payload
    assert b"Chatgpt-test-123" in payload
    assert b"\x18\x00" in payload


def test_delete_builder_matches_observed_clear_shape():
    body = build_sync_items_delete_request(
        "1060197a2b61e7875e38fc3955cf1897843",
        "019feb4f-5244-7660-a86d-6f5a60c1523b",
        1,
    )
    payload = body[5:]
    assert b"019feb4f52447660a86d6f5a60c1523b" in payload
    # change field 3 (0x1a) contains delete operation
    assert b"\x1a" in payload
