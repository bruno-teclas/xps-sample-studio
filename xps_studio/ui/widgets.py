"""Widgets desenhados à mão: teclado, forma de onda, curva de EQ, medidor."""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..core import audio as A
from ..core.model import note_name
from . import theme as T

BLACK = {1, 3, 6, 8, 10}


def _c(hexstr: str, alpha: int = 255) -> QColor:
    c = QColor(hexstr)
    c.setAlpha(alpha)
    return c


class KeyboardMap(QWidget):
    """Teclado com duas faixas: zonas originais e partes planejadas."""
    keyClicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.first, self.last = 21, 108
        self.source: List[Tuple[int, int, int]] = []          # (lo, hi, root)
        self.parts: List[Tuple[int, int, int, str]] = []      # (lo, hi, root, cor)
        self.play_range = (36, 96)
        self.selected = -1
        self.pressed: Optional[int] = None
        self.setMinimumHeight(190)
        self.setMouseTracking(True)
        self.hover: Optional[int] = None

    def set_data(self, source, parts, play_range, selected=-1):
        self.source, self.parts, self.play_range, self.selected = source, parts, play_range, selected
        self.update()

    # geometria -------------------------------------------------------
    def _whites(self):
        return [n for n in range(self.first, self.last + 1) if n % 12 not in BLACK]

    def _kw(self):
        return self.width() / max(1, len(self._whites()))

    def _x_of(self, note: int) -> Tuple[float, float]:
        kw = self._kw()
        whites_before = sum(1 for n in range(self.first, note) if n % 12 not in BLACK)
        if note % 12 in BLACK:
            return whites_before * kw - kw * 0.32, kw * 0.64
        return whites_before * kw, kw

    def _note_at(self, x: float, y: float) -> Optional[int]:
        kb_top = self.height() - 92
        if y < kb_top:
            # nas faixas: aproxima pela posição
            for n in range(self.first, self.last + 1):
                x0, w = self._x_of(n)
                if n % 12 not in BLACK and x0 <= x < x0 + w:
                    return n
            return None
        if y < kb_top + 56:
            for n in range(self.first, self.last + 1):
                if n % 12 in BLACK:
                    x0, w = self._x_of(n)
                    if x0 <= x < x0 + w:
                        return n
        for n in range(self.first, self.last + 1):
            if n % 12 not in BLACK:
                x0, w = self._x_of(n)
                if x0 <= x < x0 + w:
                    return n
        return None

    def _span(self, lo, hi):
        lo, hi = max(lo, self.first), min(hi, self.last)
        if lo > hi:
            return None
        x0, w0 = self._x_of(lo)
        x1, w1 = self._x_of(hi)
        return x0, x1 + w1 - x0

    # eventos ---------------------------------------------------------
    def mousePressEvent(self, e):
        n = self._note_at(e.position().x(), e.position().y())
        if n is not None:
            self.pressed = n
            self.update()
            self.keyClicked.emit(n)

    def mouseReleaseEvent(self, e):
        self.pressed = None
        self.update()

    def mouseMoveEvent(self, e):
        n = self._note_at(e.position().x(), e.position().y())
        if n != self.hover:
            self.hover = n
            self.setToolTip(note_name(n) if n is not None else "")
            self.update()

    # desenho ---------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        kb_top = H - 92
        lane1, lane2 = 8, 46
        f = QFont(self.font())
        f.setPointSizeF(8.5)
        p.setFont(f)

        # faixa jogável (teclado físico)
        sp = self._span(*self.play_range)
        if sp:
            p.fillRect(QRectF(sp[0], 0, sp[1], kb_top - 4), _c(T.ACCENT, 14))

        # zonas originais
        p.setPen(Qt.NoPen)
        for lo, hi, root in self.source:
            s = self._span(lo, hi)
            if not s:
                continue
            p.setBrush(_c("#5B6275", 110))
            p.drawRoundedRect(QRectF(s[0] + 1, lane1, max(2, s[1] - 2), 26), 4, 4)
            rx, rw = self._x_of(root)
            p.setBrush(_c("#C9CEDC", 200))
            p.drawEllipse(QPointF(rx + rw / 2, lane1 + 13), 2.5, 2.5)
        p.setPen(_c(T.MUTED))
        p.drawText(QRectF(6, lane1, 200, 26), Qt.AlignVCenter, "Original")

        # partes planejadas
        for i, (lo, hi, root, col) in enumerate(self.parts):
            s = self._span(lo, hi)
            if not s:
                continue
            c = QColor(col)
            g = QLinearGradient(0, lane2, 0, lane2 + 44)
            g.setColorAt(0, _c(col, 235 if i == self.selected else 190))
            g.setColorAt(1, _c(col, 140 if i == self.selected else 90))
            p.setBrush(QBrush(g))
            p.setPen(QPen(_c("#FFFFFF", 230), 2) if i == self.selected else Qt.NoPen)
            p.drawRoundedRect(QRectF(s[0] + 1, lane2, max(3, s[1] - 2), 44), 7, 7)
            rx, rw = self._x_of(root)
            p.setPen(Qt.NoPen)
            p.setBrush(_c("#FFFFFF"))
            p.drawEllipse(QPointF(rx + rw / 2, lane2 + 34), 4, 4)
            if s[1] > 34:
                p.setPen(_c("#14151A"))
                bf = QFont(f)
                bf.setBold(True)
                p.setFont(bf)
                p.drawText(QRectF(s[0] + 6, lane2 + 3, s[1] - 8, 20), Qt.AlignLeft | Qt.AlignVCenter, str(i + 1))
                p.setFont(f)
            _ = c

        # teclas brancas
        for n in self._whites():
            x, w = self._x_of(n)
            active = n == self.pressed
            p.setPen(QPen(_c("#2A2D38"), 1))
            col = T.ACCENT if active else ("#F2F3F6" if self.play_range[0] <= n <= self.play_range[1] else "#B9BDC9")
            p.setBrush(_c(col))
            p.drawRoundedRect(QRectF(x + 0.5, kb_top, w - 1, 90), 3, 3)
            if n % 12 == 0:
                p.setPen(_c("#6B7080"))
                p.drawText(QRectF(x, kb_top + 68, w, 18), Qt.AlignCenter, note_name(n))
        # teclas pretas
        for n in range(self.first, self.last + 1):
            if n % 12 in BLACK:
                x, w = self._x_of(n)
                p.setPen(Qt.NoPen)
                p.setBrush(_c(T.ACCENT if n == self.pressed else "#191B22"))
                p.drawRoundedRect(QRectF(x, kb_top, w, 56), 2, 2)
        p.end()


class Waveform(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data: Optional[np.ndarray] = None
        self.loop: Optional[Tuple[int, int]] = None
        self.color = T.ACCENT
        self.caption = ""
        self.setMinimumHeight(130)
        self._cache = None

    def set_audio(self, data: Optional[np.ndarray], loop=None, color=T.ACCENT, caption=""):
        self.data, self.loop, self.color, self.caption = data, loop, color, caption
        self._cache = None
        self.update()

    def _env(self, w):
        if self._cache is not None and self._cache[0] == w:
            return self._cache[1]
        x = self.data.mean(axis=1) if self.data.ndim > 1 else self.data
        n = len(x)
        bins = max(1, w)
        edges = np.linspace(0, n, bins + 1).astype(int)
        mn = np.empty(bins, np.float32)
        mx = np.empty(bins, np.float32)
        for i in range(bins):
            seg = x[edges[i]:max(edges[i] + 1, edges[i + 1])]
            mn[i], mx[i] = (seg.min(), seg.max()) if len(seg) else (0, 0)
        self._cache = (w, (mn, mx))
        return mn, mx

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        p.setPen(Qt.NoPen)
        p.setBrush(_c(T.BG))
        p.drawRoundedRect(QRectF(0, 0, W, H), 10, 10)
        mid = H / 2
        p.setPen(QPen(_c(T.BORDER), 1))
        p.drawLine(QPointF(8, mid), QPointF(W - 8, mid))
        if self.data is None or len(self.data) == 0:
            p.setPen(_c(T.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "Selecione uma amostra para ver a forma de onda")
            p.end()
            return
        n = len(self.data)
        if self.loop:
            x0 = self.loop[0] / n * W
            x1 = self.loop[1] / n * W
            p.fillRect(QRectF(x0, 0, x1 - x0, H), _c(T.OK, 28))
            p.setPen(QPen(_c(T.OK, 200), 1.5, Qt.DashLine))
            p.drawLine(QPointF(x0, 0), QPointF(x0, H))
            p.drawLine(QPointF(x1, 0), QPointF(x1, H))
            p.drawText(QRectF(x0 + 4, 4, 80, 16), Qt.AlignLeft, "loop")
        mn, mx = self._env(int(W))
        path = QPainterPath()
        amp = (H / 2 - 10)
        path.moveTo(0, mid - mx[0] * amp)
        for i in range(len(mx)):
            path.lineTo(i, mid - mx[i] * amp)
        for i in range(len(mn) - 1, -1, -1):
            path.lineTo(i, mid - mn[i] * amp)
        path.closeSubpath()
        g = QLinearGradient(0, 0, 0, H)
        g.setColorAt(0, _c(self.color, 230))
        g.setColorAt(0.5, _c(self.color, 150))
        g.setColorAt(1, _c(self.color, 230))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawPath(path)
        if self.caption:
            p.setPen(_c(T.MUTED))
            p.drawText(QRectF(10, H - 22, W - 20, 18), Qt.AlignRight | Qt.AlignVCenter, self.caption)
        p.end()


class EQCurve(QWidget):
    """Curva de EQ interativa: arraste os pontos (freq × ganho), roda do mouse muda o Q."""
    changed = Signal()
    FMIN, FMAX, GMAX = 20.0, 20000.0, 18.0

    def __init__(self, bands: List[A.EQBand], parent=None):
        super().__init__(parent)
        self.bands = bands
        self.drag = -1
        self.hover = -1
        self.setMinimumHeight(230)
        self.setMouseTracking(True)
        self.freqs = np.geomspace(self.FMIN, self.FMAX, 300)

    def _fx(self, f):
        return (math.log10(f) - math.log10(self.FMIN)) / (math.log10(self.FMAX) - math.log10(self.FMIN)) * (self.width() - 40) + 30

    def _xf(self, x):
        t = (x - 30) / max(1, self.width() - 40)
        return 10 ** (math.log10(self.FMIN) + t * (math.log10(self.FMAX) - math.log10(self.FMIN)))

    def _gy(self, g):
        h = self.height() - 30
        return 10 + (1 - (g + self.GMAX) / (2 * self.GMAX)) * h

    def _yg(self, y):
        h = self.height() - 30
        return (1 - (y - 10) / h) * 2 * self.GMAX - self.GMAX

    def _handle(self, b):
        y = self._gy(b.gain_db if b.kind in ("peak", "lowshelf", "highshelf") else 0)
        return QPointF(self._fx(b.freq), y)

    def _hit(self, pos):
        for i, b in enumerate(self.bands):
            if not b.enabled:
                continue
            h = self._handle(b)
            if (h - pos).manhattanLength() < 16:
                return i
        return -1

    def mousePressEvent(self, e):
        self.drag = self._hit(e.position())

    def mouseMoveEvent(self, e):
        if self.drag >= 0:
            b = self.bands[self.drag]
            b.freq = float(min(self.FMAX, max(self.FMIN, self._xf(e.position().x()))))
            if b.kind in ("peak", "lowshelf", "highshelf"):
                b.gain_db = float(round(min(self.GMAX, max(-self.GMAX, self._yg(e.position().y()))) * 2) / 2)
            self.changed.emit()
            self.update()
        else:
            h = self._hit(e.position())
            if h != self.hover:
                self.hover = h
                self.setCursor(Qt.PointingHandCursor if h >= 0 else Qt.ArrowCursor)
                self.update()

    def mouseReleaseEvent(self, e):
        self.drag = -1

    def wheelEvent(self, e):
        i = self._hit(e.position())
        if i >= 0:
            b = self.bands[i]
            b.q = float(min(10, max(0.2, b.q * (1.1 if e.angleDelta().y() > 0 else 1 / 1.1))))
            self.changed.emit()
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        p.setPen(Qt.NoPen)
        p.setBrush(_c(T.BG))
        p.drawRoundedRect(QRectF(0, 0, W, H), 12, 12)
        f = QFont(self.font())
        f.setPointSizeF(8)
        p.setFont(f)
        # grade
        for fr, lab in [(50, "50"), (100, "100"), (200, "200"), (500, "500"), (1000, "1k"),
                        (2000, "2k"), (5000, "5k"), (10000, "10k")]:
            x = self._fx(fr)
            p.setPen(QPen(_c(T.BORDER), 1))
            p.drawLine(QPointF(x, 10), QPointF(x, H - 20))
            p.setPen(_c(T.MUTED))
            p.drawText(QRectF(x - 20, H - 18, 40, 14), Qt.AlignCenter, lab)
        for g in (-12, -6, 0, 6, 12):
            y = self._gy(g)
            p.setPen(QPen(_c("#3A3F50" if g == 0 else T.BORDER), 1))
            p.drawLine(QPointF(30, y), QPointF(W - 10, y))
            p.setPen(_c(T.MUTED))
            p.drawText(QRectF(0, y - 7, 26, 14), Qt.AlignRight | Qt.AlignVCenter, f"{g:+d}" if g else "0")
        # curva
        resp = A.eq_response(self.bands, 44100, self.freqs)
        path = QPainterPath()
        fill = QPainterPath()
        y0 = self._gy(0)
        fill.moveTo(self._fx(self.freqs[0]), y0)
        for i, (fr, g) in enumerate(zip(self.freqs, resp)):
            pt = QPointF(self._fx(fr), self._gy(max(-self.GMAX - 2, min(self.GMAX + 2, g))))
            (path.moveTo if i == 0 else path.lineTo)(pt)
            fill.lineTo(pt)
        fill.lineTo(self._fx(self.freqs[-1]), y0)
        fill.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(_c(T.ACCENT, 40))
        p.drawPath(fill)
        p.setPen(QPen(_c(T.ACCENT), 2.4))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        # pontos
        for i, b in enumerate(self.bands):
            if not b.enabled:
                continue
            h = self._handle(b)
            col = T.PART_COLORS[i % len(T.PART_COLORS)]
            p.setPen(QPen(_c("#FFFFFF"), 2 if i in (self.hover, self.drag) else 1))
            p.setBrush(_c(col))
            p.drawEllipse(h, 7, 7)
            p.setPen(_c("#14151A"))
            p.drawText(QRectF(h.x() - 7, h.y() - 7, 14, 14), Qt.AlignCenter, str(i + 1))
        p.end()


class MemoryMeter(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.used = 0.0
        self.total = 720.0
        self.setMinimumHeight(10)
        self.setMaximumHeight(10)

    def set_values(self, used, total):
        self.used, self.total = used, total
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        p.setPen(Qt.NoPen)
        p.setBrush(_c(T.BORDER))
        p.drawRoundedRect(QRectF(0, 0, W, H), H / 2, H / 2)
        frac = min(1.0, self.used / self.total) if self.total else 0
        col = T.OK if frac < 0.6 else (T.WARN if frac < 0.9 else "#FF5C5C")
        if frac > 0:
            p.setBrush(_c(col))
            p.drawRoundedRect(QRectF(0, 0, max(H, W * frac), H), H / 2, H / 2)
        p.end()
