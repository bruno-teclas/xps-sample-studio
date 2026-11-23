"""Leitura/escrita de WAV e AIFF + processamento de áudio (DSP)."""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
from scipy import signal

from .model import ConversionError, Sample

# --------------------------------------------------------------------------
# Leitura
# --------------------------------------------------------------------------


def _pcm_to_float(raw: bytes, bits: int, channels: int, fmt_tag: int) -> np.ndarray:
    if fmt_tag == 3:  # IEEE float
        dt = "<f4" if bits == 32 else "<f8"
        a = np.frombuffer(raw[: len(raw) - len(raw) % (bits // 8)], dtype=dt).astype(np.float32)
    elif bits == 8:
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif bits == 16:
        a = np.frombuffer(raw[: len(raw) - len(raw) % 2], dtype="<i2").astype(np.float32) / 32768.0
    elif bits == 24:
        b = np.frombuffer(raw[: len(raw) - len(raw) % 3], dtype=np.uint8).reshape(-1, 3)
        i = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        i = np.where(i & 0x800000, i - 0x1000000, i)
        a = i.astype(np.float32) / 8388608.0
    elif bits == 32:
        a = np.frombuffer(raw[: len(raw) - len(raw) % 4], dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ConversionError(f"Profundidade de bits não suportada: {bits}")
    n = len(a) // channels
    return a[: n * channels].reshape(n, channels)


def read_wav(path: str) -> Sample:
    with open(path, "rb") as f:
        buf = f.read()
    if len(buf) < 12 or buf[:4] not in (b"RIFF", b"RF64") or buf[8:12] != b"WAVE":
        raise ConversionError(f"Arquivo WAV inválido: {path}")
    pos = 12
    fmt = None
    data = None
    loop = (None, None)
    while pos + 8 <= len(buf):
        cid = buf[pos:pos + 4]
        size = struct.unpack("<I", buf[pos + 4:pos + 8])[0]
        body = buf[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            tag, ch, rate, _, _, bits = struct.unpack("<HHIIHH", body[:16])
            if tag == 0xFFFE and len(body) >= 26:
                tag = struct.unpack("<H", body[24:26])[0]
            fmt = (tag, ch, rate, bits)
        elif cid == b"data":
            data = body
        elif cid == b"smpl" and len(body) >= 36:
            nloops = struct.unpack("<I", body[28:32])[0]
            if nloops and len(body) >= 36 + 24:
                _, _, start, end, _, _ = struct.unpack("<IIIIII", body[36:60])
                loop = (start, end + 1)
        pos += 8 + size + (size & 1)
    if fmt is None or data is None:
        raise ConversionError(f"WAV sem blocos fmt/data: {path}")
    tag, ch, rate, bits = fmt
    if tag not in (1, 3):
        raise ConversionError(f"WAV comprimido não suportado (formato {tag}): {path}")
    audio = _pcm_to_float(data, bits, ch, tag)
    import os
    return Sample(os.path.splitext(os.path.basename(path))[0], audio, rate, loop[0], loop[1])


def _ieee_ext(b: bytes) -> float:
    expon = ((b[0] & 0x7F) << 8) | b[1]
    hi = struct.unpack(">I", b[2:6])[0]
    lo = struct.unpack(">I", b[6:10])[0]
    if expon == 0 and hi == 0 and lo == 0:
        return 0.0
    f = hi * 2.0 ** (expon - 16383 - 31) + lo * 2.0 ** (expon - 16383 - 63)
    return -f if b[0] & 0x80 else f


def read_aiff(path: str) -> Sample:
    with open(path, "rb") as f:
        buf = f.read()
    if buf[:4] != b"FORM" or buf[8:12] not in (b"AIFF", b"AIFC"):
        raise ConversionError(f"AIFF inválido: {path}")
    pos = 12
    ch = bits = rate = None
    ssnd = None
    while pos + 8 <= len(buf):
        cid = buf[pos:pos + 4]
        size = struct.unpack(">I", buf[pos + 4:pos + 8])[0]
        body = buf[pos + 8:pos + 8 + size]
        if cid == b"COMM":
            ch, _, bits = struct.unpack(">hIh", body[:8])
            rate = int(round(_ieee_ext(body[8:18])))
            if buf[8:12] == b"AIFC" and len(body) >= 22 and body[18:22] not in (b"NONE", b"twos"):
                raise ConversionError(f"AIFC comprimido não suportado: {path}")
        elif cid == b"SSND":
            off = struct.unpack(">I", body[:4])[0]
            ssnd = body[8 + off:]
        pos += 8 + size + (size & 1)
    if ssnd is None or ch is None:
        raise ConversionError(f"AIFF incompleto: {path}")
    width = (bits + 7) // 8
    raw = np.frombuffer(ssnd[: len(ssnd) - len(ssnd) % width], dtype=np.uint8).reshape(-1, width)[:, ::-1]
    le = raw.tobytes()  # agora little-endian
    if width == 1:
        a = np.frombuffer(le, dtype=np.int8).astype(np.float32) / 128.0
        n = len(a) // ch
        audio = a[: n * ch].reshape(n, ch)
    else:
        audio = _pcm_to_float(le, width * 8, ch, 1)
    import os
    return Sample(os.path.splitext(os.path.basename(path))[0], audio, rate)


def read_audio(path: str) -> Sample:
    low = path.lower()
    if low.endswith((".aif", ".aiff", ".aifc")):
        return read_aiff(path)
    if low.endswith(".ncw"):
        from .ncw import read_ncw
        return read_ncw(path)
    return read_wav(path)


# --------------------------------------------------------------------------
# Escrita (WAV 16 bits com bloco smpl para raiz e loop)
# --------------------------------------------------------------------------


def float_to_int16(a: np.ndarray, dither: bool = True) -> np.ndarray:
    x = a.astype(np.float64) * 32767.0
    if dither:
        rng = np.random.default_rng(12345)
        x += rng.random(x.shape) - rng.random(x.shape)  # TPDF ±1 LSB
    return np.clip(np.round(x), -32768, 32767).astype("<i2")


def float_to_int24(a: np.ndarray) -> bytes:
    x = np.clip(np.round(a.astype(np.float64) * 8388607.0), -8388608, 8388607).astype("<i4")
    b = x.reshape(-1).view(np.uint8).reshape(-1, 4)[:, :3]
    return b.tobytes()


def write_wav16(path, data, rate, root=None, loop=None, dither=True) -> int:
    return write_wav(path, data, rate, 16, root, loop, dither)


def write_wav(path: str, data: np.ndarray, rate: int, bits: int = 16, root: Optional[int] = None,
              loop: Optional[Tuple[int, int]] = None, dither: bool = True) -> int:
    if data.ndim == 1:
        data = data[:, None]
    ch = data.shape[1]
    bps = bits // 8
    pcm = float_to_int16(data, dither).tobytes() if bits == 16 else float_to_int24(data)
    fmt = struct.pack("<HHIIHH", 1, ch, rate, rate * ch * bps, ch * bps, bits)
    chunks = [b"fmt " + struct.pack("<I", len(fmt)) + fmt]
    chunks.append(b"data" + struct.pack("<I", len(pcm)) + pcm + (b"\0" if len(pcm) & 1 else b""))
    if root is not None:
        period = int(round(1e9 / rate))
        loops = [] if loop is None else [loop]
        smpl = struct.pack("<IIIIIIIII", 0, 0, period, int(root), 0, 0, 0, len(loops), 0)
        for (s, e) in loops:
            smpl += struct.pack("<IIIIII", 0, 0, int(s), int(e) - 1, 0, 0)
        chunks.append(b"smpl" + struct.pack("<I", len(smpl)) + smpl)
    body = b"WAVE" + b"".join(chunks)
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", len(body)) + body)
    return 8 + len(body)


# --------------------------------------------------------------------------
# DSP
# --------------------------------------------------------------------------


def resample(data: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst:
        return data
    g = math.gcd(int(src), int(dst))
    up, down = dst // g, src // g
    if up > 2000 or down > 2000:  # taxas estranhas: usa aproximação
        n = int(round(data.shape[0] * dst / src))
        return signal.resample(data, n, axis=0).astype(np.float32)
    return signal.resample_poly(data, up, down, axis=0).astype(np.float32)


@dataclass
class EQBand:
    kind: str          # "lowcut", "lowshelf", "peak", "highshelf", "highcut"
    freq: float
    gain_db: float = 0.0
    q: float = 0.707
    enabled: bool = True


def _biquad(b: EQBand, fs: int) -> Optional[np.ndarray]:
    """Coeficientes RBJ (Audio EQ Cookbook) em formato SOS."""
    f = min(max(b.freq, 10.0), fs * 0.49)
    w0 = 2 * math.pi * f / fs
    cw, sw = math.cos(w0), math.sin(w0)
    A = 10 ** (b.gain_db / 40.0)
    alpha = sw / (2 * max(b.q, 0.05))
    k = b.kind
    if k == "peak":
        if abs(b.gain_db) < 1e-3:
            return None
        bb = [1 + alpha * A, -2 * cw, 1 - alpha * A]
        aa = [1 + alpha / A, -2 * cw, 1 - alpha / A]
    elif k in ("lowshelf", "highshelf"):
        if abs(b.gain_db) < 1e-3:
            return None
        sa = 2 * math.sqrt(A) * alpha
        if k == "lowshelf":
            bb = [A * ((A + 1) - (A - 1) * cw + sa), 2 * A * ((A - 1) - (A + 1) * cw), A * ((A + 1) - (A - 1) * cw - sa)]
            aa = [(A + 1) + (A - 1) * cw + sa, -2 * ((A - 1) + (A + 1) * cw), (A + 1) + (A - 1) * cw - sa]
        else:
            bb = [A * ((A + 1) + (A - 1) * cw + sa), -2 * A * ((A - 1) + (A + 1) * cw), A * ((A + 1) + (A - 1) * cw - sa)]
            aa = [(A + 1) - (A - 1) * cw + sa, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - sa]
    elif k == "lowcut":
        bb = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
        aa = [1 + alpha, -2 * cw, 1 - alpha]
    elif k == "highcut":
        bb = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]
        aa = [1 + alpha, -2 * cw, 1 - alpha]
    else:
        return None
    return np.array([bb[0] / aa[0], bb[1] / aa[0], bb[2] / aa[0], 1.0, aa[1] / aa[0], aa[2] / aa[0]])


def eq_sos(bands: List[EQBand], fs: int) -> Optional[np.ndarray]:
    rows = [r for r in (_biquad(b, fs) for b in bands if b.enabled) if r is not None]
    return np.vstack(rows) if rows else None


def eq_response(bands: List[EQBand], fs: int, freqs: np.ndarray) -> np.ndarray:
    """Resposta em dB, usada para desenhar a curva na interface."""
    sos = eq_sos(bands, fs)
    if sos is None:
        return np.zeros_like(freqs)
    _, h = signal.sosfreqz(sos, worN=freqs, fs=fs)
    return 20 * np.log10(np.maximum(np.abs(h), 1e-9))


def apply_eq(data: np.ndarray, rate: int, bands: List[EQBand]) -> np.ndarray:
    sos = eq_sos(bands, rate)
    if sos is None:
        return data
    return signal.sosfilt(sos, data, axis=0).astype(np.float32)


def to_mono(data: np.ndarray) -> np.ndarray:
    if data.ndim > 1 and data.shape[1] > 1:
        return data.mean(axis=1, keepdims=True).astype(np.float32)
    return data if data.ndim > 1 else data[:, None]


def peak_db(data: np.ndarray) -> float:
    p = float(np.max(np.abs(data))) if data.size else 0.0
    return 20 * math.log10(p) if p > 0 else -120.0


def trim_silence(data: np.ndarray, threshold_db: float = -60.0, keep_ms: float = 2.0,
                 rate: int = 44100) -> Tuple[np.ndarray, int]:
    """Remove silêncio do início. Devolve (áudio, frames removidos)."""
    thr = 10 ** (threshold_db / 20.0)
    env = np.max(np.abs(data), axis=1)
    idx = np.nonzero(env > thr)[0]
    if idx.size == 0:
        return data, 0
    start = max(0, int(idx[0]) - int(rate * keep_ms / 1000.0))
    return data[start:], start


def fade(data: np.ndarray, rate: int, fade_in_ms: float, fade_out_ms: float) -> np.ndarray:
    out = data.copy()
    n_in = min(int(rate * fade_in_ms / 1000.0), len(out))
    n_out = min(int(rate * fade_out_ms / 1000.0), len(out))
    if n_in > 1:
        out[:n_in] *= np.linspace(0, 1, n_in, dtype=np.float32)[:, None]
    if n_out > 1:
        out[-n_out:] *= np.linspace(1, 0, n_out, dtype=np.float32)[:, None]
    return out


def crossfade_loop(data: np.ndarray, ls: int, le: int, xfade: int) -> np.ndarray:
    """Suaviza a emenda do loop misturando o fim do loop com o trecho antes do início."""
    xfade = int(min(xfade, ls, (le - ls) // 2))
    if xfade < 8:
        return data
    out = data.copy()
    t = np.linspace(0, 1, xfade, dtype=np.float32)[:, None]
    fin, fout = np.sqrt(t), np.sqrt(1 - t)
    seg_end = out[le - xfade:le]
    seg_pre = data[ls - xfade:ls]
    out[le - xfade:le] = seg_end * fout + seg_pre * fin
    return out
