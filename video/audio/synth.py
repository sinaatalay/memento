"""Instruments and DSP for the Memento score. Everything here is synthesized; nothing is sampled."""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from scipy import signal

SR = 48000
TAU = 2 * np.pi

_NAMES = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6,
          "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}


def note(name: str) -> int:
    """'F#4' -> 66."""
    return 12 * (int(name[-1]) + 1) + _NAMES[name[:-1]]


def hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def n_samples(sec: float) -> int:
    return int(round(sec * SR))


def _sos(kind: str, f, order: int = 2):
    return signal.butter(order, f, btype=kind, fs=SR, output="sos")


def lowpass(x, f, order=2):
    return signal.sosfilt(_sos("low", min(f, SR / 2 - 100), order), x, axis=0)


def highpass(x, f, order=2):
    return signal.sosfilt(_sos("high", f, order), x, axis=0)


def bandpass(x, lo, hi, order=2):
    return signal.sosfilt(_sos("band", [lo, min(hi, SR / 2 - 100)], order), x, axis=0)


def smooth_env(L: int, attack: float, release: float, hold: float | None = None) -> np.ndarray:
    """Raised-cosine attack, optional hold, raised-cosine release to zero at the end."""
    t = np.arange(L) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    env = 0.5 - 0.5 * np.cos(np.pi * a)
    end = L / SR
    r0 = end - release if hold is None else hold
    r = np.clip((t - r0) / max(release, 1e-4), 0, 1)
    return env * (0.5 + 0.5 * np.cos(np.pi * r))


class Bus:
    """A stereo track things are placed on, in seconds."""

    def __init__(self, seconds: float):
        self.x = np.zeros((n_samples(seconds), 2), np.float64)

    def add(self, sig: np.ndarray, t: float, gain: float = 1.0, pan: float = 0.0):
        i = n_samples(t)
        if sig.ndim == 1:
            p = (np.clip(pan, -1, 1) + 1) * np.pi / 4
            st = np.stack([sig * np.cos(p), sig * np.sin(p)], 1) * np.sqrt(2)
        else:
            st = sig
        if i < 0:
            st = st[-i:]
            i = 0
        n = min(len(st), len(self.x) - i)
        if n > 0:
            self.x[i:i + n] += gain * st[:n]


# ---------------------------------------------------------------- instruments


@lru_cache(maxsize=512)
def piano(m: int, vel: float = 0.5, hold: float = 3.0, seed: int = 0) -> np.ndarray:
    """A felt piano: inharmonic string partials, three detuned strings, a soft hammer."""
    rng = np.random.default_rng(1000 + m * 7 + seed)
    f0 = hz(m)
    t60 = float(np.interp(m, [21, 40, 60, 84, 108], [15, 10, 6.5, 3.0, 1.2]))
    rel = 0.35
    L = n_samples(min(hold + rel + 0.15, t60) + 0.05)
    t = np.arange(L) / SR
    B = float(np.clip(1.1e-4 * 2 ** ((m - 60) / 16), 4e-5, 2e-3))
    felt = 0.45 * (1 - 0.5 * vel)
    tau0 = t60 / 6.9
    y = np.zeros(L)
    for n in range(1, int(min(40, 11000 / f0)) + 1):
        fn = n * f0 * np.sqrt(1 + B * n * n)
        if fn > 15000:
            break
        a = n ** -1.2 * np.exp(-(n - 1) * felt) * (0.3 + 0.7 * abs(np.sin(np.pi * n * 0.121)))
        if a < 2e-4:
            continue
        tau = tau0 / (1 + 0.11 * (n - 1) + 0.004 * (n - 1) ** 2)
        env = 0.7 * np.exp(-t / (tau * 0.28)) + 0.3 * np.exp(-t / tau)
        strings = 3 if (m >= 45 and n <= 9) else 1
        part = np.zeros(L)
        for s in range(strings):
            cents = (s - (strings - 1) / 2) * 0.8 * rng.uniform(0.6, 1.4) if strings > 1 else 0.0
            part += np.sin(TAU * fn * 2 ** (cents / 1200) * t + rng.uniform(0, TAU))
        y += a * env * part / strings
    att = 0.003 + 0.01 * (1 - vel)
    y *= np.clip(t / att, 0, 1) ** 1.6
    thump = rng.standard_normal(L) * np.exp(-t / 0.008)
    y += 0.05 * vel * lowpass(thump, 500 + 2500 * vel)
    y *= np.where(t < hold, 1.0, np.exp(-(t - hold) / (rel / 3)))
    y = lowpass(y, 1500 + 5500 * vel)
    fade = min(L, n_samples(0.02))
    y[-fade:] *= np.linspace(1, 0, fade)
    return (y * 0.62 * vel ** 1.3).astype(np.float64)


@lru_cache(maxsize=512)
def celesta(m: int, vel: float = 0.5, decay: float = 1.6, seed: int = 0) -> np.ndarray:
    """Struck glass: clear, bright attack, pure tail. The sound of a memory."""
    rng = np.random.default_rng(2000 + m * 11 + seed)
    f0 = hz(m)
    L = n_samples(decay * 3.2 + 0.05)
    t = np.arange(L) / SR
    y = np.zeros(L)
    for r, a in zip([1.0, 2.0, 3.0, 4.07, 5.19, 6.83], [1.0, 0.3, 0.09, 0.06, 0.03, 0.012]):
        f = f0 * r
        if f > 17000:
            continue
        d = decay / r ** 0.85
        y += a * np.exp(-t / d) * np.sin(TAU * f * t + rng.uniform(0, TAU))
    # a slightly detuned twin makes the tail breathe
    y += 0.35 * np.exp(-t / decay) * np.sin(TAU * f0 * 2 ** (1.8 / 1200) * t + rng.uniform(0, TAU))
    click = highpass(rng.standard_normal(L) * np.exp(-t / 0.0012), 3000) * 0.04
    y = (y + click) * np.clip(t / 0.0015, 0, 1)
    fade = n_samples(0.03)
    y[-fade:] *= np.linspace(1, 0, fade)
    return y * 0.085 * vel


@lru_cache(maxsize=64)
def glass(m: int, vel: float = 0.5, decay: float = 1.6) -> np.ndarray:
    """A pure sine with a long bloom; doubles melodies an octave up."""
    f = hz(m)
    L = n_samples(decay * 3)
    t = np.arange(L) / SR
    y = np.sin(TAU * f * t) * np.exp(-t / decay) * np.clip(t / 0.02, 0, 1)
    y += 0.2 * np.sin(TAU * 2 * f * t) * np.exp(-t / (decay * 0.4))
    fade = n_samples(0.05)
    y[-fade:] *= np.linspace(1, 0, fade)
    return y * 0.035 * vel


def pad(midis: list[int], dur: float, attack: float = 1.5, release: float = 2.0,
        bright: float = 0.5, seed: int = 0) -> np.ndarray:
    """Warm stereo pad: soft saw-ish partials, three drifting voices per note, lowpassed."""
    rng = np.random.default_rng(3000 + seed)
    L = n_samples(dur + release)
    t = np.arange(L) / SR
    y = np.zeros((L, 2))
    for m in midis:
        f = hz(m)
        for v in range(3):
            cents = (v - 1) * 6 + rng.uniform(-1.5, 1.5)
            lfo = 1 + 0.0011 * np.sin(TAU * rng.uniform(0.08, 0.22) * t + rng.uniform(0, TAU))
            phase = TAU * f * 2 ** (cents / 1200) * np.cumsum(lfo) / SR
            sig = np.zeros(L)
            for k, a in enumerate([1.0, 0.45, 0.22, 0.11, 0.06, 0.03], 1):
                if f * k > 7000:
                    break
                sig += a * np.sin(k * phase + rng.uniform(0, TAU))
            p = ((v - 1) * 0.55 + 1) * np.pi / 4
            y[:, 0] += sig * np.cos(p)
            y[:, 1] += sig * np.sin(p)
    y = lowpass(y, 600 + 2600 * bright)
    y *= smooth_env(L, attack, release, hold=dur)[:, None]
    return y * 0.028 / np.sqrt(len(midis))


def drone(midis: list[int], dur: float, attack: float = 2.0, release: float = 2.5, seed: int = 0) -> np.ndarray:
    """Night: low sines that breathe, and a little air."""
    rng = np.random.default_rng(4000 + seed)
    L = n_samples(dur + release)
    t = np.arange(L) / SR
    y = np.zeros((L, 2))
    for i, m in enumerate(midis):
        f = hz(m)
        breath = 0.75 + 0.25 * np.sin(TAU * (0.07 + 0.03 * i) * t + rng.uniform(0, TAU))
        for ch in range(2):
            y[:, ch] += breath * np.sin(TAU * f * (1 + (ch - 0.5) * 0.0015) * t + rng.uniform(0, TAU))
    air = lowpass(rng.standard_normal((L, 2)), 380, order=2) * 0.35
    y = y * 0.05 / len(midis) + air * 0.012
    return y * smooth_env(L, attack, release, hold=dur)[:, None]


def key_click(vel: float = 0.5, seed: int = 0) -> np.ndarray:
    """A quiet, soft keyboard key."""
    rng = np.random.default_rng(5000 + seed)
    L = n_samples(0.05)
    t = np.arange(L) / SR
    r = rng.uniform(0.85, 1.2)
    body = bandpass(rng.standard_normal(L), 1600 * r, 5200 * r) * np.exp(-t / 0.0055)
    thock = np.sin(TAU * 150 * r * t) * np.exp(-t / 0.011) * 0.5
    return (0.8 * body + thock) * 0.05 * vel


def wood_tick(vel: float = 0.5, pitch: float = 1.0, seed: int = 0) -> np.ndarray:
    """A clock's tick: two resonances of a small wooden block."""
    rng = np.random.default_rng(6000 + seed)
    L = n_samples(0.14)
    t = np.arange(L) / SR
    x = rng.standard_normal(L) * np.exp(-t / 0.002)
    y = np.zeros(L)
    for f, a in [(1150 * pitch, 1.0), (2480 * pitch, 0.5), (620 * pitch, 0.4)]:
        b, a_ = signal.iirpeak(f, 22, fs=SR)
        y += a * signal.lfilter(b, a_, x)
    return y * np.exp(-t / 0.03) * 0.12 * vel


def whoosh(dur: float, f0: float, f1: float, width: float = 0.8, peak: float = 0.65,
           seed: int = 0) -> np.ndarray:
    """Filtered air moving: a band of noise whose centre glides from f0 to f1."""
    rng = np.random.default_rng(7000 + seed)
    L = n_samples(dur)
    x = rng.standard_normal(L + 2048)
    f, tt, Z = signal.stft(x, SR, nperseg=1024)
    k = np.clip(tt / dur, 0, 1)
    fc = f0 * (f1 / f0) ** k
    logf = np.log2(np.maximum(f, 20))[:, None]
    mask = np.exp(-0.5 * ((logf - np.log2(fc)[None, :]) / width) ** 2)
    _, y = signal.istft(Z * mask, SR, nperseg=1024)
    y = y[:L]
    t = np.arange(L) / L
    env = np.where(t < peak, np.sin(np.pi / 2 * t / peak) ** 2, np.cos(np.pi / 2 * (t - peak) / (1 - peak)) ** 2)
    y = y * env
    return y / (np.max(np.abs(y)) + 1e-9) * 0.2


def glide(dur: float, m0: float, m1: float, vel: float = 0.5) -> np.ndarray:
    """A pitch travelling from m0 to m1, glassy; the sound of a memory flying back."""
    L = n_samples(dur)
    t = np.arange(L) / SR
    k = t / dur
    ease = k * k * (3 - 2 * k)
    f = hz(m0) * (hz(m1) / hz(m0)) ** ease
    phase = TAU * np.cumsum(f) / SR
    y = np.sin(phase) + 0.25 * np.sin(2 * phase) + 0.08 * np.sin(3 * phase)
    env = np.sin(np.pi * np.clip(k, 0, 1)) ** 1.5
    return y * env * 0.05 * vel


def riser(dur: float, seed: int = 0) -> np.ndarray:
    """Tension into a yes: air rising, getting brighter and louder."""
    w = whoosh(dur, 250, 5000, width=0.9, peak=0.97, seed=seed)
    L = len(w)
    k = np.arange(L) / L
    return w * (0.25 + 0.75 * k ** 2)


def reverse(x: np.ndarray) -> np.ndarray:
    return x[::-1].copy()


# ---------------------------------------------------------------- space and finish


@lru_cache(maxsize=4)
def reverb_ir(sec: float = 3.4, seed: int = 3) -> np.ndarray:
    """A synthetic hall: decorrelated noise, darker as it decays."""
    rng = np.random.default_rng(8000 + seed)
    L = n_samples(sec)
    t = np.arange(L) / SR
    ir = np.zeros((L, 2))
    for ch in range(2):
        n = rng.standard_normal(L)
        acc = np.zeros(L)
        for lo, hi, t60 in [(40, 300, 3.0), (300, 1500, 2.6), (1500, 5000, 1.8), (5000, 14000, 0.9)]:
            acc += bandpass(n, lo, hi) * np.exp(-6.9 * t / t60)
        acc *= 1 - np.exp(-t / 0.015)
        ir[:, ch] = acc
    ir = np.vstack([np.zeros((n_samples(0.022), 2)), ir])
    return ir / np.sqrt(np.sum(ir ** 2, axis=0, keepdims=True))


def reverb(x: np.ndarray, sec: float = 3.4) -> np.ndarray:
    ir = reverb_ir(sec)
    out = np.zeros_like(x)
    for ch in range(2):
        out[:, ch] = signal.fftconvolve(x[:, ch], ir[:, ch])[: len(x)]
    return out


def compress(x: np.ndarray, threshold_db: float = -20, ratio: float = 2.0, attack: float = 0.01,
             release: float = 0.25, block: int = 64) -> np.ndarray:
    """Gentle glue: block RMS detector, attack/release follower, soft ratio."""
    nb = len(x) // block
    blocks = x[: nb * block].reshape(nb, block, 2)
    level = np.sqrt(np.mean(blocks ** 2, axis=(1, 2)) + 1e-12)
    a_att = np.exp(-block / (attack * SR))
    a_rel = np.exp(-block / (release * SR))
    env = np.zeros(nb)
    e = 0.0
    for i, v in enumerate(level):
        a = a_att if v > e else a_rel
        e = a * e + (1 - a) * v
        env[i] = e
    db = 20 * np.log10(env + 1e-9)
    over = np.maximum(0, db - threshold_db)
    g_blocks = 10 ** (-(over * (1 - 1 / ratio)) / 20)
    centers = (np.arange(nb) + 0.5) * block
    gain = np.interp(np.arange(len(x)), centers, g_blocks)
    return x * gain[:, None]


def _biquad(kind: str, f0: float, gain_db: float, q: float = 0.707):
    """RBJ cookbook peaking and high-shelf filters."""
    A = 10 ** (gain_db / 40)
    w = TAU * f0 / SR
    cw, sw = np.cos(w), np.sin(w)
    alpha = sw / (2 * q)
    if kind == "peak":
        b = [1 + alpha * A, -2 * cw, 1 - alpha * A]
        a = [1 + alpha / A, -2 * cw, 1 - alpha / A]
    else:  # high shelf
        sq = 2 * np.sqrt(A) * alpha
        b = [A * ((A + 1) + (A - 1) * cw + sq), -2 * A * ((A - 1) + (A + 1) * cw), A * ((A + 1) + (A - 1) * cw - sq)]
        a = [(A + 1) - (A - 1) * cw + sq, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - sq]
    return np.array(b) / a[0], np.array(a) / a[0]


def eq(x: np.ndarray, bands: list[tuple[str, float, float, float]]) -> np.ndarray:
    for kind, f0, g, q in bands:
        b, a = _biquad(kind, f0, g, q)
        x = signal.lfilter(b, a, x, axis=0)
    return x


def soft_limit(x: np.ndarray, ceiling: float = 0.94) -> np.ndarray:
    return np.tanh(x / ceiling) * ceiling
