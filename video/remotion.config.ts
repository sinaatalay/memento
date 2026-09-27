import { Config } from "@remotion/cli/config";

Config.setVideoImageFormat("jpeg");
Config.setJpegQuality(95);
Config.setConcurrency(8);
Config.setChromiumOpenGlRenderer("angle");
Config.setCodec("h264");
Config.setCrf(16);
