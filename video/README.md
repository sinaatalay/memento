# Memento launch film

A 60-second Remotion film: paper, ink, one accent. Memories are dots on a time axis; a memory with a
recipe reaches forward to the moment it matters. The score is synthesized from the same timeline, so
every note lands on its picture.

The presenter's lines are in [SCRIPT.md](SCRIPT.md).

## Build

```sh
bun install
bun run audio        # timeline -> audio/cues.json -> public/audio/score.wav (uv, numpy)
bun run studio       # preview
bun run render       # out/memento.mp4 (draft, with presenter placeholder and script guide)
bunx remotion render MementoClean out/memento-clean.mp4   # no guides
```

`bun scripts/stills.ts 3.4 21 40.95` renders single frames (seconds) to `out/stills/`.

## How it's put together

| File | Role |
|---|---|
| `src/timeline.ts` | The one clock: script lines, cues, story time, camera, night terminator, presenter |
| `src/world/draw.ts` | The canvas world: log time axis, memory cloud, recipe arcs, Jev's fan-out, the wordmark |
| `src/world/memories.ts` | The flight, the memories born in chat, and months of older ones (seeded) |
| `src/recipe.ts` | The GBrain page before and after, and when River types each character |
| `src/parts/` | The page, the notification, paper and grain, the presenter placeholder |
| `scripts/export-cues.ts` | Exports every sound-worthy moment from the drawing code |
| `audio/synth.py`, `audio/score.py` | Felt piano, glass, pads, air, clicks, reverb, mastering |

When the recorded voice arrives, re-time `LINES` and the cues that follow them in `timeline.ts`, then
run `bun run audio`. Picture and score move together.
