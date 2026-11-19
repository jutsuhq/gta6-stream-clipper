# Rules a GTA 6 clip has to survive

Three sets of rules apply to every clip: the game publisher's video policy, the platform you post on, and the campaign that pays you (if any). This file sums them up in plain words. It is not legal advice. Read the sources yourself before you post; they change.

Checked: 2026-11-19.

## 1. Rockstar Games / Take-Two video policy

Source: [Policy on posting copyrighted Rockstar Games material](https://support.rockstargames.com/articles/7bNaeoMFTV0iUDGhStTXvz/policy-on-posting-copyrighted-rockstar-games-material) (Rockstar Support, last updated January 10, 2025).

What it says, in short:

- Take-Two generally does not object to fans using game footage for **non-commercial** use that does not deliberately spoil the plot.
- "Non-commercial" means you don't make money from the footage and don't use it to promote a product or service. Take-Two can take footage down at any time.
- The policy is meant for occasional fan use. It does not cover film, TV, streaming distribution deals, music videos, ads or books.

Things the policy lists as take-down examples:

| Example | What it means for clips | How the skill handles it |
|---|---|---|
| Spoilers | The ending, edited combinations of cutscenes or story dialogue, big story reveals. Cutscenes inside a longer playthrough are fine; isolated cinematics are removed. | Picks carry `spoiler` and `kind`. `check` fails `kind: cutscene` and fails spoilers unless the campaign allows them. |
| In-game entertainment | TV shows, comedy sets and similar in-game shows posted on their own. | `check` fails `kind: in-game-tv`. |
| Encouraging cheating | Money/XP exploits, duping, god-mode glitches, mod menus in online modes. | Rubric tells Claude not to pick these. |
| Promoting mods or ports | Unauthorised mods, ports to other platforms or engines. | Rubric tells Claude not to pick these. |
| Pre-release footage | Leaked footage from before release. | Not relevant after launch; never clip leaked material. |

**Paid clipping campaigns are commercial.** The policy's tolerance covers non-commercial fan use. If a campaign pays per view, you are outside what the policy describes, and Take-Two can remove the clip or strike the account. `check` prints a WARN for every clip when the campaign file has `"paid": true`. That warning is the honest state of things; the tool cannot make a paid clip "safe".

Questions go to the address on the policy page.

## 2. Platforms

### YouTube (Shorts and long-form)

Sources: [YouTube channel monetization policies](https://support.google.com/youtube/answer/1311392), [FAQ: Reused content and the YouTube Partner Program](https://support.google.com/youtube/community-guide/271248162).

- "Reused content" is content taken from YouTube or elsewhere **without significant original commentary, substantive changes, or educational or entertainment value**. Channels made of it can be refused or removed from the Partner Program.
- Clips, compilations and reactions are reviewed under this policy. A raw re-upload of someone else's stream segment is the textbook case.
- The streamer's own reaction is part of the source, not something you added. Your own additions are the edit, the hook, captions and context.
- Copyright claims (Content ID) are separate from the reused-content rule. Game audio and licensed in-game radio music can trigger claims.

### TikTok

Source: [Integrity and Authenticity, Community Guidelines](https://www.tiktok.com/safety/en/policies-and-engagement/integrity-authenticity) and [For You feed Eligibility Standards](https://www.tiktok.com/safety/en/policies-and-engagement/fyf-standards).

- Content that includes unoriginal or reused material without anything new is not eligible for the For You feed. In practice the clip still posts but gets little or no reach.

### Instagram Reels

Not summarised here yet. Check Instagram's own recommendation guidelines before posting reposted stream footage. The same advice applies: edit, caption and add something.

## 3. The campaign

Clipping campaigns (Whop Content Rewards, clip marketplaces, a streamer's own program) each set their own rules. Typical ones:

- clip length range, aspect ratio, minimum resolution
- required hashtags, @mentions or a link in bio
- which streamers and which VODs you may clip
- banned words or topics, no spoilers, no reuploads of the full VOD
- one account per person, no bought views, how views are counted and when payout happens

Copy the real rules from the campaign page into a campaign file (see `campaigns/example.json` and `references/campaign.md`). `check` tests what it can measure: length, format, tags, banned words, allowed streamers and spoilers. It cannot check view rules or payout terms.

## 4. Credit the streamer

Always credit the streamer and link the source. It is required by most campaigns, it is fair, and it helps when a platform reviews a reused-content report. `render` writes the credit into every `post.md`.
