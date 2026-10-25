# Demo video

`final_candidate.mp4`: 1:50, 1280×800, H.264 + AAC, 11 MB. Kept out of git (`submission/video/*.mp4` is ignored). Recorded automatically on 25 Oct 2026 from the redesigned public site (https://wichai-pan.github.io/inside-the-game/, commit d4a0446), following `../video_script.md`.

- Headless Google Chrome via Playwright; frames captured as screenshots at ~25–30 fps (Chrome's own video recorder crops the bottom of the viewport on this Mac).
- Voice-over: macOS `say`, voice "Daniel" (en_GB), 185 wpm. Synthetic voice, no music.
- The caption boxes and the rejection card are injected into the page during recording; the site itself is unchanged. The rejection reason and the passing line in scene 8 are the real ones stored in `docs/synthetic/remix-42.json` (moment 3:39, casual, English).
- Scene 10 shows the real output of `python -m insidegame.ask "Who scored the goals, and how good were the chances?"` from 25 Oct, rendered as a terminal card; scorers, xG and distances were checked against the bundle.
- `final_candidate.srt`: English subtitles, one cue per scene.
- `final_candidate_contact_sheet.jpg`: two frames per scene.

## Remaining weaknesses

- The synthetic voice is clear but flat; a human voice-over on the same timings would present better.
- Scene 6 holds its last frame for about a second.

## Re-recording

Uses Playwright and a self-contained ffmpeg from npm (Homebrew's ffmpeg on this Mac fails to load `libx265`). Selectors used: `#seek`, `#play`, `#profile`, `#lang`, `#speed`, `#player`, `#dots g.pl`, `#pitch`, `#transcript-card`.

```sh
mkdir rec && cd rec && npm init -y && npm i playwright@1.56.1 ffmpeg-static@5.2.0
cp ../submission/video/tools/record.mjs ../submission/video/tools/compose.py . && mkdir vo && cp ../submission/video/tools/lines.txt vo/
# 1. voice-over: one file per line of lines.txt (-nostdin stops ffmpeg from eating the loop's input)
i=1; while IFS= read -r l; do say -v Daniel -r 185 -o vo/s$i.aiff "$l"; node_modules/ffmpeg-static/ffmpeg -nostdin -y -i vo/s$i.aiff -ar 44100 -ac 1 vo/s$i.wav; i=$((i+1)); done < vo/lines.txt
# 2. scene durations from the voice-over (+0.6 s each)
python3 compose.py durations
# 3. record all scenes, or some: node record.mjs 4,8
node record.mjs
# 4. mux each scene with its voice-over and concatenate into final_candidate.mp4
python3 compose.py mux
```
