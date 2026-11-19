# GTA 6 stream clipper

A Claude Code skill that turns a GTA 6 stream VOD into vertical 9:16 clips for TikTok, YouTube Shorts and Instagram Reels, and checks every clip against the rules it has to survive: Rockstar's video policy, the platform's reused-content rules and your clipping campaign.

You give Claude a VOD link. It finds the moments chat reacted to, transcribes only those minutes, picks the clips and writes the hooks itself, renders 1080x1920 video with a facecam split and word-by-word captions, then tells you which clips pass, which need a second look and which must not be posted.

## Install

As a plugin, inside Claude Code:

```
/plugin marketplace add jutsuhq/gta6-stream-clipper
/plugin install gta6-clipper@gta6-stream-clipper
```

Or copy the skill folder by hand:

```bash
git clone https://github.com/jutsuhq/gta6-stream-clipper
cp -r gta6-stream-clipper/skills/gta6-clipper ~/.claude/skills/
```

You also need:

- [uv](https://docs.astral.sh/uv/) (the script declares its own Python packages; the first run installs them, a few hundred MB)
- [ffmpeg](https://ffmpeg.org/) on PATH

Then ask Claude something like *"clip the best moments from this GTA 6 stream: <url>"*. The skill runs `doctor` first and tells you what is missing.

## How it works

One work folder per VOD. Every step writes files there, so you can stop and resume.

| Step | What happens | Output |
|---|---|---|
| `prepare` | yt-dlp reads metadata, the replay heatmap and the YouTube live chat replay; downloads audio only; measures loudness per second | `state.json`, `chat.json`, `loudness.npy` |
| `signals` | Scores every second from chat speed (weighted by hype words like LMAO, KEKW, W), loudness spikes and the heatmap, all as robust z-scores; picks the top windows with a minimum gap | `candidates.json` |
| `transcribe` | faster-whisper on the candidate windows only, with word timestamps | `candidates.md` |
| pick | Claude reads `candidates.md` and the rubric, then writes tight start and end times, a hook, a title, the clip `kind` and a spoiler flag | `picks.json` |
| `render` | Pulls only the needed seconds of video from the source, finds the facecam with a face detector, stacks cam over gameplay (or uses a blurred background), burns word captions and the hook, normalises audio to -14 LUFS | `out/<id>.mp4`, `out/<id>_sheet.jpg`, `out/<id>.md` |
| review | Claude looks at each 12-frame contact sheet and re-renders a clip with fixes if the crop or captions are wrong | |
| `check` | Format, length, loudness, cutscene and in-game TV clips, spoilers, required hashtags and mentions, banned words, allowed streamers, credit, commercial-use warning | `check.md`, exit code 1 on any FAIL |

The skill never uploads anything. You post the clips yourself.

## A real run

A 5 h 20 min GTA 6 launch stream on YouTube with 4,962 chat messages, on a MacBook (CPU only, faster-whisper `small`):

| Step | Time |
|---|---|
| `prepare` (audio 311 MB, chat replay) | about 3 min |
| `signals` | seconds |
| `transcribe` (12 windows of 50 s) | about 6 min |
| `render` (3 clips, 29-45 s each) | about 3 min |

Of the 12 candidate windows, Claude skipped three story cutscenes, a sponsor read and a stream-settings chat, and picked a truck dying in a river, a physics bug with a floating cat and a confused death. All three passed `check` with one warning each: the example campaign is paid, and Rockstar's policy only covers non-commercial use.

## Rules, in short

Full text with sources: [`skills/gta6-clipper/references/rules.md`](skills/gta6-clipper/references/rules.md).

- **Rockstar / Take-Two** tolerates fan footage for non-commercial use that does not deliberately spoil the story. Isolated cutscenes, in-game TV shows, cheats and mods are take-down examples. A paid clipping campaign is commercial, so it is outside that tolerance. `check` warns on every clip in a paid campaign; the tool cannot make such a clip "safe".
- **YouTube** can refuse or remove monetisation for reused content without significant original commentary or changes. **TikTok** keeps unoriginal reposts out of the For You feed. The hook, captions, edit and context are your contribution; the streamer's reaction is not.
- **Campaigns** set their own length, hashtag, streamer and spoiler rules. Put them in a campaign file ([example](skills/gta6-clipper/campaigns/example.json), [fields](skills/gta6-clipper/references/campaign.md)).
- **Always credit the streamer** and link the source. `render` writes the credit into every post text.

This is a summary, not legal advice. The rules change; read the sources before you post.

## What it does not do

- **No Twitch chat download.** Twitch VODs work for video, audio and loudness, but chat is not fetched. If you have a chat export, pass it with `--chat` (yt-dlp live_chat JSON, a JSON list of `{t, text}`, or `seconds<TAB>text` lines).
- **No live mode.** It works on finished VODs.
- **No visual event detection.** Moments come from chat, loudness and the heatmap, so a silent moment nobody reacted to will be missed.
- **No uploading or scheduling.**
- **No game assets.** No Rockstar logos, artwork or official font are included or added.

## Development

```bash
uv run --python 3.12 --with pytest --with numpy pytest -q   # unit tests
bash tests/smoke.sh                                            # end-to-end on a synthetic VOD (needs ffmpeg)
```

CI runs the unit tests on Linux, macOS and Windows and the smoke test on Linux.

## License

MIT for the code. The bundled Anton font is under the SIL Open Font License ([`OFL.txt`](skills/gta6-clipper/fonts/OFL.txt)).

This project is not affiliated with Rockstar Games or Take-Two Interactive. Grand Theft Auto is their trademark.
