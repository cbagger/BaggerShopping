from __future__ import annotations

from dataclasses import dataclass


def concat(*parts: bytes) -> bytes:
    return b"".join(parts)


def encode_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("Only non-negative varints are supported")
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def tag(field_number: int, wire_type: int) -> bytes:
    return encode_varint((field_number << 3) | wire_type)


def uint_field(field_number: int, value: int) -> bytes:
    return concat(tag(field_number, 0), encode_varint(value))


def bytes_field(field_number: int, value: bytes) -> bytes:
    return concat(tag(field_number, 2), encode_varint(len(value)), value)


def string_field(field_number: int, value: str) -> bytes:
    return bytes_field(field_number, value.encode("utf-8"))


def grpc_web_frame(message: bytes) -> bytes:
    return b"\x00" + len(message).to_bytes(4, "big") + message


def build_sync_items_add_request(list_id: str, name: str, timestamp_ms: int) -> bytes:
    item = string_field(1, name)

    operation = concat(
        bytes_field(1, item),
        uint_field(5, 0),
        uint_field(6, 1),
        bytes_field(7, b""),
    )

    change = bytes_field(1, operation)

    message = concat(
        string_field(1, list_id),
        uint_field(2, timestamp_ms),
        bytes_field(3, change),
        uint_field(5, 0),
        uint_field(6, 0),
    )

    return grpc_web_frame(message)


def fixed32_field(field_number: int, value: int) -> bytes:
    if value < 0 or value > 0xFFFFFFFF:
        raise ValueError("fixed32 must fit in 32 bits")
    return concat(tag(field_number, 5), value.to_bytes(4, "little"))


def normalize_item_id(item_id: str) -> str:
    """Samsung's SyncItems protobuf uses the UUID hex without hyphens."""
    compact = item_id.replace("-", "").strip()
    if not compact:
        raise ValueError("item_id cannot be empty")
    return compact


def _sync_items_envelope(list_id: str, timestamp_ms: int, change: bytes) -> bytes:
    message = concat(
        string_field(1, list_id),
        uint_field(2, timestamp_ms),
        bytes_field(3, change),
        uint_field(5, 0),
        uint_field(6, 0),
    )
    return grpc_web_frame(message)


def build_sync_items_checked_request(
    list_id: str,
    item_id: str,
    name: str,
    checked: bool,
    timestamp_ms: int,
) -> bytes:
    """Build the observed Samsung Food SyncItems update operation.

    Captured from the web app on 2026-08-10:
      - change field 2 = item update
      - update field 1 = compact item UUID
      - update field 2 = item payload
      - update field 3 = checked (0/1)
    """
    item_payload = concat(
        string_field(1, name),
        bytes_field(2, b""),
        bytes_field(3, b""),
        fixed32_field(4, 0),
        bytes_field(5, b""),
    )

    update = concat(
        string_field(1, normalize_item_id(item_id)),
        bytes_field(2, item_payload),
        uint_field(3, 1 if checked else 0),
        uint_field(5, 0),
        uint_field(6, 0),
    )

    return _sync_items_envelope(
        list_id,
        timestamp_ms,
        bytes_field(2, update),
    )


def build_sync_items_delete_request(
    list_id: str,
    item_id: str,
    timestamp_ms: int,
) -> bytes:
    """Build the observed Samsung Food 'Clear' deletion operation.

    The captured web request identifies exactly one item. This lets the mobile
    client expose an individual delete action while still using Samsung's own
    observed mutation shape.
    """
    delete_op = concat(
        string_field(1, normalize_item_id(item_id)),
        uint_field(5, 0),
        uint_field(6, 0),
    )

    return _sync_items_envelope(
        list_id,
        timestamp_ms,
        bytes_field(3, delete_op),
    )


@dataclass
class GrpcWebResponse:
    message: bytes
    grpc_status: int | None
    grpc_message: str | None


def parse_grpc_web_response(data: bytes) -> GrpcWebResponse:
    pos = 0
    message = b""
    grpc_status = None
    grpc_message = None

    while pos + 5 <= len(data):
        flags = data[pos]
        length = int.from_bytes(data[pos + 1:pos + 5], "big")
        payload = data[pos + 5:pos + 5 + length]
        pos += 5 + length

        if flags & 0x80:
            text = payload.decode("ascii", errors="ignore")
            for line in text.splitlines():
                key, _, value = line.partition(":")
                if key.lower() == "grpc-status":
                    try:
                        grpc_status = int(value.strip())
                    except ValueError:
                        pass
                elif key.lower() == "grpc-message":
                    grpc_message = value.strip()
        elif flags == 0:
            message += payload

    return GrpcWebResponse(
        message=message,
        grpc_status=grpc_status,
        grpc_message=grpc_message,
    )


def extract_printable_strings(data: bytes, min_len: int = 8) -> list[str]:
    out: list[str] = []
    current = bytearray()
    for b in data:
        if 32 <= b <= 126:
            current.append(b)
        else:
            if len(current) >= min_len:
                out.append(current.decode("ascii", errors="ignore"))
            current.clear()
    if len(current) >= min_len:
        out.append(current.decode("ascii", errors="ignore"))
    return out
