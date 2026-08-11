from __future__ import annotations

import struct
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


def float_field(field_number: int, value: float) -> bytes:
    """Encode a protobuf fixed32 float.

    Samsung Food shopping-item captures from 2026-08-11 show item payload
    field 4 as IEEE-754 little-endian float quantity (e.g. 3.0 = 00 00 40 40,
    4.0 = 00 00 80 40) and field 5 as the unit string.
    """
    return concat(tag(field_number, 5), struct.pack("<f", float(value)))


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


def _item_payload(
    name: str,
    quantity: float | None = None,
    unit: str | None = None,
) -> bytes:
    parts = [
        string_field(1, name),
        bytes_field(2, b""),
        bytes_field(3, b""),
    ]

    # Samsung uses 0/empty for items without an explicit quantity. Preserve an
    # existing quantity/unit on any update so check/uncheck cannot erase it.
    if quantity is None:
        parts.append(fixed32_field(4, 0))
    else:
        parts.append(float_field(4, quantity))
    parts.append(string_field(5, unit or ""))
    return concat(*parts)


def _build_sync_items_update_request(
    list_id: str,
    item_id: str,
    name: str,
    checked: bool,
    timestamp_ms: int,
    quantity: float | None = None,
    unit: str | None = None,
) -> bytes:
    update = concat(
        string_field(1, normalize_item_id(item_id)),
        bytes_field(2, _item_payload(name, quantity, unit)),
        uint_field(3, 1 if checked else 0),
        uint_field(5, 0),
        uint_field(6, 0),
    )

    return _sync_items_envelope(
        list_id,
        timestamp_ms,
        bytes_field(2, update),
    )


def build_sync_items_checked_request(
    list_id: str,
    item_id: str,
    name: str,
    checked: bool,
    timestamp_ms: int,
    quantity: float | None = None,
    unit: str | None = None,
) -> bytes:
    """Build the observed Samsung Food SyncItems item-update operation."""
    return _build_sync_items_update_request(
        list_id,
        item_id,
        name,
        checked,
        timestamp_ms,
        quantity,
        unit,
    )


def build_sync_items_quantity_request(
    list_id: str,
    item_id: str,
    name: str,
    checked: bool,
    quantity: float,
    unit: str,
    timestamp_ms: int,
) -> bytes:
    if quantity <= 0:
        raise ValueError("quantity must be greater than zero")
    return _build_sync_items_update_request(
        list_id,
        item_id,
        name,
        checked,
        timestamp_ms,
        quantity,
        unit,
    )


def build_sync_items_delete_request(
    list_id: str,
    item_id: str,
    timestamp_ms: int,
) -> bytes:
    """Build the observed Samsung Food 'Clear' deletion operation."""
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
