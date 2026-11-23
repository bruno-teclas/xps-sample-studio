"""Testes do núcleo (rode com: python -m pytest tests)."""
import os
import struct
import sys
import tempfile
import zlib

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xps_studio.core import audio as A  # noqa: E402
from xps_studio.core import engine as E  # noqa: E402
from xps_studio.core.loader import load_instrument  # noqa: E402

SF2 = "/usr/share/sounds/sf2/TimGM6mb.sf2"


def _tone(freq, secs=1.0, rate=48000, ch=1):
    t = np.arange(int(secs * rate)) / rate
    x = (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.repeat(x[:, None], ch, axis=1)


def _make_wavs(d, notes):
    for n in notes:
        f = 440 * 2 ** ((n - 69) / 12)
        A.write_wav16(os.path.join(d, f"Flute_{n}.wav"), _tone(f, 1.0, 48000, 2), 48000, n, (4000, 40000), False)


def _pitch(data, rate):
    x = data[:, 0][: rate // 2]
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.argmax(spec) * rate / len(x)


def test_wav_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "a.wav")
        A.write_wav16(p, _tone(440, 0.5, 44100, 2), 44100, 69, (100, 2000), False)
        s = A.read_wav(p)
        assert s.rate == 44100 and s.channels == 2 and (s.loop_start, s.loop_end) == (100, 2000)


def test_sf2_to_xps():
    if not os.path.exists(SF2):
        return
    inst = load_instrument(SF2)
    assert inst.zones
    rs = E.ReduceSettings(target_count=4)
    entries = E.plan(inst, rs)
    assert 1 <= len(entries) <= 4
    for e in entries:
        assert e.hi <= e.root + 36
    rend = E.render_all(entries, E.ProcessSettings())
    with tempfile.TemporaryDirectory() as d:
        res = E.export(inst, rend, d)
        files = os.listdir(res["import"])
        assert len(files) == len(entries)
        for fn in files:
            s = A.read_wav(os.path.join(res["import"], fn))
            assert s.rate == 44100 and fn.replace(".", "").isalnum() and len(fn) <= 24


def test_folder_reduction_and_pitch():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "src")
        os.makedirs(src)
        _make_wavs(src, range(36, 97, 3))  # 21 amostras
        inst = load_instrument(src)
        assert len(inst.zones) == 21
        entries = E.plan(inst, E.ReduceSettings(target_count=12, key_low=36, key_high=96, mode="wav"))
        assert len(entries) == 8, "XPS-10X: no máximo 8 partes por Scene"
        entries = E.plan(inst, E.ReduceSettings(target_count=6, key_low=36, key_high=96))
        assert len(entries) == 6
        keys = set()
        for e in entries:
            keys.update(range(e.lo, e.hi + 1))
        assert set(range(36, 97)) <= keys, "o teclado inteiro deve ficar coberto"
        rend = E.render_all(entries, E.ProcessSettings())
        r = rend[0]
        expected = 440 * 2 ** ((r.entry.root - 69) / 12)
        assert abs(_pitch(r.data, 44100) - expected) / expected < 0.01
        assert r.loop is not None and len(r.data) == r.loop[1]  # cortado no fim do loop


def test_tune_is_baked():
    with tempfile.TemporaryDirectory() as d:
        A.write_wav16(os.path.join(d, "x_69.wav"), _tone(440, 1.0, 44100), 44100, None, None, False)
        inst = load_instrument(os.path.join(d, "x_69.wav"))
        inst.zones[0].tune_cents = 100  # +1 semitom
        rend = E.render_all(E.plan(inst, E.ReduceSettings(target_count=1)), E.ProcessSettings(trim_start=False))
        f = _pitch(rend[0].data, 44100)
        assert abs(f - 466.16) < 4, f


def test_sfz():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "samples"))
        _make_wavs(os.path.join(d, "samples"), [48, 60, 72])
        with open(os.path.join(d, "t.sfz"), "w") as f:
            f.write("<control> default_path=samples/\n<group> lovel=0 hivel=127\n"
                    "<region> sample=Flute_48.wav lokey=c2 hikey=b2 pitch_keycenter=48\n"
                    "<region> sample=Flute_60.wav lokey=c3 hikey=b3 pitch_keycenter=c4 loop_mode=loop_continuous\n"
                    "<region> sample=Flute_72.wav key=72\n")
        inst = load_instrument(os.path.join(d, "t.sfz"))
        assert [z.root for z in inst.zones] == [48, 60, 72]
        assert inst.zones[1].loop_on


def test_nki_k2_xml():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "Samples"))
        _make_wavs(os.path.join(d, "Samples"), [50, 62])
        xml = ('<?xml version="1.0"?><K2_Container><Programs><K2_Program><Zones>'
               '<K2_Zone><V name="lowKey" value="0"/><V name="highKey" value="55"/><V name="rootKey" value="50"/>'
               '<Sample><V name="file" value="@b0aSamples@Flute_50.wav"/></Sample></K2_Zone>'
               '<K2_Zone><V name="lowKey" value="56"/><V name="highKey" value="127"/><V name="rootKey" value="62"/>'
               '<Sample><V name="file" value="@b0aSamples@Flute_62.wav"/></Sample></K2_Zone>'
               '</Zones></K2_Program></Programs></K2_Container>').encode()
        with open(os.path.join(d, "test.nki"), "wb") as f:
            f.write(struct.pack("<I", 0x7FA89012) + b"\0" * 166 + zlib.compress(xml))
        inst = load_instrument(os.path.join(d, "test.nki"))
        assert sorted(z.root for z in inst.zones) == [50, 62]


def test_24bit_and_legacy_target():
    with tempfile.TemporaryDirectory() as d:
        A.write_wav(os.path.join(d, "x_60.wav"), _tone(261.6, 0.5, 44100), 44100, 24, 60, None, False)
        s = A.read_wav(os.path.join(d, "x_60.wav"))
        assert abs(A.peak_db(s.data) - A.peak_db(_tone(1, 0.5))) < 0.5
        inst = load_instrument(os.path.join(d, "x_60.wav"))
        rs = E.ReduceSettings(target_count=1, target="xps10")
        e = E.plan(inst, rs)
        assert e[0].hi <= 60 + 24
        out = os.path.join(d, "out")
        E.export(inst, E.render_all(e, E.ProcessSettings()), out, target="xps10")
        assert os.listdir(os.path.join(out, "IMPORT")) == ["X6001.WAV"]


def test_eq_changes_spectrum():
    x = _tone(100, 1.0, 44100)
    y = A.apply_eq(x, 44100, [A.EQBand("lowshelf", 200, -12, 0.707)])
    assert A.peak_db(y) < A.peak_db(x) - 8


# ---------------------------------------------------------------------------
# Kontakt 5+ e NCW
# ---------------------------------------------------------------------------

def _encode_ncw(path, data_int, bits_per_sample=16, rate=44100, mid_side=False, mode="delta"):
    """Codificador NCW mínimo (só para testar o decodificador)."""
    n, ch = data_int.shape
    nblocks = (n + 511) // 512
    blocks = []
    for b in range(nblocks):
        seg = np.zeros((512, ch), dtype=np.int64)
        part = data_int[b * 512:(b + 1) * 512]
        seg[:len(part)] = part
        if mid_side:
            l, r = seg[:, 0].copy(), seg[:, 1].copy()
            # mid + side = L, mid - side = R  → side = (L-R)/2 (dados de teste pares)
            seg[:, 0], seg[:, 1] = (l + r) // 2, (l - r) // 2
        out = b""
        for c in range(ch):
            x = seg[:, c]
            if mode == "delta":
                d = np.diff(x, append=x[-1])
                bits = int(max(2, int(np.abs(d).max()).bit_length() + 1))
                vals, base = d, int(x[0])
            else:
                bits = -bits_per_sample
                vals, base = x, 0
            nb = abs(bits)
            u = (vals & ((1 << nb) - 1)).astype(np.uint64)
            bitarr = ((u[:, None] >> np.arange(nb, dtype=np.uint64)) & 1).astype(np.uint8).reshape(-1)
            payload = np.packbits(bitarr, bitorder="little").tobytes()
            flags = 1 if mid_side else 0
            out += struct.pack("<IihHI", 0x3E9A0C16, base, bits, flags, 0) + payload
        blocks.append(out)
    offs = [0]
    for bl in blocks:
        offs.append(offs[-1] + len(bl))
    head = struct.pack("<IIHHIIIII", 0xD69EA801, 0x131, ch, bits_per_sample, rate, n, 120, 120 + 4 * len(offs), 0)
    head += b"\0" * 88
    with open(path, "wb") as f:
        f.write(head + struct.pack(f"<{len(offs)}I", *offs) + b"".join(blocks))


def test_ncw_decoder():
    from xps_studio.core.ncw import read_ncw
    rng = np.random.default_rng(1)
    t = np.arange(3000)
    l = (np.sin(t / 20) * 12000).astype(np.int64) * 2
    r = (np.cos(t / 33) * 9000).astype(np.int64) * 2
    data = np.stack([l, r], axis=1)
    with tempfile.TemporaryDirectory() as d:
        for ms in (False, True):
            for mode in ("delta", "trunc"):
                p = os.path.join(d, f"x_{ms}_{mode}.ncw")
                _encode_ncw(p, data, mid_side=ms, mode=mode)
                s = read_ncw(p)
                got = np.round(s.data * 32768).astype(np.int64)
                assert s.rate == 44100 and s.data.shape == (3000, 2), s.data.shape
                assert np.array_equal(got, data), (ms, mode, np.abs(got - data).max())


def test_fastlz_roundtrip_literal():
    from xps_studio.core.kontakt5 import fastlz_decompress
    # bloco só de literais (nível 1): ctrl = n-1
    raw = bytes(range(20))
    assert fastlz_decompress(bytes([19]) + raw, 20) == raw


def test_kontakt68_nki():
    tpl = os.path.join(os.path.dirname(__file__), "k68_template.nki")
    if not os.path.exists(tpl):
        return
    from xps_studio.core.kontakt5 import _parse_chunks, _preset_data, _read_filelist
    with open(tpl, "rb") as f:
        files = _read_filelist(_parse_chunks(_preset_data(f.read())))
    with tempfile.TemporaryDirectory() as d:
        lib = os.path.join(d, "Minha Biblioteca")
        sdir = os.path.join(lib, "Samples", "FM8 Jam Samples")
        os.makedirs(sdir)
        import shutil
        shutil.copy(tpl, os.path.join(lib, "FM8 Jam.nki"))
        for i, fp in enumerate(files):
            name = os.path.basename(fp)
            note = int(name.split("_")[2])
            f0 = 440 * 2 ** ((note - 69) / 12)
            tone = _tone(f0, 0.3, 44100, 2)
            if i % 2:  # metade em NCW
                _encode_ncw(os.path.join(sdir, name[:-4] + ".ncw"),
                            np.round(tone * 16000).astype(np.int64) * 2, mode="delta")
            else:
                A.write_wav16(os.path.join(sdir, name), tone, 44100, None, None, False)
        inst = load_instrument(os.path.join(lib, "FM8 Jam.nki"))
        assert inst.source_format.startswith("NKI (Kontakt 5")
        assert len(inst.zones) == 61
        assert not inst.zones[0].sample.loaded  # carregamento sob demanda
        entries = E.plan(inst, E.ReduceSettings(target_count=8))
        rend = E.render_all(entries, E.ProcessSettings())
        assert len(rend) == 8
        for r in rend:
            expected = 440 * 2 ** ((r.entry.root - 69) / 12)
            got = _pitch(r.data, 44100)
            assert abs(got - expected) / expected < 0.02, (r.entry.root, got, expected)


def test_svz_export_structure():
    import zlib as Zl
    from xps_studio.core import svz as Z
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "src")
        os.makedirs(src)
        _make_wavs(src, range(36, 97, 4))
        inst = load_instrument(src)
        rs = E.ReduceSettings(target_count=12, mode="svz")
        entries = E.plan(inst, rs)
        assert len(entries) == 12          # além do limite de 8 do modo WAV
        for stereo in (False, True):
            ps = E.ProcessSettings(rate=48000, mono=not stereo)
            rend = E.render_all(entries, ps)
            out = os.path.join(d, f"out{stereo}")
            res = E.export_svz(inst, rend, out)
            data = open(res["svz"], "rb").read()
            info = Z.read_container(data)
            assert info["model"] == "KY019"
            assert list(info["blocks"]) == ["DIFa", "PATa", "USPa", "MSPa", "USDa"]
            nlay = 2 if stereo else 1
            assert info["blocks"]["MSPa"]["count"] == nlay
            assert info["blocks"]["USPa"]["count"] == 12 * nlay + 1   # + espaçador
            assert info["blocks"]["PATa"]["unit"] == 1632
            for tag in ("PATa", "USPa", "MSPa"):
                b = info["blocks"][tag]
                for i, rec in enumerate(Z.records(data, info, tag)):
                    crc = struct.unpack_from("<I", data, b["offset"] + 16 + 4 * i)[0]
                    assert crc == Zl.crc32(rec)
            ms = Z.records(data, info, "MSPa")[0]
            keymap = [struct.unpack_from("<H", ms, 16 + 8 * k)[0] for k in range(128)]
            for i, e in enumerate(entries):
                idx = i * nlay + 1
                assert all(keymap[k] == idx for k in range(e.lo, e.hi + 1))
            usp = Z.records(data, info, "USPa")
            for i, e in enumerate(entries):
                rec = usp[i * nlay]
                assert rec[0x19] == e.root and rec[0x14] == 0   # raiz + loop para frente
                end = struct.unpack_from("<I", rec, 0x24)[0]
                ls = struct.unpack_from("<I", rec, 0x20)[0]
                assert 0 < ls < end
            tone = Z.records(data, info, "PATa")[0]
            assert tone[0xDF] == 3 and struct.unpack_from("<H", tone, 0xE2)[0] == 1
            if stereo:
                assert tone[0xDF + 0x7C] == 3 and struct.unpack_from("<H", tone, 0xE2 + 0x7C)[0] == 2
            b = info["blocks"]["USDa"]
            o = b["offset"]
            i0, off, size, crc = struct.unpack_from("<IIII", data, o + 16)
            chunk = data[o + off:o + off + size]
            assert chunk[:4] == b"SMPd" and struct.unpack_from("<I", chunk, 0x0C)[0] == 48000
            assert chunk[8] == 1 and Zl.crc32(chunk) == crc


if __name__ == "__main__":  # noqa
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
