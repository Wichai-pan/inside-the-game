"""Steps 2 and 4 of the draft video: scene durations from the voice-over, then mux and concatenate.

Run from the recording folder: `python3 compose.py durations` before `node record.mjs`, `python3 compose.py mux` after.
"""
import json
import re
import subprocess
import sys

FFMPEG = "node_modules/ffmpeg-static/ffmpeg"
SCENES = 10


def durations():
    out = []
    for i in range(1, SCENES + 1):
        info = subprocess.run([FFMPEG, "-i", f"vo/s{i}.wav"], capture_output=True, text=True).stderr
        h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info).groups()
        out.append(round(int(h) * 3600 + int(m) * 60 + float(s) + 0.9, 2))
    json.dump(out, open("vo/durations.json", "w"))
    print(out, round(sum(out), 1), "s")


def mux():
    dur = json.load(open("vo/durations.json"))
    with open("clips/list.txt", "w") as lst:
        for i, d in enumerate(dur, 1):
            subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", f"clips/scene{i}/frames.txt",
                            "-i", f"vo/s{i}.wav", "-filter_complex",
                            f"[0:v]fps=30,scale=1280:800,format=yuv420p,tpad=stop_mode=clone:stop_duration=2,trim=duration={d},setpts=PTS-STARTPTS[v];"
                            f"[1:a]adelay=300|300,apad,atrim=duration={d}[a]",
                            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "20", "-c:a", "aac", "-ar", "44100", "-ac", "2",
                            f"clips/s{i}.mp4"], check=True)
            lst.write(f"file 's{i}.mp4'\n")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", "clips/list.txt",
                    "-c", "copy", "-movflags", "+faststart", "draft.mp4"], check=True)


if __name__ == "__main__":
    {"durations": durations, "mux": mux}[sys.argv[1]]()
