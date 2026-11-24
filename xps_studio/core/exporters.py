"""Exportação de instrumentos do Criador: SoundFont 2, Kontakt (.nki), SFZ e Roland .SVZ.

* SF2: escrito do zero conforme a especificação SoundFont 2.01.
* NKI: formato Kontakt 1 (XML comprimido com zlib), aberto por todas as versões do Kontakt –
  os modelos XML em assets/nki vêm do ConvertWithMoss (LGPLv3).
* SVZ: timbre ZEN-Core/XPS com até 4 camadas (partials), veja svz.py.
"""
from __future__ import annotations

import math
import os
import re
import struct
import time
import zlib
from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy import signal

from . import audio as A
from .model import ConversionError, Group, Instrument, Sample, Zone, note_name

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")


# --------------------------------------------------------------------------
# Preparação do áudio de cada zona
# --------------------------------------------------------------------------

@dataclass
class ZoneAudio:
    zone: Zone
    group: Group
    data: np.ndarray            # float32 (frames, canais), já cortado/invertido
    rate: int
    loop: Optional[Tuple[int, int]]   # relativo ao início, fim exclusivo
    name: str


def safe_name(s: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9 _\-#()]", "_", s).strip()
    return (s or "sample")[:maxlen]


def zone_audio(inst: Instrument, z: Zone, rate: Optional[int] = None, bake_tune: bool = False) -> ZoneAudio:
    smp = z.sample
    end = z.play_end()
    data = smp.data[z.start:end]
    loop = None
    if z.loop_on and smp.loop_start is not None and smp.loop_end is not None:
        ls, le = smp.loop_start - z.start, min(smp.loop_end, end) - z.start
        if 0 <= ls < le and le - ls >= 16:
            loop = (ls, le)
    if z.reverse:
        data = data[::-1]
        loop = None
    data = np.ascontiguousarray(data, dtype=np.float32)
    g = inst.group_of(z)
    r = smp.rate
    tune = (z.tune_cents + g.tune_cents) if bake_tune else 0.0
    if (rate and rate != r) or abs(tune) > 0.01:
        eff = r * (2 ** (tune / 1200.0))
        target = rate or r
        ratio = Fraction(target / eff).limit_denominator(1000)
        data = signal.resample_poly(data, ratio.numerator, ratio.denominator, axis=0).astype(np.float32)
        k = float(ratio)
        if loop:
            loop = (int(round(loop[0] * k)), min(len(data), int(round(loop[1] * k))))
        r = target
    return ZoneAudio(z, g, data, r, loop, smp.name)


def _unique_names(items: List[ZoneAudio], maxlen: int) -> List[str]:
    used, out = set(), []
    for it in items:
        base = safe_name(it.name, maxlen - 3)
        nm, k = base, 2
        while nm.lower() in used:
            nm, k = f"{base[:maxlen - 3]}_{k}", k + 1
        used.add(nm.lower())
        out.append(nm)
    return out


def _dedupe(inst: Instrument, zones: List[Zone], **kw) -> Tuple[List[ZoneAudio], List[int]]:
    """Uma entrada de áudio por (amostra, início, fim, reverso); devolve também o índice de cada zona."""
    cache: Dict[tuple, int] = {}
    audios: List[ZoneAudio] = []
    idx = []
    for z in zones:
        key = (id(z.sample), z.start, z.play_end(), z.reverse, z.loop_on,
               (z.tune_cents + inst.group_of(z).tune_cents) if kw.get("bake_tune") else 0)
        if key not in cache:
            cache[key] = len(audios)
            audios.append(zone_audio(inst, z, **kw))
        idx.append(cache[key])
    return audios, idx


# --------------------------------------------------------------------------
# SoundFont 2
# --------------------------------------------------------------------------

def _chunk(cid: bytes, data: bytes) -> bytes:
    return cid + struct.pack("<I", len(data)) + data + (b"\0" if len(data) & 1 else b"")


def _list(kind: bytes, chunks: List[bytes]) -> bytes:
    return _chunk(b"LIST", kind + b"".join(chunks))


def _timecents(sec: float) -> int:
    return int(max(-12000, min(8000, round(1200 * math.log2(max(sec, 0.001))))))


def _name20(s: str) -> bytes:
    b = s.encode("latin-1", "replace")[:19]
    return b + b"\0" * (20 - len(b))


def write_sf2(inst: Instrument, path: str) -> str:
    zones = inst.audible_zones()
    if not zones:
        raise ConversionError("O instrumento não tem zonas para exportar.")
    audios, idx = _dedupe(inst, zones)
    names = _unique_names(audios, 20)
    pcm = bytearray()
    shdr = []
    sample_ids: List[Tuple[int, Optional[int]]] = []   # (id esquerdo/mono, id direito)
    for au, nm in zip(audios, names):
        chans = [au.data[:, c] for c in range(au.data.shape[1])] if au.data.shape[1] > 1 else [au.data[:, 0]]
        ids = []
        for c, x in enumerate(chans[:2]):
            start = len(pcm) // 2
            pcm += A.float_to_int16(x[:, None], True).tobytes() + b"\0" * 92   # 46 pontos de silêncio
            end = start + len(x)
            if au.loop:
                ls, le = start + au.loop[0], start + au.loop[1]
            else:
                ls, le = start, max(start + 1, end - 1)
            ids.append(len(shdr))
            stype = 1 if len(chans) == 1 else (4 if c == 0 else 2)
            suffix = "" if len(chans) == 1 else ("L" if c == 0 else "R")
            shdr.append([_name20((nm[:18] + suffix)), start, end, ls, le, au.rate, 60, 0, 0, stype])
        if len(ids) == 2:
            shdr[ids[0]][8], shdr[ids[1]][8] = ids[1], ids[0]
        sample_ids.append((ids[0], ids[1] if len(ids) > 1 else None))
    # instrumento: uma zona por zona (ou duas, se estéreo)
    igen: List[Tuple[int, int]] = []
    ibag: List[Tuple[int, int]] = []

    def add_zone(gens: List[Tuple[int, int]]):
        ibag.append((len(igen), 0))
        igen.extend(gens)

    def u(v):
        return int(v) & 0xFFFF

    for z, ai in zip(zones, idx):
        au = audios[ai]
        g = inst.group_of(z)
        lid, rid = sample_ids[ai]
        tune = z.tune_cents + g.tune_cents
        coarse = int(math.trunc(tune / 100))
        fine = int(round(tune - coarse * 100))
        atten = int(round(max(0.0, -(z.gain_db + g.gain_db)) * 10 / 0.4))   # cB (convenção EMU 0,4 dB)
        pan = max(-1.0, min(1.0, z.pan + g.pan))
        sus = max(1e-4, min(1.0, g.sustain))
        base = [
            (43, (min(127, z.hi_key) << 8) | max(0, z.lo_key)),
            (44, (min(127, z.hi_vel) << 8) | max(0, z.lo_vel)),
            (34, u(_timecents(g.attack))), (35, u(_timecents(g.hold) if g.hold > 0 else -12000)),
            (36, u(_timecents(g.decay))), (37, u(int(round(-200 * math.log10(sus))))),
            (38, u(_timecents(g.release))), (48, u(min(1440, atten))),
            (51, u(coarse)), (52, u(fine)), (54, 1 if au.loop else 0), (58, max(0, min(127, z.root))),
        ]
        if rid is None:
            add_zone(base + [(17, u(round(pan * 500))), (53, lid)])
        else:
            add_zone(base + [(17, u(-500)), (53, lid)])
            add_zone(base + [(17, u(500)), (53, rid)])
    inst_name = inst.name or "Instrumento"
    ibag.append((len(igen), 0))
    igen.append((0, 0))
    inst_rec = struct.pack("<20sH", _name20(inst_name), 0) + struct.pack("<20sH", _name20("EOI"), len(ibag) - 1)
    ibag_b = b"".join(struct.pack("<HH", a, b) for a, b in ibag)
    igen_b = b"".join(struct.pack("<HH", o, a) for o, a in igen)
    imod_b = b"\0" * 10
    # preset com uma zona que aponta para o instrumento 0
    phdr = struct.pack("<20sHHHIII", _name20(inst_name), 0, 0, 0, 0, 0, 0) + \
        struct.pack("<20sHHHIII", _name20("EOP"), 0, 0, 1, 0, 0, 0)
    pbag = struct.pack("<HH", 0, 0) + struct.pack("<HH", 1, 0)
    pgen = struct.pack("<HH", 41, 0) + struct.pack("<HH", 0, 0)
    pmod = b"\0" * 10
    shdr_b = b"".join(struct.pack("<20sIIIIIBbHH", *r) for r in shdr) + struct.pack("<20sIIIIIBbHH", _name20("EOS"), 0, 0, 0, 0, 0, 0, 0, 0, 0)
    info = _list(b"INFO", [_chunk(b"ifil", struct.pack("<HH", 2, 1)), _chunk(b"isng", b"EMU8000\0"),
                           _chunk(b"INAM", inst_name.encode("latin-1", "replace")[:255] + b"\0"),
                           _chunk(b"ISFT", b"XPS Sample Studio\0")])
    sdta = _list(b"sdta", [_chunk(b"smpl", bytes(pcm))])
    pdta = _list(b"pdta", [_chunk(b"phdr", phdr), _chunk(b"pbag", pbag), _chunk(b"pmod", pmod),
                           _chunk(b"pgen", pgen), _chunk(b"inst", inst_rec), _chunk(b"ibag", ibag_b),
                           _chunk(b"imod", imod_b), _chunk(b"igen", igen_b), _chunk(b"shdr", shdr_b)])
    body = b"sfbk" + info + sdta + pdta
    if not path.lower().endswith(".sf2"):
        path += ".sf2"
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


# --------------------------------------------------------------------------
# SFZ (+ WAVs)
# --------------------------------------------------------------------------

def write_sfz(inst: Instrument, path: str) -> str:
    zones = inst.audible_zones()
    if not zones:
        raise ConversionError("O instrumento não tem zonas para exportar.")
    if not path.lower().endswith(".sfz"):
        path += ".sfz"
    base = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.join(os.path.dirname(path), f"{base} Samples")
    os.makedirs(folder, exist_ok=True)
    audios, idx = _dedupe(inst, zones)
    names = _unique_names(audios, 60)
    for au, nm in zip(audios, names):
        A.write_wav(os.path.join(folder, nm + ".wav"), au.data, au.rate, 24, au.zone.root, au.loop, False)
    lines = [f"// {inst.name} – criado no XPS Sample Studio", "<control>", f"default_path={base} Samples/", ""]
    for gi, g in enumerate(inst.groups):
        gz = [(z, i) for z, i in zip(zones, idx) if inst.group_of(z) is g]
        if not gz:
            continue
        lines += [f"<group> // {g.name}", f"ampeg_attack={g.attack:.4f} ampeg_hold={g.hold:.4f} "
                  f"ampeg_decay={g.decay:.4f} ampeg_sustain={g.sustain * 100:.1f} ampeg_release={g.release:.4f}"]
        for z, i in gz:
            au = audios[i]
            op = [f"<region> sample={names[i]}.wav", f"lokey={z.lo_key} hikey={z.hi_key} pitch_keycenter={z.root}",
                  f"lovel={max(1, z.lo_vel)} hivel={z.hi_vel}",
                  f"tune={int(round(z.tune_cents + g.tune_cents))} volume={z.gain_db + g.gain_db:.2f}",
                  f"pan={max(-100, min(100, round((z.pan + g.pan) * 100)))}"]
            if au.loop:
                op.append(f"loop_mode=loop_continuous loop_start={au.loop[0]} loop_end={au.loop[1] - 1}")
            else:
                op.append("loop_mode=no_loop")
            lines.append(" ".join(op))
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


# --------------------------------------------------------------------------
# Kontakt (.nki no formato Kontakt 1, abre em todas as versões)
# --------------------------------------------------------------------------

def _tpl(name: str) -> str:
    with open(os.path.join(ASSETS, "nki", name), "r", encoding="utf-8") as f:
        return f.read()


def _fd(v: float) -> str:
    return f"{v:.6f}"


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def _db_to_value(db: float) -> float:
    return 2 ** (db / 6.0)


def write_nki(inst: Instrument, path: str) -> str:
    zones = inst.audible_zones()
    if not zones:
        raise ConversionError("O instrumento não tem zonas para exportar.")
    if not path.lower().endswith(".nki"):
        path += ".nki"
    base = os.path.splitext(os.path.basename(path))[0]
    folder_name = re.sub(r"[^A-Za-z0-9 _\-]", "_", base) + " Samples"
    folder = os.path.join(os.path.dirname(path), folder_name)
    os.makedirs(folder, exist_ok=True)
    audios, idx = _dedupe(inst, zones)
    names = [re.sub(r"[^A-Za-z0-9_\-]", "_", n) for n in _unique_names(audios, 48)]
    for au, nm in zip(audios, names):
        A.write_wav(os.path.join(folder, nm + ".wav"), au.data, au.rate, 24, au.zone.root, au.loop, False)

    text = _tpl("Kontakt1_01_Program_Start.xml")
    rep = {"%PROGRAM_INDEX%": "0", "%PROGRAM_NAME%": _esc(inst.name or "Instrumento"),
           "%PROGRAM_MIDI_CHANNEL%": "0", "%MASTER_VOLUME%": _fd(1.0), "%MASTER_PANNING%": _fd(0.5),
           "%MASTER_TRANSPOSE%": "0", "%MASTER_TUNE%": _fd(1.0), "%MASTER_LOW_KEY%": "0",
           "%MASTER_HIGH_KEY%": "127", "%PROGRAM_MAX_VOICES%": "64"}
    for k, v in rep.items():
        text = text.replace(k, v)
    text = re.sub(r"[ \t]*%PROGRAM_VOICE_GROUPS%\r?\n", "", text)
    gtpl, ztpl, ltpl, sc = _tpl("Kontakt1_02_Group.xml"), _tpl("Kontakt1_04_Zone.xml"), \
        _tpl("Kontakt1_05_Loop.xml"), _tpl("Kontakt1_08_Start_Criteria.xml")
    groups_xml, zones_xml = [], []
    used_groups = [g for g in inst.groups if any(inst.group_of(z) is g for z in zones)]
    zone_index = 0
    for gi, g in enumerate(used_groups):
        gx = gtpl
        grep = {
            "%GROUP_INDEX%": str(gi), "%GROUP_NAME%": _esc(g.name or f"Group {gi + 1}"),
            "%GROUP_KEY_TRACKING%": "yes", "%GROUP_REVERSE%": "no", "%GROUP_RELEASE_TRIGGER%": "no",
            "%GROUP_VOICE_GROUP%": "-1",
            "%GROUP_START_CRITERIA%": sc.replace("%START_CRITERIA_MODE%", "always").replace("%START_CRITERIA_CYCLE_CLASS%", "0"),
            "%FILTER_TYPE%": "lp2pole", "%FILTER_BYPASS%": "yes", "%FILTER_CUTOFF%": _fd(1.0), "%FILTER_RESONANCE%": _fd(0.0),
            "%ENVELOPE_INTENSITY%": _fd(1.0), "%ENVELOPE_ATTACK_CURVE%": _fd(0.0),
            "%ENVELOPE_ATTACK%": _fd(g.attack * 1000), "%ENVELOPE_DECAY%": _fd(g.decay * 1000),
            "%ENVELOPE_HOLD%": _fd(g.hold * 1000), "%ENVELOPE_RELEASE%": _fd(g.release * 1000),
            "%ENVELOPE_SUSTAIN%": _fd(g.sustain),
            "%AMP_VELOCITY_MOD%": _fd(1.0), "%PITCH_BEND%": _fd(2 / 12.0), "%FILTER_CUTOFF_VELOCITY_MOD%": _fd(0.0),
            "%LFO_MODULATORS%": "", "%LFO_MOD_WHEEL_MODULATORS%": "",
        }
        for pre in ("FILTER_CUTOFF_ENVELOPE", "PITCH_ENVELOPE"):
            grep.update({f"%{pre}_INTENSITY%": _fd(0), f"%{pre}_ATTACK_CURVE%": _fd(0), f"%{pre}_ATTACK%": _fd(0),
                         f"%{pre}_DECAY%": _fd(0), f"%{pre}_HOLD%": _fd(0), f"%{pre}_RELEASE%": _fd(0),
                         f"%{pre}_SUSTAIN%": _fd(1.0 if pre.startswith("FILTER") else 0)})
        for k, v in grep.items():
            gx = gx.replace(k, v)
        groups_xml.append(gx)
        for z, i in zip(zones, idx):
            if inst.group_of(z) is not g:
                continue
            au = audios[i]
            zx = ztpl
            zrep = {
                "%GROUP_INDEX%": str(gi), "%ZONE_INDEX%": str(zone_index),
                "%ZONE_SAMPLE_START%": "0", "%ZONE_SAMPLE_END%": str(len(au.data)),
                "%ZONE_VEL_LOW%": str(max(1, z.lo_vel)), "%ZONE_VEL_HIGH%": str(z.hi_vel),
                "%ZONE_KEY_LOW%": str(z.lo_key), "%ZONE_KEY_HIGH%": str(z.hi_key),
                "%ZONE_VEL_CROSS_LOW%": "0", "%ZONE_VEL_CROSS_HIGH%": "0",
                "%ZONE_KEY_CROSS_LOW%": "0", "%ZONE_KEY_CROSS_HIGH%": "0",
                "%ZONE_KEY_ROOT%": str(z.root),
                "%ZONE_VOLUME%": _fd(_db_to_value(z.gain_db + g.gain_db)),
                "%ZONE_PAN%": _fd(0.5 + 0.5 * max(-1.0, min(1.0, z.pan + g.pan))),
                "%ZONE_TUNE%": _fd(2 ** ((z.tune_cents + g.tune_cents) / 1200.0)),
                "%ZONE_SAMPLE_NAME%": _esc(f"{folder_name}\\{names[i]}.wav"),
            }
            if au.loop:
                loops = (ltpl.replace("%LOOP_INDEX%", "0").replace("%LOOP_START%", str(au.loop[0]))
                         .replace("%LOOP_LENGTH%", str(au.loop[1] - au.loop[0])).replace("%LOOP_MODE%", "until_end")
                         .replace("%LOOP_ALTERNATING%", "no").replace("%LOOP_TUNING%", "1.0").replace("%LOOP_XFADE%", "0"))
            else:
                loops = ""
            zrep["%ZONE_LOOPS%"] = loops
            for k, v in zrep.items():
                zx = zx.replace(k, v)
            zones_xml.append(zx)
            zone_index += 1
    xml = ('<?xml version="1.0"?>\n' + text + "".join(groups_xml) + _tpl("Kontakt1_03_Groups_Zones.xml")
           + "".join(zones_xml) + _tpl("Kontakt1_06_Program_End.xml"))
    data = xml.encode("utf-8")
    total = sum(au.data.nbytes // 2 for au in audios)
    header = struct.pack("<IIHHIIIIII", 0xB36EE55E, 0x24, 0x50, 0x02, 0, 0, 1, int(time.time()), total & 0xFFFFFFFF, 0)
    with open(path, "wb") as f:
        f.write(header + zlib.compress(data, 6))
    return path


# --------------------------------------------------------------------------
# Roland .SVZ com camadas (XPS-10X / ZEN-Core)
# --------------------------------------------------------------------------

def write_svz(inst: Instrument, path: str, stereo: bool = False, header: Optional[bytes] = None) -> Tuple[str, List[str]]:
    """Cada camada (grupo) vira um partial com sua faixa de velocidade (máx. 4 partials).
    Estéreo usa 2 partials por camada (máx. 2 camadas)."""
    from . import svz as Z
    zones = inst.audible_zones()
    if not zones:
        raise ConversionError("O instrumento não tem zonas para exportar.")
    warnings: List[str] = []
    groups = [g for g in inst.groups if any(inst.group_of(z) is g for z in zones)]
    max_layers = 2 if stereo else 4
    if len(groups) > max_layers:
        warnings.append(f"O SVZ aceita {max_layers} camada(s) {'estéreo' if stereo else 'mono'}; "
                        f"as camadas a partir da {max_layers + 1}ª foram juntadas à última.")
    layer_of = {id(g): min(i, max_layers - 1) for i, g in enumerate(groups)}
    nl = min(len(groups), max_layers)
    layers: List[Z.SvzLayer] = []
    base = re.sub(r"[^A-Za-z0-9]", "", (inst.name or "TONE").upper())[:10] or "TONE"
    for li in range(nl):
        zs = [z for z in zones if layer_of[id(inst.group_of(z))] == li]
        vlo = max(1, min(z.lo_vel for z in zs)) if zs else 1
        vhi = max(z.hi_vel for z in zs) if zs else 127
        for side in (("L", "R") if stereo else ("",)):
            layers.append(Z.SvzLayer(f"{base[:11]}{li + 1}{side}", pan=-64 if side == "L" else (63 if side == "R" else 0),
                                     vel_low=vlo, vel_high=vhi))
    tag = f"{zlib.crc32((inst.name + str(len(zones))).encode()) & 0xFFFF:04X}"
    samples: List[Z.SvzSample] = []
    cache: Dict[tuple, List[int]] = {}
    for z in zones:
        li = layer_of[id(inst.group_of(z))]
        key = (id(z.sample), z.start, z.play_end(), z.reverse, z.loop_on, z.tune_cents + inst.group_of(z).tune_cents, z.gain_db)
        if key not in cache:
            au = zone_audio(inst, z, rate=Z.SVZ_RATE, bake_tune=True)
            d = au.data * (10 ** ((z.gain_db + inst.group_of(z).gain_db) / 20.0))
            peak = float(np.max(np.abs(d))) if d.size else 0
            if peak > 0.999:
                d = d / peak * 0.999
            chans = ([d[:, 0], d[:, 1]] if d.shape[1] > 1 else [d[:, 0], d[:, 0]]) if stereo else [A.to_mono(d)[:, 0]]
            ids = []
            for c, x in enumerate(chans):
                side = ("L" if c == 0 else "R") if stereo else ""
                nm = f"{base[:5]}{len(cache) + 1:03d}{side}{tag}"[:16]
                samples.append(Z.SvzSample(nm, A.float_to_int16(x[:, None], True)[:, 0], z.root, au.loop, 127))
                ids.append(len(samples))
            cache[key] = ids
        ids = cache[key]
        for c, sid in enumerate(ids):
            lay = layers[li * (2 if stereo else 1) + c]
            for k in range(max(0, z.lo_key), min(127, z.hi_key) + 1):
                lay.key_to_sample[k] = sid
    total = sum(len(s.pcm) for s in samples) / Z.SVZ_RATE
    if total > 720:
        warnings.append(f"O timbre tem {total / 60:.1f} min de áudio – o XPS-10X tem cerca de 12 min de memória.")
    data = Z.build_svz((inst.name or "Sample Tone")[:16], samples, layers, header)
    if not path.lower().endswith(".svz"):
        path += ".SVZ"
    with open(path, "wb") as f:
        f.write(data)
    return path, warnings
