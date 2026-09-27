"""Cut the presenter's take to his speech, map each section onto the film, composite. uv run --no-project scripts/assemble.py"""
import json, subprocess, sys
T = json.load(open("out/vo/transcript.json"))
PAD0, PAD1 = 0.12, 0.18
# (transcript segment indices, film interval) in story order
SECTIONS = [
    ([0], (0.0, 4.0)),
    ([1, 2], (4.0, 8.2)),
    ([3], (8.2, 12.0)),
    ([4, 5, 6], (12.0, 17.6)),
    ([7, 8], (17.6, 25.9)),
    ([9], (25.9, 28.8)),
    ([10, 11, 12], (28.8, 38.9)),
    ([13, 14], (38.9, 42.6)),
    ([15], (42.6, 45.9)),
    ([16], (45.9, 51.1)),
    ([17, 18], (51.1, 59.0)),
]
LEAD, TAIL = 0.6, 2.8
fc, vl, al, fl, sl = [], [], [], [], []
k = 0
for si, (segs, (f0, f1)) in enumerate(SECTIONS):
    parts = []
    for j in segs:
        s = max(0, T[j]["s"] - PAD0); e = T[j]["e"] + PAD1
        parts.append((s, e))
    dur = sum(e - s for s, e in parts)
    if si == 0: dur += LEAD
    if si == len(SECTIONS) - 1: dur += TAIL
    # presenter + voice pieces
    for pi, (s, e) in enumerate(parts):
        fc.append(f"[0:v]trim={s:.3f}:{e:.3f},setpts=PTS-STARTPTS[pv{k}]")
        fc.append(f"[0:a]atrim={s:.3f}:{e:.3f},asetpts=PTS-STARTPTS[pa{k}]")
        if si == 0 and pi == 0:
            fc.append(f"[pv{k}]tpad=start_duration={LEAD}:start_mode=clone[pv{k}b]"); vl.append(f"[pv{k}b]")
            fc.append(f"[pa{k}]adelay={int(LEAD*1000)}:all=1[pa{k}b]"); al.append(f"[pa{k}b]")
        elif si == len(SECTIONS) - 1 and pi == len(parts) - 1:
            fc.append(f"[pv{k}]tpad=stop_duration={TAIL}:stop_mode=clone[pv{k}b]"); vl.append(f"[pv{k}b]")
            fc.append(f"[pa{k}]apad=pad_dur={TAIL}[pa{k}b]"); al.append(f"[pa{k}b]")
        else:
            vl.append(f"[pv{k}]"); al.append(f"[pa{k}]")
        k += 1
    # film piece stretched to the section's spoken length
    f = dur / (f1 - f0)
    fc.append(f"[1:v]trim={f0:.3f}:{f1:.3f},setpts=(PTS-STARTPTS)*{f:.5f}[fv{si}]")
    tempo = 1 / f
    chain = []
    while tempo < 0.5: chain.append("atempo=0.5"); tempo /= 0.5
    while tempo > 2.0: chain.append("atempo=2.0"); tempo /= 2.0
    chain.append(f"atempo={tempo:.5f}")
    fc.append(f"[1:a]atrim={f0:.3f}:{f1:.3f},asetpts=PTS-STARTPTS,{','.join(chain)},apad=whole_dur={dur:.3f},atrim=0:{dur:.3f}[fa{si}]")
    fl.append(f"[fv{si}]"); sl.append(f"[fa{si}]")
    print(f"section {si}: film {f0}-{f1} -> {dur:.2f}s (x{f:.2f})", file=sys.stderr)
fc.append(f"{''.join(fl)}concat=n={len(fl)}:v=1:a=0,fps=30[film]")
fc.append(f"{''.join(sl)}concat=n={len(sl)}:v=0:a=1[score]")
fc.append(f"{''.join(vl)}concat=n={len(vl)}:v=1:a=0,fps=30[pres]")
fc.append(f"{''.join(al)}concat=n={len(al)}:v=0:a=1[voice]")
S = 300
fc.append(f"[pres]crop=760:760:520:190,scale={S}:{S},format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='if(lte(hypot(X-{S/2},Y-{S/2}),{S/2-1}),255,0)'[bub]")
fc.append(f"color=c=0xF7F5F1:s={S+12}x{S+12},format=rgba,geq=r='247':g='245':b='241':a='if(lte(hypot(X-{(S+12)/2},Y-{(S+12)/2}),{(S+12)/2-1}),255,0)'[ring]")
fc.append(f"[film][ring]overlay=54:{1080-S-12-54}:shortest=1[f1]")
fc.append(f"[f1][bub]overlay=60:{1080-S-60}:shortest=1[vout]")
fc.append("[voice]highpass=f=80,acompressor=threshold=-20dB:ratio=3:attack=5:release=120,loudnorm=I=-16:TP=-1.5:LRA=9[vn]")
fc.append("[score]volume=0.32[sc]")
fc.append("[vn][sc]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.9[aout]")
open("out/vo/graph.txt", "w").write(";\n".join(fc))
cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", "out/vo/raw.mov", "-i", sys.argv[1] if len(sys.argv) > 1 else "out/film-clean.mp4",
       "-/filter_complex", "out/vo/graph.txt", "-map", "[vout]", "-map", "[aout]",
       "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "out/memento-final.mp4"]
subprocess.run(cmd, check=True)
print("done", file=sys.stderr)
