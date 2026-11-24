"""Widgets do Criador: mapa tecla × velocidade, editor de forma de onda e envelope ADSR."""
from __future__ import annotations

import math
from typing import List, Optional, Set, Tuple

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..core.model import Group, Instrument, Zone, note_name
from . import theme as T

BLACK = {1, 3, 6, 8, 10}
MIME_SAMPLES = "application/x-xps-samples"


def C(hexstr: str, alpha: int = 255) -> QColor:
    c = QColor(hexstr)
    c.setAlpha(alpha)
    return c


# ---------------------------------------------------------------------------
# Mapa de zonas (teclas × velocidade)
# ---------------------------------------------------------------------------

class MappingEditor(QWidget):
    selectionChanged = Signal()
    aboutToEdit = Signal()
    zonesEdited = Signal()
    keyPlayed = Signal(int, int)
    samplesDropped = Signal(list, int, int)   # índices do pool, tecla, velocidade

    LM, TM, KB = 34, 8, 54   # margem esquerda, topo, altura do teclado

    def __init__(self, parent=None):
        super().__init__(parent)
        self.inst: Optional[Instrument] = None
        self.selected: Set[int] = set()
        self.active_group = -1
        self.k0, self.k1 = 21, 108
        self.test_velocity = 100
        self._drag = None
        self._hover_key: Optional[int] = None
        self._pressed_key: Optional[int] = None
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(260)

    # --- geometria --------------------------------------------------------
    def kw(self) -> float:
        return (self.width() - self.LM - 6) / max(1, self.k1 - self.k0 + 1)

    def x_of(self, key: float) -> float:
        return self.LM + (key - self.k0) * self.kw()

    def key_at(self, x: float) -> int:
        return int(math.floor((x - self.LM) / self.kw() + self.k0))

    def grid_h(self) -> float:
        return self.height() - self.TM - self.KB - 4

    def y_of(self, vel: float) -> float:
        return self.TM + (1 - vel / 128.0) * self.grid_h()

    def vel_at(self, y: float) -> int:
        return int(round((1 - (y - self.TM) / self.grid_h()) * 128))

    def zone_rect(self, z: Zone) -> QRectF:
        x0, x1 = self.x_of(z.lo_key), self.x_of(z.hi_key + 1)
        y0, y1 = self.y_of(z.hi_vel + 1), self.y_of(max(0, z.lo_vel))
        return QRectF(x0, y0, x1 - x0, y1 - y0)

    def set_instrument(self, inst: Optional[Instrument]):
        self.inst = inst
        self.selected = set()
        self.update()

    def zone_at(self, pos: QPointF) -> Optional[int]:
        if not self.inst:
            return None
        hits = [i for i, z in enumerate(self.inst.zones) if self.zone_rect(z).adjusted(-1, -1, 1, 1).contains(pos)]
        if not hits:
            return None
        pref = [i for i in hits if i in self.selected] or \
            [i for i in hits if self.inst.zones[i].group == self.active_group] or hits
        return pref[-1]

    # --- eventos ----------------------------------------------------------
    def wheelEvent(self, e):
        d = e.angleDelta().y() or e.angleDelta().x()
        span = self.k1 - self.k0
        if e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            anchor = self.key_at(e.position().x())
            new = int(max(12, min(127, span * (0.85 if d > 0 else 1.18))))
            frac = (anchor - self.k0) / max(1, span)
            k0 = int(round(anchor - frac * new))
            self.k0 = max(0, min(127 - new, k0))
            self.k1 = self.k0 + new
        else:
            step = max(1, span // 12) * (-1 if d > 0 else 1)
            self.k0 = max(0, min(127 - span, self.k0 + step))
            self.k1 = self.k0 + span
        self.update()

    def mousePressEvent(self, e):
        p = e.position()
        self.setFocus()
        if p.y() > self.height() - self.KB:
            k = self.key_at(p.x())
            if 0 <= k <= 127:
                self._pressed_key = k
                self.update()
                self.keyPlayed.emit(k, self.test_velocity)
            return
        i = self.zone_at(p)
        if i is None:
            if not (e.modifiers() & Qt.ShiftModifier):
                self.selected = set()
                self.selectionChanged.emit()
            self.update()
            return
        if e.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier | Qt.MetaModifier):
            self.selected ^= {i}
        elif i not in self.selected:
            self.selected = {i}
        self.selectionChanged.emit()
        r = self.zone_rect(self.inst.zones[i])
        edge = 6
        mode = "move"
        if abs(p.x() - r.left()) < edge:
            mode = "left"
        elif abs(p.x() - r.right()) < edge:
            mode = "right"
        elif abs(p.y() - r.top()) < edge:
            mode = "top"
        elif abs(p.y() - r.bottom()) < edge:
            mode = "bottom"
        if e.button() == Qt.LeftButton:
            self.aboutToEdit.emit()
            self._drag = dict(mode=mode, key=self.key_at(p.x()), vel=self.vel_at(p.y()), moved=False,
                              orig={j: (z.lo_key, z.hi_key, z.lo_vel, z.hi_vel, z.root)
                                    for j, z in enumerate(self.inst.zones) if j in self.selected})
        self.update()

    def mouseMoveEvent(self, e):
        p = e.position()
        if self._drag and self.inst:
            dk = self.key_at(p.x()) - self._drag["key"]
            dv = self.vel_at(p.y()) - self._drag["vel"]
            m = self._drag["mode"]
            for j, (lo, hi, vlo, vhi, root) in self._drag["orig"].items():
                z = self.inst.zones[j]
                if m == "move":
                    dk2 = max(-lo, min(127 - hi, dk))
                    dv2 = max(-vlo, min(127 - vhi, dv)) if (e.modifiers() & Qt.ShiftModifier) else 0
                    z.lo_key, z.hi_key, z.root = lo + dk2, hi + dk2, max(0, min(127, root + dk2))
                    z.lo_vel, z.hi_vel = vlo + dv2, vhi + dv2
                elif m == "left":
                    z.lo_key = max(0, min(hi, lo + dk))
                elif m == "right":
                    z.hi_key = min(127, max(lo, hi + dk))
                elif m == "top":
                    z.hi_vel = min(127, max(vlo, vhi + dv))
                elif m == "bottom":
                    z.lo_vel = max(0, min(vhi, vlo + dv))
            self._drag["moved"] = True
            self.update()
            return
        # cursor
        cur = Qt.ArrowCursor
        i = self.zone_at(p)
        if i is not None and p.y() < self.height() - self.KB:
            r = self.zone_rect(self.inst.zones[i])
            if abs(p.x() - r.left()) < 6 or abs(p.x() - r.right()) < 6:
                cur = Qt.SizeHorCursor
            elif abs(p.y() - r.top()) < 6 or abs(p.y() - r.bottom()) < 6:
                cur = Qt.SizeVerCursor
            else:
                cur = Qt.OpenHandCursor
            z = self.inst.zones[i]
            self.setToolTip(f"{z.sample.name}\n{note_name(z.lo_key)}–{note_name(z.hi_key)} · raiz {note_name(z.root)}"
                            f" · vel {z.lo_vel}–{z.hi_vel}")
        elif p.y() > self.height() - self.KB:
            k = self.key_at(p.x())
            self.setToolTip(note_name(k) if 0 <= k <= 127 else "")
        else:
            self.setToolTip("")
        self.setCursor(cur)
        k = self.key_at(p.x())
        if k != self._hover_key:
            self._hover_key = k
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag:
            moved = self._drag["moved"]
            self._drag = None
            if moved:
                self.zonesEdited.emit()
        self._pressed_key = None
        self.update()

    def leaveEvent(self, e):
        self._hover_key = None
        self.update()

    def keyPressEvent(self, e):
        if not self.inst or not self.selected:
            return
        if e.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            self.aboutToEdit.emit()
            dk = -1 if e.key() == Qt.Key_Left else 1 if e.key() == Qt.Key_Right else 0
            dv = 1 if e.key() == Qt.Key_Up else -1 if e.key() == Qt.Key_Down else 0
            for j in self.selected:
                z = self.inst.zones[j]
                if 0 <= z.lo_key + dk and z.hi_key + dk <= 127:
                    z.lo_key += dk
                    z.hi_key += dk
                    z.root = max(0, min(127, z.root + dk))
                if 0 <= z.lo_vel + dv and z.hi_vel + dv <= 127:
                    z.lo_vel += dv
                    z.hi_vel += dv
            self.zonesEdited.emit()
            self.update()

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_SAMPLES):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME_SAMPLES):
            self._hover_key = self.key_at(e.position().x())
            self.update()
            e.acceptProposedAction()

    def dropEvent(self, e):
        raw = bytes(e.mimeData().data(MIME_SAMPLES)).decode()
        ids = [int(x) for x in raw.split(",") if x.strip().isdigit()]
        k = max(0, min(127, self.key_at(e.position().x())))
        v = max(1, min(127, self.vel_at(e.position().y())))
        self.samplesDropped.emit(ids, k, v)
        e.acceptProposedAction()

    # --- desenho ----------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), C(T.PANEL))
        gy0, gy1 = self.TM, self.TM + self.grid_h()
        f = QFont(self.font())
        f.setPointSizeF(8)
        p.setFont(f)
        # colunas das teclas pretas e oitavas
        kw = self.kw()
        for k in range(self.k0, self.k1 + 1):
            x = self.x_of(k)
            if k % 12 in BLACK:
                p.fillRect(QRectF(x, gy0, kw, gy1 - gy0), C("#111318"))
            if k % 12 == 0:
                p.setPen(QPen(C(T.BORDER), 1))
                p.drawLine(QPointF(x, gy0), QPointF(x, gy1))
        if self._hover_key is not None and self.k0 <= self._hover_key <= self.k1:
            p.fillRect(QRectF(self.x_of(self._hover_key), gy0, kw, gy1 - gy0), C(T.TEXT, 10))
        for v in (32, 64, 96, 127):
            y = self.y_of(v)
            p.setPen(QPen(C(T.BORDER_SOFT), 1, Qt.DashLine))
            p.drawLine(QPointF(self.LM, y), QPointF(W - 6, y))
            p.setPen(C(T.FAINT))
            p.drawText(QRectF(0, y - 7, self.LM - 6, 14), Qt.AlignRight | Qt.AlignVCenter, str(v))
        p.save()
        p.rotate(-90)
        p.setPen(C(T.FAINT))
        p.drawText(QRectF(-gy1, 0, gy1 - gy0, 12), Qt.AlignCenter, "")
        p.restore()
        # zonas
        if self.inst:
            order = sorted(range(len(self.inst.zones)),
                           key=lambda i: (i in self.selected, self.inst.zones[i].group == self.active_group))
            solo = any(g.solo for g in self.inst.groups)
            for i in order:
                z = self.inst.zones[i]
                g = self.inst.group_of(z)
                col = T.GROUP_COLORS[(g.color if g.color else z.group) % len(T.GROUP_COLORS)]
                r = self.zone_rect(z)
                if r.right() < self.LM or r.left() > W:
                    continue
                dim = (self.active_group >= 0 and z.group != self.active_group) or g.mute or (solo and not g.solo)
                sel = i in self.selected
                grad = QLinearGradient(r.topLeft(), r.bottomLeft())
                a = 70 if dim else (215 if sel else 150)
                grad.setColorAt(0, C(col, a))
                grad.setColorAt(1, C(col, int(a * 0.6)))
                p.setBrush(grad)
                p.setPen(QPen(C("#FFFFFF", 235), 1.6) if sel else QPen(C(col, 200 if not dim else 90), 1))
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
                # raiz
                if self.k0 <= z.root <= self.k1:
                    rx = self.x_of(z.root) + kw / 2
                    p.setPen(Qt.NoPen)
                    p.setBrush(C("#FFFFFF", 230 if not dim else 110))
                    tri = QPainterPath()
                    tri.moveTo(rx - 4, r.bottom() - 1)
                    tri.lineTo(rx + 4, r.bottom() - 1)
                    tri.lineTo(rx, r.bottom() - 7)
                    tri.closeSubpath()
                    p.drawPath(tri)
                if r.width() > 46 and r.height() > 16:
                    p.setPen(C("#0E0F13" if not dim else T.TEXT, 230))
                    p.drawText(r.adjusted(5, 2, -4, -2), Qt.AlignLeft | Qt.AlignTop, z.sample.name)
        # teclado
        ky = H - self.KB
        for k in range(self.k0, self.k1 + 1):
            if k % 12 in BLACK:
                continue
            x = self.x_of(k)
            w = kw
            if (k + 1) % 12 in BLACK:
                w += kw / 2
            if k % 12 not in (0, 5) and (k - 1) % 12 in BLACK:
                x -= kw / 2
                w += kw / 2
            pressed = k == self._pressed_key
            p.setPen(QPen(C("#1B1E25"), 1))
            p.setBrush(C(T.ACCENT if pressed else "#E9EBF0"))
            p.drawRoundedRect(QRectF(x + 0.5, ky, w - 1, self.KB - 2), 3, 3)
            if k % 12 == 0 and w > 14:
                p.setPen(C("#6B7080"))
                p.drawText(QRectF(x, ky + self.KB - 18, w, 14), Qt.AlignCenter, note_name(k))
        for k in range(self.k0, self.k1 + 1):
            if k % 12 in BLACK:
                x = self.x_of(k)
                p.setPen(Qt.NoPen)
                p.setBrush(C(T.ACCENT if k == self._pressed_key else "#15171C"))
                p.drawRoundedRect(QRectF(x + 1, ky, kw - 2, self.KB * 0.6), 2, 2)
        p.end()


# ---------------------------------------------------------------------------
# Editor de forma de onda
# ---------------------------------------------------------------------------

class SampleEditor(QWidget):
    aboutToEdit = Signal()
    markersChanged = Signal()

    MARKS = ("start", "end", "loop_start", "loop_end")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.zone: Optional[Zone] = None
        self.v0, self.v1 = 0, 1
        self._drag: Optional[str] = None
        self._env = None
        self.setMouseTracking(True)
        self.setMinimumHeight(150)

    def set_zone(self, z: Optional[Zone]):
        changed = z is None or self.zone is None or z.sample is not self.zone.sample
        self.zone = z
        if changed:
            self._env = None
            self.v0, self.v1 = 0, (z.sample.frames if z else 1)
        self.update()

    def refresh(self):
        self._env = None
        if self.zone:
            self.v1 = min(self.v1, self.zone.sample.frames)
            if self.v1 - self.v0 < 16:
                self.v0, self.v1 = 0, self.zone.sample.frames
        self.update()

    def zoom_all(self):
        if self.zone:
            self.v0, self.v1 = 0, self.zone.sample.frames
            self._env = None
            self.update()

    def zoom_loop(self):
        z = self.zone
        if z and z.sample.loop_start is not None:
            pad = max(200, (z.sample.loop_end - z.sample.loop_start) // 6)
            self.v0 = max(0, z.sample.loop_start - pad)
            self.v1 = min(z.sample.frames, z.sample.loop_end + pad)
            self._env = None
            self.update()

    # posições
    def fx(self, frame: float) -> float:
        return 10 + (frame - self.v0) / max(1, self.v1 - self.v0) * (self.width() - 20)

    def xf(self, x: float) -> int:
        return int(self.v0 + (x - 10) / max(1, self.width() - 20) * (self.v1 - self.v0))

    def marker_pos(self, m: str) -> Optional[int]:
        z = self.zone
        if not z:
            return None
        s = z.sample
        if m == "start":
            return z.start
        if m == "end":
            return z.play_end()
        if m == "loop_start":
            return s.loop_start
        return s.loop_end

    def wheelEvent(self, e):
        if not self.zone:
            return
        n = self.zone.sample.frames
        d = e.angleDelta().y() or e.angleDelta().x()
        span = self.v1 - self.v0
        if e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier) or abs(e.angleDelta().y()) >= abs(e.angleDelta().x()):
            anchor = self.xf(e.position().x())
            new = int(max(64, min(n, span * (0.8 if d > 0 else 1.25))))
            frac = (anchor - self.v0) / max(1, span)
            v0 = int(anchor - frac * new)
            self.v0 = max(0, min(n - new, v0))
            self.v1 = self.v0 + new
        else:
            step = int(span * 0.1) * (-1 if d > 0 else 1)
            self.v0 = max(0, min(n - span, self.v0 + step))
            self.v1 = self.v0 + span
        self._env = None
        self.update()

    def mousePressEvent(self, e):
        if not self.zone:
            return
        x = e.position().x()
        best, bd = None, 8.0
        for m in self.MARKS:
            pos = self.marker_pos(m)
            if pos is None:
                continue
            d = abs(self.fx(pos) - x)
            if d < bd:
                best, bd = m, d
        if best:
            self.aboutToEdit.emit()
            self._drag = best

    def mouseMoveEvent(self, e):
        z = self.zone
        if not z:
            return
        x = e.position().x()
        if self._drag:
            f = max(0, min(z.sample.frames, self.xf(x)))
            s = z.sample
            m = self._drag
            if m == "start":
                z.start = max(0, min(f, z.play_end() - 16))
            elif m == "end":
                z.end = max(z.start + 16, f)
            elif m == "loop_start":
                if s.loop_end is None:
                    s.loop_end = z.play_end()
                s.loop_start = max(0, min(f, s.loop_end - 16))
                z.loop_on = True
            elif m == "loop_end":
                if s.loop_start is None:
                    s.loop_start = z.start
                s.loop_end = max(s.loop_start + 16, min(f, s.frames))
                z.loop_on = True
            self.update()
            return
        near = any(self.marker_pos(m) is not None and abs(self.fx(self.marker_pos(m)) - x) < 8 for m in self.MARKS)
        self.setCursor(Qt.SizeHorCursor if near else Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = None
            self.markersChanged.emit()

    def _envelope(self, w: int):
        if self._env is not None and self._env[0] == (w, self.v0, self.v1):
            return self._env[1]
        d = self.zone.sample.data
        x = d[self.v0:self.v1]
        x = x.mean(axis=1) if x.ndim > 1 else x
        bins = max(1, w)
        n = len(x)
        if n == 0:
            mn = mx = np.zeros(bins, np.float32)
        elif n <= bins:
            mn = mx = np.interp(np.linspace(0, n - 1, bins), np.arange(n), x).astype(np.float32)
        else:
            edges = np.linspace(0, n, bins + 1).astype(int)
            mx = np.maximum.reduceat(x, edges[:-1])
            mn = np.minimum.reduceat(x, edges[:-1])
        self._env = ((w, self.v0, self.v1), (mn, mx))
        return mn, mx

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        p.setPen(Qt.NoPen)
        p.setBrush(C(T.PANEL))
        p.drawRoundedRect(QRectF(0, 0, W, H), 10, 10)
        f = QFont(self.font())
        f.setPointSizeF(8)
        p.setFont(f)
        if not self.zone:
            p.setPen(C(T.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "Selecione uma zona no mapa para editar a amostra")
            p.end()
            return
        z = self.zone
        s = z.sample
        mid = H / 2
        peak = float(np.max(np.abs(s.data))) if s.frames else 1.0
        amp = (H / 2 - 16) * min(6.0, 0.95 / max(peak, 1e-4))   # zoom vertical automático (só na tela)
        # região fora de start/end
        xs, xe = self.fx(z.start), self.fx(z.play_end())
        p.fillRect(QRectF(10, 0, max(0, xs - 10), H), C("#000000", 90))
        p.fillRect(QRectF(xe, 0, max(0, W - 10 - xe), H), C("#000000", 90))
        if s.loop_start is not None and s.loop_end is not None:
            x0, x1 = self.fx(s.loop_start), self.fx(s.loop_end)
            p.fillRect(QRectF(x0, 0, x1 - x0, H), C(T.OK, 30 if z.loop_on else 12))
        p.setPen(QPen(C(T.BORDER), 1))
        p.drawLine(QPointF(10, mid), QPointF(W - 10, mid))
        mn, mx = self._envelope(int(W - 20))
        path = QPainterPath()
        path.moveTo(10, mid - mx[0] * amp)
        for i in range(len(mx)):
            path.lineTo(10 + i, mid - mx[i] * amp)
        for i in range(len(mn) - 1, -1, -1):
            path.lineTo(10 + i, mid - mn[i] * amp)
        path.closeSubpath()
        g = QLinearGradient(0, 0, 0, H)
        g.setColorAt(0, C(T.ACCENT, 235))
        g.setColorAt(0.5, C(T.ACCENT, 150))
        g.setColorAt(1, C(T.ACCENT, 235))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawPath(path)
        # marcadores
        for m, col, lab in (("start", T.TEXT, "S"), ("end", T.TEXT, "E"),
                            ("loop_start", T.OK, "L"), ("loop_end", T.OK, "L")):
            pos = self.marker_pos(m)
            if pos is None:
                continue
            x = self.fx(pos)
            if x < 6 or x > W - 6:
                continue
            p.setPen(QPen(C(col, 230), 1.4, Qt.SolidLine if m in ("start", "end") else Qt.DashLine))
            p.drawLine(QPointF(x, 0), QPointF(x, H))
            p.setPen(Qt.NoPen)
            p.setBrush(C(col))
            p.drawRoundedRect(QRectF(x - 7, 2, 14, 13), 3, 3)
            p.setPen(C("#0E0F13"))
            p.drawText(QRectF(x - 7, 2, 14, 13), Qt.AlignCenter, lab)
        p.setPen(C(T.MUTED))
        info = f"{s.name}  ·  {s.frames / s.rate:.2f} s  ·  {s.rate} Hz  ·  {'estéreo' if s.channels > 1 else 'mono'}"
        if z.reverse:
            info += "  ·  INVERTIDA"
        p.drawText(QRectF(14, H - 18, W - 28, 16), Qt.AlignLeft | Qt.AlignVCenter, info)
        if W > 720:
            p.drawText(QRectF(14, H - 18, W - 28, 16), Qt.AlignRight | Qt.AlignVCenter,
                       "roda: zoom · arraste S/E/L para ajustar")
        p.end()


# ---------------------------------------------------------------------------
# Envelope ADSR
# ---------------------------------------------------------------------------

class EnvelopeEditor(QWidget):
    aboutToEdit = Signal()
    changed = Signal()
    TMAX = 6.0   # segundos por segmento no fim da escala

    def __init__(self, parent=None):
        super().__init__(parent)
        self.group: Optional[Group] = None
        self._drag = None
        self.setMinimumHeight(120)
        self.setMouseTracking(True)

    def set_group(self, g: Optional[Group]):
        self.group = g
        self.update()

    def _tx(self, sec: float) -> float:
        seg = (self.width() - 24) / 3.4
        return math.sqrt(max(0.0, min(self.TMAX, sec)) / self.TMAX) * seg

    def _xt(self, px: float) -> float:
        seg = (self.width() - 24) / 3.4
        return (max(0.0, px) / seg) ** 2 * self.TMAX

    def _pts(self):
        g = self.group
        H = self.height()
        top, bot = 12, H - 14
        x0 = 12
        xa = x0 + self._tx(g.attack)
        xh = xa + self._tx(g.hold)
        xd = xh + self._tx(g.decay)
        ys = bot - g.sustain * (bot - top)
        xs = xd + (self.width() - 24) * 0.25
        xr = xs + self._tx(g.release)
        return dict(a=QPointF(xa, top), h=QPointF(xh, top), d=QPointF(xd, ys), s=QPointF(xs, ys), r=QPointF(xr, bot)), top, bot, x0

    def mousePressEvent(self, e):
        if not self.group:
            return
        pts, *_ = self._pts()
        for k in ("a", "d", "r"):
            if (pts[k] - e.position()).manhattanLength() < 14:
                self.aboutToEdit.emit()
                self._drag = k
                return

    def mouseMoveEvent(self, e):
        if not self.group:
            return
        pts, top, bot, x0 = self._pts()
        if self._drag:
            g = self.group
            x, y = e.position().x(), e.position().y()
            if self._drag == "a":
                g.attack = round(max(0.0005, self._xt(x - x0)), 4)
            elif self._drag == "d":
                g.decay = round(max(0.005, self._xt(x - pts["h"].x())), 4)
                g.sustain = round(max(0.0, min(1.0, (bot - y) / (bot - top))), 3)
            elif self._drag == "r":
                g.release = round(max(0.005, self._xt(x - pts["s"].x())), 4)
            self.update()
            self.changed.emit()
            return
        near = any((pts[k] - e.position()).manhattanLength() < 14 for k in ("a", "d", "r"))
        self.setCursor(Qt.PointingHandCursor if near else Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        p.setPen(Qt.NoPen)
        p.setBrush(C(T.PANEL))
        p.drawRoundedRect(QRectF(0, 0, W, H), 10, 10)
        if not self.group:
            p.end()
            return
        pts, top, bot, x0 = self._pts()
        path = QPainterPath(QPointF(x0, bot))
        for k in ("a", "h", "d", "s", "r"):
            path.lineTo(pts[k])
        fill = QPainterPath(path)
        fill.lineTo(pts["r"].x(), bot)
        fill.closeSubpath()
        p.setBrush(C(T.ACCENT, 40))
        p.drawPath(fill)
        p.setPen(QPen(C(T.ACCENT), 2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        f = QFont(self.font())
        f.setPointSizeF(8)
        p.setFont(f)
        for k, lab in (("a", "A"), ("d", "D/S"), ("r", "R")):
            pt = pts[k]
            p.setPen(QPen(C("#FFFFFF"), 1.5))
            p.setBrush(C(T.ACCENT))
            p.drawEllipse(pt, 5.5, 5.5)
            p.setPen(C(T.MUTED))
            p.drawText(QRectF(pt.x() - 20, pt.y() + (8 if k != "r" else -22), 40, 14), Qt.AlignCenter, lab)
        p.end()
