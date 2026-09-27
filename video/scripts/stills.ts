/**
 * Render a handful of frames without a full render: bun scripts/stills.ts 3.6 12 20.5 ...
 * Arguments are seconds. Output: out/stills/<seconds>.png
 */
import { bundle } from "@remotion/bundler";
import { renderStill, selectComposition } from "@remotion/renderer";
import { mkdirSync } from "node:fs";
import path from "node:path";

const args = process.argv.slice(2);
const compId = process.env.COMP ?? "Memento";
const times = args.map(Number);
const root = path.resolve(import.meta.dir, "..");
mkdirSync(path.join(root, "out/stills"), { recursive: true });

const serveUrl = await bundle({ entryPoint: path.join(root, "src/index.ts"), publicDir: path.join(root, "public") });
const composition = await selectComposition({ serveUrl, id: compId, inputProps: {} });
for (const s of times) {
  const frame = Math.min(composition.durationInFrames - 1, Math.round(s * composition.fps));
  const output = path.join(root, `out/stills/${s.toFixed(2)}.png`);
  await renderStill({ serveUrl, composition, frame, output, chromiumOptions: { gl: "angle" } });
  console.log(output);
}
