"""Leitor de SFZ (útil como ponte para Kontakt 4.2+ via ConvertWithMoss)."""
from __future__ import annotations

import os
import re
from typing import Dict

from .audio import read_audio
from .model import ConversionError, Instrument, Zone

_NOTE = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


def parse_note(v: str) -> int:
    v = v.strip()
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    m = re.fullmatch(r"([a-gA-G])([#b]?)(-?\d+)", v)
    if not m:
        raise ValueError(v)
    n = _NOTE[m.group(1).lower()] + (1 if m.group(2) == "#" else -1 if m.group(2) == "b" else 0)
    return (int(m.group(3)) + 1) * 12 + n


def load_sfz(path: str) -> Instrument:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    base = os.path.dirname(os.path.abspath(path))
    inst = Instrument(os.path.splitext(os.path.basename(path))[0], path, "SFZ")
    levels: Dict[str, Dict[str, str]] = {"control": {}, "global": {}, "master": {}, "group": {}}
    current = None
    cache = {}
    tokens = re.split(r"(<\w+>)", text)

    def emit(op: Dict[str, str]):
        merged = {}
        for lvl in ("global", "master", "group"):
            merged.update(levels[lvl])
        merged.update(op)
        if "sample" not in merged:
            return
        rel = merged["sample"].replace("\\", "/")
        dp = levels["control"].get("default_path", "").replace("\\", "/")
        fpath = os.path.normpath(os.path.join(base, dp, rel))
        if not os.path.exists(fpath):
            inst.notes.append(f"Amostra não encontrada: {rel}")
            return
        try:
            if fpath not in cache:
                cache[fpath] = read_audio(fpath)
            smp = cache[fpath]
        except ConversionError as e:
            inst.notes.append(str(e))
            return

        def note(k, d):
            try:
                return parse_note(merged[k]) if k in merged else d
            except ValueError:
                return d

        key = note("key", None)
        lo = note("lokey", key if key is not None else 0)
        hi = note("hikey", key if key is not None else 127)
        root = note("pitch_keycenter", key if key is not None else 60)
        num = lambda k, d: float(merged.get(k, d))
        z = Zone(smp, root=root, lo_key=lo, hi_key=hi, lo_vel=int(num("lovel", 0)), hi_vel=int(num("hivel", 127)),
                 tune_cents=num("tune", 0) + num("transpose", 0) * 100, gain_db=num("volume", 0),
                 pan=max(-1.0, min(1.0, num("pan", 0) / 100.0)))
        mode = merged.get("loop_mode", merged.get("loopmode", ""))
        ls = merged.get("loop_start", merged.get("loopstart"))
        le = merged.get("loop_end", merged.get("loopend"))
        if ls is not None and le is not None:
            from .model import Sample
            # cópia rasa para que cada região tenha o próprio loop
            smp = Sample(smp.name, smp.data, smp.rate, int(float(ls)), int(float(le)) + 1)
            z.sample = smp
        if mode in ("loop_continuous", "loop_sustain") or (not mode and smp.loop_start is not None):
            z.loop_on = smp.loop_start is not None
        inst.zones.append(z)

    for tok in tokens:
        m = re.fullmatch(r"<(\w+)>", tok)
        if m:
            current = m.group(1).lower()
            if current in levels:
                if current in ("global", "master"):
                    levels["group"] = {}
                levels[current] = {} if current != "control" else levels["control"]
            continue
        if current is None:
            continue
        ops = dict(re.findall(r"(\w+)=((?:(?!\s\w+=).)*)", tok.strip(), flags=re.S))
        ops = {k: v.strip() for k, v in ops.items()}
        if current == "region":
            emit(ops)
        elif current in levels:
            levels[current].update(ops)
    if not inst.zones:
        raise ConversionError("Nenhuma região com amostra válida foi encontrada no SFZ.")
    return inst
