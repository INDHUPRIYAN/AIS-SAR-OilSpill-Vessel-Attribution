"""Regenerate docs/video/ALL_PROMPTS.md from the six act files.

The act files are the source of truth: each ``### Scene NN`` section holds one ```text
fenced prompt. This script concatenates every prompt with the six locked blocks from
00_LOCKED_BLOCKS.md so each entry in ALL_PROMPTS.md can be pasted into Google Flow as-is.

Run from anywhere:  python scripts/build_video_prompts.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

VIDEO_DIR = Path(__file__).resolve().parents[1] / "docs" / "video"
ACT_FILES = [
    "ACT_1_THE_DISCHARGE.md",
    "ACT_2_THE_EYE.md",
    "ACT_3_THE_REVERSAL.md",
    "ACT_4_THE_SUSPECTS.md",
    "ACT_5_GODS_EYE.md",
    "ACT_6_THE_VERDICT.md",
]
FENCE = re.compile(r"```text\n(.*?)\n```", re.S)

HEADER = """# All prompts — paste-ready

Every one of the thirty scene prompts with the six locked blocks already appended. Copy a
whole fenced block into Flow's prompt field as-is.

The **negative** block is not in these prompts — it goes in Flow's separate negative-prompt
field, once, and stays there for the whole session:

```text
{negative}
```

Read the acts if you want the direction notes; this file is for generating.

Regenerate this file after editing any act file:
`python scripts/build_video_prompts.py`

---
"""


def main() -> int:
    blocks = FENCE.findall((VIDEO_DIR / "00_LOCKED_BLOCKS.md").read_text(encoding="utf-8"))
    if len(blocks) != 7:
        print(f"expected 7 locked blocks, found {len(blocks)}", file=sys.stderr)
        return 1
    tail = "\n\n".join(b.strip() for b in blocks[:6])
    negative = blocks[6].strip()

    out = [HEADER.format(negative=negative)]
    scenes = 0

    for name in ACT_FILES:
        text = (VIDEO_DIR / name).read_text(encoding="utf-8")
        out.append("\n## %s\n" % text.split("\n", 1)[0].lstrip("# ").strip())
        for section in re.split(r"\n### ", text)[1:]:
            heading = section.split("\n", 1)[0].strip()
            start = re.search(r"\*\*Start frame:\*\*\s*(.+?)\n", section)
            prompt = FENCE.search(section)
            if prompt is None:
                print(f"{name}: no prompt fence under {heading!r}", file=sys.stderr)
                return 1
            scenes += 1
            out.append("\n### %s\n" % heading)
            out.append("*Start frame: %s*\n" % (start.group(1).strip() if start else "—"))
            out.append("```text\n%s\n\n%s\n```\n" % (prompt.group(1).strip(), tail))

    out.append(
        "\n---\n\n*%d scenes · 8 seconds each · %d:%02d total.*\n"
        % (scenes, scenes * 8 // 60, scenes * 8 % 60)
    )
    (VIDEO_DIR / "ALL_PROMPTS.md").write_text("".join(out), encoding="utf-8")
    print(f"wrote {VIDEO_DIR / 'ALL_PROMPTS.md'} — {scenes} scenes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
