"""Leitor de instrumentos Kontakt 4.2 a 8 (.nki).

Formatos suportados:
* Kontakt 5–8 (contêiner "hsin" da Native Instruments, com sub-árvores
  comprimidas em FastLZ);
* Kontakt 4.2 (cabeçalho Kontakt 2 + preset comprimido em FastLZ);
* monólitos do Kontakt 5+ (o .nki traz as amostras dentro do arquivo);
* amostras WAV/AIFF e NCW (compressão da Native Instruments).

A estrutura do formato foi descoberta pelo projeto ConvertWithMoss
(Jürgen Moßgraber, LGPLv3 – https://github.com/git-moss/ConvertWithMoss).
Este arquivo é uma tradução para Python dessa lógica de leitura e é
distribuído sob a mesma licença LGPLv3.

Bibliotecas protegidas (Kontakt Player / criptografadas) não podem ser lidas.
"""
from __future__ import annotations

import io
import math
import os
import struct
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from .model import ConversionError, Instrument, Sample, Zone

# ---------------------------------------------------------------------------
# Utilidades de leitura
# ---------------------------------------------------------------------------


class R:
    """Leitor sequencial little-endian sobre bytes."""

    def __init__(self, data: bytes, pos: int = 0, end: Optional[int] = None):
        self.b = data
        self.p = pos
        self.end = len(data) if end is None else end

    def left(self) -> int:
        return self.end - self.p

    def take(self, n: int) -> bytes:
        n = max(0, min(n, self.left()))
        v = self.b[self.p:self.p + n]
        self.p += n
        return v

    def u8(self) -> int:
        if self.left() < 1:
            return -1
        v = self.b[self.p]
        self.p += 1
        return v

    def _s(self, fmt, n):
        if self.left() < n:
            raise ConversionError("Arquivo Kontakt truncado ou corrompido.")
        v = struct.unpack_from(fmt, self.b, self.p)[0]
        self.p += n
        return v

    def u16(self): return self._s("<H", 2)
    def s16(self): return self._s("<h", 2)
    def u32(self): return self._s("<I", 4)
    def s32(self): return self._s("<i", 4)
    def u64(self): return self._s("<Q", 8)
    def f32(self): return self._s("<f", 4)
    def f64(self): return self._s("<d", 8)

    def utf16(self) -> str:
        n = self.u32()
        return self.take(n * 2).decode("utf-16-le", "replace")

    def block64(self) -> "R":
        size = self.u64()
        if size < 8:
            raise ConversionError("Bloco inválido no arquivo Kontakt.")
        start = self.p
        self.p += size - 8
        return R(self.b, start, min(start + size - 8, self.end))


def fastlz_decompress(src: bytes, out_size: int) -> bytes:
    """Descompressão FastLZ (níveis 1 e 2)."""
    if not src:
        return b""
    level = (src[0] >> 5) + 1
    out = bytearray()
    ip = 0
    n = len(src)
    ctrl = src[ip] & 31
    ip += 1
    while True:
        if ctrl >= 32:
            ln = (ctrl >> 5) - 1
            ofs = (ctrl & 31) << 8
            ref = len(out) - ofs - 1
            if level == 1:
                if ln == 6:
                    ln += src[ip]
                    ip += 1
                ref -= src[ip]
                ip += 1
            else:
                if ln == 6:
                    while True:
                        code = src[ip]
                        ip += 1
                        ln += code
                        if code != 255:
                            break
                code = src[ip]
                ip += 1
                ref -= code
                if code == 255 and ofs == (31 << 8):
                    ofs = (src[ip] << 8) | src[ip + 1]
                    ip += 2
                    ref = len(out) - ofs - 8191 - 1
            ln += 3
            if ref < 0:
                raise ConversionError("Dados FastLZ inválidos (provavelmente criptografados).")
            if ref + ln <= len(out):
                out += out[ref:ref + ln]
            else:
                for i in range(ln):
                    out.append(out[ref + i])
        else:
            ctrl += 1
            out += src[ip:ip + ctrl]
            ip += ctrl
        if ip >= n:
            break
        ctrl = src[ip]
        ip += 1
    if out_size and len(out) != out_size:
        raise ConversionError("Tamanho descomprimido inesperado (arquivo protegido ou corrompido).")
    return bytes(out)


# ---------------------------------------------------------------------------
# Contêiner "hsin" (Kontakt 5+)
# ---------------------------------------------------------------------------

T_TERMINATOR, T_PRESET_CHUNK, T_SUB_TREE, T_AUTHORIZATION = 1, 109, 115, 106


class _Found:
    def __init__(self):
        self.presets: List[bytes] = []
        self.encrypted = False


def _read_item(r: R, found: _Found, depth: int = 0):
    if depth > 32:
        return
    blk = r.block64()
    if blk.u32() != 1 or blk.take(4) != b"hsin":
        raise ConversionError("Contêiner Kontakt inválido.")
    blk.u32()
    blk.u32()
    blk.take(16)
    _read_data_chunk(blk, found, depth)
    blk.u32()  # versão
    nchild = blk.u32()
    for _ in range(nchild):
        blk.u32()
        blk.take(4)
        blk.u32()
        _read_item(blk, found, depth + 1)


def _read_data_chunk(r: R, found: _Found, depth: int):
    blk = r.block64()
    blk.take(4)
    ctype = blk.u32()
    blk.u32()  # versão
    if ctype != T_TERMINATOR:
        _read_data_chunk(blk, found, depth)
    data = R(blk.b, blk.p, blk.end)
    if ctype == T_PRESET_CHUNK:
        data.u32()  # versão
        data.u32()  # tipo do dicionário
        if data.u32() >= 1:
            size = data.u32()
            data.u32()
            found.presets.append(data.take(size))
    elif ctype == T_SUB_TREE:
        data.u32()
        compressed = data.u8() > 0
        if compressed:
            unc = data.u32()
            comp = data.u32()
            raw = data.take(comp)
            try:
                sub = fastlz_decompress(raw, unc)
                _read_item(R(sub), found, depth + 1)
            except (ConversionError, IndexError):
                found.encrypted = True
        else:
            _read_item(data, found, depth + 1)
    elif ctype == T_AUTHORIZATION:
        pass


# ---------------------------------------------------------------------------
# Blocos do preset (KontaktPresetChunk)
# ---------------------------------------------------------------------------

BANK, GROUP, PROGRAM, PROGRAM_CONTAINER = 0x03, 0x04, 0x28, 0x29
PAR_SCRIPT, PAR_FX_SEND_LEVELS, VOICE_GROUPS = 0x06, 0x17, 0x32
GROUP_LIST, ZONE_LIST, LOOP_ARRAY = 0x33, 0x34, 0x39
PARAMETER_ARRAY_8, PARAMETER_ARRAY_16, PARAMETER_ARRAY_32 = 0x3A, 0x3B, 0x3C
FILENAME_LIST, INSERT_BUS, SAVE_SETTINGS, FILENAME_LIST_EX, QUICK_BROWSE = 0x3D, 0x45, 0x47, 0x4B, 0x4E
STRUCTURED = {BANK, PROGRAM_CONTAINER, PROGRAM, PAR_SCRIPT, PAR_FX_SEND_LEVELS, VOICE_GROUPS,
              PARAMETER_ARRAY_8, INSERT_BUS, SAVE_SETTINGS, QUICK_BROWSE}


class Chunk:
    __slots__ = ("id", "version", "public", "children")

    def __init__(self, cid=-1):
        self.id = cid
        self.version = -1
        self.public = b""
        self.children: List["Chunk"] = []


def _read_chunk(r: R) -> Chunk:
    c = Chunk(r.u16())
    size = r.u32()
    obj = R(r.b, r.p, min(r.p + size, r.end))
    r.p += size
    if c.id == GROUP_LIST:
        _read_array(obj, size, False, c)
        for ch in c.children:
            ch.id = GROUP
    elif c.id == ZONE_LIST:
        _read_array(obj, size, True, c)
    elif c.id in STRUCTURED:
        _read_structure(obj, size, c)
    elif c.id == PARAMETER_ARRAY_16:
        _read_fixed(obj, 16, c)
    elif c.id == PARAMETER_ARRAY_32:
        _read_fixed(obj, 32, c)
    else:
        c.public = obj.take(size)
    return c


def _read_array(r: R, size: int, has_ref: bool, parent: Chunk):
    n = r.u32()
    for _ in range(n):
        ref = r.u32() if has_ref else -1
        c = Chunk(ref)
        _read_structure(r, size, c)
        parent.children.append(c)


def _read_fixed(r: R, size: int, parent: Chunk):
    r.u8()
    parent.version = r.u16()
    slots = size
    if parent.version >= 0x13:
        slots = r.u32()
    for i in range(slots):
        if r.left() <= 0:
            break
        if r.u8() > 0:
            c = Chunk(r.u16())
            c.public = r.take(r.u32())
            parent.children.append(c)


def _read_structure(r: R, size: int, c: Chunk):
    structured = r.u8() > 0
    if not structured:
        if size > 0:
            c.public = r.take(size - 1)
        return
    c.version = r.u16()
    r.take(r.u32())  # dados privados
    c.public = r.take(r.u32())
    kids = r.u32()
    sub = R(r.b, r.p, min(r.p + kids, r.end))
    r.p += kids
    while sub.left() > 0:
        c.children.append(_read_chunk(sub))


def _parse_chunks(data: bytes) -> List[Chunk]:
    r = R(data)
    out = []
    while r.left() > 0:
        out.append(_read_chunk(r))
    return out


def _find_all(chunks: List[Chunk], cid: int) -> List[Chunk]:
    res = []
    for c in chunks:
        if c.id == cid:
            res.append(c)
        else:
            res.extend(_find_all(c.children, cid))
    return res


# ---------------------------------------------------------------------------
# Programa, grupos, zonas, loops e lista de arquivos
# ---------------------------------------------------------------------------


class KGroup:
    def __init__(self, c: Chunk):
        r = R(c.public)
        self.name = r.utf16()
        self.volume = r.f32()
        self.pan = r.f32()
        self.tune = r.f32()
        r.u8()                       # key tracking
        self.reverse = r.u8() > 0
        self.release_trigger = r.u8() > 0
        r.u8()
        r.s32()
        r.s16()
        r.s32()
        r.s32()
        self.muted = r.u8() > 0
        self.soloed = r.u8() > 0


class KZone:
    def __init__(self, c: Chunk):
        self.group = c.id
        r = R(c.public)
        self.start = r.u32()
        self.end_trim = r.u32()
        r.u32()
        self.lo_vel, self.hi_vel, self.lo_key, self.hi_key = r.u16(), r.u16(), r.u16(), r.u16()
        r.u16(), r.u16(), r.u16(), r.u16()
        self.root = r.u16()
        self.volume = r.f32()
        self.pan = r.f32()
        self.tune = r.f32()
        self.file_id = -1
        self.rate = 44100
        self.channels = 1
        self.frames = 0
        if c.version >= 0x9A:
            r.u8()
            r.u8()
            r.u32()
            if r.left() == 0:
                return
        self.file_id = r.u32()
        r.u32()  # resolução
        self.rate = r.u32()
        self.channels = r.u8()
        self.frames = r.u32()
        self.loops: List[Tuple[int, int, int]] = []

    loops: List[Tuple[int, int, int]] = []


def _parse_loops(data: bytes) -> List[Tuple[int, int, int]]:
    """Devolve [(modo, início, tamanho)]. Modos: 1 até o fim, 2 até soltar, 3 one-shot."""
    if len(data) < 2:
        return []
    r = R(data)
    mask = r.u16()
    out = []
    for i in range(8):
        if mask & (1 << i):
            if r.left() < 27:
                break
            r.u16()  # versão 0x60
            mode = r.s32()
            start = r.u32()
            length = r.u32()
            r.u32()
            r.u8()
            r.f32()
            r.u32()
            if r.left() > 0:
                r.u8()
            out.append((mode, start, length))
    return out


def _read_program_header(data: bytes) -> Tuple[str, float, float]:
    r = R(data)
    name = r.utf16()
    r.f64()
    r.u8()
    vol = r.f32()
    r.f32()
    tune = r.f32()
    return name, vol, tune


def _read_filelist(chunks: List[Chunk]) -> List[str]:
    fl = next((c for c in chunks if c.id == FILENAME_LIST), None) or \
        next((c for c in chunks if c.id == FILENAME_LIST_EX), None)
    if fl is None:
        return []
    r = R(fl.public)

    def read_file():
        parts = []
        for _ in range(r.u32()):
            t = r.u8()
            if t == 0:
                d = r.take(2).decode("latin-1").strip()
                parts.append((d + ":/") if d else "/")
            elif t == 1:
                d = r.utf16()
                parts.append((d + ":/") if d else "/")
            elif t == 2:
                parts.append(r.utf16() + "/")
            elif t == 3:
                parts.append("../")
            elif t in (4, 8, 9):
                parts.append(r.utf16())
            elif t == 6:
                pass
            else:
                raise ConversionError(f"Segmento de caminho desconhecido ({t}).")
        return "".join(parts)

    def read_files():
        if r.left() <= 0:
            return []
        return [read_file() for _ in range(r.s32())]

    if fl.id == FILENAME_LIST_EX:
        version = r.u16()
        if version == 3:
            files = []
            if r.left() > 0:
                n = r.s32()
                r.take(8)
                for _ in range(n - 1):
                    files.append(read_file())
                    if r.left() > 0:
                        r.take(4)
                        r.u32()
                        r.take(20)
            return files
    read_files()           # arquivos especiais
    return read_files()    # amostras


# ---------------------------------------------------------------------------
# Detecção e leitura dos formatos
# ---------------------------------------------------------------------------

def detect(buf: bytes) -> str:
    if len(buf) >= 16 and buf[12:16] == b"hsin":
        return "k5"
    if buf[:16] == b"/\\ NI FC MTD  /\\":
        return "monolith"
    if len(buf) >= 10 and buf[:4] == b"\x12\x90\xa8\x7f" and struct.unpack_from("<H", buf, 8)[0] == 0x110:
        return "k42"
    return "other"


def _monolith_files(buf: bytes) -> Tuple[bytes, Dict[str, bytes]]:
    r = R(buf)
    r.take(16)
    r.take(248)
    if r.take(8) != b"\xf0" * 8:
        raise ConversionError("Monólito do Kontakt inválido.")
    count = r.u64()
    r.u64()
    if r.take(16) != b"/\\ NI FC TOC  /\\":
        raise ConversionError("Monólito do Kontakt inválido.")
    r.take(600)
    entries = []
    for _ in range(count):
        idx = r.u64()
        r.take(16)
        name = r.take(600).decode("utf-16-le", "replace").split("\x00")[0].strip()
        r.u64()
        end = r.u64()
        entries.append((idx, name, end))
    r.take(8)
    r.take(16)
    r.take(16)
    r.take(592)
    base = r.p
    files: Dict[str, bytes] = {}
    main = None
    prev = 0
    for idx, name, end in sorted(entries):
        data = buf[base + prev: base + end]
        prev = end
        low = name.lower()
        if low.endswith(".nki"):
            main = data
        else:
            files[os.path.basename(low.replace("\\", "/"))] = data
    if main is None:
        raise ConversionError("O monólito não contém um .nki.")
    return main, files


def _preset_data(buf: bytes) -> bytes:
    kind = detect(buf)
    if kind == "k42":
        comp_len = struct.unpack_from("<I", buf, 4)[0]
        unc_len = struct.unpack_from("<I", buf, 182 + 4)[0]
        raw = buf[222:222 + comp_len]
        return fastlz_decompress(raw, unc_len)
    found = _Found()
    _read_item(R(buf), found)
    if not found.presets:
        if found.encrypted:
            raise ConversionError(
                "Este instrumento é de uma biblioteca protegida (Kontakt Player/criptografada) "
                "e não pode ser convertido.")
        raise ConversionError("Não encontrei os dados do instrumento dentro deste .nki.")
    return found.presets[0]


def _db(v: float) -> float:
    return 20.0 * math.log10(v) if v and v > 0 else -60.0


def _cents(*ratios: float) -> float:
    p = 1.0
    for x in ratios:
        p *= x if x and x > 0 else 1.0
    return 1200.0 * math.log2(p)


def load_kontakt5(path: str, index_files: Callable[[], Dict[str, str]], read_audio) -> Instrument:
    with open(path, "rb") as f:
        buf = f.read()
    kind = detect(buf)
    embedded: Dict[str, bytes] = {}
    label = {"k5": "NKI (Kontakt 5–8)", "k42": "NKI (Kontakt 4.2)", "monolith": "NKI monolítico (Kontakt 5+)"}[kind]
    if kind == "monolith":
        main, embedded = _monolith_files(buf)
        buf = main
    chunks = _parse_chunks(_preset_data(buf))
    files = _read_filelist(chunks)
    programs = _find_all(chunks, PROGRAM)
    if not programs:
        raise ConversionError("O .nki não contém nenhum instrumento (programa).")
    prog = programs[0]
    pname, pvol, ptune = _read_program_header(prog.public)
    groups: List[KGroup] = []
    zones: List[KZone] = []
    for ch in prog.children:
        if ch.id == GROUP_LIST:
            for g in ch.children:
                try:
                    groups.append(KGroup(g))
                except ConversionError:
                    groups.append(None)
        elif ch.id == ZONE_LIST:
            for zc in ch.children:
                kz = KZone(zc)
                kz.loops = []
                for sub in zc.children:
                    if sub.id == LOOP_ARRAY:
                        kz.loops = _parse_loops(sub.public)
                zones.append(kz)

    inst = Instrument(pname or os.path.splitext(os.path.basename(path))[0], path, label)
    solo = any(g and g.soloed for g in groups)
    idx: Optional[Dict[str, str]] = None
    cache: Dict[str, Tuple[np.ndarray, int]] = {}
    missing = set()
    skipped_release = 0

    def make_loader(key: str, src, start: int, end_trim: int):
        def load():
            if key not in cache:
                if isinstance(src, bytes):
                    from .ncw import decode_ncw
                    if key.endswith(".ncw"):
                        s = decode_ncw(src, key)
                    else:
                        import tempfile
                        with tempfile.NamedTemporaryFile(suffix=os.path.splitext(key)[1], delete=False) as t:
                            t.write(src)
                        try:
                            s = read_audio(t.name)
                        finally:
                            os.unlink(t.name)
                else:
                    s = read_audio(src)
                if len(cache) > 48:
                    cache.clear()
                cache[key] = (s.data, s.rate)
            data, _ = cache[key]
            stop = len(data) - end_trim if end_trim and end_trim < len(data) else len(data)
            return data[start:stop]
        return load

    for kz in zones:
        if kz.file_id < 0 or kz.file_id >= len(files):
            continue
        g = groups[kz.group] if 0 <= kz.group < len(groups) else None
        if g is not None:
            if (solo and not g.soloed) or (not solo and g.muted):
                continue
            if g.release_trigger:
                skipped_release += 1
                continue
        fname = os.path.basename(files[kz.file_id].replace("\\", "/"))
        low = fname.lower()
        src = None
        if embedded:
            src = embedded.get(low)
            if src is None:
                stem = os.path.splitext(low)[0]
                src = next((v for k, v in embedded.items() if os.path.splitext(k)[0] == stem), None)
        if src is None:
            if idx is None:
                idx = index_files()
            src = idx.get(low)
            if src is None:
                stem = os.path.splitext(low)[0]
                for ext in (".ncw", ".wav", ".aif", ".aiff"):
                    if stem + ext in idx:
                        src = idx[stem + ext]
                        break
        if src is None:
            missing.add(fname)
            continue
        key = low if isinstance(src, bytes) else src
        frames = max(1, kz.frames - kz.start - kz.end_trim) if kz.frames else 0
        ls = le = None
        loop_on = False
        for mode, start, length in kz.loops:
            if mode in (1, 2) and length > 0:
                ls, le = start - kz.start, start - kz.start + length
                if ls >= 0:
                    loop_on = True
                    break
                ls = le = None
        smp = Sample(os.path.splitext(fname)[0], None, kz.rate or 44100, ls, le,
                     loader=make_loader(key, src, kz.start, kz.end_trim),
                     frames=frames, channels=max(1, kz.channels))
        gv = g.volume if g else 1.0
        gt = g.tune if g else 1.0
        gp = g.pan if g else 0.0
        inst.zones.append(Zone(
            smp, root=max(0, min(127, kz.root)), lo_key=min(127, kz.lo_key), hi_key=min(127, kz.hi_key),
            lo_vel=min(127, kz.lo_vel), hi_vel=min(127, kz.hi_vel),
            tune_cents=_cents(kz.tune, gt, ptune), loop_on=loop_on,
            gain_db=max(-60.0, _db(kz.volume * gv * pvol)), pan=max(-1.0, min(1.0, kz.pan + gp))))

    if skipped_release:
        inst.notes.append(f"{skipped_release} zona(s) de release (soltar a tecla) foram ignoradas.")
    if missing:
        inst.notes.append(f"{len(missing)} amostra(s) não encontrada(s) – deixe a pasta Samples da biblioteca "
                          "junto do .nki (ou na pasta acima dele).")
    if not inst.zones:
        raise ConversionError(
            "Nenhuma amostra deste .nki foi encontrada.\n"
            "Coloque o .nki junto com a pasta Samples da biblioteca (como ela vem instalada) e tente de novo.")
    return inst
