"""Janela principal do XPS Sample Studio."""
from __future__ import annotations

import os
import tempfile
import traceback
from typing import List, Optional

import numpy as np
from PySide6.QtCore import QObject, QRunnable, QSettings, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox,
                               QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import audio as A
from ..core import engine as E
from ..core.loader import FILTER, SUPPORTED, load_instrument
from ..core.model import ConversionError, Instrument, note_name
from . import theme as T
from .widgets import EQCurve, KeyboardMap, MemoryMeter, Waveform

APP_NAME = "XPS Sample Studio"

EQ_PRESETS = {
    "Plano": [(False, 30, 0, .707), (True, 120, 0, .707), (True, 1000, 0, 1), (True, 8000, 0, .707), (False, 18000, 0, .707)],
    "Mais brilho": [(False, 30, 0, .707), (True, 120, 0, .707), (True, 3000, 1.5, .9), (True, 9000, 4, .707), (False, 18000, 0, .707)],
    "Limpar graves": [(True, 60, 0, .707), (True, 250, -3, .707), (True, 400, -2, 1.2), (True, 8000, 0, .707), (False, 18000, 0, .707)],
    "Presença (palco)": [(True, 40, 0, .707), (True, 150, -1.5, .707), (True, 2500, 3, 1), (True, 10000, 2, .707), (False, 18000, 0, .707)],
    "Quente": [(False, 30, 0, .707), (True, 180, 2.5, .707), (True, 3500, -1.5, 1), (True, 9000, -3, .707), (True, 16000, 0, .707)],
}


# ----------------------------------------------------------------------------
# Execução em segundo plano
# ----------------------------------------------------------------------------

class _Signals(QObject):
    done = Signal(object)
    error = Signal(str)
    progress = Signal(int, int)


class Job(QRunnable):
    def __init__(self, fn, *args, **kw):
        super().__init__()
        self.fn, self.args, self.kw = fn, args, kw
        self.s = _Signals()

    def run(self):
        try:
            self.s.done.emit(self.fn(*self.args, **self.kw))
        except ConversionError as e:
            self.s.error.emit(str(e))
        except Exception as e:  # noqa
            traceback.print_exc()
            self.s.error.emit(f"Erro inesperado: {e}")


class Player:
    """Reprodução simples via QtMultimedia (arquivo WAV temporário)."""

    def __init__(self):
        self.ok = False
        self._init_done = False
        self.tmp = tempfile.mkdtemp(prefix="xpsstudio_")
        self.n = 0

    def _ensure(self):
        """Cria o player só no primeiro uso (abre o programa mais rápido e evita falhas sem placa de som)."""
        if self._init_done:
            return
        self._init_done = True
        if os.environ.get("XPS_NO_AUDIO"):
            return
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            self.out = QAudioOutput()
            self.out.setVolume(0.9)
            self.player = QMediaPlayer()
            self.player.setAudioOutput(self.out)
            self.ok = True
        except Exception:
            self.ok = False

    def play(self, data: np.ndarray, rate: int):
        self._ensure()
        if not self.ok or data is None or len(data) == 0:
            return
        self.n += 1
        path = os.path.join(self.tmp, f"p{self.n % 4}.wav")
        self.player.stop()
        self.player.setSource(QUrl())
        A.write_wav16(path, np.clip(data, -1, 1), rate, dither=False)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def stop(self):
        if self.ok:
            self.player.stop()


# ----------------------------------------------------------------------------
# Auxiliares de layout
# ----------------------------------------------------------------------------

def card(*widgets, spacing=12, margins=18) -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(margins, margins, margins, margins)
    lay.setSpacing(spacing)
    for w in widgets:
        (lay.addLayout if hasattr(w, "addWidget") and not isinstance(w, QWidget) else lay.addWidget)(w)
    return f


def label(text, name=None, wrap=False) -> QLabel:
    l = QLabel(text)
    if name:
        l.setObjectName(name)
    l.setWordWrap(wrap)
    return l


def row(*items, spacing=10) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setSpacing(spacing)
    for it in items:
        if it is None:
            h.addStretch(1)
        elif isinstance(it, QWidget):
            h.addWidget(it)
        else:
            h.addLayout(it)
    return h


def stat(value: str, caption: str):
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(2)
    a = label(value, "stat")
    v.addWidget(a)
    v.addWidget(label(caption, "statLabel"))
    return w, a


def note_combo(default: int) -> QComboBox:
    c = QComboBox()
    for n in range(0, 128):
        c.addItem(note_name(n), n)
    c.setCurrentIndex(default)
    c.setMaxVisibleItems(16)
    return c


class DropZone(QFrame):
    dropped = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.setMinimumHeight(230)
        v = QVBoxLayout(self)
        v.setAlignment(Qt.AlignCenter)
        icon = label("⬇", None)
        icon.setStyleSheet(f"font-size:40px;color:{T.ACCENT};")
        icon.setAlignment(Qt.AlignCenter)
        t = label("Arraste aqui seu instrumento", "h2")
        t.setAlignment(Qt.AlignCenter)
        s = label("Kontakt .nki  ·  SoundFont .sf2  ·  .sfz  ·  WAV  ·  pasta de WAVs", "muted")
        s.setAlignment(Qt.AlignCenter)
        self.b_file = QPushButton("Abrir arquivo…")
        self.b_file.setObjectName("primary")
        self.b_dir = QPushButton("Abrir pasta de WAVs…")
        v.addWidget(icon)
        v.addWidget(t)
        v.addWidget(s)
        v.addSpacing(10)
        v.addLayout(row(None, self.b_file, self.b_dir, None))

    def _set(self, on):
        self.setProperty("hover", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._set(True)

    def dragLeaveEvent(self, e):
        self._set(False)

    def dropEvent(self, e):
        self._set(False)
        urls = e.mimeData().urls()
        if urls:
            self.dropped.emit(urls[0].toLocalFile())


# ----------------------------------------------------------------------------
# Janela
# ----------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 820)
        self.setMinimumSize(1060, 700)
        self.settings = QSettings("XPSSampleStudio", "XPSSampleStudio")
        self.pool = QThreadPool.globalInstance()
        self.player = Player()
        self.inst: Optional[Instrument] = None
        self.path: Optional[str] = None
        self.rs = E.ReduceSettings(target=self.settings.value("target", "xps10x"))
        self.ps = E.ProcessSettings()
        self.entries: List[E.PlanEntry] = []
        self.selected = 0
        self._render_cache = {}
        self._replan_timer = QTimer(self, singleShot=True, interval=60, timeout=self.replan)

        root = QWidget()
        root.setObjectName("root")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._sidebar())
        self.stack = QStackedWidget()
        h.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        for page in (self._page_import(), self._page_map(), self._page_sound(), self._page_export()):
            page.setObjectName("page")
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.viewport().setObjectName("page")
            sc.setWidget(page)
            self.stack.addWidget(sc)
        self.go(0)
        self._update_enabled()

    # ------------------------------------------------------------------ sidebar
    def _sidebar(self):
        w = QWidget()
        w.setObjectName("sidebar")
        w.setFixedWidth(250)
        v = QVBoxLayout(w)
        v.setContentsMargins(18, 22, 18, 18)
        v.setSpacing(6)
        logo = QLabel("◆")
        logo.setStyleSheet(f"font-size:26px;color:{T.ACCENT};")
        v.addLayout(row(logo, self._brand(), None))
        v.addSpacing(24)
        self.nav = []
        grp = QButtonGroup(self)
        for i, (ico, txt) in enumerate([("①", "Importar"), ("②", "Mapa de teclas"), ("③", "Som e EQ"), ("④", "Exportar")]):
            b = QPushButton(f"  {ico}   {txt}")
            b.setObjectName("nav")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=i: self.go(k))
            grp.addButton(b)
            v.addWidget(b)
            self.nav.append(b)
        v.addStretch(1)
        v.addWidget(label("TECLADO DE DESTINO", "brandSub"))
        self.target_cb = QComboBox()
        for t in E.TARGETS.values():
            self.target_cb.addItem(t.name, t.key)
        self.target_cb.setCurrentIndex(max(0, self.target_cb.findData(self.rs.target)))
        self.target_cb.currentIndexChanged.connect(self._target_changed)
        v.addWidget(self.target_cb)
        v.addSpacing(8)
        v.addWidget(label("FORMATO DE SAÍDA", "brandSub"))
        self.mode_cb = QComboBox()
        self.mode_cb.addItem("Timbre .SVZ (pronto)", "svz")
        self.mode_cb.addItem("WAVs + guia", "wav")
        self.mode_cb.setToolTip("SVZ: um timbre com todas as amostras já mapeadas, importado por IMPORT TONE.\n"
                                "WAVs: amostras soltas para SAMPLE IMPORT, uma por parte.")
        self.mode_cb.setCurrentIndex(max(0, self.mode_cb.findData(self.settings.value("mode", "svz"))))
        self.mode_cb.currentIndexChanged.connect(self._target_changed)
        v.addWidget(self.mode_cb)
        v.addSpacing(14)
        v.addWidget(label("MEMÓRIA DE AMOSTRAS", "brandSub"))
        self.side_meter = MemoryMeter()
        v.addWidget(self.side_meter)
        self.side_mem = label("—", "muted")
        v.addWidget(self.side_mem)
        return w

    def _brand(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(label("XPS Sample Studio", "brand"))
        v.addWidget(label("NKI · SF2 → ROLAND XPS", "brandSub"))
        return w

    def go(self, i):
        if i > 0 and not self.inst:
            i = 0
        self.stack.setCurrentIndex(i)
        self.nav[i].setChecked(True)
        if i == 3:
            self._refresh_export()

    def _update_enabled(self):
        for b in self.nav[1:]:
            b.setEnabled(self.inst is not None)

    @property
    def target(self) -> E.Target:
        return E.TARGETS[self.rs.target]

    def _target_changed(self):
        self.rs.target = self.target_cb.currentData()
        self.settings.setValue("target", self.rs.target)
        t = self.target
        # o XPS-10 antigo não importa timbres .SVZ
        svz_ok = t.key == "xps10x"
        item = self.mode_cb.model().item(0)
        item.setEnabled(svz_ok)
        if not svz_ok and self.mode_cb.currentData() == "svz":
            self.mode_cb.blockSignals(True)
            self.mode_cb.setCurrentIndex(1)
            self.mode_cb.blockSignals(False)
        self.rs.mode = self.mode_cb.currentData()
        self.settings.setValue("mode", self.rs.mode)
        self.ps.rate = 48000 if self.rs.mode == "svz" else 44100
        self._render_cache.clear()
        mx = E.max_samples(self.rs)
        self.count_slider.setMaximum(mx)
        self.count_spin.setMaximum(mx)
        self.bits_cb.clear()
        for b in t.bits_options:
            self.bits_cb.addItem(f"{b} bits", b)
        self.bits_cb.setEnabled(self.rs.mode == "wav")
        if self.rs.mode == "svz":
            self.count_hint.setText(f"Modo SVZ: todas as amostras ficam dentro de um único timbre (até {mx}), "
                                    f"já mapeadas no teclado. Menos amostras = menos memória "
                                    f"(o {t.name} tem ~{t.total_seconds / 60:.0f} min).")
        else:
            self.count_hint.setText(f"Modo WAV: o {t.name} toca até {t.max_parts} partes por "
                                    f"{'Scene' if t.key == 'xps10x' else 'Performance'}; cada amostra alcança "
                                    f"{t.max_up // 12} oitavas acima da raiz.")
        if hasattr(self, "exp_sub"):
            self._refresh_export()
        self._schedule()

    # ------------------------------------------------------------------ página 1
    def _page_import(self):
        p = QWidget()
        v = QVBoxLayout(p)
        v.setContentsMargins(36, 30, 36, 30)
        v.setSpacing(18)
        v.addWidget(label("Importar instrumento", "h1"))
        v.addWidget(label("Converta instrumentos do Kontakt e SoundFonts em amostras prontas para o seu Roland XPS.", "muted"))
        self.drop = DropZone()
        self.drop.dropped.connect(self.open_path)
        self.drop.b_file.clicked.connect(self._ask_file)
        self.drop.b_dir.clicked.connect(self._ask_dir)
        v.addWidget(self.drop)

        self.load_bar = QProgressBar()
        self.load_bar.setRange(0, 0)
        self.load_bar.hide()
        v.addWidget(self.load_bar)

        h = QHBoxLayout()
        h.setSpacing(18)
        # info
        self.info_name = label("Nenhum instrumento carregado", "h2")
        self.info_meta = label("", "muted", wrap=True)
        s1, self.st_zones = stat("–", "zonas")
        s2, self.st_layers = stat("–", "camadas de velocidade")
        s3, self.st_samples = stat("–", "amostras distintas")
        self.info_notes = label("", None, wrap=True)
        self.info_notes.setStyleSheet(f"color:{T.WARN};")
        self.b_next1 = QPushButton("Continuar  →")
        self.b_next1.setObjectName("primary")
        self.b_next1.clicked.connect(lambda: self.go(1))
        self.b_next1.setEnabled(False)
        info = card(self.info_name, self.info_meta, row(s1, s2, s3, None, spacing=36), self.info_notes,
                    row(None, self.b_next1))
        h.addWidget(info, 3)
        # presets SF2
        self.preset_search = QLineEdit()
        self.preset_search.setPlaceholderText("Buscar preset…")
        self.preset_search.textChanged.connect(self._filter_presets)
        self.preset_list = QListWidget()
        self.preset_list.setMinimumHeight(220)
        self.preset_list.itemClicked.connect(self._preset_chosen)
        self.preset_card = card(label("Presets do SoundFont", "h2"), self.preset_search, self.preset_list)
        self.preset_card.hide()
        h.addWidget(self.preset_card, 2)
        v.addLayout(h)
        tip = label("Dica: instrumentos do Kontakt 5 a 8 podem ser convertidos para .sfz no ConvertWithMoss "
                    "(gratuito) e abertos aqui. Bibliotecas protegidas não podem ser convertidas.", "muted", wrap=True)
        v.addWidget(tip)
        v.addStretch(1)
        return p

    def _ask_file(self):
        d = self.settings.value("lastdir", os.path.expanduser("~"))
        f, _ = QFileDialog.getOpenFileName(self, "Abrir instrumento", d, FILTER)
        if f:
            self.open_path(f)

    def _ask_dir(self):
        d = self.settings.value("lastdir", os.path.expanduser("~"))
        f = QFileDialog.getExistingDirectory(self, "Pasta com WAVs", d)
        if f:
            self.open_path(f)

    def open_path(self, path: str, preset: Optional[int] = None):
        if not os.path.isdir(path) and not path.lower().endswith(SUPPORTED):
            self.error("Formato não suportado. Use .nki, .sf2, .sfz, WAV/AIFF ou uma pasta de WAVs.")
            return
        self.settings.setValue("lastdir", os.path.dirname(path))
        self.path = path
        if path.lower().endswith(".sf2") and preset is None:
            from ..core.sf2 import list_presets
            try:
                presets = list_presets(path)
            except ConversionError as e:
                self.error(str(e))
                return
            self.preset_list.clear()
            for idx, name, bank, prog in presets:
                it = QListWidgetItem(f"{bank:03d}:{prog + 1:03d}   {name}")
                it.setData(Qt.UserRole, idx)
                self.preset_list.addItem(it)
            self.preset_card.setVisible(len(presets) > 1)
            if presets:
                self.preset_list.setCurrentRow(0)
                preset = presets[0][0]
        else:
            if not path.lower().endswith(".sf2"):
                self.preset_card.hide()
        self.load_bar.show()
        job = Job(load_instrument, path, preset)
        job.s.done.connect(self._loaded)
        job.s.error.connect(self._load_failed)
        self.pool.start(job)

    def _filter_presets(self, text):
        for i in range(self.preset_list.count()):
            it = self.preset_list.item(i)
            it.setHidden(text.lower() not in it.text().lower())

    def _preset_chosen(self, it):
        self.open_path(self.path, it.data(Qt.UserRole))

    def _load_failed(self, msg):
        self.load_bar.hide()
        self.error(msg)

    def _loaded(self, inst: Instrument):
        self.load_bar.hide()
        self.inst = inst
        self._render_cache.clear()
        self.rs.locked = []
        self.info_name.setText(inst.name)
        rates = sorted({z.sample.rate for z in inst.zones})
        chans = sorted({z.sample.channels for z in inst.zones})
        self.info_meta.setText(f"{inst.source_format}  ·  {os.path.basename(inst.source_path)}  ·  "
                               f"{'/'.join(str(r) for r in rates)} Hz  ·  {'estéreo' if 2 in chans else 'mono'}")
        self.st_zones.setText(str(len(inst.zones)))
        layers = inst.velocity_layers()
        self.st_layers.setText(str(len(layers)))
        self.st_samples.setText(str(len({id(z.sample) for z in inst.zones})))
        self.info_notes.setText("\n".join("⚠ " + n for n in inst.notes[:6]))
        self.b_next1.setEnabled(True)
        # faixa de velocidade padrão: a camada mais forte que cubra 100
        self.vel_slider.setValue(100)
        roots = [z.root for z in inst.zones]
        lo, hi = min(roots), max(roots)
        self.key_lo.setCurrentIndex(min(36, lo))
        self.key_hi.setCurrentIndex(max(96, hi) if hi <= 108 else 96)
        n_cands = len(E.candidates(inst, 100))
        self.count_slider.setValue(min(E.max_samples(self.rs), 16, max(1, n_cands)))
        self.prefix.setText(E.safe_prefix(inst.name))
        self._update_enabled()
        self.replan()

    # ------------------------------------------------------------------ página 2
    def _page_map(self):
        p = QWidget()
        v = QVBoxLayout(p)
        v.setContentsMargins(36, 30, 36, 30)
        v.setSpacing(16)
        v.addWidget(label("Mapa de teclas e redução de amostras", "h1"))
        v.addWidget(label("Escolha quantas amostras usar. As notas retiradas são cobertas pelas amostras vizinhas, "
                          "esticadas pelo teclado.", "muted", wrap=True))

        self.count_slider = QSlider(Qt.Horizontal)
        self.count_slider.setRange(1, self.target.max_parts)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, self.target.max_parts)
        self.count_spin.setFixedWidth(60)
        self.count_slider.valueChanged.connect(self.count_spin.setValue)
        self.count_spin.valueChanged.connect(self.count_slider.setValue)
        self.count_spin.valueChanged.connect(self._schedule)
        self.count_hint = label("", "muted", wrap=True)

        self.vel_slider = QSlider(Qt.Horizontal)
        self.vel_slider.setRange(1, 127)
        self.vel_slider.setValue(100)
        self.vel_val = label("100", None)
        self.vel_val.setFixedWidth(34)
        self.vel_slider.valueChanged.connect(lambda x: (self.vel_val.setText(str(x)), self._schedule()))

        self.key_lo = note_combo(36)
        self.key_hi = note_combo(96)
        self.key_lo.currentIndexChanged.connect(self._schedule)
        self.key_hi.currentIndexChanged.connect(self._schedule)

        self.spread_btns = []
        seg = QHBoxLayout()
        seg.setSpacing(0)
        sg = QButtonGroup(self)
        for key, txt, tip in [("center", "Centralizada", "Cada amostra cobre metade do caminho até as vizinhas"),
                              ("up", "Para cima", "Cada amostra só é esticada para cima (do jeito do teclado)"),
                              ("down", "Para baixo", "Cada amostra só é esticada para baixo (soa mais natural em vozes)")]:
            b = QPushButton(txt)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setChecked(key == "center")
            b.clicked.connect(lambda _=False, k=key: (setattr(self.rs, "spread", k), self._schedule()))
            sg.addButton(b)
            seg.addWidget(b)
            self.spread_btns.append(b)

        g = QGridLayout()
        g.setHorizontalSpacing(18)
        g.setVerticalSpacing(10)
        g.addWidget(label("Número de amostras", "h2"), 0, 0)
        g.addLayout(row(self.count_slider, self.count_spin), 0, 1)
        g.addWidget(label("Camada de velocidade", "h2"), 1, 0)
        g.addLayout(row(self.vel_slider, self.vel_val), 1, 1)
        g.addWidget(label("Extensão do teclado", "h2"), 2, 0)
        g.addLayout(row(label("de", "muted"), self.key_lo, label("até", "muted"), self.key_hi, None,
                        label("Distribuição", "muted"), seg), 2, 1)
        g.setColumnStretch(1, 1)
        g.setColumnMinimumWidth(0, 180)
        top = card(g, self.count_hint)
        v.addWidget(top)

        self.kbd = KeyboardMap()
        self.kbd.keyClicked.connect(self._key_clicked)
        v.addWidget(card(row(label("Teclado", "h2"), None,
                             label("Clique numa tecla para ouvir como ela vai soar no XPS", "muted")), self.kbd))

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Parte", "Amostra", "Raiz", "Faixa", "Loop", "Duração", "Fixar"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setMinimumHeight(250)
        self.table.itemSelectionChanged.connect(self._row_selected)

        self.wave = Waveform()
        b_orig = QPushButton("▶  Original")
        b_proc = QPushButton("▶  Processado")
        b_proc.setObjectName("primary")
        b_stop = QPushButton("■")
        b_orig.clicked.connect(lambda: self._preview(False))
        b_proc.clicked.connect(lambda: self._preview(True))
        b_stop.clicked.connect(self.player.stop)
        right = card(label("Amostra selecionada", "h2"), self.wave, row(b_orig, b_proc, b_stop, None))
        h = QHBoxLayout()
        h.setSpacing(16)
        h.addWidget(card(label("Partes que serão criadas", "h2"), self.table), 3)
        h.addWidget(right, 2)
        v.addLayout(h)
        nxt = QPushButton("Ajustar o som  →")
        nxt.setObjectName("primary")
        nxt.clicked.connect(lambda: self.go(2))
        v.addLayout(row(None, nxt))
        self._target_changed_labels_later = True
        return p

    def _schedule(self, *_):
        self._replan_timer.start()

    def replan(self):
        if not self.inst or not hasattr(self, "table"):
            return
        self.rs.target_count = self.count_spin.value()
        self.rs.velocity = self.vel_slider.value()
        lo, hi = self.key_lo.currentData(), self.key_hi.currentData()
        self.rs.key_low, self.rs.key_high = min(lo, hi), max(lo, hi)
        self.entries = E.plan(self.inst, self.rs)
        self.selected = min(self.selected, len(self.entries) - 1)
        self._render_cache.clear()
        self._fill_table()
        self._update_keyboard()
        self._update_memory()

    def _fill_table(self):
        t = self.table
        t.blockSignals(True)
        t.setRowCount(len(self.entries))
        for i, e in enumerate(self.entries):
            z = e.zone
            col = T.PART_COLORS[i % len(T.PART_COLORS)]
            it = QTableWidgetItem(f"  {i + 1}")
            it.setForeground(Qt.black)
            it.setBackground(_qcolor(col))
            t.setItem(i, 0, it)
            name = z.sample.name + (f"   ⚠ {e.warning}" if e.warning else "")
            t.setItem(i, 1, QTableWidgetItem(name))
            t.setItem(i, 2, QTableWidgetItem(note_name(e.root)))
            t.setItem(i, 3, QTableWidgetItem(f"{note_name(e.lo)} – {note_name(e.hi)}"))
            t.setItem(i, 4, QTableWidgetItem("● sim" if z.loop_on and z.sample.loop_start is not None else "não"))
            t.setItem(i, 5, QTableWidgetItem(f"{self._est_seconds(e):.1f} s"))
            cb = QCheckBox()
            cb.setChecked(e.root in self.rs.locked)
            cb.setToolTip("Mantém esta amostra mesmo ao reduzir a quantidade")
            cb.toggled.connect(lambda on, r=e.root: self._lock(r, on))
            w = QWidget()
            l = QHBoxLayout(w)
            l.setContentsMargins(10, 0, 0, 0)
            l.addWidget(cb)
            t.setCellWidget(i, 6, w)
        t.blockSignals(False)
        if self.entries:
            t.selectRow(max(0, self.selected))
            self._row_selected()

    def _lock(self, root, on):
        if on and root not in self.rs.locked:
            self.rs.locked.append(root)
        elif not on and root in self.rs.locked:
            self.rs.locked.remove(root)

    def _update_keyboard(self):
        src = []
        if self.inst:
            for z in E.layer_zones(self.inst, self.rs.velocity):
                src.append((z.lo_key, z.hi_key, z.root))
        parts = [(e.lo, e.hi, e.root, T.PART_COLORS[i % len(T.PART_COLORS)]) for i, e in enumerate(self.entries)]
        self.kbd.set_data(src, parts, (self.rs.key_low, self.rs.key_high), self.selected)

    def _row_selected(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows or not self.entries:
            return
        self.selected = rows[0].row()
        e = self.entries[self.selected]
        smp = e.zone.sample
        loop = (smp.loop_start, smp.loop_end) if e.zone.loop_on and smp.loop_start is not None else None
        self.wave.set_audio(smp.data, loop, T.PART_COLORS[self.selected % len(T.PART_COLORS)],
                            f"{smp.name} · {smp.duration:.2f} s · {smp.rate} Hz · raiz {note_name(e.root)}")
        self._update_keyboard()

    # ------------------------------------------------------------------ áudio
    def _rendered(self, i) -> E.Rendered:
        if i not in self._render_cache:
            self._render_cache[i] = E.render_entry(self.entries[i], self.ps)
        return self._render_cache[i]

    def _preview(self, processed: bool, idx: Optional[int] = None):
        if not self.entries:
            return
        i = self.selected if idx is None else idx
        if processed:
            r = self._rendered(i)
            self.player.play(self._loop_for_preview(r.data, r.loop, r.rate), r.rate)
        else:
            s = self.entries[i].zone.sample
            self.player.play(s.data[: int(s.rate * 6)], s.rate)

    @staticmethod
    def _loop_for_preview(data, loop, rate, seconds=4.0):
        if not loop:
            return data[: int(rate * seconds)]
        out = [data]
        total = len(data)
        seg = data[loop[0]:loop[1]]
        while total < rate * seconds and len(seg) > 0:
            out.append(seg)
            total += len(seg)
        y = np.concatenate(out)[: int(rate * seconds)]
        return A.fade(y, rate, 0, 120)

    def _key_clicked(self, note: int):
        for i, e in enumerate(self.entries):
            if e.lo <= note <= e.hi:
                self.table.selectRow(i)
                r = self._rendered(i)
                semis = min(note - e.root, self.target.max_up)
                ratio = 2 ** (semis / 12.0)
                data = self._loop_for_preview(r.data, r.loop, r.rate, 3.0 * ratio)
                n = int(len(data) / ratio)
                if n > 16:
                    idx = np.minimum((np.arange(n) * ratio).astype(np.int64), len(data) - 1)
                    data = data[idx]
                self.player.play(data, r.rate)
                return

    # ------------------------------------------------------------------ página 3
    def _page_sound(self):
        p = QWidget()
        v = QVBoxLayout(p)
        v.setContentsMargins(36, 30, 36, 30)
        v.setSpacing(16)
        v.addWidget(label("Som, equalização e processamento", "h1"))
        v.addWidget(label("Arraste os pontos da curva para equalizar. Use a roda do mouse sobre um ponto para "
                          "mudar a largura (Q). Tudo é aplicado em todas as amostras.", "muted", wrap=True))
        self.eq = EQCurve(self.ps.eq)
        self.eq.changed.connect(self._eq_from_curve)
        self.eq_preset = QComboBox()
        self.eq_preset.addItems(list(EQ_PRESETS.keys()))
        self.eq_preset.currentTextChanged.connect(self._apply_eq_preset)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        for c, h in enumerate(["", "Banda", "Frequência (Hz)", "Ganho (dB)", "Q"]):
            grid.addWidget(label(h, "muted"), 0, c)
        names = {"lowcut": "Corta-graves", "lowshelf": "Graves", "peak": "Médios",
                 "highshelf": "Agudos", "highcut": "Corta-agudos"}
        self.band_ui = []
        for i, b in enumerate(self.ps.eq):
            en = QCheckBox()
            en.setChecked(b.enabled)
            fr = QSpinBox()
            fr.setRange(20, 20000)
            fr.setValue(int(b.freq))
            ga = QDoubleSpinBox()
            ga.setRange(-18, 18)
            ga.setSingleStep(0.5)
            ga.setValue(b.gain_db)
            ga.setEnabled(b.kind in ("peak", "lowshelf", "highshelf"))
            q = QDoubleSpinBox()
            q.setRange(0.2, 10)
            q.setSingleStep(0.1)
            q.setValue(b.q)
            dot = label(str(i + 1))
            dot.setFixedSize(24, 24)
            dot.setAlignment(Qt.AlignCenter)
            dot.setStyleSheet(f"background:{T.PART_COLORS[i]};color:#14151A;border-radius:12px;font-weight:700;")
            grid.addWidget(dot, i + 1, 0)
            grid.addLayout(row(en, label(names[b.kind])), i + 1, 1)
            grid.addWidget(fr, i + 1, 2)
            grid.addWidget(ga, i + 1, 3)
            grid.addWidget(q, i + 1, 4)
            for wdg, sig in ((en, en.toggled), (fr, fr.valueChanged), (ga, ga.valueChanged), (q, q.valueChanged)):
                sig.connect(self._eq_from_controls)
            self.band_ui.append((en, fr, ga, q))
        eq_card = card(row(label("Equalizador", "h2"), None, label("Predefinição", "muted"), self.eq_preset),
                       self.eq, grid)
        v.addWidget(eq_card)

        # processamento
        self.cb_mono = QCheckBox("Converter para mono (economiza memória)")
        self.cb_mono.setChecked(self.ps.mono)
        self.cb_norm = QCheckBox("Normalizar para")
        self.cb_norm.setChecked(self.ps.normalize)
        self.sp_norm = QDoubleSpinBox()
        self.sp_norm.setRange(-12, 0)
        self.sp_norm.setValue(self.ps.normalize_db)
        self.sp_norm.setSuffix(" dB")
        self.cb_group = QCheckBox("Mesmo ganho para todas (mantém o equilíbrio entre notas)")
        self.cb_group.setChecked(self.ps.normalize_group)
        self.cb_trim = QCheckBox("Remover silêncio do início")
        self.cb_trim.setChecked(self.ps.trim_start)
        self.sp_max = QDoubleSpinBox()
        self.sp_max.setRange(0.2, 180)
        self.sp_max.setValue(self.ps.max_seconds)
        self.sp_max.setSuffix(" s")
        self.sp_fade = QSpinBox()
        self.sp_fade.setRange(0, 2000)
        self.sp_fade.setValue(int(self.ps.fade_out_ms))
        self.sp_fade.setSuffix(" ms")
        self.sp_xf = QSpinBox()
        self.sp_xf.setRange(0, 500)
        self.sp_xf.setValue(int(self.ps.loop_xfade_ms))
        self.sp_xf.setSuffix(" ms")
        self.cb_cut = QCheckBox("Cortar o áudio no fim do loop (o XPS repete do loop até o fim)")
        self.cb_cut.setChecked(self.ps.cut_after_loop)
        for w in (self.cb_mono, self.cb_norm, self.cb_group, self.cb_trim, self.cb_cut):
            w.toggled.connect(self._proc_changed)
        for w in (self.sp_norm, self.sp_max, self.sp_fade, self.sp_xf):
            w.valueChanged.connect(self._proc_changed)
        g = QGridLayout()
        g.setHorizontalSpacing(18)
        g.setVerticalSpacing(10)
        g.addWidget(self.cb_mono, 0, 0)
        g.addLayout(row(self.cb_norm, self.sp_norm, None), 0, 1)
        g.addWidget(self.cb_trim, 1, 0)
        g.addWidget(self.cb_group, 1, 1)
        g.addLayout(row(label("Duração máxima (sem loop)"), self.sp_max, None), 2, 0)
        g.addLayout(row(label("Fade-out"), self.sp_fade, None), 2, 1)
        g.addLayout(row(label("Crossfade do loop"), self.sp_xf, None), 3, 0)
        g.addWidget(self.cb_cut, 3, 1)
        b_a = QPushButton("▶  Ouvir original")
        b_b = QPushButton("▶  Ouvir processado")
        b_b.setObjectName("primary")
        b_a.clicked.connect(lambda: self._preview(False))
        b_b.clicked.connect(lambda: self._preview(True))
        self.proc_wave = Waveform()
        self.proc_wave.setMinimumHeight(110)
        v.addWidget(card(label("Processamento", "h2"), g))
        v.addWidget(card(row(label("Comparar (amostra selecionada no mapa)", "h2"), None, b_a, b_b), self.proc_wave))
        nxt = QPushButton("Exportar  →")
        nxt.setObjectName("primary")
        nxt.clicked.connect(lambda: self.go(3))
        v.addLayout(row(None, nxt))
        return p

    def _apply_eq_preset(self, name):
        for b, (en, fr, ga, q) in zip(self.ps.eq, EQ_PRESETS[name]):
            b.enabled, b.freq, b.gain_db, b.q = en, float(fr), float(ga), float(q)
        self._sync_band_ui()
        self.eq.update()
        self._proc_changed()

    def _sync_band_ui(self):
        for b, (en, fr, ga, q) in zip(self.ps.eq, self.band_ui):
            for w in (en, fr, ga, q):
                w.blockSignals(True)
            en.setChecked(b.enabled)
            fr.setValue(int(round(b.freq)))
            ga.setValue(b.gain_db)
            q.setValue(b.q)
            for w in (en, fr, ga, q):
                w.blockSignals(False)

    def _eq_from_curve(self):
        self._sync_band_ui()
        self._proc_changed()

    def _eq_from_controls(self):
        for b, (en, fr, ga, q) in zip(self.ps.eq, self.band_ui):
            b.enabled, b.freq, b.gain_db, b.q = en.isChecked(), float(fr.value()), float(ga.value()), float(q.value())
        self.eq.update()
        self._proc_changed()

    def _proc_changed(self, *_):
        ps = self.ps
        ps.mono = self.cb_mono.isChecked()
        ps.normalize = self.cb_norm.isChecked()
        ps.normalize_db = self.sp_norm.value()
        ps.normalize_group = self.cb_group.isChecked()
        ps.trim_start = self.cb_trim.isChecked()
        ps.max_seconds = self.sp_max.value()
        ps.fade_out_ms = float(self.sp_fade.value())
        ps.loop_xfade_ms = float(self.sp_xf.value())
        ps.cut_after_loop = self.cb_cut.isChecked()
        self._render_cache.clear()
        self._update_memory()
        if hasattr(self, "_wave_timer"):
            self._wave_timer.start()
        else:
            self._wave_timer = QTimer(self, singleShot=True, interval=150, timeout=self._update_proc_wave)
            self._wave_timer.start()

    def _update_proc_wave(self):
        if self.entries and self.stack.currentIndex() == 2:
            r = self._rendered(self.selected)
            self.proc_wave.set_audio(r.data, r.loop, T.ACCENT, f"processado · {len(r.data) / r.rate:.2f} s · {r.rate / 1000:g} kHz")

    # ------------------------------------------------------------------ memória
    def _est_seconds(self, e: E.PlanEntry) -> float:
        s = e.zone.sample
        if e.zone.loop_on and s.loop_end is not None and self.ps.cut_after_loop:
            return s.loop_end / s.rate
        return min(s.duration, self.ps.max_seconds)

    def _update_memory(self):
        t = self.target
        used = sum(self._est_seconds(e) for e in self.entries)
        self.side_meter.set_values(used, t.total_seconds)
        self.side_mem.setText(f"{used:.1f} s de ~{t.total_seconds / 60:.0f} min  ({used / t.total_seconds * 100:.0f}%)")
        if hasattr(self, "exp_meter"):
            self.exp_meter.set_values(used, t.total_seconds)

    # ------------------------------------------------------------------ página 4
    def _page_export(self):
        p = QWidget()
        v = QVBoxLayout(p)
        v.setContentsMargins(36, 30, 36, 30)
        v.setSpacing(16)
        v.addWidget(label("Exportar para o teclado", "h1"))
        self.exp_sub = label("", "muted", wrap=True)
        v.addWidget(self.exp_sub)
        s1, self.ex_n = stat("–", "amostras")
        s2, self.ex_secs = stat("–", "segundos de áudio")
        s3, self.ex_pct = stat("–", "da memória de amostras")
        s4, self.ex_fmt = stat("–", "formato")
        self.exp_meter = MemoryMeter()
        v.addWidget(card(row(s1, s2, s3, s4, None, spacing=48), self.exp_meter))

        self.out_dir = QLineEdit(self.settings.value("outdir", os.path.join(os.path.expanduser("~"), "XPS Export")))
        b_dir = QPushButton("Escolher…")
        b_dir.clicked.connect(self._ask_out)
        self.prefix = QLineEdit()
        self.prefix.setMaxLength(12)
        self.prefix.setPlaceholderText("Nome curto (A–Z, 0–9)")
        self.bits_cb = QComboBox()
        for b in self.target.bits_options:
            self.bits_cb.addItem(f"{b} bits", b)
        g = QGridLayout()
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(10)
        g.addWidget(label("Pasta de destino"), 0, 0)
        g.addLayout(row(self.out_dir, b_dir), 0, 1)
        g.addWidget(label("Prefixo dos arquivos"), 1, 0)
        g.addLayout(row(self.prefix, label("Resolução"), self.bits_cb, None), 1, 1)
        self._ref_header = None
        self.ref_lbl = label("Cabeçalho padrão ZEN-Core (KY019)", "muted")
        b_ref = QPushButton("Usar SVZ exportado do meu teclado…")
        b_ref.setToolTip("Opcional: se o teclado recusar o arquivo, escolha um .SVZ exportado por ele "
                         "(UTILITY › EXPORT TONE). O programa copia a identificação do modelo desse arquivo.")
        b_ref.clicked.connect(self._ask_ref_svz)
        g.addWidget(label("Compatibilidade"), 2, 0)
        g.addLayout(row(b_ref, self.ref_lbl, None), 2, 1)
        g.setColumnStretch(1, 1)
        hint = label("SVZ: copie a pasta ROLAND gerada para a raiz do pendrive formatado no teclado e use "
                     "MENU › UTILITY › IMPORT TONE.  WAVs: copie a pasta IMPORT e siga o guia gerado junto.",
                     "muted", wrap=True)
        self.b_export = QPushButton("Exportar amostras")
        self.b_export.setObjectName("primary")
        self.b_export.clicked.connect(self.do_export)
        self.exp_bar = QProgressBar()
        self.exp_bar.hide()
        v.addWidget(card(label("Destino", "h2"), g, hint, row(self.exp_bar, None, self.b_export)))

        self.result_lbl = label("", "h2", wrap=True)
        self.b_open_dir = QPushButton("Abrir pasta")
        self.b_open_guide = QPushButton("Abrir guia de importação")
        self.b_open_guide.setObjectName("primary")
        self.b_open_dir.clicked.connect(lambda: self._open(self._last["import"]))
        self.b_open_guide.clicked.connect(lambda: self._open(self._last["html"]))
        self.result_card = card(self.result_lbl, row(self.b_open_dir, self.b_open_guide, None))
        self.result_card.hide()
        v.addWidget(self.result_card)
        v.addStretch(1)
        self._target_changed()
        return p

    def _refresh_export(self):
        t = self.target
        used = sum(self._est_seconds(e) for e in self.entries)
        if self.rs.mode == "svz":
            self.exp_sub.setText(f"{t.name}: um arquivo .SVZ com o timbre completo (amostras 48 kHz já mapeadas). "
                                 f"No teclado: MENU › UTILITY › IMPORT TONE.")
            self.b_export.setText("Exportar timbre .SVZ")
        else:
            self.exp_sub.setText(f"{t.name}: WAV 44,1 kHz, nomes alfanuméricos de até {t.name_len} caracteres, "
                                 f"pasta IMPORT do pendrive.")
            self.b_export.setText("Exportar amostras WAV")
        self.ex_n.setText(str(len(self.entries)))
        self.ex_secs.setText(f"{used:.1f}")
        self.ex_pct.setText(f"{used / t.total_seconds * 100:.0f}%")
        if self.rs.mode == "svz":
            self.ex_fmt.setText(f"SVZ · 48k · {'mono' if self.ps.mono else 'canais do original'}")
        else:
            self.ex_fmt.setText(f"WAV · 44,1k · {self.bits_cb.currentData() or 16}b")
        self._update_memory()

    def _ask_ref_svz(self):
        from ..core import svz as Z
        f, _ = QFileDialog.getOpenFileName(self, "SVZ exportado pelo teclado", self.out_dir.text(), "Roland SVZ (*.svz *.SVZ)")
        if not f:
            return
        try:
            with open(f, "rb") as fh:
                info = Z.read_container(fh.read(1 << 16))
        except Exception as e:  # noqa
            self.error(f"Não consegui ler esse SVZ: {e}")
            return
        self._ref_header = info["header"]
        self.ref_lbl.setText(f"Usando a identificação “{info['model']}” de {os.path.basename(f)}")

    def _ask_out(self):
        d = QFileDialog.getExistingDirectory(self, "Pasta de destino", self.out_dir.text())
        if d:
            self.out_dir.setText(d)

    def do_export(self):
        if not self.entries:
            return
        out = self.out_dir.text().strip()
        if not out:
            self.error("Escolha a pasta de destino.")
            return
        self.settings.setValue("outdir", out)
        self.b_export.setEnabled(False)
        self.exp_bar.setRange(0, len(self.entries))
        self.exp_bar.setValue(0)
        self.exp_bar.show()
        self.result_card.hide()
        inst, entries, ps = self.inst, list(self.entries), self.ps
        prefix, target, bits = self.prefix.text() or inst.name, self.rs.target, self.bits_cb.currentData() or 16
        mode, header = self.rs.mode, self._ref_header
        job = Job(lambda: None)

        def work():
            rend = E.render_all(entries, ps, lambda a, b: job.s.progress.emit(a, b))
            if mode == "svz":
                res = E.export_svz(inst, rend, out, prefix, target, header)
            else:
                res = E.export(inst, rend, out, prefix, target, bits)
            res["mode"] = mode
            res["n"] = len(rend)
            res["secs"] = E.total_seconds(rend)
            return res

        job.fn = work
        job.s.progress.connect(lambda a, b: self.exp_bar.setValue(a))
        job.s.done.connect(self._exported)
        job.s.error.connect(lambda m: (self.b_export.setEnabled(True), self.exp_bar.hide(), self.error(m)))
        self.pool.start(job)

    def _exported(self, res):
        self._last = res
        self.b_export.setEnabled(True)
        self.exp_bar.hide()
        if res.get("mode") == "svz":
            self.result_lbl.setText(f"✓  Timbre exportado com {res['n']} amostra(s) – {res['secs']:.1f} s no total.\n"
                                    f"{res['svz']}\nCopie a pasta ROLAND para a raiz do pendrive e use "
                                    f"MENU › UTILITY › IMPORT TONE.")
            self.b_open_guide.setText("Abrir instruções")
        else:
            self.result_lbl.setText(f"✓  {res['n']} amostra(s) exportada(s) – {res['secs']:.1f} s no total.\n"
                                    f"{res['import']}")
            self.b_open_guide.setText("Abrir guia de importação")
        self.result_card.show()

    def _open(self, path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ------------------------------------------------------------------
    def error(self, msg):
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QMessageBox.Warning)
        box.setText(msg)
        box.exec()


def _qcolor(hexstr):
    from PySide6.QtGui import QColor
    return QColor(hexstr)
