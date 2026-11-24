"""Criador de instrumentos: monte multisamples com camadas e exporte para XPS-10X, Kontakt, SF2 e SFZ."""
from __future__ import annotations

import copy
import os
from typing import List, Optional

import numpy as np
from PySide6.QtCore import QMimeData, QSettings, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QDrag
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QProgressBar, QPushButton,
                               QSizePolicy, QSlider, QSpinBox, QSplitter, QTabWidget, QToolButton, QVBoxLayout,
                               QWidget)

from ..core import audio as A
from ..core import editing as ED
from ..core import exporters as X
from ..core.model import ConversionError, Group, Instrument, Sample, Zone, note_name
from . import theme as T
from .builder_widgets import MIME_SAMPLES, EnvelopeEditor, MappingEditor, SampleEditor
from .common import Job, label, row
from .icons import icon, qsize

AUDIO_EXT = (".wav", ".aif", ".aiff", ".ncw")


def tool(ic: str, tip: str, text: str = "", checkable: bool = False):
    if text:
        b = QPushButton("  " + text)
        b.setIcon(icon(ic))
        b.setIconSize(qsize(15))
    else:
        b = QToolButton()
        b.setObjectName("tool")
        b.setIcon(icon(ic))
        b.setIconSize(qsize(16))
    b.setToolTip(tip)
    b.setCheckable(checkable)
    return b


def panel(*widgets, title: str = "", margins=12, spacing=8) -> QFrame:
    f = QFrame()
    f.setObjectName("panel")
    v = QVBoxLayout(f)
    v.setContentsMargins(margins, margins, margins, margins)
    v.setSpacing(spacing)
    if title:
        v.addWidget(label(title.upper(), "section"))
    for w in widgets:
        (v.addLayout if not isinstance(w, QWidget) else v.addWidget)(w)
    return f


class PoolList(QListWidget):
    """Lista de amostras arrastáveis para o mapa."""

    def __init__(self):
        super().__init__()
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.files_dropped = None

    def startDrag(self, actions):
        ids = [str(it.data(Qt.UserRole)) for it in self.selectedItems()]
        if not ids:
            return
        md = QMimeData()
        md.setData(MIME_SAMPLES, ",".join(ids).encode())
        d = QDrag(self)
        d.setMimeData(md)
        d.exec(Qt.CopyAction)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        if e.mimeData().hasUrls() and self.files_dropped:
            self.files_dropped([u.toLocalFile() for u in e.mimeData().urls()])
            e.acceptProposedAction()


class ExportDialog(QDialog):
    def __init__(self, parent, name: str, outdir: str, n_groups: int):
        super().__init__(parent)
        self.setWindowTitle("Exportar instrumento")
        self.setMinimumWidth(520)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 20)
        v.setSpacing(14)
        v.addWidget(label("Exportar instrumento", "h1"))
        v.addWidget(label("Escolha um ou mais formatos. Cada formato é gravado numa subpasta.", "muted", wrap=True))
        self.cb = {}
        specs = [
            ("svz", "Roland XPS-10X  ·  timbre .SVZ", "Importe por MENU › UTILITY › IMPORT TONE. Até 4 camadas (mono) ou 2 (estéreo).", True),
            ("nki", "Native Instruments Kontakt  ·  .nki", "Formato Kontakt 1 + pasta de WAVs – abre em qualquer versão do Kontakt.", True),
            ("sf2", "SoundFont 2  ·  .sf2", "Arquivo único com as amostras embutidas.", True),
            ("sfz", "SFZ  ·  .sfz + WAVs", "Formato aberto, aceito por muitos samplers.", False),
        ]
        box = QFrame()
        box.setObjectName("card")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(16, 14, 16, 14)
        for key, title, sub, on in specs:
            c = QCheckBox(title)
            c.setChecked(on)
            c.setStyleSheet("font-weight:600;")
            self.cb[key] = c
            bl.addWidget(c)
            s = label(sub, "muted", wrap=True)
            s.setContentsMargins(26, 0, 0, 6)
            bl.addWidget(s)
        v.addWidget(box)
        self.stereo = QCheckBox("SVZ estéreo (usa 2 partials por camada)")
        self.stereo.setChecked(False)
        if n_groups > 2:
            self.stereo.setToolTip("Com mais de 2 camadas o SVZ estéreo junta as camadas extras.")
        v.addWidget(self.stereo)
        self.name = QLineEdit(name)
        self.dir = QLineEdit(outdir)
        bdir = QPushButton("Escolher…")
        bdir.clicked.connect(self._pick)
        f = QFormLayout()
        f.addRow("Nome", self.name)
        f.addRow("Pasta", row(self.dir, bdir))
        v.addLayout(f)
        ok = QPushButton("Exportar")
        ok.setObjectName("primary")
        ok.setIcon(icon("export", "#170E0A"))
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        v.addLayout(row(None, cancel, ok))

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "Pasta de destino", self.dir.text())
        if d:
            self.dir.setText(d)

    def formats(self) -> List[str]:
        return [k for k, c in self.cb.items() if c.isChecked()]


class BuilderPage(QWidget):
    message = Signal(str)

    def __init__(self, player, error_cb, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.player = player
        self.error = error_cb
        self.settings = QSettings("XPSSampleStudio", "XPSSampleStudio")
        self.pool_samples: List[Sample] = []
        self.inst = Instrument("Novo instrumento")
        self.inst.groups = [Group("Camada 1")]
        self.undo_stack: list = []
        self.redo_stack: list = []
        self.project_path: Optional[str] = None
        self._loading = False
        self._build()
        self._refresh_all()

    # ------------------------------------------------------------------ layout
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # barra superior
        self.title = QLineEdit(self.inst.name)
        self.title.setObjectName("title")
        self.title.setMinimumWidth(260)
        self.title.editingFinished.connect(lambda: setattr(self.inst, "name", self.title.text().strip() or "Instrumento"))
        self.b_new = tool("file", "Novo instrumento", "Novo")
        self.b_open = tool("folder", "Abrir projeto ou instrumento (.xpsinst, .nki, .sf2, .sfz)", "Abrir")
        self.b_save = tool("save", "Salvar projeto (.xpsinst)", "Salvar")
        self.b_undo = tool("undo", "Desfazer (Ctrl+Z)")
        self.b_redo = tool("redo", "Refazer (Ctrl+Shift+Z)")
        self.b_export = QPushButton("  Exportar")
        self.b_export.setObjectName("primary")
        self.b_export.setIcon(icon("export", "#170E0A"))
        self.b_new.clicked.connect(self.new_instrument)
        self.b_open.clicked.connect(self.open_any)
        self.b_save.clicked.connect(self.save_project)
        self.b_undo.clicked.connect(self.undo)
        self.b_redo.clicked.connect(self.redo)
        self.b_export.clicked.connect(self.export)
        self.stats = label("", "muted")
        self.stats.setMinimumWidth(0)
        self.stats.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        tcol = QVBoxLayout()
        tcol.setSpacing(0)
        tcol.addWidget(self.title)
        tcol.addWidget(self.stats)
        self.stats.setContentsMargins(7, 0, 0, 0)
        self.stats.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        top = row(tcol, None, self.b_new, self.b_open, self.b_save, self.b_undo, self.b_redo,
                  self.b_export, spacing=6)
        root.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(8)
        split.setChildrenCollapsible(False)

        # --- coluna esquerda: amostras
        self.pool = PoolList()
        self.pool.files_dropped = self.add_files
        self.pool.itemDoubleClicked.connect(lambda it: self._audition_pool(it.data(Qt.UserRole)))
        b_add = tool("plus", "Adicionar arquivos WAV/AIFF/NCW")
        b_dir = tool("folder", "Adicionar uma pasta inteira de amostras")
        b_map = QPushButton("  Mapear no teclado")
        b_map.setIcon(icon("wand"))
        b_map.setToolTip("Cria zonas com as amostras selecionadas na camada atual: raiz pelo nome do arquivo "
                         "ou por detecção de nota, faixas distribuídas automaticamente.")
        b_rm = tool("trash", "Remover amostras selecionadas da lista")
        b_add.clicked.connect(self._ask_files)
        b_dir.clicked.connect(self._ask_dir)
        b_map.clicked.connect(self.map_selected)
        b_rm.clicked.connect(self.remove_pool)
        hint = label("Arraste amostras para o mapa ou use “Mapear”. Duplo clique para ouvir.", "muted", wrap=True)
        hint.setStyleSheet("font-size:11px;")
        left = panel(row(b_add, b_dir, None, b_rm, spacing=6), self.pool, b_map, hint, title="Amostras")
        left.setMinimumWidth(190)
        split.addWidget(left)

        # --- centro: mapa + editor de amostra
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(10)
        self.map = MappingEditor()
        self.map.selectionChanged.connect(self._selection_changed)
        self.map.aboutToEdit.connect(self.push_undo)
        self.map.zonesEdited.connect(self._zones_edited)
        self.map.keyPlayed.connect(self.play_key)
        self.map.samplesDropped.connect(self._dropped)
        self.vel_slider = QSlider(Qt.Horizontal)
        self.vel_slider.setRange(1, 127)
        self.vel_slider.setValue(100)
        self.vel_slider.setFixedWidth(90)
        self.vel_lbl = label("100", "muted")
        self.vel_slider.valueChanged.connect(lambda v: (setattr(self.map, "test_velocity", v), self.vel_lbl.setText(str(v))))
        self.only_group = tool("eye", "Destacar só a camada selecionada (as outras ficam esmaecidas)", checkable=True)
        self.only_group.setChecked(True)
        self.only_group.toggled.connect(self._group_selected)
        b_spread = tool("split", "Distribuir as faixas de teclas entre as raízes (zonas selecionadas ou camada atual)")
        b_spread.clicked.connect(self.spread_ranges)
        b_dup = tool("copy", "Duplicar zonas selecionadas")
        b_dup.clicked.connect(self.duplicate_zones)
        b_del = tool("trash", "Apagar zonas selecionadas (Delete)")
        b_del.clicked.connect(self.delete_zones)
        b_zin = tool("zoomin", "Aproximar teclado (Ctrl+roda)")
        b_zout = tool("zoomout", "Afastar teclado")
        b_zin.clicked.connect(lambda: self._zoom_keys(0.7))
        b_zout.clicked.connect(lambda: self._zoom_keys(1.4))
        self.vel_lbl.setFixedWidth(26)
        self.vel_slider.setToolTip("Velocidade usada ao clicar no teclado para ouvir")
        map_bar = row(label("MAPA", "section"), None, self.vel_slider, self.vel_lbl, self.only_group, b_zin, b_zout, b_spread, b_dup, b_del, spacing=4)
        mp = panel(map_bar, self.map, margins=10)
        cv.addWidget(mp, 3)

        self.wave = SampleEditor()
        self.wave.aboutToEdit.connect(self.push_undo)
        self.wave.markersChanged.connect(self._sample_edited)
        tb = [
            ("play", "Ouvir a zona na raiz", self.play_selected), ("normalize", "Normalizar (-1 dB)", self.op_normalize),
            ("reverse", "Inverter (tocar de trás para frente)", self.op_reverse),
            ("fadein", "Fade-in de 10 ms", self.op_fade_in), ("fadeout", "Fade-out de 80 ms", self.op_fade_out),
            ("scissors", "Remover silêncio do início e do fim", self.op_trim),
            ("tuner", "Detectar a nota (raiz e afinação)", self.op_detect),
            ("loop", "Encontrar um loop automaticamente", self.op_find_loop),
            ("sparkle", "Suavizar a emenda do loop (crossfade)", self.op_xfade),
            ("zoomout", "Ver a amostra inteira", lambda: self.wave.zoom_all()),
        ]
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addStretch(1)
        bar.setSpacing(4)
        for ic, tip, fn in tb:
            b = tool(ic, tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
        self.loop_cb = QCheckBox("Loop")
        self.loop_cb.toggled.connect(self._loop_toggled)
        bar.addWidget(self.loop_cb)
        cv.addWidget(panel(bar, self.wave, margins=10), 2)
        split.addWidget(center)

        # --- direita: inspetor
        tabs = QTabWidget()
        tabs.addTab(self._zone_tab(), "Zona")
        tabs.addTab(self._groups_tab(), "Camadas")
        self.tabs = tabs
        right = panel(tabs, margins=8)
        right.setMinimumWidth(260)
        split.addWidget(right)
        split.setSizes([220, 900, 290])
        root.addWidget(split, 1)

    def _spin(self, lo, hi, dec=0, step=1.0, suffix=""):
        s = QDoubleSpinBox() if dec else QSpinBox()
        s.setRange(lo, hi)
        if dec:
            s.setDecimals(dec)
            s.setSingleStep(step)
        if suffix:
            s.setSuffix(suffix)
        s.setKeyboardTracking(False)
        return s

    def _note_combo(self):
        c = QComboBox()
        for n in range(128):
            c.addItem(note_name(n), n)
        c.setMaxVisibleItems(16)
        return c

    def _zone_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 10, 6, 6)
        v.setSpacing(10)
        self.z_title = label("Nenhuma zona selecionada", "h2", wrap=True)
        v.addWidget(self.z_title)
        self.z_root = self._note_combo()
        self.z_lo = self._note_combo()
        self.z_hi = self._note_combo()
        self.z_vlo = self._spin(0, 127)
        self.z_vhi = self._spin(0, 127)
        self.z_tune = self._spin(-1200, 1200, 1, 1.0, " cents")
        self.z_gain = self._spin(-48, 24, 1, 0.5, " dB")
        self.z_pan = self._spin(-100, 100, 0, 1, "")
        self.z_group = QComboBox()
        self.z_reverse = QCheckBox("Tocar invertida")
        g = QGridLayout()
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(8)
        rows = [("Raiz", self.z_root), ("Tecla inferior", self.z_lo), ("Tecla superior", self.z_hi),
                ("Velocidade mín.", self.z_vlo), ("Velocidade máx.", self.z_vhi), ("Afinação", self.z_tune),
                ("Volume", self.z_gain), ("Pan (−100…100)", self.z_pan), ("Camada", self.z_group)]
        for i, (t, wdg) in enumerate(rows):
            g.addWidget(label(t, "muted"), i, 0)
            g.addWidget(wdg, i, 1)
        v.addLayout(g)
        v.addWidget(self.z_reverse)
        for wdg in (self.z_root, self.z_lo, self.z_hi, self.z_group):
            wdg.currentIndexChanged.connect(self._zone_fields_changed)
        for wdg in (self.z_vlo, self.z_vhi, self.z_tune, self.z_gain, self.z_pan):
            wdg.valueChanged.connect(self._zone_fields_changed)
        self.z_reverse.toggled.connect(self._zone_fields_changed)
        tip = label("Dica: arraste as zonas no mapa para mover; arraste as bordas para mudar a faixa. "
                    "Shift + arrastar também move a velocidade. Setas do teclado movem a seleção.", "muted", wrap=True)
        tip.setStyleSheet("font-size:11px;")
        v.addWidget(tip)
        v.addStretch(1)
        return w

    def _groups_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 10, 6, 6)
        v.setSpacing(10)
        self.g_list = QListWidget()
        self.g_list.setMaximumHeight(150)
        self.g_list.currentRowChanged.connect(self._group_selected)
        b_add = tool("plus", "Nova camada")
        b_dup = tool("copy", "Duplicar camada (com as zonas)")
        b_rm = tool("trash", "Apagar camada e suas zonas")
        b_split = QPushButton("  Dividir vel.")
        b_split.setIcon(icon("layers"))
        b_split.setToolTip("Divide 1–127 em faixas iguais, uma por camada, na ordem da lista (de cima = mais suave).")
        b_add.clicked.connect(self.add_group)
        b_dup.clicked.connect(self.duplicate_group)
        b_rm.clicked.connect(self.remove_group)
        b_split.clicked.connect(self.split_velocity)
        v.addWidget(self.g_list)
        v.addLayout(row(b_add, b_dup, b_rm, None, b_split, spacing=6))
        self.g_name = QLineEdit()
        self.g_mute = QCheckBox("Mudo")
        self.g_solo = QCheckBox("Solo")
        self.g_gain = self._spin(-48, 24, 1, 0.5, " dB")
        self.g_pan = self._spin(-100, 100)
        self.g_tune = self._spin(-2400, 2400, 1, 1.0, " cents")
        f = QGridLayout()
        f.setHorizontalSpacing(8)
        f.setVerticalSpacing(8)
        for i, (t, wdg) in enumerate([("Nome", self.g_name), ("Volume", self.g_gain), ("Pan", self.g_pan),
                                      ("Afinação", self.g_tune)]):
            f.addWidget(label(t, "muted"), i, 0)
            f.addWidget(wdg, i, 1)
        v.addLayout(f)
        v.addLayout(row(self.g_mute, self.g_solo, None))
        v.addWidget(label("ENVELOPE DE AMPLITUDE", "section"))
        self.env = EnvelopeEditor()
        self.env.aboutToEdit.connect(self.push_undo)
        self.env.changed.connect(self._env_changed)
        v.addWidget(self.env)
        self.e_a = self._spin(0, 20, 3, 0.01, " s")
        self.e_h = self._spin(0, 20, 3, 0.01, " s")
        self.e_d = self._spin(0, 30, 3, 0.05, " s")
        self.e_s = self._spin(0, 100, 0, 1, " %")
        self.e_r = self._spin(0, 30, 3, 0.05, " s")
        eg = QGridLayout()
        eg.setHorizontalSpacing(6)
        for i, (t, wdg) in enumerate([("A", self.e_a), ("H", self.e_h), ("D", self.e_d), ("S", self.e_s), ("R", self.e_r)]):
            eg.addWidget(label(t, "muted"), 0, i)
            eg.addWidget(wdg, 1, i)
        v.addLayout(eg)
        for wdg in (self.g_gain, self.g_pan, self.g_tune, self.e_a, self.e_h, self.e_d, self.e_s, self.e_r):
            wdg.valueChanged.connect(self._group_fields_changed)
        self.g_name.editingFinished.connect(self._group_fields_changed)
        self.g_mute.toggled.connect(self._group_fields_changed)
        self.g_solo.toggled.connect(self._group_fields_changed)
        v.addStretch(1)
        return w

    # ------------------------------------------------------------------ desfazer
    def _snapshot(self):
        return ([z.copy() for z in self.inst.zones], [g.copy() for g in self.inst.groups],
                list(self.pool_samples), {id(s): (s.loop_start, s.loop_end) for s in self.pool_samples})

    def push_undo(self):
        self.undo_stack.append(self._snapshot())
        self.undo_stack = self.undo_stack[-60:]
        self.redo_stack.clear()

    def _restore(self, snap):
        zones, groups, pool, loops = snap
        self.inst.zones, self.inst.groups, self.pool_samples = zones, groups, pool
        for s in pool:
            if id(s) in loops:
                s.loop_start, s.loop_end = loops[id(s)]
        self.map.selected = {i for i in self.map.selected if i < len(zones)}
        self._refresh_all()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self._snapshot())
            self._restore(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self._snapshot())
            self._restore(self.redo_stack.pop())

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Z and e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            (self.redo if e.modifiers() & Qt.ShiftModifier else self.undo)()
        elif e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_zones()
        elif e.key() == Qt.Key_Space:
            self.play_selected()
        else:
            super().keyPressEvent(e)

    # ------------------------------------------------------------------ atualização
    def _refresh_all(self):
        self._loading = True
        self.title.setText(self.inst.name)
        if not self.inst.groups:
            self.inst.groups = [Group("Camada 1")]
        self._refresh_pool()
        self._refresh_groups()
        self.map.set_instrument(self.inst)
        self._loading = False
        self._selection_changed()
        self._update_stats()

    def _update_stats(self):
        zs = self.inst.zones
        secs = sum(s.frames / s.rate for s in {id(z.sample): z.sample for z in zs}.values())
        self.stats.setText(f"{len(zs)} zona(s) · {len(self.inst.groups)} camada(s) · {secs:.1f} s de áudio")
        self.b_undo.setEnabled(bool(self.undo_stack))
        self.b_redo.setEnabled(bool(self.redo_stack))

    def _refresh_pool(self):
        self.pool.clear()
        used = {id(z.sample) for z in self.inst.zones}
        for i, s in enumerate(self.pool_samples):
            hint = getattr(s, "root_hint", None)
            it = QListWidgetItem(icon("play" if id(s) in used else "box", T.ACCENT if id(s) in used else T.MUTED, 14),
                                 f"{s.name}")
            it.setToolTip(f"{s.name}\n{s.frames / s.rate:.2f} s · {s.rate} Hz · {'estéreo' if s.channels > 1 else 'mono'}"
                          + (f"\nnota provável: {note_name(hint)}" if hint is not None else ""))
            it.setData(Qt.UserRole, i)
            self.pool.addItem(it)

    def _refresh_groups(self):
        cur = max(0, self.g_list.currentRow())
        self.g_list.blockSignals(True)
        self.g_list.clear()
        for i, g in enumerate(self.inst.groups):
            n = sum(1 for z in self.inst.zones if z.group == i)
            vr = [(z.lo_vel, z.hi_vel) for z in self.inst.zones if z.group == i]
            vtxt = f" · vel {min(a for a, _ in vr)}–{max(b for _, b in vr)}" if vr else ""
            flags = (" · mudo" if g.mute else "") + (" · solo" if g.solo else "")
            it = QListWidgetItem(icon("layers", T.GROUP_COLORS[(g.color or i) % len(T.GROUP_COLORS)], 14),
                                 f"{g.name}   ({n}){vtxt}{flags}")
            self.g_list.addItem(it)
        self.g_list.setCurrentRow(min(cur, len(self.inst.groups) - 1))
        self.g_list.blockSignals(False)
        self.z_group.blockSignals(True)
        self.z_group.clear()
        for g in self.inst.groups:
            self.z_group.addItem(g.name)
        self.z_group.blockSignals(False)
        self._group_selected()

    def current_group(self) -> int:
        return max(0, min(len(self.inst.groups) - 1, self.g_list.currentRow()))

    def _group_selected(self, *_):
        gi = self.current_group()
        self.map.active_group = gi if self.only_group.isChecked() else -1
        self.map.update()
        g = self.inst.groups[gi]
        self._loading = True
        self.g_name.setText(g.name)
        self.g_gain.setValue(g.gain_db)
        self.g_pan.setValue(int(round(g.pan * 100)))
        self.g_tune.setValue(g.tune_cents)
        self.g_mute.setChecked(g.mute)
        self.g_solo.setChecked(g.solo)
        self.e_a.setValue(g.attack)
        self.e_h.setValue(g.hold)
        self.e_d.setValue(g.decay)
        self.e_s.setValue(g.sustain * 100)
        self.e_r.setValue(g.release)
        self.env.set_group(g)
        self._loading = False

    def _group_fields_changed(self, *_):
        if self._loading:
            return
        self.push_undo()
        g = self.inst.groups[self.current_group()]
        g.name = self.g_name.text().strip() or g.name
        g.gain_db = self.g_gain.value()
        g.pan = self.g_pan.value() / 100.0
        g.tune_cents = self.g_tune.value()
        g.mute = self.g_mute.isChecked()
        g.solo = self.g_solo.isChecked()
        g.attack, g.hold, g.decay = self.e_a.value(), self.e_h.value(), self.e_d.value()
        g.sustain, g.release = self.e_s.value() / 100.0, self.e_r.value()
        self.env.update()
        self._loading = True
        cur = self.current_group()
        self._refresh_groups_labels()
        self.g_list.setCurrentRow(cur)
        self._loading = False
        self.map.update()

    def _refresh_groups_labels(self):
        for i, g in enumerate(self.inst.groups):
            it = self.g_list.item(i)
            if it:
                n = sum(1 for z in self.inst.zones if z.group == i)
                it.setText(f"{g.name}   ({n})" + (" · mudo" if g.mute else "") + (" · solo" if g.solo else ""))
        for i, g in enumerate(self.inst.groups):
            if i < self.z_group.count():
                self.z_group.setItemText(i, g.name)

    def _env_changed(self):
        g = self.inst.groups[self.current_group()]
        self._loading = True
        self.e_a.setValue(g.attack)
        self.e_d.setValue(g.decay)
        self.e_s.setValue(g.sustain * 100)
        self.e_r.setValue(g.release)
        self._loading = False

    def selected_zones(self) -> List[Zone]:
        return [self.inst.zones[i] for i in sorted(self.map.selected) if i < len(self.inst.zones)]

    def _selection_changed(self):
        zs = self.selected_zones()
        z = zs[0] if zs else None
        self.wave.set_zone(z)
        self._loading = True
        enabled = z is not None
        for wdg in (self.z_root, self.z_lo, self.z_hi, self.z_vlo, self.z_vhi, self.z_tune, self.z_gain,
                    self.z_pan, self.z_group, self.z_reverse, self.loop_cb):
            wdg.setEnabled(enabled)
        if z:
            self.z_title.setText(z.sample.name if len(zs) == 1 else f"{len(zs)} zonas selecionadas")
            self.z_root.setCurrentIndex(z.root)
            self.z_lo.setCurrentIndex(z.lo_key)
            self.z_hi.setCurrentIndex(z.hi_key)
            self.z_vlo.setValue(z.lo_vel)
            self.z_vhi.setValue(z.hi_vel)
            self.z_tune.setValue(z.tune_cents)
            self.z_gain.setValue(z.gain_db)
            self.z_pan.setValue(int(round(z.pan * 100)))
            self.z_group.setCurrentIndex(min(z.group, self.z_group.count() - 1))
            self.z_reverse.setChecked(z.reverse)
            self.loop_cb.setChecked(z.loop_on)
        else:
            self.z_title.setText("Nenhuma zona selecionada")
        self._loading = False

    def _zone_fields_changed(self, *_):
        if self._loading:
            return
        zs = self.selected_zones()
        if not zs:
            return
        self.push_undo()
        single = len(zs) == 1
        for z in zs:
            if single:
                z.root = self.z_root.currentData()
                z.lo_key = self.z_lo.currentData()
                z.hi_key = max(z.lo_key, self.z_hi.currentData())
            z.lo_vel = self.z_vlo.value()
            z.hi_vel = max(z.lo_vel, self.z_vhi.value())
            z.tune_cents = self.z_tune.value()
            z.gain_db = self.z_gain.value()
            z.pan = self.z_pan.value() / 100.0
            z.group = max(0, self.z_group.currentIndex())
            z.reverse = self.z_reverse.isChecked()
        self.map.update()
        self.wave.update()
        self._refresh_groups_labels()
        self._update_stats()

    def _zones_edited(self):
        self._selection_changed()
        self._refresh_groups_labels()
        self._update_stats()

    def _sample_edited(self):
        self._selection_changed()
        self.wave.refresh()
        self._update_stats()

    def _loop_toggled(self, on):
        if self._loading:
            return
        zs = self.selected_zones()
        if not zs:
            return
        self.push_undo()
        for z in zs:
            s = z.sample
            if on and s.loop_start is None:
                lp = ED.find_loop(s.data[z.start:z.play_end()], s.rate)
                if lp:
                    s.loop_start, s.loop_end = lp[0] + z.start, lp[1] + z.start
                else:
                    s.loop_start, s.loop_end = int(z.play_end() * 0.5), z.play_end()
            z.loop_on = on
        self.wave.update()

    # ------------------------------------------------------------------ amostras
    def _ask_files(self):
        d = self.settings.value("builder_dir", os.path.expanduser("~"))
        fs, _ = QFileDialog.getOpenFileNames(self, "Adicionar amostras", d, "Áudio (*.wav *.aif *.aiff *.ncw)")
        if fs:
            self.settings.setValue("builder_dir", os.path.dirname(fs[0]))
            self.add_files(fs)

    def _ask_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Pasta de amostras", self.settings.value("builder_dir", os.path.expanduser("~")))
        if d:
            self.settings.setValue("builder_dir", d)
            self.add_files([d])

    def add_files(self, paths: List[str]):
        files = []
        for p in paths:
            if os.path.isdir(p):
                for rootd, _, fns in os.walk(p):
                    files += [os.path.join(rootd, f) for f in sorted(fns) if f.lower().endswith(AUDIO_EXT)]
            elif p.lower().endswith(AUDIO_EXT):
                files.append(p)
            elif p.lower().endswith((".nki", ".sf2", ".sfz", ".xpsinst")):
                self.open_path(p)
                return
        if not files:
            return
        job = Job(self._read_files, files)
        job.s.done.connect(self._files_read)
        job.s.error.connect(self.error)
        QThreadPool.globalInstance().start(job)
        self.message.emit(f"Lendo {len(files)} arquivo(s)…")

    @staticmethod
    def _read_files(files):
        out, errors = [], []
        for f in files:
            try:
                s = A.read_audio(f)
                hint = ED.note_from_name(f)
                if hint is None:
                    lp = (s.loop_start, s.loop_end) if s.loop_start is not None else None
                    r = ED.detect_pitch(s.data, s.rate, lp)
                    if r and r[2] > 0.7:
                        hint = r[0]
                s.root_hint = hint
                s.vel_hint = ED.velocity_from_name(f)
                out.append(s)
            except ConversionError as e:
                errors.append(f"{os.path.basename(f)}: {e}")
        return out, errors

    def _files_read(self, res):
        samples, errors = res
        self.push_undo()
        self.pool_samples.extend(samples)
        self._refresh_pool()
        for i in range(len(self.pool_samples) - len(samples), len(self.pool_samples)):
            self.pool.item(i).setSelected(True)
        self.message.emit(f"{len(samples)} amostra(s) adicionada(s). Use “Mapear no teclado” ou arraste para o mapa.")
        if errors:
            self.error("Alguns arquivos não puderam ser lidos:\n" + "\n".join(errors[:10]))
        self._update_stats()

    def remove_pool(self):
        ids = sorted({it.data(Qt.UserRole) for it in self.pool.selectedItems()}, reverse=True)
        if not ids:
            return
        self.push_undo()
        drop = {id(self.pool_samples[i]) for i in ids}
        self.inst.zones = [z for z in self.inst.zones if id(z.sample) not in drop]
        for i in ids:
            del self.pool_samples[i]
        self.map.selected = set()
        self._refresh_all()

    def _make_zone(self, s: Sample, key: Optional[int] = None) -> Zone:
        root = key if key is not None else (getattr(s, "root_hint", None) or 60)
        z = Zone(s, root=root, lo_key=root, hi_key=root, lo_vel=1, hi_vel=127,
                 loop_on=s.loop_start is not None, group=self.current_group())
        vh = getattr(s, "vel_hint", None)
        others = [x for x in self.inst.zones if x.group == z.group]
        if others:
            z.lo_vel, z.hi_vel = min(o.lo_vel for o in others), max(o.hi_vel for o in others)
        _ = vh
        return z

    def map_selected(self):
        ids = sorted({it.data(Qt.UserRole) for it in self.pool.selectedItems()})
        if not ids:
            ids = [i for i, s in enumerate(self.pool_samples) if id(s) not in {id(z.sample) for z in self.inst.zones}]
        if not ids:
            self.message.emit("Selecione amostras na lista para mapear.")
            return
        self.push_undo()
        new = []
        for i in ids:
            s = self.pool_samples[i]
            z = self._make_zone(s)
            if getattr(s, "root_hint", None) is None:
                lp = (s.loop_start, s.loop_end) if s.loop_start is not None else None
                r = ED.detect_pitch(s.data, s.rate, lp)
                if r:
                    z.root = z.lo_key = z.hi_key = r[0]
                    z.tune_cents = -round(r[1], 1)
            new.append(z)
        self.inst.zones.extend(new)
        gi = self.current_group()
        ED.auto_ranges([z for z in self.inst.zones if z.group == gi and z.lo_vel == new[0].lo_vel])
        self.map.selected = {len(self.inst.zones) - len(new) + k for k in range(len(new))}
        self._refresh_all()
        self.message.emit(f"{len(new)} zona(s) criada(s) na camada “{self.inst.groups[gi].name}”.")

    def _dropped(self, ids, key, vel):
        if not ids:
            return
        self.push_undo()
        start = len(self.inst.zones)
        for k, i in enumerate(ids):
            if 0 <= i < len(self.pool_samples):
                s = self.pool_samples[i]
                kk = min(127, key + k)
                z = self._make_zone(s, kk)
                hint = getattr(s, "root_hint", None)
                if len(ids) > 1 and hint is not None:
                    z.root = z.lo_key = z.hi_key = hint
                self.inst.zones.append(z)
        self.map.selected = set(range(start, len(self.inst.zones)))
        self._refresh_all()

    def _audition_pool(self, i):
        if 0 <= i < len(self.pool_samples):
            s = self.pool_samples[i]
            self.player.play(s.data[: int(s.rate * 8)], s.rate)

    # ------------------------------------------------------------------ zonas
    def spread_ranges(self):
        zs = self.selected_zones()
        if len(zs) < 2:
            gi = self.current_group()
            zs = [z for z in self.inst.zones if z.group == gi]
        if not zs:
            return
        self.push_undo()
        ED.auto_ranges(zs)
        self.map.update()
        self._selection_changed()

    def duplicate_zones(self):
        zs = self.selected_zones()
        if not zs:
            return
        self.push_undo()
        start = len(self.inst.zones)
        self.inst.zones.extend(z.copy() for z in zs)
        self.map.selected = set(range(start, len(self.inst.zones)))
        self._refresh_all()

    def delete_zones(self):
        if not self.map.selected:
            return
        self.push_undo()
        self.inst.zones = [z for i, z in enumerate(self.inst.zones) if i not in self.map.selected]
        self.map.selected = set()
        self._refresh_all()

    def _zoom_keys(self, f):
        m = self.map
        span = m.k1 - m.k0
        new = int(max(12, min(127, span * f)))
        c = (m.k0 + m.k1) // 2
        m.k0 = max(0, min(127 - new, c - new // 2))
        m.k1 = m.k0 + new
        m.update()

    # ------------------------------------------------------------------ camadas
    def add_group(self):
        self.push_undo()
        n = len(self.inst.groups)
        self.inst.groups.append(Group(f"Camada {n + 1}", color=n))
        self._refresh_groups()
        self.g_list.setCurrentRow(n)

    def duplicate_group(self):
        gi = self.current_group()
        self.push_undo()
        g = self.inst.groups[gi].copy()
        g.name += " (cópia)"
        g.color = len(self.inst.groups)
        self.inst.groups.append(g)
        ni = len(self.inst.groups) - 1
        for z in [z for z in self.inst.zones if z.group == gi]:
            c = z.copy()
            c.group = ni
            self.inst.zones.append(c)
        self._refresh_all()
        self.g_list.setCurrentRow(ni)

    def remove_group(self):
        if len(self.inst.groups) <= 1:
            self.message.emit("O instrumento precisa de pelo menos uma camada.")
            return
        gi = self.current_group()
        self.push_undo()
        self.inst.zones = [z for z in self.inst.zones if z.group != gi]
        for z in self.inst.zones:
            if z.group > gi:
                z.group -= 1
        del self.inst.groups[gi]
        self.map.selected = set()
        self._refresh_all()

    def split_velocity(self):
        used = [i for i in range(len(self.inst.groups)) if any(z.group == i for z in self.inst.zones)]
        if len(used) < 2:
            self.message.emit("Crie pelo menos duas camadas com zonas para dividir a velocidade.")
            return
        self.push_undo()
        ED.split_velocity(self.inst, used)
        self._refresh_all()
        self.message.emit("Velocidade dividida entre as camadas: " +
                          ", ".join(f"{self.inst.groups[i].name}" for i in used) + ".")

    # ------------------------------------------------------------------ edição de amostra
    def _replace_sample(self, old: Sample, new: Sample):
        for z in self.inst.zones:
            if z.sample is old:
                z.sample = new
        for i, s in enumerate(self.pool_samples):
            if s is old:
                self.pool_samples[i] = new
        for attr in ("root_hint", "vel_hint"):
            if hasattr(old, attr):
                setattr(new, attr, getattr(old, attr))

    def _with_sample(self, fn, label_txt):
        zs = self.selected_zones()
        if not zs:
            self.message.emit("Selecione uma zona no mapa.")
            return
        self.push_undo()
        done = set()
        for z in zs:
            s = z.sample
            if id(s) in done:
                continue
            new = Sample(s.name, fn(s.data, s, z), s.rate, s.loop_start, s.loop_end)
            self._replace_sample(s, new)
            done.add(id(new))
        self.wave.set_zone(zs[0])
        self.wave.refresh()
        self.message.emit(label_txt)

    def op_normalize(self):
        self._with_sample(lambda d, s, z: ED.normalize(d, -1.0), "Amostra normalizada em -1 dB.")

    def op_fade_in(self):
        self._with_sample(lambda d, s, z: np.concatenate([d[:z.start], ED.fade_in(d[z.start:], int(s.rate * 0.01))]),
                          "Fade-in aplicado.")

    def op_fade_out(self):
        def f(d, s, z):
            e = z.play_end()
            return np.concatenate([ED.fade_out(d[:e], int(s.rate * 0.08)), d[e:]])
        self._with_sample(f, "Fade-out aplicado.")

    def op_reverse(self):
        zs = self.selected_zones()
        if not zs:
            return
        self.push_undo()
        for z in zs:
            z.reverse = not z.reverse
        self._selection_changed()
        self.wave.update()

    def op_trim(self):
        zs = self.selected_zones()
        if not zs:
            return
        self.push_undo()
        for z in zs:
            a, b = ED.silence_bounds(z.sample.data, -55)
            z.start = max(0, a - int(z.sample.rate * 0.002))
            z.end = min(z.sample.frames, b + int(z.sample.rate * 0.01))
            if z.sample.loop_end is not None and z.end < z.sample.loop_end:
                z.end = z.sample.loop_end
        self.wave.update()
        self.message.emit("Silêncio do início e do fim removido (início/fim ajustados).")

    def op_detect(self):
        zs = self.selected_zones()
        if not zs:
            return
        self.push_undo()
        res = []
        for z in zs:
            s = z.sample
            lp = (s.loop_start, s.loop_end) if s.loop_start is not None else None
            r = ED.detect_pitch(s.data[z.start:z.play_end()], s.rate, lp and (lp[0] - z.start, lp[1] - z.start))
            if r:
                shift = r[0] - z.root
                z.root = r[0]
                if len(zs) == 1 and z.lo_key == z.hi_key:
                    z.lo_key = z.hi_key = z.root
                elif shift and z.lo_key + shift >= 0 and z.hi_key + shift <= 127 and z.lo_key == z.hi_key:
                    z.lo_key += shift
                    z.hi_key += shift
                z.tune_cents = -round(r[1], 1)
                res.append(f"{note_name(r[0])} {r[1]:+.0f} c")
            else:
                res.append("?")
        self._selection_changed()
        self.map.update()
        self.message.emit("Nota detectada: " + ", ".join(res[:8]))

    def op_find_loop(self):
        zs = self.selected_zones()
        if not zs:
            return
        self.push_undo()
        n = 0
        for z in zs:
            s = z.sample
            lp = ED.find_loop(s.data[z.start:z.play_end()], s.rate)
            if lp:
                s.loop_start, s.loop_end = lp[0] + z.start, lp[1] + z.start
                z.loop_on = True
                n += 1
        self._selection_changed()
        self.wave.zoom_loop()
        self.message.emit(f"Loop encontrado em {n} de {len(zs)} zona(s)." if n else "Não encontrei um loop limpo.")

    def op_xfade(self):
        def f(d, s, z):
            if s.loop_start is None:
                return d
            return A.crossfade_loop(d, s.loop_start, s.loop_end, int(s.rate * 0.03))
        self._with_sample(f, "Crossfade de 30 ms aplicado na emenda do loop.")

    # ------------------------------------------------------------------ áudio
    def play_key(self, note: int, vel: int):
        zs = [z for z in self.inst.audible_zones() if z.lo_key <= note <= z.hi_key and z.lo_vel <= vel <= z.hi_vel]
        if not zs:
            self.message.emit(f"Nenhuma zona em {note_name(note)} com velocidade {vel}.")
            return
        mix = None
        for z in zs[:6]:
            y = ED.render_preview(self.inst, z, note, 44100, 1.0)
            if y.shape[1] == 1:
                y = np.repeat(y, 2, axis=1)
            mix = y if mix is None else mix[: len(y)] + y[: len(mix)]
        mix *= vel / 127.0
        self.player.play(np.clip(mix, -1, 1), 44100)
        idx = {i for i, z in enumerate(self.inst.zones) if z in zs}
        if idx and not (idx & self.map.selected):
            self.map.selected = {min(idx)}
            self._selection_changed()
            self.map.update()

    def play_selected(self):
        zs = self.selected_zones()
        if zs:
            z = zs[0]
            y = ED.render_preview(self.inst, z, z.root, 44100, 1.5)
            self.player.play(y, 44100)

    # ------------------------------------------------------------------ arquivos
    def new_instrument(self):
        if self.inst.zones and QMessageBox.question(self, "Novo instrumento", "Descartar o instrumento atual?") != QMessageBox.Yes:
            return
        self.inst = Instrument("Novo instrumento")
        self.inst.groups = [Group("Camada 1")]
        self.pool_samples = []
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.project_path = None
        self._refresh_all()

    def open_any(self):
        d = self.settings.value("builder_dir", os.path.expanduser("~"))
        f, _ = QFileDialog.getOpenFileName(self, "Abrir", d, "Projetos e instrumentos (*.xpsinst *.nki *.sf2 *.sfz)")
        if f:
            self.settings.setValue("builder_dir", os.path.dirname(f))
            self.open_path(f)

    def open_path(self, path: str):
        low = path.lower()
        try:
            if low.endswith(".xpsinst"):
                from ..core.project import load_project
                inst = load_project(path)
                self.project_path = path
            elif low.endswith(".sf2"):
                from ..core.sf2 import list_presets, load_sf2
                presets = list_presets(path)
                idx = presets[0][0]
                if len(presets) > 1:
                    names = [f"{b:03d}:{p + 1:03d}  {n}" for _, n, b, p in presets]
                    choice, ok = QInputDialog.getItem(self, "Escolha o preset", "Preset do SoundFont:", names, 0, False)
                    if not ok:
                        return
                    idx = presets[names.index(choice)][0]
                inst = load_sf2(path, idx)
            else:
                from ..core.loader import load_instrument
                inst = load_instrument(path)
        except ConversionError as e:
            self.error(str(e))
            return
        self.load_instrument(inst)

    def load_instrument(self, inst: Instrument):
        """Carrega um instrumento (de qualquer formato) para edição."""
        new = Instrument(inst.name, inst.source_path, inst.source_format)
        new.groups = [g.copy() for g in inst.groups] or [Group("Camada 1")]
        pool, seen = [], {}
        for z in inst.zones:
            s = z.sample
            if id(s) not in seen:
                seen[id(s)] = s
                pool.append(s)
            c = z.copy()
            c.group = min(c.group, len(new.groups) - 1)
            c.lo_vel = max(1, c.lo_vel)
            new.zones.append(c)
        self.inst = new
        self.pool_samples = pool
        self.undo_stack.clear()
        self.redo_stack.clear()
        keys = [z.root for z in new.zones] or [60]
        self.map.k0 = max(0, min(min(z.lo_key for z in new.zones) if new.zones else 21, 127 - 48))
        self.map.k1 = min(127, max(self.map.k0 + 48, max(z.hi_key for z in new.zones) if new.zones else 108))
        _ = keys
        self._refresh_all()
        self.message.emit(f"“{inst.name}” aberto no Criador: {len(new.zones)} zona(s), {len(new.groups)} camada(s).")

    def save_project(self):
        from ..core.project import save_project
        path = self.project_path
        if not path:
            d = self.settings.value("builder_dir", os.path.expanduser("~"))
            path, _ = QFileDialog.getSaveFileName(self, "Salvar projeto", os.path.join(d, self.inst.name + ".xpsinst"),
                                                  "Projeto XPS Sample Studio (*.xpsinst)")
            if not path:
                return
        self.inst.name = self.title.text().strip() or self.inst.name
        try:
            self.project_path = save_project(self.inst, path)
            self.message.emit(f"Projeto salvo: {self.project_path}")
        except Exception as e:  # noqa
            self.error(f"Não foi possível salvar: {e}")

    def export(self):
        if not self.inst.zones:
            self.error("Adicione e mapeie amostras antes de exportar.")
            return
        self.inst.name = self.title.text().strip() or self.inst.name
        d = self.settings.value("builder_out", os.path.join(os.path.expanduser("~"), "XPS Export"))
        dlg = ExportDialog(self, self.inst.name, d, len(self.inst.groups))
        if dlg.exec() != QDialog.Accepted:
            return
        fmts, out = dlg.formats(), dlg.dir.text().strip()
        name = dlg.name.text().strip() or self.inst.name
        if not fmts or not out:
            return
        self.settings.setValue("builder_out", out)
        stereo = dlg.stereo.isChecked()
        inst = Instrument(name, groups=[g.copy() for g in self.inst.groups])
        inst.zones = [z.copy() for z in self.inst.zones]
        job = Job(self._do_export, inst, fmts, out, stereo)
        job.s.done.connect(self._exported)
        job.s.error.connect(self.error)
        QThreadPool.globalInstance().start(job)
        self.message.emit("Exportando…")

    @staticmethod
    def _do_export(inst: Instrument, fmts: List[str], out: str, stereo: bool):
        import re
        base = re.sub(r"[^A-Za-z0-9 _\-]", "_", inst.name).strip() or "Instrumento"
        files, warns = [], []
        for f in fmts:
            if f == "svz":
                d = os.path.join(out, "XPS-10X", "ROLAND", "SOUND")
                os.makedirs(d, exist_ok=True)
                p, w = X.write_svz(inst, os.path.join(d, re.sub(r"[^A-Za-z0-9]", "", base.upper())[:12] or "TONE"), stereo)
                files.append(p)
                warns += w
            elif f == "nki":
                d = os.path.join(out, "Kontakt")
                os.makedirs(d, exist_ok=True)
                files.append(X.write_nki(inst, os.path.join(d, base)))
            elif f == "sf2":
                d = os.path.join(out, "SoundFont")
                os.makedirs(d, exist_ok=True)
                files.append(X.write_sf2(inst, os.path.join(d, base)))
            elif f == "sfz":
                d = os.path.join(out, "SFZ")
                os.makedirs(d, exist_ok=True)
                files.append(X.write_sfz(inst, os.path.join(d, base)))
        return out, files, warns

    def _exported(self, res):
        out, files, warns = res
        box = QMessageBox(self)
        box.setWindowTitle("Exportação concluída")
        box.setIcon(QMessageBox.Information)
        box.setText("Instrumento exportado:\n\n" + "\n".join("• " + os.path.relpath(f, out) for f in files)
                    + ("\n\nAvisos:\n" + "\n".join("• " + w for w in warns) if warns else "")
                    + "\n\nXPS-10X: copie a pasta ROLAND para a raiz do pendrive e use MENU › UTILITY › IMPORT TONE.")
        b_open = box.addButton("Abrir pasta", QMessageBox.AcceptRole)
        box.addButton("Fechar", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is b_open:
            QDesktopServices.openUrl(QUrl.fromLocalFile(out))
        self.message.emit(f"Exportado em {out}")
