# -*- coding: utf-8 -*-
"""Windows DPAPI 加解密（cookie 落盘用）。非 Windows 或失败时退回明文。"""
from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes

PREFIX = "dpapi:"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _protect(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    blob_in, _buf = _blob(data)
    blob_out = _DATA_BLOB()
    if not crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _unprotect(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    blob_in, _buf = _blob(data)
    blob_out = _DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def protect_text(text: str) -> str:
    if os.name != "nt":
        return text
    try:
        return PREFIX + base64.b64encode(_protect(text.encode("utf-8"))).decode("ascii")
    except Exception:
        return text


def unprotect_text(text: str) -> str:
    text = (text or "").strip()
    if not text.startswith(PREFIX):
        return text
    if os.name != "nt":
        return text
    try:
        raw = base64.b64decode(text[len(PREFIX):])
        return _unprotect(raw).decode("utf-8")
    except Exception:
        return text
