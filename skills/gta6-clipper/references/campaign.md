# Campaign files

A campaign file holds the rules of one clipping campaign so `render` and `check` can apply them. Copy `campaigns/example.json`, then fill it from the campaign page. Leave out what the campaign does not say.

| Field | Type | Used by | Meaning |
|---|---|---|---|
| `name` | string | check | Shown in `check.md`. |
| `paid` | bool | check | `true` adds the Rockstar commercial-use warning to every clip. |
| `min_seconds`, `max_seconds` | number | render, check | Clip length limits. Defaults 10 and 90. |
| `required_hashtags` | list | render, check | Added to every `post.md`; `check` fails if one is missing. |
| `required_mentions` | list | render, check | Same, for `@accounts`. |
| `banned_words` | list | check | Whole-word match in title, post text and the words spoken in the clip. |
| `allowed_creators` | list | check | Streamer names allowed by the campaign; matched against the VOD uploader. |
| `allow_spoilers` | bool | check | `false` fails spoiler clips, `true` passes them, missing gives a warning. |
| `platforms` | list | you | Where you will post. Not checked; a reminder. |
| `notes` | string | you | Anything else from the campaign page: view counting, payout rules, deadlines. |

`check` cannot verify view rules, payout terms, account limits or whether you are allowed to join. Read the campaign page.
