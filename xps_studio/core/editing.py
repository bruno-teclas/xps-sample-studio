"""Ferramentas de edição de amostras e de montagem de instrumentos."""
from __future__ import annotations

import math
import os
import re
from typing import List, Optional, Tuple

import numpy as np

from . import audio as A
from .model import Group, Instrument, Sample, Zone, note_name

# --------------------------------------------------------------------------
# Edição destrutiva (o editor guarda uma cópia para desfazer)
# --------------------------------------------------------------------------


def normalize(data: np.ndarray, peak_db: float = -1.0) -> np.ndarray:
    p = float(np.max(np.abs(data))) if data.size else 0.0
    if p <= 0:
        return data
    return (data * (10 ** (peak_db / 20.0) / p)).astype(np.float32)


def gain(data: np.ndarray, db: float) -> np.ndarray:
    return np.clip(data * (10 ** (db / 20.0)), -1, 1).astype(np.float32)


def reverse(data: np.ndarray) -> np.ndarray:
    return data[::-1].copy()


def fade_in(data: np.ndarray, frames: int) -> np.ndarray:
    out = data.copy()
    n = max(0, min(frames, len(out)))
    if n > 1:
        out[:n] *= (np.linspace(0, 1, n, dtype=np.float32) ** 2)[:, None]
    return out


def fade_out(data: np.ndarray, frames: int) -> np.ndarray:
    out = data.copy()
    n = max(0, min(frames, len(out)))
    if n > 1:
        out[-n:] *= (np.linspace(1, 0, n, dtype=np.float32) ** 2)[:, None]
    return out


def crop(smp: Sample, start: int, end: int) -> Sample:
    """Corta a amostra e reposiciona o loop."""
    start = max(0, start)
    end = min(smp.frames, end) if end > 0 else smp.frames
    data = smp.data[start:end].copy()
    ls = le = None
    if smp.loop_start is not None and smp.loop_end is not None:
        ls, le = smp.loop_start - start, min(end, smp.loop_end) - start
        if ls < 0 or le - ls < 16:
            ls = le = None
    return Sample(smp.name, data, smp.rate, ls, le)


def silence_bounds(data: np.ndarray, threshold_db: float = -55.0) -> Tuple[int, int]:
    thr = 10 ** (threshold_db / 20.0)
    env = np.max(np.abs(data), axis=1) if data.ndim > 1 else np.abs(data)
    idx = np.nonzero(env > thr)[0]
    if idx.size == 0:
        return 0, len(data)
    return int(idx[0]), int(idx[-1]) + 1


def to_mono(data: np.ndarray) -> np.ndarray:
    return A.to_mono(data)


# --------------------------------------------------------------------------
# Detecção de nota (YIN simplificado)
# --------------------------------------------------------------------------

def detect_pitch(data: np.ndarray, rate: int, loop: Optional[Tuple[int, int]] = None
                 ) -> Optional[Tuple[int, float, float]]:
    """Devolve (nota MIDI, desvio em cents, confiança 0..1) ou None."""
    if data.ndim > 1 and data.shape[1] > 1:
        # usa o canal mais forte: somar L+R pode cancelar a fundamental (diferença de fase)
        x = data[:, int(np.argmax(np.sum(data.astype(np.float64) ** 2, axis=0)))]
    else:
        x = data.reshape(-1)
    if loop and loop[1] - loop[0] > 32 and len(x) < rate * 0.5:
        # amostras curtas com loop (comuns em SoundFonts): repete o loop para ter material
        reps = int(rate * 0.5 / (loop[1] - loop[0])) + 1
        x = np.concatenate([x[:loop[1]]] + [x[loop[0]:loop[1]]] * reps)
    if len(x) < 1024:
        return None
    a, b = silence_bounds(x[:, None], -45)
    x = x[a:b]
    # pula o ataque e analisa um trecho estável
    off = min(len(x) // 4, int(rate * 0.08))
    seg = x[off: off + int(rate * 0.35)].astype(np.float64)
    if len(seg) < 2048:
        seg = x[: int(rate * 0.35)].astype(np.float64)
    if len(seg) < 1024 or np.max(np.abs(seg)) < 1e-4:
        return None
    seg = seg - seg.mean()
    W = len(seg) // 2
    max_tau = min(W, int(rate / 25))      # até 25 Hz
    min_tau = max(2, int(rate / 4200))    # até 4,2 kHz
    # função diferença via FFT
    n = 1 << int(math.ceil(math.log2(2 * len(seg))))
    fa = np.fft.rfft(seg[:W], n)
    fb = np.fft.rfft(seg, n)
    ac = np.fft.irfft(np.conj(fa) * fb, n)[: max_tau + 1]   # r(tau) = sum_{j<W} x_j x_{j+tau}
    energy = np.cumsum(seg ** 2)
    e0 = energy[W - 1]
    taus = np.arange(1, max_tau + 1)
    d = np.zeros(max_tau + 1)
    d[1:] = e0 + (energy[taus + W - 1] - energy[taus - 1]) - 2 * ac[1:]
    cmnd = np.ones_like(d)
    cs = np.cumsum(d[1:])
    cmnd[1:] = d[1:] * np.arange(1, max_tau + 1) / np.maximum(cs, 1e-12)
    tau = None
    for t in range(min_tau, max_tau):
        if cmnd[t] < 0.15:
            while t + 1 < max_tau and cmnd[t + 1] < cmnd[t]:
                t += 1
            tau = t
            break
    if tau is None:
        t = int(np.argmin(cmnd[min_tau:max_tau])) + min_tau
        if cmnd[t] > 0.45:
            return None
        tau = t
    # interpolação parabólica
    if 1 <= tau < max_tau:
        y0, y1, y2 = cmnd[tau - 1], cmnd[tau], cmnd[tau + 1]
        den = (y0 - 2 * y1 + y2)
        tau_f = tau + (0.5 * (y0 - y2) / den if abs(den) > 1e-12 else 0.0)
    else:
        tau_f = float(tau)
    freq = rate / tau_f
    midi = 69 + 12 * math.log2(freq / 440.0)
    note = int(round(midi))
    cents = (midi - note) * 100
    conf = float(max(0.0, min(1.0, 1 - cmnd[tau])))
    if not 0 <= note <= 127:
        return None
    return note, cents, conf


# --------------------------------------------------------------------------
# Busca de loop
# --------------------------------------------------------------------------

def find_loop(data: np.ndarray, rate: int, min_len_s: float = 0.25,
              region: Optional[Tuple[int, int]] = None) -> Optional[Tuple[int, int]]:
    """Procura um loop sem emenda audível na parte sustentada da amostra."""
    x = data.mean(axis=1) if data.ndim > 1 else data
    n = len(x)
    a, b = region if region else (int(n * 0.35), int(n * 0.92))
    a, b = max(0, a), min(n - 2, b)
    min_len = int(rate * min_len_s)
    if b - a < min_len + 64:
        a, b = int(n * 0.2), n - 2
        min_len = max(256, (b - a) // 3)
        if b - a < min_len + 32:
            return None
    W = 256
    # candidatos: cruzamentos por zero subindo
    zc = np.nonzero((x[:-1] <= 0) & (x[1:] > 0))[0]
    starts = zc[(zc >= max(a, W)) & (zc < b - min_len)]
    ends = zc[(zc > a + min_len) & (zc <= b)]
    if len(starts) == 0 or len(ends) == 0:
        return None
    rng = np.random.default_rng(0)
    if len(starts) > 60:
        starts = np.sort(rng.choice(starts, 60, replace=False))
    if len(ends) > 400:
        ends = np.sort(rng.choice(ends, 400, replace=False))
    best, best_cost = None, 1e18
    rms = float(np.sqrt(np.mean(x[a:b] ** 2)) + 1e-9)
    for s in starts:
        ref = x[s - W:s]
        cand = ends[ends - s >= min_len]
        for e in cand:
            seg = x[e - W:e]
            cost = float(np.mean((seg - ref) ** 2)) / (rms ** 2)
            cost += 0.02 * abs(math.log((np.sqrt(np.mean(x[e - 2048:e] ** 2)) + 1e-9) /
                                        (np.sqrt(np.mean(x[s:s + 2048] ** 2)) + 1e-9)))
            if cost < best_cost:
                best_cost, best = cost, (int(s), int(e))
    return best


# --------------------------------------------------------------------------
# Montagem de instrumentos
# --------------------------------------------------------------------------

_NOTE_RE = re.compile(r"(?<![A-Za-z])([A-Ga-g][#b]?-?\d)(?![0-9])")


def note_from_name(name: str) -> Optional[int]:
    from .loader import _note_from_name
    return _note_from_name(name)


def velocity_from_name(name: str) -> Optional[int]:
    m = re.search(r"(?:vel|v)[_\- ]?(\d{1,3})(?!\d)", os.path.basename(name), re.I)
    if m and 1 <= int(m.group(1)) <= 127:
        return int(m.group(1))
    for word, v in (("pp", 30), ("mp", 64), ("mf", 85), ("ff", 120), ("soft", 40), ("hard", 120)):
        if re.search(rf"(?<![a-z]){word}(?![a-z])", os.path.basename(name).lower()):
            return v
    return None


def auto_ranges(zones: List[Zone], lo: int = 0, hi: int = 127):
    """Distribui as faixas de teclas entre as raízes (metade do caminho até a vizinha)."""
    zs = sorted(zones, key=lambda z: z.root)
    for i, z in enumerate(zs):
        z.lo_key = lo if i == 0 else (zs[i - 1].root + z.root) // 2 + 1
        z.hi_key = hi if i == len(zs) - 1 else (z.root + zs[i + 1].root) // 2
        if z.lo_key > z.hi_key:
            z.lo_key = z.hi_key = z.root


def auto_map(inst: Instrument, zones: List[Zone], detect: bool = True):
    """Raiz pelo nome do arquivo (ou detecção de nota) e faixas automáticas por camada."""
    for z in zones:
        n = note_from_name(z.sample.name)
        if n is None and detect:
            smp = z.sample
            lp = (smp.loop_start, smp.loop_end) if smp.loop_start is not None else None
            r = detect_pitch(smp.data, smp.rate, lp)
            if r:
                n, cents, _ = r
                z.tune_cents = -round(cents, 1)
        if n is not None:
            z.root = n
    by_group = {}
    for z in inst.zones:
        by_group.setdefault((z.group, z.lo_vel, z.hi_vel), []).append(z)
    for zs in by_group.values():
        auto_ranges(zs)


def split_velocity(inst: Instrument, group_indices: List[int]):
    """Divide 1–127 em faixas iguais, uma por camada (na ordem dada: da mais suave à mais forte)."""
    n = len(group_indices)
    if n == 0:
        return
    edges = [round(1 + i * 127 / n) for i in range(n + 1)]
    for i, gi in enumerate(group_indices):
        lo, hi = edges[i], (edges[i + 1] - 1 if i < n - 1 else 127)
        for z in inst.zones:
            if z.group == gi:
                z.lo_vel, z.hi_vel = lo, hi


def adsr_envelope(g: Group, rate: int, held_s: float, total_frames: int) -> np.ndarray:
    """Envelope para pré-escuta: ataque, hold, decay até o sustain, release ao soltar."""
    t = np.arange(total_frames) / rate
    env = np.empty(total_frames, np.float32)
    a, h, d = max(1e-4, g.attack), max(0.0, g.hold), max(1e-3, g.decay)
    s = max(0.0, min(1.0, g.sustain))
    r = max(1e-3, g.release)
    on = np.minimum(t, held_s)
    seg = np.where(on < a, on / a,
                   np.where(on < a + h, 1.0,
                            s + (1 - s) * np.exp(-(on - a - h) / (d / 4.0))))
    env[:] = seg
    rel = t > held_s
    if rel.any():
        lvl = float(seg[np.argmax(rel) - 1]) if np.argmax(rel) > 0 else s
        env[rel] = lvl * np.exp(-(t[rel] - held_s) / (r / 4.0))
    return env


def render_preview(inst: Instrument, z: Zone, note: int, rate: int = 44100, held_s: float = 1.2) -> np.ndarray:
    """Áudio para ouvir uma zona numa tecla: afinação, início/fim, loop, reverso e envelope da camada."""
    g = inst.group_of(z)
    smp = z.sample
    data = smp.data
    end = z.play_end()
    data = data[z.start:end]
    if z.reverse:
        data = data[::-1]
    loop = None
    if z.loop_on and not z.reverse and smp.loop_start is not None and smp.loop_end is not None:
        ls, le = smp.loop_start - z.start, min(smp.loop_end, end) - z.start
        if 0 <= ls < le:
            loop = (ls, le)
    semis = (note - z.root) + (z.tune_cents + g.tune_cents) / 100.0
    ratio = (2 ** (semis / 12.0)) * smp.rate / rate
    total = int(rate * (held_s + max(0.05, g.release)))
    pos = np.arange(total) * ratio
    if loop:
        ls, le = loop
        L = le - ls
        over = pos >= le
        pos = np.where(over, ls + np.mod(pos - ls, L), pos)
    valid = pos < len(data) - 1
    pos = np.where(valid, pos, 0)
    i = pos.astype(np.int64)
    frac = (pos - i)[:, None].astype(np.float32)
    out = data[i] * (1 - frac) + data[np.minimum(i + 1, len(data) - 1)] * frac
    out[~valid] = 0
    env = adsr_envelope(g, rate, held_s, total)
    out = out * env[:, None] * (10 ** ((z.gain_db + g.gain_db) / 20.0))
    return np.clip(out, -1, 1).astype(np.float32)


def describe_zone(z: Zone) -> str:
    return f"{z.sample.name}  ·  {note_name(z.lo_key)}–{note_name(z.hi_key)}  raiz {note_name(z.root)}  vel {z.lo_vel}–{z.hi_vel}"
