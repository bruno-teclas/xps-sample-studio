"""Componentes de interface compartilhados."""
from __future__ import annotations

import os
import tempfile
import traceback

import numpy as np
from PySide6.QtCore import QObject, QRunnable, Qt, QUrl, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..core import audio as A
from ..core.model import ConversionError, note_name


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


