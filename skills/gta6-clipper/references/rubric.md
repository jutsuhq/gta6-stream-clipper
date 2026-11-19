# Picking clips from candidates.md

`candidates.md` lists windows where chat, loudness and (when YouTube has it) the replay heatmap spiked. A spike says *something* happened. Your job is to decide whether it is a clip, and where it starts and ends.

## What makes a GTA 6 stream clip work

1. **The first two seconds explain why to keep watching.** Start right before the setup, not at the start of a sentence two topics earlier. A cop car appearing, the streamer saying "watch this", a plan being announced.
2. **There is a payoff and it lands inside the clip.** The crash, the escape, the fail, the line that made chat laugh. End 1-2 seconds after the payoff, on the reaction, not 20 seconds later.
3. **It stands alone.** A viewer who has never seen the stream understands it. If the joke needs ten minutes of context, skip it.
4. **The streamer reacts.** Clips that sell are gameplay plus a human reaction. A quiet streamer over impressive gameplay is weaker than a loud reaction to a normal moment.
5. **No dead air.** Long silence, menus, loading screens, map screens and inventory browsing kill retention. Cut them or skip the window.
6. **Short beats long.** 20-40 seconds is the default. Go longer only when the story needs it, and stay inside the campaign limits.

Good GTA 6 moment types: police chases and escapes, crashes and ragdoll fails, heists going wrong, absurd NPC behaviour, a perfect stunt, the streamer losing it at something, a funny exchange with a co-streamer or chat.

## Do not pick

- **Isolated cutscenes** or edited sequences of story scenes (`kind: cutscene`). Rockstar lists these as take-down examples.
- **In-game TV, comedy or radio shows** on their own (`kind: in-game-tv`).
- **Story reveals and the ending.** If the clip spoils the story, set `spoiler: true`; `check` fails it unless the campaign allows spoilers.
- **Cheats, money or XP exploits, mod menus, glitches that give an advantage online.**
- Moments that rely on someone else's music playing loudly (copyright claims).
- Anything where the streamer is reading out private info, or chat harassment.

## Writing the hook and title

- The hook is the text on screen for the first 3 seconds. 3-7 words. It sets up the payoff without giving it away: "He thought the cops were gone", "Worst getaway driver in Leonida".
- The title is for the post. Say what happens, in plain words. No fake claims, no "GONE WRONG" if nothing went wrong.
- Do not put Rockstar logos, official artwork or the official font on the clip. The tool renders text in a neutral font.

## picks.json

Write `picks.json` in the work folder. One object per clip, best first:

```json
[
  {
    "id": "c03",
    "start": 5021.4,
    "end": 5049.0,
    "title": "Hazard tries to outrun four cop cars in a bus",
    "hook": "He really thought the bus would work",
    "caption": "The bus did not work.",
    "kind": "chase",
    "spoiler": false,
    "hashtags": ["#gta6", "#gtavi", "#gaming"],
    "score": 9,
    "reason": "Chat spike x40 KEKW, clear setup at 5021, crash payoff at 5045."
  }
]
```

- `id`: the candidate id you took it from (add `b`, `c` if you cut two clips from one window).
- `start`/`end`: absolute seconds in the VOD (or `"1:23:41"`). Use the transcript timestamps. `render` snaps both to word edges.
- `kind`: one of `gameplay`, `reaction`, `chase`, `fail`, `funny`, `skill`, `cutscene`, `in-game-tv`, `other`. Be honest; `check` relies on it.
- `spoiler`: `true` if the clip shows a story reveal.
- `score`: 1-10, your estimate of how well it works as a short. Skip anything under 6.
- `reason`: one line on why, citing the signal and transcript evidence.

Pick 3-6 clips from 12 candidates. Fewer strong clips beat many weak ones.
