"""Decodificador de amostras NCW (Native Instruments Compressed Wave).

Baseado no trabalho de engenharia reversa do ConvertWithMoss
(Jürgen Moßgraber, LGPLv3 – https://github.com/git-moss/ConvertWithMoss).
Este arquivo é distribuído sob a mesma licença LGPLv3.
"""
from __future__ import annotations

import os
import struct

import numpy as np

from .model import ConversionError, Sample

FILE_MAGIC = 0xD69EA801
BLOCK_MAGIC = 0x3E9A0C16
NUM = 512


def _unpack(data: bytes, bits: int, count: int = NUM) -> np.ndarray:
    """Lê `count` inteiros com sinal de `bits` bits, empacotados LSB primeiro."""
    raw = np.frombuffer(data, dtype=np.uint8)
    b = np.unpackbits(raw, bitorder="little")
    n = min(count, len(b) // bits)
    b = b[: n * bits].reshape(n, bits).astype(np.int64)
    vals = (b << np.arange(bits, dtype=np.int64)).sum(axis=1)
    if bits < 64:
        sign = np.int64(1) << (bits - 1)
        vals = np.where(vals & sign, vals - (np.int64(1) << bits), vals)
    out = np.zeros(count, dtype=np.int64)
    out[:n] = vals
    return out


def decode_ncw(buf: bytes, name: str = "ncw") -> Sample:
    if len(buf) < 120:
        raise ConversionError(f"Arquivo NCW inválido: {name}")
    magic, version, ch, bps, rate, nframes, off_addr, off_data, _ = struct.unpack_from("<IIHHIIIII", buf, 0)
    if magic != FILE_MAGIC:
        raise ConversionError(f"Arquivo NCW inválido: {name}")
    pos = 4 * 2 + 2 * 2 + 4 * 5 + 88  # 120
    nblocks = (off_data - off_addr) // 4
    offsets = struct.unpack_from(f"<{nblocks}I", buf, pos)
    pos += 4 * nblocks
    data_start = pos
    out = np.zeros((nframes, ch), dtype=np.float64)
    is_float = False
    scale = float(1 << (bps - 1)) if bps else 32768.0
    for bi in range(nblocks - 1):
        p = data_start + offsets[bi]
        mid_side = False
        chans = []
        for c in range(ch):
            bm, base, bits, flags = struct.unpack_from("<IihH", buf, p)
            if bm != BLOCK_MAGIC:
                raise ConversionError(f"Bloco NCW corrompido em {name}")
            p += 16
            if flags & 1:
                mid_side = True
            if flags & 2:
                is_float = True
            size = NUM * abs(bits) // 8 if bits else NUM * (bps // 8)
            blk = buf[p:p + size]
            p += size
            if bits > 0:  # deltas
                d = _unpack(blk, bits)
                s = base + np.concatenate(([0], np.cumsum(d[:-1])))
            elif bits < 0:  # valores truncados
                s = _unpack(blk, -bits)
            else:  # sem compressão
                s = _unpack(blk, bps)
            chans.append(s)
        off = bi * NUM
        n = min(NUM, nframes - off)
        if n <= 0:
            break
        if is_float:
            vals = [np.asarray(s[:n], dtype=np.int64).astype(np.uint32).view(np.float32).astype(np.float64) for s in chans]
        else:
            vals = [np.asarray(s[:n], dtype=np.float64) / scale for s in chans]
        if mid_side and ch == 2:
            m, sd = vals
            vals = [m + sd, m - sd]
        for c in range(ch):
            out[off:off + n, c] = vals[c]
    return Sample(os.path.splitext(os.path.basename(name))[0], out.astype(np.float32), rate)


def read_ncw(path: str) -> Sample:
    with open(path, "rb") as f:
        return decode_ncw(f.read(), path)
