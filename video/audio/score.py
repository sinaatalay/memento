"""
The Memento score: felt piano, glass and air, placed on the film's own cues.

    bun scripts/export-cues.ts          # the picture's timing -> audio/cues.json
    uv run --project audio audio/score.py

Every memory that lands plays a note. Every question Jev asks is a grain of glass.
The one yes is the three-note motif that later spells the name: me-men-to.

Writes public/audio/score.wav (the film's music and sound, mastered) and stems in audio/out/.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from synth import (SR, Bus, celesta, compress, drone, eq, glass, glide, highpass, key_click, n_samples, note, pad,
                   piano, reverb, reverse, riser, soft_limit, whoosh, wood_tick)

HERE = Path(__file__).parent
ROOT = HERE.parent
cues = json.loads((HERE / "cues.json").read_text())
C = cues["cue"]
DUR = cues["duration"]
TAIL = 4.0
rng = np.random.default_rng(27)

N = note
# D major pentatonic, the only notes memories are allowed to make
PENTA = [N("D5"), N("E5"), N("F#5"), N("A5"), N("B5"), N("D6"), N("E6"), N("F#6"), N("A6"), N("B6"), N("D7")]
MOTIF = [N("F#5"), N("A5"), N("D5")]  # me - men - to


def pan_x(x: float) -> float:
    return float(np.clip((x - 0.5) * 1.5, -0.8, 0.8))


def pitch_y(y: float) -> int:
    """Higher on screen, higher note."""
    k = float(np.clip(0.5 - y * 2.2, 0, 0.999))
    return PENTA[int(k * len(PENTA))]


keys = Bus(DUR + TAIL)  # piano
glassy = Bus(DUR + TAIL)  # celesta, glass
pads = Bus(DUR + TAIL)
air = Bus(DUR + TAIL)  # whooshes, drones
dry = Bus(DUR + TAIL)  # clicks and ticks: close, little room


def chord(names: str) -> list[int]:
    return [N(n) for n in names.split()]


def human(t: float, vel: float) -> tuple[float, float]:
    """A player, not a sequencer: a few milliseconds early or late, a little softer or harder."""
    return t + float(rng.normal(0, 0.007)), round(float(vel * rng.uniform(0.92, 1.06)), 2)


def arpeggio(names: str, t: float, step: float = 0.16, vel: float = 0.38, hold: float = 3.5, pan: float = 0.0):
    for i, m in enumerate(chord(names)):
        ht, hv = human(t + i * step, vel * (1 - 0.06 * i))
        keys.add(piano(m, hv, round(hold - i * step, 2)), ht, pan=pan + 0.1 * (i - 1))


def melody(notes: list[tuple[float, str, float]], hold: float = 1.6, pan: float = 0.1, double: bool = False):
    for t, name, vel in notes:
        ht, hv = human(t, vel)
        keys.add(piano(N(name), hv, hold), ht, pan=pan)
        if double:
            glassy.add(glass(N(name) + 12, vel * 0.6, 2.2), t, pan=pan)


def motif(t: float, step: float, vel: float = 0.55, octave: int = 0, piano_too: bool = True):
    for i, m in enumerate(MOTIF):
        glassy.add(celesta(m + 12 * (octave + 1), vel, 2.4), t + i * step, pan=[-0.2, 0.15, 0.0][i])
        glassy.add(glass(m + 12 * (octave + 1), vel * 0.5, 2.6), t + i * step, pan=[-0.2, 0.15, 0.0][i])
        if piano_too:
            keys.add(piano(m + 12 * octave, vel * 0.8, 2.5), t + i * step, pan=[-0.2, 0.15, 0.0][i])


# ------------------------------------------------------------------ 1. a memory is born

keys.add(piano(N("D2"), 0.22, 6.0), 0.18, pan=-0.1)
air.add(whoosh(1.5, 260, 1500, width=1.1, peak=0.55, seed=1), 0.05, gain=0.12)

for i, k in enumerate(cues["chatKeys"]):
    dry.add(key_click(0.55 + 0.35 * rng.random(), seed=i), k - 0.004, pan=-0.3 + 0.02 * rng.standard_normal())

# older memories surface, nearest first: a few grains of glass, not all 260
surf = cues["surfacing"]
picks = [surf[int(i)] for i in np.linspace(0, len(surf) - 1, 11)]
for i, s in enumerate(picks):
    glassy.add(celesta(PENTA[5 + (i * 3) % 6], 0.16 + 0.05 * rng.random(), 1.4, seed=i), s["t"] + 0.1,
               pan=pan_x(s["x"]))

# the sentence condenses: a breath in
air.add(reverse(whoosh(0.7, 700, 2400, width=0.7, peak=0.4, seed=2)), C["condense"] - 0.1, gain=0.14, pan=0.15)
# it lands: the first real note
keys.add(piano(N("F#4"), 0.5, 4.0), C["land"], pan=0.25)
keys.add(piano(N("D3"), 0.36, 4.0), C["land"] + 0.01, pan=0.0)
glassy.add(celesta(N("A5"), 0.5, 2.0), C["land"], pan=0.25)
pads.add(pad(chord("D3 A3 C#4 E4 F#4"), C["flowStart"] - C["land"] + 0.8, attack=1.4, release=1.6, bright=0.35, seed=1),
         C["land"] - 0.2)

# life goes on: more memories, each its own note
for i, t in enumerate(cues["montage"]):
    m = [N("B5"), N("D6"), N("E6")][i]
    glassy.add(celesta(m, 0.42, 1.8, seed=10 + i), t, pan=0.3)
    keys.add(piano(m - 12, 0.3, 1.8), t, pan=0.3)
    air.add(reverse(whoosh(0.3, 900, 2600, width=0.6, peak=0.5, seed=20 + i)), t - 0.27, gain=0.07, pan=0.25)

# ------------------------------------------------------------------ 2. passive

t0 = C["flowStart"]
pads.add(pad(chord("B2 F#3 A3 D4 E4"), 2.5, attack=1.0, release=1.4, bright=0.3, seed=2), t0 - 0.1)
arpeggio("B2 F#3 D4 A4", t0, vel=0.36)
pads.add(pad(chord("G2 D3 F#3 A3 B3"), 1.9, attack=0.9, release=1.2, bright=0.3, seed=3), t0 + 2.4)
arpeggio("G2 D3 B3 F#4", t0 + 2.5, vel=0.34)
melody([(C["crossFlight"] - 2.15, "F#5", 0.3), (C["crossFlight"] - 1.6, "E5", 0.27), (C["crossFlight"] - 1.05, "D5", 0.25)],
       hold=1.2)
pads.add(pad(chord("A2 E3 A3 D4 E4"), 0.8, attack=0.6, release=0.35, bright=0.3, seed=4), C["crossFlight"] - 0.9)
keys.add(piano(N("A2"), 0.3, 0.9), C["crossFlight"] - 0.85, pan=-0.1)
keys.add(piano(N("D4"), 0.28, 0.9), C["crossFlight"] - 0.83, pan=0.1)

# days pass under "now": a small clock
for i, tk in enumerate(cues["ticks"]):
    if tk["t"] > C["rewind"] - 0.1:
        continue
    dry.add(wood_tick(0.9 if tk["major"] else 0.45, 1.0 if tk["major"] else 1.3, seed=i), tk["t"], pan=0.35)

# Monday 11:00 goes by. Nothing. Then one low note.
glassy.add(glass(N("A5"), 0.35, 1.2), C["crossFlight"], pan=0.3)
keys.add(piano(N("D2"), 0.3, 3.0), C["crossFlight"] + 0.45, pan=-0.1)
keys.add(piano(N("F#4"), 0.2, 2.6), C["crossFlight"] + 0.47, pan=0.1)

# ------------------------------------------------------------------ 3. rewind

rw = C["rewindEnd"] - C["rewind"]
swell = np.zeros(n_samples(6.0))
for m in chord("D3 A3 D4 F#4 A4"):
    p = piano(m, 0.45, 3.5)
    swell[: len(p)] += p
swell = reverse(swell)[-n_samples(rw + 0.1):]
keys.add(swell * np.linspace(0.2, 1, len(swell)), C["rewind"], gain=0.9)
air.add(whoosh(rw + 0.2, 3000, 400, width=0.8, peak=0.8, seed=3), C["rewind"], gain=0.16, pan=0.0)
for i, tk in enumerate(cues["ticks"]):
    if C["rewind"] < tk["t"] < C["rewindEnd"]:
        dry.add(wood_tick(0.5, 1.6, seed=100 + i), tk["t"], pan=-0.2)

# ------------------------------------------------------------------ 4. the page

co = C["cardOpen"]
keys.add(piano(N("G2"), 0.4, 3.0), co, pan=-0.1)
keys.add(piano(N("D3"), 0.32, 3.0), co + 0.01)
glassy.add(celesta(N("B5"), 0.4, 2.0), co + 0.02, pan=0.1)
air.add(whoosh(0.55, 500, 1800, width=0.9, peak=0.3, seed=4), co - 0.05, gain=0.08)
pads.add(pad(chord("G2 D3 F#3 A3 B3"), C["recipeOpen"] - co + 0.3, attack=0.8, release=1.2, bright=0.28, seed=5), co)
melody([(co + 0.9, "B4", 0.3), (co + 1.7, "A4", 0.28), (co + 2.5, "F#4", 0.27)], hold=1.4)

# the recipe unfolds, River writes: a patient pulse under the typing
ro = C["recipeOpen"]
air.add(whoosh(0.7, 400, 2600, width=0.9, peak=0.6, seed=5), ro - 0.25, gain=0.1, pan=0.1)
beat = 60 / 76 / 2
plan = [
    (ro, C["typeStart"] + 2.9, "F#2 D3 A3 D4 F#4", ["D5", "A4", "F#4", "A4"]),
    (C["typeStart"] + 2.9, C["mathHi"], "B2 F#3 A3 D4 E4", ["D5", "B4", "F#4", "B4"]),
    (C["mathHi"], C["jevHi"], "G2 D3 F#3 A3 B3", ["D5", "B4", "G4", "B4"]),
    (C["jevHi"], C["cardClose"], "A2 E3 G3 D4 E4", ["E5", "A4", "E4", "A4"]),
]
for a, b, pchord, cells in plan:
    pads.add(pad(chord(pchord), b - a + 0.2, attack=0.5, release=0.9, bright=0.35, seed=int(a * 10)), a - 0.05)
    t = a
    i = 0
    while t < b - 0.05:
        if t >= C["typeStart"] - 0.4:
            accent = 1.0 if i % 4 == 0 else 0.8
            ht, hv = human(t, 0.27 * accent)
            keys.add(piano(N(cells[i % 4]), hv, round(beat * 1.9, 2)), ht, pan=0.3)
        t += beat
        i += 1
keys.add(piano(N("F#2"), 0.34, 3.0), ro, pan=-0.1)

for i, k in enumerate(cues["recipeKeys"]):
    if i % 3 == 0:
        dry.add(key_click(0.35 + 0.3 * rng.random(), seed=500 + i), k - 0.004, pan=0.05 + 0.1 * rng.standard_normal())
# "memento" is typed: a small spark
memento_done = cues["recipeKeys"][11]
glassy.add(celesta(N("F#6"), 0.45, 1.6), memento_done, pan=0.2)

# time is plain math: two ticks and a clear note
dry.add(wood_tick(1.0, 1.0, seed=900), C["mathHi"])
dry.add(wood_tick(0.8, 1.26, seed=901), C["mathHi"] + 0.13)
glassy.add(celesta(N("A5"), 0.5, 2.0), C["mathHi"] + 0.02, pan=0.35)
# meaning is a question: a fourth that doesn't resolve
glassy.add(celesta(N("E5"), 0.45, 2.0), C["jevHi"], pan=0.35)
glassy.add(celesta(N("A5"), 0.45, 2.4), C["jevHi"] + 0.2, pan=0.4)

# the page folds back into its memory
air.add(reverse(whoosh(0.55, 700, 3000, width=0.8, peak=0.5, seed=6)), C["cardClose"] - 0.05, gain=0.12)

# every memory wakes into its recipe: a ripple of glass over a blooming chord
keys.add(piano(N("D2"), 0.45, 4.0), C["alive"], pan=-0.1)
keys.add(piano(N("A2"), 0.36, 4.0), C["alive"] + 0.02)
pads.add(pad(chord("D3 A3 C#4 E4 F#4 A4"), 1.6, attack=0.35, release=1.2, bright=0.6, seed=7), C["alive"] - 0.1)
wakes = cues["wakes"]
for i, w in enumerate(wakes[:: max(1, len(wakes) // 26)]):
    glassy.add(celesta(pitch_y(w["y"]), round(0.22 + 0.15 * float(rng.random()), 2), 1.2, seed=300 + i),
               w["t"], pan=pan_x(w["x"]))

# ------------------------------------------------------------------ 5. night

dusk = cues["sweeps"][0]
air.add(whoosh(dusk["leave"] - dusk["enter"] + 0.8, 3200, 300, width=1.0, peak=0.55, seed=7), dusk["enter"] - 0.3,
        gain=0.2, pan=0.0)
for i, tk in enumerate(cues["ticks"]):
    if C["ffStart"] < tk["t"] < C["nightFull"]:
        dry.add(wood_tick(0.4, 1.4, seed=200 + i), tk["t"], pan=0.3)
night = C["dawn"] - C["nightFull"] + 0.6
air.add(drone(chord("B1 F#2 D3"), night, attack=1.6, release=2.2, seed=1), C["nightFull"] - 0.8, gain=1.0)
pads.add(pad(chord("B2 F#3 C#4 D4"), night, attack=2.2, release=2.0, bright=0.18, seed=8), C["nightFull"] - 0.6)

# stars: a few far-off notes in the quiet
quiet = [(C["nightFull"], C["email1"] - 0.1), (C["no"] + 0.5, C["email2"] - 0.1)]
for a, b in quiet:
    t = a + 0.3
    while t < b:
        glassy.add(celesta(PENTA[int(rng.integers(5, len(PENTA)))], 0.13 + 0.08 * rng.random(), 2.2,
                           seed=int(t * 100)), t, pan=float(rng.uniform(-0.7, 0.7)))
        t += float(rng.uniform(0.9, 1.6))

for fi, f in enumerate(cues["fans"]):
    # an email lands on "now"
    keys.add(piano(N("F#3") if fi == 0 else N("A3"), 0.36, 2.0), f["land"], pan=0.25)
    glassy.add(celesta(N("A6"), 0.3, 1.0, seed=40 + fi), f["land"], pan=0.3)
    air.add(reverse(whoosh(0.45, 1200, 4000, width=0.6, peak=0.6, seed=30 + fi)), f["land"] - 0.42, gain=0.1, pan=0.3)
    # one call: every memory is asked; each question reaching its memory is a grain of glass
    for j, h in enumerate(f["hits"]):
        if h["hero"]:
            continue
        glassy.add(celesta(pitch_y(h["y"]), round(0.08 + 0.05 * float(rng.random()), 2), 0.35, seed=600 + j % 40), h["t"],
                   pan=pan_x(h["x"]))
    if not f["yes"]:
        # no: the lines let go
        keys.add(piano(N("B2"), 0.26, 2.2), f["resolve"], pan=-0.1)
        keys.add(piano(N("F#3"), 0.22, 2.2), f["resolve"] + 0.02, pan=0.05)
        air.add(whoosh(0.8, 2400, 500, width=0.8, peak=0.25, seed=50), f["resolve"], gain=0.05)
    else:
        # tension into the one yes
        rs = riser(f["resolve"] - f["hits"][0]["t"] + 0.3, seed=9)
        air.add(rs, f["resolve"] - len(rs) / SR, gain=0.22)
        yes = f["resolve"]
        motif(yes, 0.1, vel=0.62, octave=0, piano_too=False)
        for m in chord("D2 A2 F#3 A3 D4 F#4"):
            keys.add(piano(m, 0.5, 3.5), yes + 0.01, pan=0.0)
        pads.add(pad(chord("D3 F#3 A3 C#4 E4 F#4"), C["dawn"] - yes + 0.8, attack=0.25, release=1.6, bright=0.55,
                     seed=9), yes - 0.05)
        # River rewrites the memory
        for k in range(14):
            dry.add(key_click(0.4 + 0.3 * rng.random(), seed=800 + k), C["rewrite"] + 0.1 + k * 0.045,
                    pan=-0.35)
        for k, m in enumerate(["D6", "E6", "F#6", "A6"]):
            glassy.add(celesta(N(m), 0.25, 1.2, seed=820 + k), C["rewrite"] + 0.35 + k * 0.09, pan=-0.3)

# ------------------------------------------------------------------ 6. morning, it reaches you

dawn = cues["sweeps"][1]
air.add(whoosh(dawn["leave"] - dawn["enter"] + 0.8, 300, 3600, width=1.0, peak=0.6, seed=8), dawn["enter"] - 0.3,
        gain=0.18)
pads.add(pad(chord("G2 D3 A3 B3 E4"), C["fire"] - C["dawn"] + 0.2, attack=1.4, release=0.8, bright=0.45, seed=10),
         C["dawn"])
melody([(C["dawn"] + 0.3, "A4", 0.3), (C["dawn"] + 0.95, "B4", 0.32), (C["dawn"] + 1.6, "D5", 0.34)], hold=1.4)
keys.add(piano(N("A2"), 0.36, 1.6), C["fire"] - 0.6, pan=-0.1)
keys.add(piano(N("E4"), 0.3, 1.2), C["fire"] - 0.58, pan=0.1)
# the memory flies back along its arc
glassy.add(glide(C["notify"] - C["fire"] + 0.08, N("D5"), N("D6"), 0.9), C["fire"], pan=0.35)
# the notification: the motif, quick
motif(C["notify"], 0.085, vel=0.6, octave=0, piano_too=False)
for m in chord("D2 D3 A3 F#4 A4"):
    keys.add(piano(m, 0.52, 4.2), C["notify"] + 0.005)
pads.add(pad(chord("D3 A3 C#4 E4 F#4"), C["outro"] - C["notify"] + 0.8, attack=0.5, release=1.8, bright=0.5, seed=11),
         C["notify"] - 0.1)
n0 = C["notify"] + 0.9
melody([(n0, "F#5", 0.34), (n0 + 0.62, "E5", 0.31), (n0 + 1.24, "D5", 0.33), (n0 + 2.1, "A4", 0.29),
        (n0 + 2.72, "B4", 0.3), (n0 + 3.34, "D5", 0.32)], hold=1.3, double=True)

# ------------------------------------------------------------------ 7. the name

o = C["outro"]
glassy.add(glide(cues["syllables"][2] - o - 0.1, N("A5"), N("D6"), 0.6), o + 0.2, pan=0.0)
pads.add(pad(chord("D3 A3 E4 F#4"), cues["syllables"][0] - o + 0.4, attack=0.6, release=1.0, bright=0.3, seed=12), o)
for i, t in enumerate(cues["syllables"]):
    m = MOTIF[i]
    keys.add(piano(m, 0.5, 3.0), t, pan=[-0.15, 0.1, 0.0][i])
    glassy.add(celesta(m + 12, 0.5, 2.6), t, pan=[-0.15, 0.1, 0.0][i])
    glassy.add(glass(m + 12, 0.35, 3.0), t, pan=[-0.15, 0.1, 0.0][i])
last = cues["syllables"][2]
keys.add(piano(N("D2"), 0.5, 6.0), last + 0.005, pan=-0.05)
keys.add(piano(N("A2"), 0.36, 6.0), last + 0.012)
pads.add(pad(chord("D3 A3 C#4 E4 F#4 A4"), C["fadeOut"] - C["tagline"] + 1.0, attack=1.6, release=2.4, bright=0.4,
             seed=13), C["tagline"] - 0.6)
glassy.add(celesta(N("A6"), 0.22, 2.4), C["credits"] + 0.1, pan=0.3)
glassy.add(celesta(N("D7"), 0.16, 2.6), C["credits"] + 0.7, pan=-0.3)

# ------------------------------------------------------------------ mix

wet = reverb(keys.x * 0.32 + glassy.x * 0.55 + pads.x * 0.35 + air.x * 0.25 + dry.x * 0.08)
mix = keys.x * 1.1 + glassy.x * 0.8 + pads.x * 0.95 + air.x * 1.3 + dry.x * 0.9 + wet * 0.85
mix = highpass(mix, 28)
mix = eq(mix, [("peak", 320, -2.0, 0.8), ("shelf", 4800, 4.5, 0.7)])
mix = compress(mix, threshold_db=-22, ratio=1.8, attack=0.015, release=0.3)
end = n_samples(DUR)
fade = n_samples(1.2)
mix = mix[:end]
mix[-fade:] *= np.linspace(1, 0, fade)[:, None] ** 2
mix = soft_limit(mix / (np.max(np.abs(mix)) + 1e-9) * 0.85)

out = HERE / "out"
out.mkdir(exist_ok=True)
sf.write(out / "score_raw.wav", mix, SR, subtype="FLOAT")
for name, bus in [("keys", keys), ("glass", glassy), ("pads", pads), ("air", air), ("dry", dry)]:
    sf.write(out / f"stem_{name}.wav", bus.x[:end], SR, subtype="FLOAT")

dest = ROOT / "public/audio/score.wav"
dest.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(
    ["ffmpeg", "-y", "-loglevel", "error", "-i", str(out / "score_raw.wav"),
     "-af", "loudnorm=I=-17:TP=-1.5:LRA=13", "-ar", str(SR), "-c:a", "pcm_s24le", str(dest)],
    check=True,
)
print(f"wrote {dest.relative_to(ROOT)}")
