import React from "react";
import { Composition } from "remotion";
import "./fonts";
import { Memento } from "./Memento";
import { FPS, H, W } from "./theme";
import { DURATION } from "./timeline";

export const Root: React.FC = () => (
  <>
    <Composition
      id="Memento"
      component={Memento}
      durationInFrames={Math.round(DURATION * FPS)}
      fps={FPS}
      width={W}
      height={H}
      defaultProps={{ guides: true }}
    />
    <Composition
      id="MementoClean"
      component={Memento}
      durationInFrames={Math.round(DURATION * FPS)}
      fps={FPS}
      width={W}
      height={H}
      defaultProps={{ guides: false }}
    />
  </>
);
