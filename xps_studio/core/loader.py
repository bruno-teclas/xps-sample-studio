"""Abre qualquer formato suportado e devolve um Instrument."""
from __future__ import annotations

import os
import re
from typing import Optional

from .audio import read_audio
from .model import ConversionError, Instrument, Zone
from .sfz import parse_note

SUPPORTED = (".sf2", ".nki", ".sfz", ".wav", ".aif", ".aiff")
FILTER = "Instrumentos (*.sf2 *.nki *.sfz *.wav *.aif *.aiff)"


def _note_from_name(name: str) -> Optional[int]:
    stem = os.path.splitext(os.path.basename(name))[0]
    m = re.findall(r"(?<![A-Za-z])([A-Ga-g][#b]?-?\d)(?![0-9])", stem)
    if m:
        try:
            return parse_note(m[-1])
        except ValueError:
            pass
    m = re.findall(r"(?<!\d)(\d{2,3})(?!\d)", stem)
    for v in reversed(m):
        if 12 <= int(v) <= 120:
            return int(v)
    return None


def load_wav_folder(path: str) -> Instrument:
    """Pasta de WAVs com a nota no nome (ex.: Piano_C3.wav, Flauta-60.wav)."""
    files = [os.path.join(path, f) for f in sorted(os.listdir(path))
             if f.lower().endswith((".wav", ".aif", ".aiff"))]
    if not files:
        raise ConversionError("A pasta não contém arquivos WAV/AIFF.")
    inst = Instrument(os.path.basename(os.path.normpath(path)), path, "Pasta de WAV")
    for fp in files:
        smp = read_audio(fp)
        note = _note_from_name(fp)
        if note is None:
            inst.notes.append(f"Nota não identificada no nome “{os.path.basename(fp)}” – usada C4.")
            note = 60
        inst.zones.append(Zone(smp, root=note, lo_key=note, hi_key=note, loop_on=smp.loop_start is not None))
    _auto_ranges(inst)
    return inst


def _auto_ranges(inst: Instrument):
    zs = sorted(inst.zones, key=lambda z: z.root)
    for i, z in enumerate(zs):
        z.lo_key = 0 if i == 0 else (zs[i - 1].root + z.root) // 2 + 1
        z.hi_key = 127 if i == len(zs) - 1 else (z.root + zs[i + 1].root) // 2


def load_instrument(path: str, preset_index: Optional[int] = None) -> Instrument:
    if os.path.isdir(path):
        return load_wav_folder(path)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".sf2":
        from .sf2 import load_sf2
        return load_sf2(path, preset_index)
    if ext == ".nki":
        from .nki import load_nki
        return load_nki(path)
    if ext == ".sfz":
        from .sfz import load_sfz
        return load_sfz(path)
    if ext in (".wav", ".aif", ".aiff"):
        smp = read_audio(path)
        note = _note_from_name(path) or 60
        inst = Instrument(smp.name, path, "WAV")
        inst.zones.append(Zone(smp, root=note, lo_key=0, hi_key=127, loop_on=smp.loop_start is not None))
        return inst
    raise ConversionError(f"Formato não suportado: {ext}")
