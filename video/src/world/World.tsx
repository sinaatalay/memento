import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { continueRender, delayRender, useCurrentFrame, useVideoConfig } from "remotion";
import { fontsReady } from "../fonts";
import { H, W } from "../theme";
import { drawWorld } from "./draw";

export const World: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const ref = useRef<HTMLCanvasElement>(null);
  const [handle] = useState(() => delayRender("world: fonts"));
  const released = useRef(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    fontsReady.then(() => setReady(true));
  }, []);

  useLayoutEffect(() => {
    if (!ready || !ref.current) return;
    const c = ref.current;
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== W * dpr) {
      c.width = W * dpr;
      c.height = H * dpr;
    }
    const ctx = c.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    drawWorld(ctx, frame / fps);
    if (!released.current) {
      released.current = true;
      continueRender(handle);
    }
  }, [frame, fps, ready, handle]);

  return <canvas ref={ref} style={{ position: "absolute", left: 0, top: 0, width: W, height: H }} />;
};
