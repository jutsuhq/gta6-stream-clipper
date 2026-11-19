---
name: gta6-clipper
description: Turn a GTA 6 stream VOD (YouTube or Twitch URL, or a local video) into vertical 9:16 clips for TikTok, YouTube Shorts and Reels. Finds moments from chat spikes, loudness and the replay heatmap, transcribes only those windows, lets Claude pick and write hooks, renders 1080x1920 with a facecam split and word-by-word captions, then checks each clip against Rockstar's video policy, platform reused-content rules and a clipping campaign's rules. Use when the user wants to clip a GTA 6 stream, make Shorts or TikToks from a VOD, join a GTA 6 clipping campaign, or check clips before posting.
---

# GTA 6 stream clipper

One work folder per VOD. Every step writes files there, so you can stop and resume anywhere.

All commands run the bundled script with uv. `SKILL_DIR` below is this skill's base directory (shown when the skill loads):

```bash
uv run SKILL_DIR/scripts/clip.py <command> ...
```

The first run installs Python packages into uv's cache (a few hundred MB, mostly faster-whisper and OpenCV). ffmpeg must be on PATH.

## Workflow

1. **Check the setup** (first time only): `uv run SKILL_DIR/scripts/clip.py doctor`. If ffmpeg is missing, tell the user how to install it for their OS and stop.

2. **Ask for the campaign rules** if the user is clipping for a campaign. Write them to a campaign JSON (template `SKILL_DIR/campaigns/example.json`, fields in `SKILL_DIR/references/campaign.md`). No campaign: skip the `--campaign` flags.

3. **Prepare** the VOD. Downloads metadata, audio only, and the chat replay when YouTube has one:
   ```bash
   uv run SKILL_DIR/scripts/clip.py prepare "<url or file>" [--work clips/name] [--chat chat.json]
   ```
   A 5-hour VOD takes a few minutes. Twitch chat is not downloaded; if the user has a chat export, pass it with `--chat` (yt-dlp live_chat JSON, a JSON list of `{t, text}`, or `seconds<TAB>text` lines). Without chat the tool uses loudness and the heatmap.

4. **Find candidates**: `uv run SKILL_DIR/scripts/clip.py signals <work>` (default 12 windows of about 50 s).

5. **Transcribe the candidates only**: `uv run SKILL_DIR/scripts/clip.py transcribe <work>` (`--model base` is faster, `--model medium` is more accurate). It writes `<work>/candidates.md`.

6. **Scout what already works** (optional, once per batch): ask the user for 3-10 channels or clip pages in the same niche, then
   ```bash
   uv run SKILL_DIR/scripts/clip.py scout <url> [<url> ...] [--rank ratio|vph] [--min-ratio 3]
   ```
   Sources: YouTube `/videos` or `/shorts`, Twitch `/clips?filter=clips&range=7d`, TikTok `@user`. `ratio` ranks each entry against its own channel's median views, so outliers from small channels count; `vph` (views per hour, Twitch and TikTok only) shows what is moving right now. Read `scout/<date>/scout.md` and every image in `thumbs/`, write what the outliers share to `scout/<date>/patterns.md`, and use it for hooks and titles in the next step.

7. **Pick clips yourself.** Read `SKILL_DIR/references/rubric.md`, then `<work>/candidates.md`. Write `<work>/picks.json` exactly as the rubric shows: tight start and end, hook, title, honest `kind` and `spoiler`. Show the user your picks in a short table (id, time, length, hook, why) before rendering.

8. **Render**:
   ```bash
   uv run SKILL_DIR/scripts/clip.py render <work> [--campaign c.json] [--layout auto|cam-top|blur|center] [--style bold|box|clean]
   ```
   `auto` finds the streamer's facecam and stacks it above the gameplay; without a facecam it uses a blurred background. Rendering pulls only the needed seconds of video.

9. **Look at every contact sheet** (`<work>/out/<id>_sheet.jpg`, 12 frames) with the Read tool. Check for: a facecam crop that misses the face or shows game HUD, captions covering something important, the hook covering the face, black frames, a cut that starts mid-action. Fix and re-render that id with `--ids <id>`:
   - wrong facecam: `--cam x,y,w,h` (source pixels) or `--layout blur`; delete `<work>/facecam.json` to re-detect;
   - bad cut: edit `start`/`end` in picks.json;
   - captions in the way: `--style box` or `--style clean`.

10. **Fresh-eyes review.** You wrote the hooks, so you are the worst judge of them. Run
   ```bash
   uv run SKILL_DIR/scripts/clip.py review <work> [--refs scout/<date>/thumbs]
   ```
   It writes `<work>/review/<id>.md` per clip. For each one start a new subagent (Agent tool) whose prompt is only that file's content, nothing about the stream or your reasoning. Record its last line:
   ```bash
   uv run SKILL_DIR/scripts/clip.py review <work> --record <id> --verdict yes|no --faults "fault one; fault two"
   ```
   On a no, fix the faults, re-render with `--ids <id>`, and review again with a new subagent. Faults go to `lessons.md` next to the work folder; later briefs include them, so read it before picking clips for the next VOD.

11. **Check**: `uv run SKILL_DIR/scripts/clip.py check <work> [--campaign c.json]`. It writes `<work>/check.md` and exits 1 on any FAIL, including a clip the reviewer said no to. Never tell the user a FAIL clip is ready. Explain each WARN in one line, especially the commercial-use warning for paid campaigns (see `SKILL_DIR/references/rules.md`).

12. **Hand over**: list each clip with its video path, `post.md` (title, caption, hashtags, streamer credit) and check result. Do not upload anything; the user posts the clips themselves.

## Rules you follow

- Never pick isolated cutscenes, in-game TV shows, story reveals the campaign forbids, cheats or exploits. The reasons are in `references/rules.md`.
- Always keep the streamer credit and source link in the post text.
- Do not add Rockstar logos, artwork or the official font.
- If the user asks whether a paid campaign clip is "allowed by Rockstar", give the honest answer from `references/rules.md`: the policy covers non-commercial fan use, and paid clips are outside it.
- Disk: audio for a 5-hour stream is about 300 MB; clips are 5-15 MB each. Delete `<work>/audio.*` when done if space is tight.
