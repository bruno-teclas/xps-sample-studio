"""Redução de amostras, processamento e exportação para Roland XPS-10X (e XPS-10).

Regras do XPS-10X (Reference Manual / especificações da Roland):
* SAMPLE › IMPORT lê WAV 44,1/48 kHz, 16/24 bits da pasta IMPORT do pendrive
  (formatado no próprio teclado). Nomes com até 24 caracteres (com a extensão),
  só caracteres alfanuméricos de 1 byte.
* ~3 min por amostra (44,1 kHz) e ~12 minutos no total (memória dividida com as Expansions).
* Cada importação cria um User Tone; a amostra soa da tecla mais grave até
  3 oitavas acima da Original Key.
* Uma Scene tem 8 partes, cada uma com KEY RANGE próprio → multisample de até 8 amostras.
* O loop repete do Loop Start até o fim → o áudio é cortado logo após o fim do loop.
"""
from __future__ import annotations

import html
import json
import math
import os
import re
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from typing import Callable, Dict, List, Optional

import numpy as np
from scipy import signal

from . import audio as A
from .model import ConversionError, Instrument, Sample, Zone, note_name


@dataclass(frozen=True)
class Target:
    key: str
    name: str
    max_parts: int
    max_up: int               # semitons que a amostra alcança acima da Original Key
    bits_options: tuple
    max_file_seconds: float   # por amostra (estéreo)
    total_seconds: float      # memória total aproximada
    name_len: int             # incluindo ".WAV"
    steps: tuple


XPS10X = Target(
    "xps10x", "Roland XPS-10X", 8, 36, (16, 24), 180.0, 720.0, 24,
    (
        "No XPS-10X, formate o pendrive (se for a primeira vez). Isso cria a pasta IMPORT.",
        "No computador, copie os arquivos .WAV da pasta IMPORT gerada para a pasta IMPORT do pendrive.",
        "Conecte o pendrive, pressione MENU › SAMPLE › IMPORT.",
        "Para cada arquivo da tabela: selecione o arquivo, escolha o User Tone de destino e confirme. "
        "Um User Tone é criado junto com a amostra.",
        "Ajuste Original Key e Loop no EDIT SAMPLE conforme a tabela (veja “SAMPLE parameters” no Parameter Guide) "
        "e salve a amostra.",
        "Monte a Scene: coloque cada User Tone numa parte (1 a 8) – botão [10] USER ou [9] SAMPLE/EXP.",
        "Abra KEY TOUCH (SHIFT + MENU), aba KEY RANGE, e ajuste o limite inferior e superior de cada parte.",
        "Grave a Scene (WRITE) para guardar o instrumento.",
    ),
)

XPS10 = Target(
    "xps10", "Roland XPS-10 (modelo antigo)", 16, 24, (16,), 180.0, 380.0, 12,
    (
        "No XPS-10, formate o pendrive (MENU › UTILITY › FORMAT). Isso cria a pasta IMPORT.",
        "Copie os .WAV da pasta IMPORT gerada para a pasta IMPORT do pendrive.",
        "No teclado: MENU › SAMPLE IMPORT. Para cada arquivo escolha o destino (Sp501…), Original Key e Loop Switch.",
        "Se precisar, ajuste Loop e End (ms) em MENU › SAMPLE EDIT.",
        "PERFORM › 016: Init Perform › MENU › PERFORM EDIT: em cada parte escolha o Sp, ligue o Keyboard Switch "
        "e ajuste LOWER/UPPER.",
        "Salve a performance (WRITE).",
    ),
)

TARGETS = {t.key: t for t in (XPS10X, XPS10)}
XPS_RATE = 44100
XPS_MAX_FILE = 32 * 1024 * 1024  # limite do XPS-10 antigo


# --------------------------------------------------------------------------
# Configurações
# --------------------------------------------------------------------------

@dataclass
class ReduceSettings:
    target_count: int = 8          # 1..16
    velocity: int = 100            # camada de velocidade usada
    key_low: int = 36              # C2 – teclado de 61 teclas
    key_high: int = 96             # C7
    spread: str = "center"         # center | up | down
    target: str = "xps10x"
    mode: str = "svz"              # svz (timbre pronto) | wav (amostras soltas + guia)
    locked: List[int] = field(default_factory=list)   # raízes que o usuário fixou


@dataclass
class ProcessSettings:
    mono: bool = False
    bits: int = 16
    normalize: bool = True
    normalize_db: float = -1.0
    normalize_group: bool = True   # mesmo ganho para todas (mantém o equilíbrio)
    trim_start: bool = True
    trim_db: float = -60.0
    max_seconds: float = 6.0       # para amostras sem loop
    fade_out_ms: float = 40.0
    loop_xfade_ms: float = 15.0
    cut_after_loop: bool = True
    dither: bool = True
    rate: int = 44100              # 48000 para SVZ
    eq: List[A.EQBand] = field(default_factory=lambda: default_eq())


def default_eq() -> List[A.EQBand]:
    return [
        A.EQBand("lowcut", 30.0, 0.0, 0.707, False),
        A.EQBand("lowshelf", 120.0, 0.0, 0.707, True),
        A.EQBand("peak", 1000.0, 0.0, 1.0, True),
        A.EQBand("highshelf", 8000.0, 0.0, 0.707, True),
        A.EQBand("highcut", 18000.0, 0.0, 0.707, False),
    ]


# --------------------------------------------------------------------------
# Seleção de camada e redução
# --------------------------------------------------------------------------

@dataclass
class Candidate:
    zone: Zone
    root: int


def layer_zones(inst: Instrument, velocity: int) -> List[Zone]:
    zs = [z for z in inst.zones if z.lo_vel <= velocity <= z.hi_vel]
    if not zs:  # nenhuma cobre: pega a camada mais próxima
        best = min(inst.zones, key=lambda z: min(abs(z.lo_vel - velocity), abs(z.hi_vel - velocity)))
        zs = [z for z in inst.zones if (z.lo_vel, z.hi_vel) == (best.lo_vel, best.hi_vel)]
    return zs


def candidates(inst: Instrument, velocity: int) -> List[Candidate]:
    """Uma entrada por amostra/raiz distinta da camada escolhida (preferindo a de faixa maior)."""
    seen: Dict[int, Candidate] = {}
    for z in layer_zones(inst, velocity):
        key = z.root
        span = z.hi_key - z.lo_key
        if key not in seen or span > (seen[key].zone.hi_key - seen[key].zone.lo_key):
            seen[key] = Candidate(z, z.root)
    return sorted(seen.values(), key=lambda c: c.root)


def choose(cands: List[Candidate], rs: ReduceSettings) -> List[Candidate]:
    n = max(1, min(max_samples(rs), rs.target_count))
    if len(cands) <= n:
        return list(cands)
    chosen = [c for c in cands if c.root in rs.locked][:n]
    free = [c for c in cands if c not in chosen]
    need = n - len(chosen)
    if need > 0:
        lo, hi = rs.key_low, rs.key_high
        # alvos espaçados por igual no teclado
        if need == 1:
            targets = [(lo + hi) / 2]
        else:
            step = (hi - lo) / need
            targets = [lo + step * (i + 0.5) for i in range(need)]
        for t in targets:
            best = min(free, key=lambda c: abs(c.root - t))
            chosen.append(best)
            free.remove(best)
    return sorted(chosen, key=lambda c: c.root)


SVZ_MAX_SAMPLES = 64


def max_samples(rs: "ReduceSettings") -> int:
    """No modo SVZ o multisample fica dentro de um único timbre; no modo WAV cada amostra ocupa uma parte."""
    return SVZ_MAX_SAMPLES if rs.mode == "svz" else TARGETS[rs.target].max_parts


@dataclass
class PlanEntry:
    zone: Zone
    root: int
    lo: int
    hi: int
    warning: str = ""


def plan(inst: Instrument, rs: ReduceSettings) -> List[PlanEntry]:
    chosen = choose(candidates(inst, rs.velocity), rs)
    out: List[PlanEntry] = []
    lo_all, hi_all = rs.key_low, rs.key_high
    for i, c in enumerate(chosen):
        prev_r = chosen[i - 1].root if i > 0 else None
        next_r = chosen[i + 1].root if i + 1 < len(chosen) else None
        if prev_r is None:
            lo = min(lo_all, c.root)
        elif rs.spread == "up":
            lo = c.root
        elif rs.spread == "down":
            lo = prev_r + 1
        else:
            lo = (prev_r + c.root) // 2 + 1
        if next_r is None:
            hi = max(hi_all, c.root)
        elif rs.spread == "up":
            hi = next_r - 1
        elif rs.spread == "down":
            hi = c.root
        else:
            hi = (c.root + next_r) // 2
        out.append(PlanEntry(c.zone, c.root, lo, hi))
    # Regra do teclado: alcance máximo acima da raiz (XPS-10X: 3 oitavas) → a próxima zona cobre a sobra
    for i, e in enumerate(out):
        limit = e.root + TARGETS[rs.target].max_up
        if e.hi > limit:
            if i + 1 < len(out):
                out[i + 1].lo = limit + 1
            else:
                e.warning = f"Teclas acima de {note_name(limit)} não mudam de altura (limite de alcance acima da Original Key)."
            e.hi = limit
    return out


# --------------------------------------------------------------------------
# Processamento de áudio
# --------------------------------------------------------------------------

@dataclass
class Rendered:
    entry: PlanEntry
    data: np.ndarray
    loop: Optional[tuple]
    rate: int = 44100
    filename: str = ""
    slot: int = 0
    level: int = 100


def _to_rate(smp: Sample, tune_cents: float, rate: int = 44100):
    """Converte a taxa e embute a afinação fina (o XPS-10 não tem fine tune por amostra)."""
    eff = smp.rate * (2.0 ** (tune_cents / 1200.0))
    ratio = Fraction(rate / eff).limit_denominator(1000)
    data = smp.data.astype(np.float32)
    if ratio != 1:
        data = signal.resample_poly(data, ratio.numerator, ratio.denominator, axis=0).astype(np.float32)
    k = float(ratio)
    loop = None
    if smp.loop_start is not None and smp.loop_end is not None:
        loop = (int(round(smp.loop_start * k)), int(round(smp.loop_end * k)))
        loop = (max(0, loop[0]), min(len(data), loop[1]))
        if loop[1] - loop[0] < 16:
            loop = None
    return data, loop


def render_entry(e: PlanEntry, ps: ProcessSettings) -> Rendered:
    z = e.zone
    R = ps.rate
    data, loop = _to_rate(z.sample, z.tune_cents, R)
    if not z.loop_on:
        loop = None
    if abs(z.gain_db) > 0.01:
        data = data * (10 ** (z.gain_db / 20.0))
    if ps.mono:
        data = A.to_mono(data)
    elif data.shape[1] == 1:
        pass
    if ps.trim_start:
        data, cut = A.trim_silence(data, ps.trim_db, 2.0, R)
        if loop:
            loop = (loop[0] - cut, loop[1] - cut)
            if loop[0] < 0:
                loop = None
    data = A.apply_eq(data, R, ps.eq)
    if loop:
        xf = int(R * ps.loop_xfade_ms / 1000.0)
        data = A.crossfade_loop(data, loop[0], loop[1], xf)
        if ps.cut_after_loop:
            data = data[: loop[1]]
    else:
        maxn = int(ps.max_seconds * R) if ps.max_seconds > 0 else len(data)
        if len(data) > maxn:
            data = data[:maxn]
        data = A.fade(data, R, 0.0, ps.fade_out_ms)
    data = A.fade(data, R, 0.5, 0.0)  # evita clique no ataque
    return Rendered(e, data.astype(np.float32), loop, R)


def render_all(entries: List[PlanEntry], ps: ProcessSettings,
               progress: Optional[Callable[[int, int], None]] = None) -> List[Rendered]:
    out = []
    for i, e in enumerate(entries):
        out.append(render_entry(e, ps))
        if progress:
            progress(i + 1, len(entries))
    if ps.normalize and out:
        target = 10 ** (ps.normalize_db / 20.0)
        if ps.normalize_group:
            peak = max(float(np.max(np.abs(r.data))) for r in out) or 1.0
            for r in out:
                r.data *= target / peak
        else:
            for r in out:
                p = float(np.max(np.abs(r.data))) or 1.0
                r.data *= target / p
    return out


def estimate_bytes(rendered: List[Rendered]) -> int:
    return sum(r.data.shape[0] * r.data.shape[1] * 2 + 44 for r in rendered)


# --------------------------------------------------------------------------
# Exportação
# --------------------------------------------------------------------------

def _alnum(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", s)


def note_tag(n: int) -> str:
    """Nota só com caracteres alfanuméricos: C#4 → Cs4, C-1 → Cm1."""
    return note_name(n).replace("#", "s").replace("-", "m")


def safe_prefix(name: str, n: int = 10) -> str:
    s = _alnum(name.upper())
    return (s or "SMP")[:n]


def file_name(prefix: str, idx: int, root: int, target: Target) -> str:
    if target.name_len >= 20:
        return f"{prefix[:10]}{idx:02d}{note_tag(root)}.WAV"
    return f"{prefix[:5]}{idx:02d}.WAV"


def export(inst: Instrument, rendered: List[Rendered], out_dir: str, prefix: Optional[str] = None,
           target: str = "xps10x", bits: int = 16) -> Dict[str, str]:
    if not rendered:
        raise ConversionError("Nada para exportar.")
    t = TARGETS[target]
    if bits not in t.bits_options:
        bits = t.bits_options[0]
    prefix = safe_prefix(prefix or inst.name)
    imp = os.path.join(out_dir, "IMPORT")
    os.makedirs(imp, exist_ok=True)
    for i, r in enumerate(rendered):
        r.filename = file_name(prefix, i + 1, r.entry.root, t)
        assert len(r.filename) <= t.name_len
        r.slot = i + 1
        secs = r.data.shape[0] / XPS_RATE
        if secs > t.max_file_seconds:
            raise ConversionError(f"{r.filename} tem {secs:.0f} s – o {t.name} aceita ~{t.max_file_seconds:.0f} s por amostra.")
        size = A.write_wav(os.path.join(imp, r.filename), r.data, XPS_RATE, bits, r.entry.root, r.loop, True)
        if t is XPS10 and size > XPS_MAX_FILE:
            raise ConversionError(f"{r.filename} passou de 32 MB.")
    guide_html = os.path.join(out_dir, f"GUIA_{t.key.upper()}_{prefix}.html")
    guide_txt = os.path.join(out_dir, f"GUIA_{t.key.upper()}_{prefix}.txt")
    with open(guide_html, "w", encoding="utf-8") as f:
        f.write(guide_as_html(inst, rendered, t))
    with open(guide_txt, "w", encoding="utf-8") as f:
        f.write(guide_as_text(inst, rendered, t))
    with open(os.path.join(out_dir, f"{prefix}_mapa.json"), "w", encoding="utf-8") as f:
        json.dump({"teclado": t.name, "instrumento": inst.name,
                   "amostras": [row(r, t) for r in rendered]}, f, indent=2, ensure_ascii=False)
    return {"import": imp, "html": guide_html, "txt": guide_txt}


def total_seconds(rendered: List[Rendered]) -> float:
    return sum(r.data.shape[0] / r.rate for r in rendered)


def ms(frames: int) -> int:
    return int(round(frames * 1000.0 / XPS_RATE))


def row(r: Rendered, t: Target) -> dict:
    e = r.entry
    return {
        "parte": r.slot,
        "arquivo": r.filename,
        "destino": f"Sp{500 + r.slot}" if t is XPS10 else f"User Tone (parte {r.slot})",
        "original_key": note_name(e.root),
        "lower": note_name(e.lo),
        "upper": note_name(e.hi),
        "loop": "ON" if r.loop else "OFF",
        "loop_start_ms": ms(r.loop[0]) if r.loop else None,
        "loop_start_amostra": r.loop[0] if r.loop else None,
        "end_ms": ms(r.data.shape[0]),
        "aviso": e.warning,
    }


def guide_as_text(inst: Instrument, rendered: List[Rendered], t: Target = XPS10X) -> str:
    lines = [f"GUIA DE IMPORTAÇÃO – {t.name.upper()}", f"Instrumento: {inst.name}  (origem: {inst.source_format})", ""]
    lines += [f"{i}. {s}" for i, s in enumerate(t.steps, 1)]
    lines.append("")
    lines.append(f"{'Parte':<6}{'Arquivo':<24}{'Orig.Key':<10}{'Inferior':<10}{'Superior':<10}{'Loop':<6}{'Loop ms':<9}{'Fim ms':<8}")
    for r in rendered:
        d = row(r, t)
        lines.append(f"{d['parte']:<6}{d['arquivo']:<24}{d['original_key']:<10}{d['lower']:<10}"
                     f"{d['upper']:<10}{d['loop']:<6}{str(d['loop_start_ms'] if d['loop_start_ms'] is not None else '-'):<9}{d['end_ms']:<8}")
        if d["aviso"]:
            lines.append(f"      ! {d['aviso']}")
    tot = total_seconds(rendered)
    lines += ["", f"Duração total: {tot:.1f} s de ~{t.total_seconds / 60:.0f} min disponíveis"]
    return "\n".join(lines) + "\n"


def guide_as_html(inst: Instrument, rendered: List[Rendered], t: Target = XPS10X) -> str:
    esc = html.escape
    rows = []
    for r in rendered:
        d = row(r, t)
        warn = f'<div class="warn">⚠ {esc(d["aviso"])}</div>' if d["aviso"] else ""
        ls = d["loop_start_ms"]
        rows.append(
            f"<tr><td>{d['parte']}</td><td><code>{esc(d['arquivo'])}</code></td>"
            f"<td><b>{d['original_key']}</b></td><td>{d['lower']}</td><td>{d['upper']}</td>"
            f"<td><span class='pill {'on' if r.loop else 'off'}'>{d['loop']}</span></td>"
            f"<td>{ls if ls is not None else '–'}</td><td>{d['end_ms']}</td></tr>"
            + (f"<tr><td></td><td colspan='7'>{warn}</td></tr>" if warn else ""))
    steps = "".join(f"<li>{esc(s)}</li>" for s in t.steps)
    tot = total_seconds(rendered)
    pct = min(100, tot / t.total_seconds * 100)
    return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">
<title>Guia {esc(t.name)} – {esc(inst.name)}</title>
<style>
body{{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:920px;margin:40px auto;padding:0 24px;color:#1b1d24}}
h1{{font-size:26px;margin:0 0 4px}} .sub{{color:#666;margin-bottom:24px}}
table{{border-collapse:collapse;width:100%;font-size:14px}} th,td{{padding:8px 10px;border-bottom:1px solid #e3e5ea;text-align:left}}
th{{background:#f4f5f8;font-weight:600}} code{{background:#f1f2f5;padding:2px 5px;border-radius:4px}}
.pill{{padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600}} .on{{background:#d9f5e6;color:#0b7a43}} .off{{background:#eee;color:#666}}
.warn{{color:#a15c00;font-size:13px}} ol li{{margin:6px 0}} .box{{background:#f7f8fb;border-radius:10px;padding:14px 18px;margin:18px 0}}
.bar{{height:8px;background:#e8eaf0;border-radius:4px;overflow:hidden;margin-top:6px}} .bar i{{display:block;height:100%;background:#ff6a3d;width:{pct:.1f}%}}
</style></head><body>
<h1>Guia de importação – {esc(t.name)}</h1>
<div class="sub">Instrumento <b>{esc(inst.name)}</b> · origem {esc(inst.source_format)} · {len(rendered)} amostra(s) ·
{tot:.1f} s de ~{t.total_seconds / 60:.0f} min de memória<div class="bar"><i></i></div></div>
<div class="box"><ol>{steps}</ol></div>
<table><tr><th>Parte</th><th>Arquivo</th><th>Original Key</th><th>Tecla inferior</th><th>Tecla superior</th><th>Loop</th><th>Loop start (ms)</th><th>Fim (ms)</th></tr>
{''.join(rows)}</table>
<p class="sub" style="margin-top:20px">Os tempos valem na Original Key. Os arquivos também levam a raiz e o loop gravados no bloco “smpl” do WAV. Gerado pelo XPS Sample Studio.</p>
</body></html>"""


# --------------------------------------------------------------------------
# Exportação como timbre .SVZ (importado inteiro pelo teclado)
# --------------------------------------------------------------------------

def svz_name(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9 _\-]", "", name).strip() or "Sample Tone"
    return s[:16]


def export_svz(inst: Instrument, rendered: List[Rendered], out_dir: str, prefix: Optional[str] = None,
               target: str = "xps10x", header: Optional[bytes] = None) -> Dict[str, str]:
    """Gera ROLAND/SOUND/<NOME>.SVZ com um timbre pronto (multisample mapeado no teclado)."""
    from . import svz as Z
    if not rendered:
        raise ConversionError("Nada para exportar.")
    t = TARGETS[target]
    base = safe_prefix(prefix or inst.name, 12)
    tone_name = svz_name(inst.name)
    stereo = any(r.data.ndim > 1 and r.data.shape[1] > 1 for r in rendered)
    pools: List[List[Z.SvzSample]] = [[], []] if stereo else [[]]
    layers = [Z.SvzLayer(f"{base[:13]}_L" if stereo else base, pan=-64 if stereo else 0)]
    if stereo:
        layers.append(Z.SvzLayer(f"{base[:13]}_R", pan=63))
    # nomes únicos (o teclado reaproveita amostras com o mesmo nome já existentes na memória)
    tag = f"{zlib_crc(rendered) & 0xFFFF:04X}"
    samples: List[Z.SvzSample] = []
    for i, r in enumerate(rendered):
        e = r.entry
        chans = [r.data[:, 0], r.data[:, 1]] if stereo and r.data.shape[1] > 1 else \
            [A.to_mono(r.data)[:, 0]] * (2 if stereo else 1)
        for c, mono in enumerate(chans):
            pcm = A.float_to_int16(mono[:, None], True)[:, 0]
            side = ("L" if c == 0 else "R") if stereo else ""
            nm = f"{base[:6]}{i + 1:02d}{side}{tag}"[:16]
            samples.append(Z.SvzSample(nm, pcm, e.root, r.loop, 127))
            idx = len(samples)   # 1-based
            for k in range(max(0, e.lo), min(127, e.hi) + 1):
                layers[c].key_to_sample[k] = idx
        r.filename = f"{base}.SVZ"
        r.slot = i + 1
    data = Z.build_svz(tone_name, samples, layers, header)
    sound = os.path.join(out_dir, "ROLAND", "SOUND")
    os.makedirs(sound, exist_ok=True)
    path = os.path.join(sound, f"{base}.SVZ")
    with open(path, "wb") as f:
        f.write(data)
    guide = os.path.join(out_dir, f"COMO_IMPORTAR_{base}.txt")
    with open(guide, "w", encoding="utf-8") as f:
        f.write(svz_guide(inst, rendered, t, tone_name, os.path.basename(path), stereo))
    return {"import": sound, "svz": path, "html": guide, "txt": guide}


def zlib_crc(rendered: List[Rendered]) -> int:
    import zlib
    c = 0
    for r in rendered:
        c = zlib.crc32(np.ascontiguousarray(r.data[:4096]).tobytes(), c)
    return c


def svz_guide(inst: Instrument, rendered: List[Rendered], t: Target, tone: str, fname: str, stereo: bool) -> str:
    lines = [
        f"COMO IMPORTAR NO {t.name.upper()}",
        f"Instrumento: {inst.name}  ·  timbre “{tone}”  ·  {len(rendered)} amostra(s) "
        f"{'estéreo' if stereo else 'mono'}  ·  {total_seconds(rendered):.1f} s",
        "",
        "1. Formate o pendrive no próprio teclado (se for a primeira vez).",
        f"2. Copie a pasta ROLAND gerada para a raiz do pendrive (o arquivo fica em ROLAND/SOUND/{fname}).",
        "3. No teclado: MENU › UTILITY › IMPORT TONE.",
        f"4. Escolha {fname}, marque o timbre (deixe marcada a opção de importar com as amostras) e confirme.",
        "5. O timbre aparece entre os User Tones, já com todas as amostras mapeadas no teclado.",
        "",
        "Mapa de teclas:",
    ]
    for i, r in enumerate(rendered):
        e = r.entry
        lines.append(f"  {i + 1:>2}. {note_name(e.lo):>4} – {note_name(e.hi):<4} raiz {note_name(e.root):<4}"
                     f" {'loop' if r.loop else ''}")
    return "\n".join(lines) + "\n"
