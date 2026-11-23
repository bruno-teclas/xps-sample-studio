"""Leitor de instrumentos Kontakt (.nki).

Suporte nativo: Kontakt 1 e Kontakt 2 a 4.1 (o instrumento é guardado como
XML, normalmente comprimido com zlib) com amostras WAV/AIFF abertas.

Kontakt 4.2 a 8 usam um formato binário proprietário diferente. Para esses
arquivos o programa explica como fazer a ponte via SFZ (veja o README).
Bibliotecas comerciais protegidas/criptografadas não são suportadas.
"""
from __future__ import annotations

import os
import re
import zlib
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

from .audio import read_audio
from .model import ConversionError, Instrument, Zone

AUDIO_RE = re.compile(r"([^/\\:@\x00\"<>|]+?\.(?:wav|aif|aiff|ncw))", re.I)

K42_MSG = (
    "Não consegui identificar a versão deste .nki.\n\n"
    "Como converter:\n"
    "1. Abra o .nki no ConvertWithMoss (gratuito, mossgrabers.de) e converta para SFZ;\n"
    "   ou, no próprio Kontakt, use File › Save as… com “Patch + samples” e versão compatível.\n"
    "2. Arraste o .sfz gerado para este programa.\n\n"
    "Bibliotecas comerciais protegidas (Kontakt Player/criptografadas) não podem ser convertidas."
)


def _norm(k: str) -> str:
    return re.sub(r"[^a-z0-9]", "", k.lower())


ALIASES = {
    "lo_key": {"lowkey", "lokey", "keylow", "keyrangelow", "lowkeyrange", "keylo"},
    "hi_key": {"highkey", "hikey", "keyhigh", "keyrangehigh", "highkeyrange", "keyhi"},
    "root": {"rootkey", "root", "rootnote", "keycenter", "pitchkeycenter", "basenote"},
    "lo_vel": {"lowvelocity", "lovel", "lowvel", "velocitylow", "velrangelow", "vello"},
    "hi_vel": {"highvelocity", "hivel", "highvel", "velocityhigh", "velrangehigh", "velhi"},
    "tune": {"tune", "zonetune", "pitch", "finetune"},
    "volume": {"volume", "zonevolume", "gain"},
    "loop_start": {"loopstart", "start"},
    "loop_len": {"looplength", "length"},
    "loop_end": {"loopend", "end"},
    "loop_mode": {"loopmode", "mode"},
    "sample_index": {"sampleindex", "sampleid", "sampleno"},
}


def _extract_xml(buf: bytes) -> Optional[str]:
    # 1) XML em texto puro dentro do arquivo
    for marker in (b"<?xml", b"<K2_Container", b"<program", b"<Program"):
        i = buf.find(marker)
        if i >= 0:
            return buf[i:].decode("utf-8", "replace")
    # 2) blocos zlib
    for i in range(len(buf) - 2):
        if buf[i] == 0x78 and buf[i + 1] in (0x01, 0x5E, 0x9C, 0xDA):
            try:
                d = zlib.decompressobj().decompress(buf[i:], 64 * 1024 * 1024)
            except zlib.error:
                continue
            if b"<" in d[:512] and (b"zone" in d.lower() or b"Zone" in d):
                j = d.find(b"<")
                return d[j:].decode("utf-8", "replace")
    return None


def _params(el: ET.Element) -> Dict[str, str]:
    """Junta atributos e pares <V name=.. value=..> de um elemento e filhos diretos."""
    out: Dict[str, str] = {}

    def take(e):
        name = e.attrib.get("name") or e.attrib.get("Name")
        if name is not None and ("value" in e.attrib or "Value" in e.attrib):
            out.setdefault(_norm(name), e.attrib.get("value", e.attrib.get("Value")))
        for k, v in e.attrib.items():
            out.setdefault(_norm(k), v)
        if e.text and e.text.strip() and len(e) == 0:
            out.setdefault(_norm(e.tag), e.text.strip())

    take(el)
    for child in el.iter():
        if child is el:
            continue
        take(child)
    return out


def _get(p: Dict[str, str], key: str, default=None):
    for alias in ALIASES[key]:
        if alias in p:
            try:
                return float(p[alias])
            except (TypeError, ValueError):
                continue
    return default


def _file_index(base_dir: str, depth: int = 4) -> Dict[str, str]:
    idx: Dict[str, str] = {}
    base_depth = base_dir.rstrip(os.sep).count(os.sep)
    for root, dirs, files in os.walk(base_dir):
        if root.count(os.sep) - base_depth >= depth:
            dirs[:] = []
        for fn in files:
            if fn.lower().endswith((".wav", ".aif", ".aiff", ".ncw")):
                idx.setdefault(fn.lower(), os.path.join(root, fn))
    return idx


def _resolve(name: str, idx: Dict[str, str]) -> Optional[str]:
    low = name.lower()
    if low in idx:
        return idx[low]
    stem = os.path.splitext(low)[0]
    for ext in (".wav", ".aif", ".aiff"):
        if stem + ext in idx:
            return idx[stem + ext]
    return None


def _tune_cents(v: float) -> float:
    # Kontakt guarda a afinação da zona em semitons; valores grandes já estão em cents
    return float(v) * 100.0 if abs(v) <= 48 else float(v)


def detect_version(buf: bytes) -> str:
    head = buf[:256]
    if b"hsin" in head or b"DSIN" in head or b"hsin" in buf[:4096]:
        return "k42+"
    return "xml"


def _library_index(path: str) -> Dict[str, str]:
    base = os.path.dirname(os.path.abspath(path))
    idx = _file_index(base, 5)
    parent = os.path.dirname(base)
    if parent and parent != base:
        for k, v in _file_index(parent, 5).items():
            idx.setdefault(k, v)
    return idx


def load_nki(path: str) -> Instrument:
    from . import kontakt5
    with open(path, "rb") as f:
        head = f.read(64)
    if kontakt5.detect(head + b"\0" * 16) != "other":
        return kontakt5.load_kontakt5(path, lambda: _library_index(path), read_audio)
    with open(path, "rb") as f:
        buf = f.read()
    if detect_version(buf) == "k42+":
        raise ConversionError(K42_MSG)
    xml = _extract_xml(buf)
    if not xml:
        raise ConversionError(
            "Não foi possível ler o conteúdo deste .nki. Ele pode ser protegido, de uma versão "
            "não suportada ou estar corrompido.\n\n" + K42_MSG)
    # Corta qualquer lixo depois do fechamento do elemento raiz
    m = re.match(r"\s*(?:<\?xml[^>]*\?>)?\s*<([A-Za-z_][\w\-\.]*)", xml)
    if m:
        end = xml.rfind(f"</{m.group(1)}>")
        if end > 0:
            xml = xml[: end + len(m.group(1)) + 3]
    try:
        root_el = ET.fromstring(xml)
    except ET.ParseError as e:
        raise ConversionError(f"XML do instrumento inválido ({e}).")

    idx = _library_index(path)

    # Lista global de amostras (Kontakt 1 referencia por índice)
    global_files: List[str] = []
    for el in root_el.iter():
        tag = el.tag.lower()
        if "sample" in tag and "zone" not in tag:
            for v in list(el.attrib.values()) + [el.text or ""]:
                mm = AUDIO_RE.search(v or "")
                if mm:
                    global_files.append(mm.group(1).strip())
                    break

    zone_els = [e for e in root_el.iter() if "zone" in e.tag.lower() and not e.tag.lower().endswith("zones")]
    inst = Instrument(os.path.splitext(os.path.basename(path))[0], path, "NKI (Kontakt 1–4.1)")
    missing = set()
    for ze in zone_els:
        p = _params(ze)
        fname = None
        for v in p.values():
            mm = AUDIO_RE.search(v or "")
            if mm:
                fname = mm.group(1).strip()
                break
        if fname is None:
            si = _get(p, "sample_index")
            if si is not None and 0 <= int(si) < len(global_files):
                fname = global_files[int(si)]
        if fname is None:
            continue
        fpath = _resolve(os.path.basename(fname.replace("\\", "/")), idx)
        if fpath is None:
            missing.add(os.path.basename(fname))
            continue
        try:
            smp = read_audio(fpath)
        except ConversionError as e:
            inst.notes.append(str(e))
            continue
        lo = int(_get(p, "lo_key", 0))
        hi = int(_get(p, "hi_key", 127))
        root = int(_get(p, "root", (lo + hi) // 2))
        z = Zone(smp, root=max(0, min(127, root)), lo_key=max(0, lo), hi_key=min(127, hi),
                 lo_vel=int(_get(p, "lo_vel", 0)), hi_vel=int(_get(p, "hi_vel", 127)),
                 tune_cents=_tune_cents(_get(p, "tune", 0.0)))
        ls = _get(p, "loop_start")
        ln = _get(p, "loop_len")
        le = _get(p, "loop_end")
        mode = _get(p, "loop_mode", 0)
        if ls is not None and (ln or le) and mode not in (0, None):
            s = int(ls)
            e = int(s + ln) if ln else int(le)
            if 0 <= s < e <= smp.frames:
                smp.loop_start, smp.loop_end = s, e
                z.loop_on = True
        elif smp.loop_start is not None:
            z.loop_on = True
        inst.zones.append(z)
    if missing:
        ncw = any(m.lower().endswith(".ncw") for m in missing)
        msg = f"{len(missing)} amostra(s) não encontrada(s) ou em formato .ncw"
        if ncw:
            msg += " – descomprima as amostras .ncw para WAV (veja o README)"
        inst.notes.append(msg + ".")
    if not inst.zones:
        raise ConversionError(
            "Nenhuma amostra utilizável foi encontrada neste .nki.\n"
            + ("\n".join(inst.notes) + "\n" if inst.notes else "")
            + "Verifique se a pasta Samples da biblioteca está junto do arquivo .nki.")
    return inst
