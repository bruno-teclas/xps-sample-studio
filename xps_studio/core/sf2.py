"""Leitor de SoundFont 2 (.sf2)."""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from .model import ConversionError, Instrument, Sample, Zone

# Geradores usados
G_START, G_END, G_LSTART, G_LEND = 0, 1, 2, 3
G_START_C, G_END_C, G_LSTART_C, G_LEND_C = 4, 12, 45, 50
G_PAN = 17
G_INSTRUMENT, G_KEYRANGE, G_VELRANGE = 41, 43, 44
G_ATTEN, G_COARSE, G_FINE, G_SAMPLEID, G_MODES, G_ROOT = 48, 51, 52, 53, 54, 58


@dataclass
class _SHdr:
    name: str
    start: int
    end: int
    lstart: int
    lend: int
    rate: int
    pitch: int
    corr: int
    link: int
    stype: int


def _riff_chunks(f, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        cid, size = struct.unpack("<4sI", f.read(8))
        yield cid, pos + 8, size
        pos += 8 + size + (size & 1)


def _cstr(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode("latin-1", "replace").strip()


class SF2File:
    def __init__(self, path: str):
        self.path = path
        self._f = open(path, "rb")
        f = self._f
        head = f.read(12)
        if head[:4] != b"RIFF" or head[8:12] != b"sfbk":
            raise ConversionError("Este arquivo não é um SoundFont 2 válido.")
        total = struct.unpack("<I", head[4:8])[0] + 8
        self.smpl_off = None
        self.smpl_len = 0
        pd: Dict[bytes, bytes] = {}
        self.bank_name = os.path.splitext(os.path.basename(path))[0]
        for cid, off, size in _riff_chunks(f, 12, total):
            if cid != b"LIST":
                continue
            f.seek(off)
            kind = f.read(4)
            for sid, soff, ssize in _riff_chunks(f, off + 4, off + size):
                if kind == b"sdta" and sid == b"smpl":
                    self.smpl_off, self.smpl_len = soff, ssize
                elif kind == b"pdta":
                    f.seek(soff)
                    pd[sid] = f.read(ssize)
                elif kind == b"INFO" and sid == b"INAM":
                    f.seek(soff)
                    nm = _cstr(f.read(ssize))
                    if nm:
                        self.bank_name = nm
        need = [b"phdr", b"pbag", b"pgen", b"inst", b"ibag", b"igen", b"shdr"]
        if self.smpl_off is None or any(k not in pd for k in need):
            raise ConversionError("SoundFont incompleto ou corrompido.")
        self.smpl = np.memmap(path, dtype="<i2", mode="r", offset=self.smpl_off,
                              shape=(self.smpl_len // 2,))
        self.phdr = [(_cstr(r[0]), r[1], r[2], r[3]) for r in struct.iter_unpack("<20sHHH12x", pd[b"phdr"])]
        self.pbag = [r[0] for r in struct.iter_unpack("<HH", pd[b"pbag"])]
        self.pgen = list(struct.iter_unpack("<HH", pd[b"pgen"]))
        self.inst = [(_cstr(r[0]), r[1]) for r in struct.iter_unpack("<20sH", pd[b"inst"])]
        self.ibag = [r[0] for r in struct.iter_unpack("<HH", pd[b"ibag"])]
        self.igen = list(struct.iter_unpack("<HH", pd[b"igen"]))
        self.shdr = [_SHdr(_cstr(r[0]), *r[1:]) for r in struct.iter_unpack("<20sIIIIIBbHH", pd[b"shdr"])]
        self._cache: Dict[int, np.ndarray] = {}

    def close(self):
        try:
            self._f.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def presets(self) -> List[Tuple[int, str, int, int]]:
        """Lista (índice, nome, banco, programa) – o último registro é o terminador EOP."""
        out = []
        for i, (name, prog, bank, _) in enumerate(self.phdr[:-1]):
            out.append((i, name, bank, prog))
        return sorted(out, key=lambda p: (p[2], p[3]))

    @staticmethod
    def _gens(bags, gens, bag_from, bag_to):
        zones = []
        for b in range(bag_from, bag_to):
            g0 = bags[b]
            g1 = bags[b + 1] if b + 1 < len(bags) else len(gens)
            d = {}
            for oper, amt in gens[g0:g1]:
                d[oper] = amt
            zones.append(d)
        return zones

    @staticmethod
    def _s16(v: int) -> int:
        return v - 65536 if v >= 32768 else v

    @staticmethod
    def _range(v: int) -> Tuple[int, int]:
        return v & 0xFF, (v >> 8) & 0xFF

    def _pcm(self, idx: int) -> np.ndarray:
        if idx not in self._cache:
            h = self.shdr[idx]
            a = np.asarray(self.smpl[h.start:h.end], dtype=np.float32) / 32768.0
            self._cache[idx] = a
        return self._cache[idx]

    # ------------------------------------------------------------------
    def load_preset(self, pidx: int) -> Instrument:
        name, prog, bank, bag0 = self.phdr[pidx]
        bag1 = self.phdr[pidx + 1][3]
        pzones = self._gens(self.pbag, self.pgen, bag0, bag1)
        pglobal = {}
        if pzones and G_INSTRUMENT not in pzones[0]:
            pglobal = pzones.pop(0)
        inst = Instrument(name=name or f"Preset {prog}", source_path=self.path, source_format="SF2")
        raw = []  # (zone, sampleIndex, stype, link)
        for pz in pzones:
            if G_INSTRUMENT not in pz:
                continue
            pg = dict(pglobal)
            pg.update(pz)
            iidx = pg[G_INSTRUMENT]
            if iidx + 1 >= len(self.inst):
                continue
            ib0, ib1 = self.inst[iidx][1], self.inst[iidx + 1][1]
            izones = self._gens(self.ibag, self.igen, ib0, ib1)
            iglobal = {}
            if izones and G_SAMPLEID not in izones[0]:
                iglobal = izones.pop(0)
            p_klo, p_khi = self._range(pg.get(G_KEYRANGE, 0x7F00))
            p_vlo, p_vhi = self._range(pg.get(G_VELRANGE, 0x7F00))
            for iz in izones:
                if G_SAMPLEID not in iz:
                    continue
                g = dict(iglobal)
                g.update(iz)
                sid = g[G_SAMPLEID]
                if sid >= len(self.shdr) - 0:
                    continue
                h = self.shdr[sid]
                if h.stype & 0x8000:  # amostra em ROM
                    continue
                klo, khi = self._range(g.get(G_KEYRANGE, 0x7F00))
                vlo, vhi = self._range(g.get(G_VELRANGE, 0x7F00))
                klo, khi = max(klo, p_klo), min(khi, p_khi)
                vlo, vhi = max(vlo, p_vlo), min(vhi, p_vhi)
                if klo > khi or vlo > vhi:
                    continue
                s16 = self._s16
                start_off = s16(g.get(G_START, 0)) + s16(g.get(G_START_C, 0)) * 32768
                end_off = s16(g.get(G_END, 0)) + s16(g.get(G_END_C, 0)) * 32768
                ls_off = s16(g.get(G_LSTART, 0)) + s16(g.get(G_LSTART_C, 0)) * 32768
                le_off = s16(g.get(G_LEND, 0)) + s16(g.get(G_LEND_C, 0)) * 32768
                root = g.get(G_ROOT, 0xFFFF)
                root = h.pitch if root in (0xFFFF, -1) or s16(root) < 0 else root
                if root > 127:
                    root = 60
                coarse = s16(g.get(G_COARSE, 0)) + s16(pg.get(G_COARSE, 0))
                fine = s16(g.get(G_FINE, 0)) + s16(pg.get(G_FINE, 0))
                atten = s16(g.get(G_ATTEN, 0)) + s16(pg.get(G_ATTEN, 0))
                modes = g.get(G_MODES, 0) & 3
                z = dict(
                    sid=sid, klo=klo, khi=khi, vlo=vlo, vhi=vhi, root=root,
                    tune=coarse * 100 + fine + h.corr,
                    gain=-atten / 10.0 * 0.4,  # SF2 atenua ~0,4 dB por unidade na prática
                    pan=max(-1, min(1, s16(g.get(G_PAN, 0)) / 500.0)),
                    loop=modes in (1, 3),
                    so=start_off, eo=end_off, lso=ls_off, leo=le_off,
                )
                raw.append(z)
        inst.zones = self._build_zones(raw)
        if not inst.zones:
            raise ConversionError(f"O preset “{name}” não tem amostras utilizáveis.")
        return inst

    def _build_zones(self, raw) -> List[Zone]:
        used = set()
        zones: List[Zone] = []
        for i, z in enumerate(raw):
            if i in used:
                continue
            h = self.shdr[z["sid"]]
            partner = None
            if h.stype in (2, 4):  # parte de um par estéreo
                for j in range(i + 1, len(raw)):
                    o = raw[j]
                    if j in used:
                        continue
                    if (o["klo"], o["khi"], o["vlo"], o["vhi"]) == (z["klo"], z["khi"], z["vlo"], z["vhi"]) \
                            and o["sid"] == h.link:
                        partner = j
                        break
            data, ls, le = self._extract(z)
            if partner is not None:
                used.add(partner)
                o = raw[partner]
                d2, _, _ = self._extract(o)
                n = min(len(data), len(d2))
                left, right = (data, d2) if h.stype == 4 else (d2, data)
                stereo = np.stack([left[:n], right[:n]], axis=1)
                pan = 0.0
            else:
                stereo = data[:, None]
                pan = z["pan"]
            nm = h.name or f"S{z['sid']}"
            smp = Sample(nm, stereo, h.rate or 44100, ls, le)
            zones.append(Zone(smp, root=z["root"], lo_key=z["klo"], hi_key=z["khi"],
                              lo_vel=z["vlo"], hi_vel=z["vhi"], tune_cents=float(z["tune"]),
                              loop_on=z["loop"], gain_db=z["gain"], pan=pan))
        return zones

    def _extract(self, z):
        h = self.shdr[z["sid"]]
        pcm = self._pcm(z["sid"])
        s = max(0, z["so"])
        e = min(len(pcm), len(pcm) + z["eo"]) if z["eo"] <= 0 else len(pcm)
        data = pcm[s:e]
        ls = h.lstart - h.start + z["lso"] - s
        le = h.lend - h.start + z["leo"] - s
        if not (0 <= ls < le <= len(data)):
            ls, le = None, None
        return data, ls, le


def list_presets(path: str):
    sf = SF2File(path)
    try:
        return sf.presets()
    finally:
        sf.close()


def load_sf2(path: str, preset_index: Optional[int] = None) -> Instrument:
    sf = SF2File(path)
    try:
        presets = sf.presets()
        if not presets:
            raise ConversionError("O SoundFont não contém presets.")
        idx = presets[0][0] if preset_index is None else preset_index
        return sf.load_preset(idx)
    finally:
        sf.close()
