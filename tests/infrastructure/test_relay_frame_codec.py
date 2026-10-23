import base64
import json

import pytest

from snodo.infrastructure.relay_frame_codec import (
    CHUNK_SIZE,
    ChunkReassembler,
    RelayFrameError,
    decode_frame,
    encode_frame,
    split_body,
)


FRAMES = [
    {"t": "hello", "v": 1, "machine_id": "m", "snodo_version": "1", "endpoints": []},
    {"t": "res", "id": "r", "status": 200, "headers": {}},
    {"t": "chunk", "id": "r", "seq": 0, "last": True, "data_b64": ""},
    {"t": "err", "id": "r", "code": "x", "message": "y"},
    {"t": "keep", "id": "r"},
    {"t": "req", "id": "r", "hostname": "h", "method": "GET", "path": "/", "headers": {}},
    {"t": "cancel", "id": "r"},
]


@pytest.mark.parametrize("frame", FRAMES)
def test_frame_round_trip(frame):
    assert decode_frame(encode_frame(frame)) == frame


@pytest.mark.parametrize("payload", ["{", "[]", '{"t":"unknown"}', '{"t":"keep"}',
                                      '{"t":"chunk","id":"r","seq":-1,"last":true,"data_b64":""}',
                                      '{"t":"chunk","id":"r","seq":0,"last":true,"data_b64":"!"}',
                                      json.dumps({"t":"chunk", "id":"r", "seq":0, "last":True,
                                                  "data_b64":base64.b64encode(b"x" * (CHUNK_SIZE + 1)).decode()})])
def test_rejects_invalid_frames(payload):
    with pytest.raises(RelayFrameError):
        decode_frame(payload)


@pytest.mark.parametrize("size", [0, CHUNK_SIZE, CHUNK_SIZE + 1, 5 * 1024 * 1024])
def test_split_and_reassemble(size):
    body = bytes((i % 251 for i in range(size)))
    chunks = list(split_body("r", (body[i:i + 8191] for i in range(0, size, 8191))))
    assert all(len(base64.b64decode(c["data_b64"])) <= CHUNK_SIZE for c in chunks)
    assert [c["seq"] for c in chunks] == list(range(len(chunks)))
    assert chunks[-1]["last"]
    receiver = ChunkReassembler("r")
    result = None
    for chunk in chunks:
        result = receiver.add(chunk)
    assert result == body


def test_reassembler_rejects_sequence_and_limit():
    frames = list(split_body("r", b"ab"))
    with pytest.raises(RelayFrameError):
        ChunkReassembler("r").add({**frames[0], "seq": 1})
    receiver = ChunkReassembler("r", 1)
    with pytest.raises(RelayFrameError):
        receiver.add(frames[0])


def test_rejects_after_last():
    frame = next(split_body("r", b""))
    receiver = ChunkReassembler("r")
    receiver.add(frame)
    with pytest.raises(RelayFrameError):
        receiver.add(frame)
