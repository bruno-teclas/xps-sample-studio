"""Gravador de arquivos .SVZ (timbre com multisample) para teclados Roland ZEN-Core / XPS.

Um .SVZ com um timbre (Tone) que toca um multisample é importado no teclado por
UTILITY › IMPORT TONE, já com todas as amostras mapeadas no teclado – sem ajuste manual.

Estrutura (little-endian), conforme a engenharia reversa do ConvertWithMoss
(Jürgen Moßgraber, LGPLv3 – documentation/design/ZENCORE_FORMAT.md):

    cabeçalho 16 bytes "SVZa" + versão + modelo
    índice (TOC) de blocos: tag, plataforma "ZCOR", deslocamento, tamanho
    DIFa  1 × 32      registro constante
    PATa  1 × 1632    o timbre (4 partials; partial 1 toca o multisample)
    USPa  M × 64      parâmetros de cada amostra (raiz, loop, nível)
    MSPa  1 × 1040    mapa de 128 teclas → número da amostra
    USDa  M × var     áudio de cada amostra ("SMPd" + PCM 16 bits)

Os arquivos .bin em assets/svz são registros gravados por um teclado Roland de verdade
(do ConvertWithMoss); este módulo segue a licença LGPLv3.
"""
from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .model import ConversionError

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "svz")


def _load(name: str) -> bytes:
    with open(os.path.join(ASSETS, name), "rb") as f:
        return f.read()


NAME = 16
PREVIEW_OFFSET, PREVIEW_VALUES = 0x60, 182
USP_LOOP_MODE, USP_LEVEL, USP_ORIG_KEY = 0x14, 0x15, 0x19
USP_START, USP_LOOP_START, USP_END, USP_CHANNELS = 0x1C, 0x20, 0x24, 0x2C
PAT_KBD, PAT_KBD_STRIDE = 0x98, 0x0C
PAT_PARTIAL_SWITCH = 0xA4
PAT_VEL_LOW, PAT_VEL_HIGH = 0xA0, 0xA1
PAT_OSC_STRIDE = 0x7C
PAT_PAN, PAT_WAVE_GROUP, PAT_WAVE_L, PAT_WAVE_R = 0xCE, 0xDF, 0xE2, 0xE4
PAT_TVA_TIME, PAT_TVA_LEVEL, PAT_ENV_STRIDE = 0x37A, 0x382, 0x10
WAVE_GROUP_MULTISAMPLE = 3
LOOP_GUARD = 16
SVZ_RATE = 48000


@dataclass
class SvzSample:
    name: str
    pcm: np.ndarray            # int16 mono
    root: int
    loop: Optional[Tuple[int, int]] = None   # (início, fim exclusivo) em frames
    level: int = 127


@dataclass
class SvzLayer:
    """Um multisample (uma camada) tocado por um partial."""
    name: str
    key_to_sample: List[int] = field(default_factory=lambda: [0] * 128)   # 1-based
    pan: int = 0
    vel_low: int = 1
    vel_high: int = 127


def _pad(name: str, zero: bool) -> bytes:
    b = "".join(c if 32 <= ord(c) < 127 else "_" for c in name).encode("ascii")[:NAME]
    return b + (b"\0" if zero else b" ") * (NAME - len(b))


def _u16(buf: bytearray, off: int, v: int):
    struct.pack_into("<H", buf, off, int(v) & 0xFFFF)


def _u32(buf: bytearray, off: int, v: int):
    struct.pack_into("<I", buf, off, int(v) & 0xFFFFFFFF)


def _block(records: List[bytes]) -> bytes:
    n = len(records)
    unit = len(records[0]) if records else 0
    head = struct.pack("<IIII", n, unit, 16 + 4 * n, 0)
    crcs = b"".join(struct.pack("<I", zlib.crc32(r) & 0xFFFFFFFF) for r in records)
    return head + crcs + b"".join(records)


def _usda(chunks: List[bytes]) -> bytes:
    n = len(chunks)
    data_off = 16 + 16 * n
    dir_ = b""
    off = data_off
    for i, c in enumerate(chunks):
        dir_ += struct.pack("<IIII", i, off, len(c), zlib.crc32(c) & 0xFFFFFFFF)
        off += len(c)
    return struct.pack("<IIII", n, 0, data_off, 0) + dir_ + b"".join(chunks)


def _container(header: bytes, tags: List[str], bodies: List[bytes]) -> bytes:
    toc = b""
    off = 16 + 16 * len(tags)
    for t, b in zip(tags, bodies):
        toc += t.encode("ascii") + b"ZCOR" + struct.pack("<II", off, len(b))
        off += len(b)
    return header[:16] + toc + b"".join(bodies)


def _uspa(s: SvzSample, end: int) -> bytes:
    r = bytearray(_load("uspa.bin"))
    r[0:NAME] = _pad(s.name, True)
    r[USP_LOOP_MODE] = 0 if s.loop else 1
    r[USP_LEVEL] = max(0, min(127, s.level))
    r[USP_ORIG_KEY] = max(0, min(127, s.root))
    _u32(r, USP_START, 0)
    _u32(r, USP_LOOP_START, s.loop[0] if s.loop else 0)
    _u32(r, USP_END, end)
    r[USP_CHANNELS] = 2
    return bytes(r)


def _smpd(s: SvzSample, pcm: np.ndarray, end: int) -> bytes:
    h = bytearray(_load("smpd_header.bin"))
    h[0:4] = b"SMPd"
    _u32(h, 4, 2 * end)
    h[8] = 1
    h[9] = 16
    _u32(h, 0x0C, SVZ_RATE)
    h[0x10:0x10 + NAME] = _pad(s.name, True)
    n = len(pcm)
    if n >= PREVIEW_VALUES:
        edges = (np.arange(PREVIEW_VALUES + 1) * n) // PREVIEW_VALUES
        a = np.abs(pcm.astype(np.int64))
        for i in range(PREVIEW_VALUES):
            lo, hi = edges[i], max(edges[i] + 1, edges[i + 1])
            _u16(h, PREVIEW_OFFSET + 2 * i, int(a[lo:hi].mean()))
    return bytes(h) + pcm.astype("<i2").tobytes()


def _tone(name: str, layers: List[SvzLayer], wave_numbers: List[int], attack: int) -> bytes:
    stereo_tpl = len(layers) > 1
    r = bytearray(_load("pata_stereo.bin" if stereo_tpl else "pata_multisample.bin"))
    r[0:NAME] = _pad(name, False)
    count = len(layers)
    if count >= 3:   # partials 3–4 copiados de um partial válido
        _clone_partial(r, 0, 2)
    if count >= 4:
        _clone_partial(r, 1, 3)
    for p in range(4):
        osc = p * PAT_OSC_STRIDE
        kbd = p * PAT_KBD_STRIDE
        if p == 3:
            sw = 127
        elif p + 1 < count:
            sw = 1
        elif p < count:
            sw = 0
        else:
            sw = 127
        r[PAT_PARTIAL_SWITCH + kbd] = sw
        if p < count:
            ly = layers[p]
            r[PAT_WAVE_GROUP + osc] = WAVE_GROUP_MULTISAMPLE
            _u16(r, PAT_WAVE_L + osc, wave_numbers[p])
            # mono: o mesmo multisample nos dois lados; camadas estéreo: cada partial é um lado
            _u16(r, PAT_WAVE_R + osc, wave_numbers[p] if count == 1 else 0)
            r[PAT_PAN + osc] = ly.pan & 0xFF
            r[PAT_VEL_LOW + kbd] = max(1, min(127, ly.vel_low))
            r[PAT_VEL_HIGH + kbd] = max(1, min(127, ly.vel_high))
            t = PAT_TVA_TIME + p * PAT_ENV_STRIDE
            _u16(r, t, attack)                                  # ataque (0 gera estalo)
            if struct.unpack_from("<H", r, t + 4)[0] < 8:       # decay 0 deixa o timbre mudo
                _u16(r, t + 4, 400)
        else:
            r[PAT_WAVE_GROUP + osc] = 0
            _u16(r, PAT_WAVE_L + osc, 0)
            _u16(r, PAT_WAVE_R + osc, 0)
    return bytes(r)


def _clone_partial(r: bytearray, src: int, dst: int):
    def cp(base, stride, limit):
        a, b = base + src * stride, base + dst * stride
        n = min(stride, limit - b)
        if n > 0:
            r[b:b + n] = r[a:a + n]
    cp(PAT_PAN, PAT_OSC_STRIDE, 0x2B8)
    cp(0x2B8, 0x18, len(r))
    cp(0x318, 0x18, len(r))
    cp(PAT_TVA_TIME, PAT_ENV_STRIDE, len(r))


def _keymap(layer: SvzLayer) -> bytes:
    r = bytearray(16 + 128 * 8)
    r[0:NAME] = _pad(layer.name, True)
    for k in range(128):
        p = 16 + 8 * k
        _u16(r, p, layer.key_to_sample[k])
        r[p + 2] = 0x7F
        r[p + 4] = 0x80
    return bytes(r)


def _prepare(s: SvzSample) -> Tuple[np.ndarray, int]:
    """Devolve (pcm armazenado, fim). Com loop: [0, fim) + 16 quadros de guarda do início do loop."""
    pcm = s.pcm
    if s.loop:
        ls, le = s.loop
        le = min(le, len(pcm))
        guard = np.array([pcm[ls + i] if ls + i < len(pcm) else 0 for i in range(LOOP_GUARD)], dtype=np.int16)
        return np.concatenate([pcm[:le], guard]), le
    return pcm, len(pcm)


def build_svz(tone_name: str, samples: List[SvzSample], layers: List[SvzLayer],
              header: Optional[bytes] = None, attack: int = 8) -> bytes:
    """Monta o arquivo .svz. `samples` é o pool (1-based nos mapas); `layers` até 4."""
    if not samples:
        raise ConversionError("Nenhuma amostra para gravar no SVZ.")
    if not 1 <= len(layers) <= 4:
        raise ConversionError("Um timbre tem de 1 a 4 camadas (partials).")
    # amostra "espaçadora" inerte no fim: o teclado às vezes não carrega a última amostra do pool
    pool = list(samples) + [SvzSample("Spacer", np.zeros(64, np.int16), 60, None, 0)]
    uspa, usda = [], []
    for s in pool:
        pcm, end = _prepare(s)
        uspa.append(_uspa(s, end))
        usda.append(_smpd(s, pcm, end))
    maps = [_keymap(ly) for ly in layers]
    tone = _tone(tone_name, layers, list(range(1, len(layers) + 1)), attack)
    hdr = header or _load("svz_header.bin")
    return _container(hdr, ["DIFa", "PATa", "USPa", "MSPa", "USDa"],
                      [_block([_load("difa.bin")]), _block([tone]), _block(uspa), _block(maps), _usda(usda)])


# ---------------------------------------------------------------------------
# Leitura (para conferência e para usar o cabeçalho de um SVZ exportado pelo teclado)
# ---------------------------------------------------------------------------

def read_container(data: bytes) -> dict:
    if data[:4] != b"SVZa":
        raise ConversionError("Este arquivo não é um .SVZ da Roland.")
    end_toc = struct.unpack_from("<I", data, 0x18)[0]
    blocks = {}
    for off in range(0x10, end_toc, 16):
        tag = data[off:off + 4].decode("latin-1")
        plat = data[off + 4:off + 8].decode("latin-1")
        bo, bs = struct.unpack_from("<II", data, off + 8)
        if bo + 16 > len(data):
            blocks[tag] = dict(platform=plat, offset=bo, size=bs, count=0, unit=0, data_offset=0)
            continue
        count, unit, doff, _ = struct.unpack_from("<IIII", data, bo)
        blocks[tag] = dict(platform=plat, offset=bo, size=bs, count=count, unit=unit, data_offset=doff)
    return {"header": data[:16], "model": data[6:11].decode("latin-1", "replace"), "blocks": blocks}


def records(data: bytes, info: dict, tag: str) -> List[bytes]:
    b = info["blocks"][tag]
    start = b["offset"] + b["data_offset"]
    return [data[start + i * b["unit"]: start + (i + 1) * b["unit"]] for i in range(b["count"])]
