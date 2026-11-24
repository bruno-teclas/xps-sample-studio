"""Salvar e abrir projetos do Criador (.xpsinst = JSON + pasta de amostras WAV)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from typing import Dict

from . import audio as A
from .model import ConversionError, Group, Instrument, Sample, Zone

VERSION = 1


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 _\-#]", "_", name).strip() or "amostra"


def save_project(inst: Instrument, path: str) -> str:
    if not path.lower().endswith(".xpsinst"):
        path += ".xpsinst"
    folder = os.path.splitext(path)[0] + " Samples"
    os.makedirs(folder, exist_ok=True)
    names: Dict[int, str] = {}
    used = set()
    samples = []
    for z in inst.zones:
        sid = id(z.sample)
        if sid in names:
            continue
        base = _safe(z.sample.name)
        nm, k = base, 2
        while nm.lower() in used:
            nm, k = f"{base}_{k}", k + 1
        used.add(nm.lower())
        names[sid] = nm
        fn = nm + ".wav"
        A.write_wav(os.path.join(folder, fn), z.sample.data, z.sample.rate, 24, None,
                    (z.sample.loop_start, z.sample.loop_end) if z.sample.loop_start is not None else None, False)
        samples.append({"id": nm, "file": os.path.join(os.path.basename(folder), fn), "name": z.sample.name,
                        "rate": z.sample.rate, "loop_start": z.sample.loop_start, "loop_end": z.sample.loop_end})
    zones = []
    for z in inst.zones:
        d = {k: getattr(z, k) for k in z.__dataclass_fields__ if k != "sample"}
        d["sample"] = names[id(z.sample)]
        zones.append(d)
    doc = {"format": "XPS Sample Studio instrument", "version": VERSION, "name": inst.name,
           "groups": [asdict(g) for g in inst.groups], "samples": samples, "zones": zones}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    return path


def load_project(path: str) -> Instrument:
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    if doc.get("format") != "XPS Sample Studio instrument":
        raise ConversionError("Este arquivo não é um projeto do XPS Sample Studio.")
    base = os.path.dirname(os.path.abspath(path))
    pool: Dict[str, Sample] = {}
    for s in doc["samples"]:
        smp = A.read_wav(os.path.join(base, s["file"]))
        smp.name = s.get("name", smp.name)
        smp.loop_start, smp.loop_end = s.get("loop_start"), s.get("loop_end")
        pool[s["id"]] = smp
    inst = Instrument(doc.get("name", "Instrumento"), path, "Projeto")
    inst.groups = [Group(**{k: v for k, v in g.items() if k in Group.__dataclass_fields__}) for g in doc["groups"]] or [Group()]
    for zd in doc["zones"]:
        smp = pool[zd.pop("sample")]
        inst.zones.append(Zone(smp, **{k: v for k, v in zd.items() if k in Zone.__dataclass_fields__}))
    return inst
