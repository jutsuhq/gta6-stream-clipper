"""Review: a fresh agent that has not seen the work says yes or no to every rendered clip.

`review <work>` writes one brief per clip into <work>/review/. Hand each brief to a new agent with no other
context. `review <work> --record <id> --verdict yes|no --faults "..."` stores the answer in
<work>/review.json; `check` fails a clip with a "no" and warns on a clip nobody reviewed. Every fault is also
appended to lessons.md next to the work folder, so the next VOD starts from what went wrong before.
"""
from __future__ import annotations

import time
from pathlib import Path

from .common import die, load_json, save_json, work_dir

QUESTIONS = """\
Answer each with yes or no and one line of evidence:

1. Frame 1 alone: would a viewer scrolling past stop? Is the payoff promised or shown in the first frame?
2. Is the hook text readable at phone size and clear of the face, the captions and the platform buttons
   (keep the top 250 px and bottom 400 px of 1920 free of anything important)?
3. Does the facecam crop show the face, and does the gameplay crop show the action, in every frame?
4. Does the clip end within a couple of seconds of the payoff?
5. Does it stand alone for someone who never saw the stream?
6. Is there any isolated cutscene, in-game TV, story spoiler, cheat or exploit, Rockstar logo or official
   artwork in the frames?
7. Next to the reference thumbnails (if any): is this as strong, and if not, what exactly is weaker?
"""

ANSWER = """\
End with exactly one line:
VERDICT: yes
or
VERDICT: no | fault one; fault two; fault three
Say no if any of questions 1-6 fails. Be specific: "hook covers the face in frames 2-4", not "hook could be better".
"""


def brief(pick: dict, sheet: Path, post: str, refs: list[str], lessons: str) -> str:
    lines = [f"# Review clip {pick['id']}", "",
             "You have not seen this project before. Judge only what is in front of you.", "",
             f"- Contact sheet, 12 frames in order, first frame top left: `{sheet.resolve()}` (read it as an image)",
             f"- Length: {pick['cut_end'] - pick['cut_start']:.1f} s",
             f"- Hook on screen for the first seconds: \"{pick.get('hook', '')}\"",
             f"- Title: \"{pick.get('title', '')}\"", "", "Post text:", "", "```", post.strip(), "```", ""]
    if refs:
        lines += ["Reference thumbnails of videos that beat their channel's usual views (read them as images):", ""]
        lines += [f"- `{Path(r).resolve()}`" for r in refs]
        lines.append("")
    if lessons.strip():
        lines += ["Faults found in earlier clips. Check this clip does not repeat them:", "", lessons.strip(), ""]
    return "\n".join(lines + [QUESTIONS, ANSWER])


def parse_faults(text: str | None) -> list[str]:
    return [f.strip() for f in (text or "").replace("\n", ";").split(";") if f.strip()]


def record(work: Path, cid: str, verdict: str, faults: list[str]) -> dict:
    if verdict == "no" and not faults:
        die("a 'no' needs --faults so the clip can be fixed")
    if not (work / "out" / f"{cid}.json").exists():
        die(f"no rendered clip {cid} in {work / 'out'}")
    reviews = load_json(work / "review.json", {})
    entry = {"verdict": verdict, "faults": faults, "at": time.strftime("%Y-%m-%d %H:%M"),
             "round": reviews.get(cid, {}).get("round", 0) + 1}
    reviews[cid] = entry
    save_json(work / "review.json", reviews)
    if faults:
        with (work.parent / "lessons.md").open("a", encoding="utf-8") as f:
            f.writelines(f"- {fault} ({work.name}/{cid})\n" for fault in faults)
    return entry


def cmd_review(args):
    work = work_dir(args.work)
    if args.record:
        if args.verdict not in ("yes", "no"):
            die("--record needs --verdict yes or --verdict no")
        e = record(work, args.record, args.verdict, parse_faults(args.faults))
        print(f"{args.record}: {e['verdict']} (round {e['round']})" + (f": {'; '.join(e['faults'])}" if e["faults"] else ""))
        return
    refs = sorted(str(p) for p in Path(args.refs).glob("*.jpg"))[:args.max_refs] if args.refs else []
    lessons_path = work.parent / "lessons.md"
    lessons = "\n".join(lessons_path.read_text(encoding="utf-8").splitlines()[-args.max_lessons:]) \
        if lessons_path.exists() else ""
    wanted = set(args.ids.split(",")) if args.ids else None
    (work / "review").mkdir(exist_ok=True)
    written = []
    for meta in sorted((work / "out").glob("*.json")):
        pick = load_json(meta)
        sheet = meta.with_name(f"{pick['id']}_sheet.jpg")
        if (wanted and pick["id"] not in wanted) or not sheet.exists():
            continue
        post = meta.with_suffix(".md").read_text(encoding="utf-8") if meta.with_suffix(".md").exists() else ""
        p = work / "review" / f"{pick['id']}.md"
        p.write_text(brief(pick, sheet, post, refs, lessons), encoding="utf-8")
        written.append(str(p))
    if not written:
        die("no rendered clips with a contact sheet in out/; run `render` first")
    print("\n".join(written))
