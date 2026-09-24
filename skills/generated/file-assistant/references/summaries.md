# Summarizing Files

When the user asks for a summary of files in the working set, do not freehand it — route through the two summary skills:

1. **Consult summary-selector** with audience, purpose, source shape (single vs multi-file, harmonious vs conflicting), and the user's time budget. Take its type + level decision.
2. **Run summarizer** on the set's cached `text/*.txt` files (not the raw binaries — the cache is uniform and searchable). For multi-file sets: per-file pass first, then synthesis, keeping per-file attribution so disagreements stay visible.
3. **Cite from the cache anchors** (`file [p.2]`, `deck [Slide 5]`) even inside short levels — a TL;DR with one citation beats a paragraph with none.
4. **Respect set gaps:** files marked skipped/failed in `manifest.md` are excluded and named as excluded. Never summarize a file that was never extracted.

Shortcut only: if the user names the type and level explicitly ("give me a one-page brief"), skip the selector and go straight to the summarizer.
