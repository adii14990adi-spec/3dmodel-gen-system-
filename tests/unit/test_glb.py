from __future__ import annotations

import struct

import pytest

from processing.glb import (GLBError, append_accessor, inspect_document,
                            parse_glb, write_glb)


def test_write_and_parse_roundtrip():
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
           "nodes": [{"name": "root"}],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
           "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [1, 0, 0, 1]}}]}
    binary = b""
    acc, binary = append_accessor(doc, binary, [[0, 0, 0], [1, 0, 0], [0, 1, 0]], 5126, "VEC3", True)
    idx, binary = append_accessor(doc, binary, [0, 1, 2], 5123, "SCALAR")
    doc["accessors"][acc]["min"] = [0, 0, 0]
    doc["accessors"][acc]["max"] = [1, 1, 0]
    glb = write_glb(doc, binary)
    assert glb[:4] == b"glTF"
    parsed = parse_glb(glb)
    meta = inspect_document(parsed, "tri.glb")
    assert meta["geometry"]["vertex_count"] == 3
    assert meta["geometry"]["triangle_count"] == 1
    assert meta["geometry"]["index_count"] == 3
    assert meta["geometry"]["material_count"] == 1
    assert meta["skeleton"]["present"] is False


def test_truncated_glb_is_rejected():
    with pytest.raises(GLBError):
        parse_glb(b"glTF" + struct.pack("<II", 2, 720) + b"\x00" * 8)


def test_empty_arrays_are_stripped():
    doc = {"asset": {"version": "2.0"}, "animations": [], "skins": [], "images": []}
    parsed = parse_glb(write_glb(doc, b"\x00" * 4))
    assert "animations" not in parsed["json"]
    assert "skins" not in parsed["json"]
