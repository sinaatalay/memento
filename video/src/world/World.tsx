import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { continueRender, delayRender, useCurrentFrame, useVideoConfig } from "remotion";
import { fontsReady } from "../fonts";
import { H, W } from "../theme";
import { drawWorld } from "./draw";

const sized = (c: HTMLCanvasElement, dpr: number) => {
  if (c.width !== W * dpr) {
    c.width = W * dpr;
    c.height = H * dpr;
  }
  const ctx = c.getContext("2d")!;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
};

export const World: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const ref = useRef<HTMLCanvasElement>(null);
  const scratch = useRef<[HTMLCanvasElement, HTMLCanvasElement] | null>(null);
  const [handle] = useState(() => delayRender("world: fonts"));
  const released = useRef(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    fontsReady.then(() => setReady(true));
  }, []);

  useLayoutEffect(() => {
    if (!ready || !ref.current) return;
    const dpr = window.devicePixelRatio || 1;
    if (!scratch.current) scratch.current = [document.createElement("canvas"), document.createElement("canvas")];
    const ctx = sized(ref.current, dpr);
    const pair: [CanvasRenderingContext2D, CanvasRenderingContext2D] = [
      sized(scratch.current[0], dpr),
      sized(scratch.current[1], dpr),
    ];
    // drawImage of the scratch layers must not be scaled twice
    drawWorld(ctx, frame / fps, pair);
    if (!released.current) {
      released.current = true;
      continueRender(handle);
    }
  }, [frame, fps, ready, handle]);

  return <canvas ref={ref} style={{ position: "absolute", left: 0, top: 0, width: W, height: H }} />;
};
