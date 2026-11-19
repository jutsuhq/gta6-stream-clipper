#!/usr/bin/env bash
# End-to-end run on a synthetic VOD: prepare -> signals -> render -> check (no whisper download).
set -euo pipefail
C="$(cd "$(dirname "$0")/.." && pwd)/skills/gta6-clipper/scripts/clip.py"
T="$(mktemp -d)"; cd "$T"
ffmpeg -v error -y -f lavfi -i testsrc2=s=1280x720:r=30:d=240 \
  -f lavfi -i "sine=f=300:d=240,volume='if(between(t,150,162),1.0,0.05)':eval=frame" \
  -c:v libx264 -preset ultrafast -c:a aac -shortest vod.mp4
python3 -c "open('chat.txt','w').write('\n'.join([f'{t}\thi' for t in range(0,240,7)]+[f'{158+i*0.2:.1f}\tKEKW clip it' for i in range(40)]))"
uv run -q "$C" prepare vod.mp4 --work w --chat chat.txt --uploader Tester
uv run -q "$C" signals w --top 2
echo '[{"id":"c01","start":140,"end":165,"title":"Smoke","hook":"Smoke test hook","kind":"chase","spoiler":false}]' > w/picks.json
echo '{"name":"Smoke","min_seconds":15,"max_seconds":60,"required_hashtags":["#gta6"]}' > camp.json
uv run -q "$C" render w --campaign camp.json --layout blur
uv run -q "$C" review w
test -s w/review/c01.md
uv run -q "$C" review w --record c01 --verdict yes
uv run -q "$C" check w --campaign camp.json
test -s w/out/c01.mp4 && test -s w/out/c01_sheet.jpg
echo "smoke ok"
