import { loadFont } from "@remotion/fonts";
import { staticFile } from "remotion";

export const fontsReady = Promise.all([
  loadFont({ family: "Newsreader", url: staticFile("fonts/Newsreader.ttf"), weight: "200 800", style: "normal" }),
  loadFont({ family: "Newsreader", url: staticFile("fonts/Newsreader-Italic.ttf"), weight: "200 800", style: "italic" }),
  loadFont({ family: "Instrument Serif", url: staticFile("fonts/InstrumentSerif-Regular.ttf"), style: "normal" }),
  loadFont({ family: "Instrument Serif", url: staticFile("fonts/InstrumentSerif-Italic.ttf"), style: "italic" }),
  loadFont({ family: "Geist Mono", url: staticFile("fonts/GeistMono.ttf"), weight: "100 900" }),
]).then(() => document.fonts.ready);
