"""Modelo de dados comum a todos os formatos de entrada."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(n: int) -> str:
    """Nome no padrão Roland (C4 = 60)."""
    n = int(n)
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


class Sample:
    """Áudio em float32, formato (frames, canais), valores em [-1, 1].

    Pode ser "preguiçoso": com `loader`, o áudio só é lido do disco quando
    `data` é acessado (bibliotecas grandes do Kontakt têm milhares de amostras).
    """

    def __init__(self, name: str, data: Optional[np.ndarray] = None, rate: int = 44100,
                 loop_start: Optional[int] = None, loop_end: Optional[int] = None,
                 loader=None, frames: int = 0, channels: int = 1):
        self.name = name
        self._data = data
        self.rate = int(rate)
        self.loop_start = loop_start   # em frames
        self.loop_end = loop_end       # em frames (exclusivo)
        self._loader = loader
        self._frames = int(frames)
        self._channels = int(channels)

    @property
    def data(self) -> np.ndarray:
        if self._data is None:
            if self._loader is None:
                self._data = np.zeros((1, 1), np.float32)
            else:
                d = self._loader()
                if d.ndim == 1:
                    d = d[:, None]
                self._data = d.astype(np.float32, copy=False)
        return self._data

    @data.setter
    def data(self, value):
        self._data = value

    @property
    def loaded(self) -> bool:
        return self._data is not None

    @property
    def frames(self) -> int:
        if self._data is None and self._frames:
            return self._frames
        return int(self.data.shape[0])

    @property
    def channels(self) -> int:
        if self._data is None and self._loader is not None:
            return self._channels
        return int(self.data.shape[1]) if self.data.ndim > 1 else 1

    @property
    def duration(self) -> float:
        return self.frames / float(self.rate) if self.rate else 0.0


@dataclass
class Zone:
    sample: Sample
    root: int = 60
    lo_key: int = 0
    hi_key: int = 127
    lo_vel: int = 0
    hi_vel: int = 127
    tune_cents: float = 0.0
    loop_on: bool = False
    gain_db: float = 0.0
    pan: float = 0.0   # -1..1

    def copy(self) -> "Zone":
        return Zone(**{k: getattr(self, k) for k in self.__dataclass_fields__})


@dataclass
class Instrument:
    name: str
    source_path: str = ""
    source_format: str = ""
    zones: List[Zone] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)   # avisos para o usuário

    def velocity_layers(self) -> List[tuple]:
        layers = sorted({(z.lo_vel, z.hi_vel) for z in self.zones})
        return layers


class ConversionError(Exception):
    """Erro com mensagem amigável para exibir ao usuário."""
