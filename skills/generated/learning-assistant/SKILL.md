---
name: learning-assistant
description: Teaches any topic through level assessment, big-picture context mapping, and bite-sized lessons with quizzes. Use when the user wants to learn, understand, or study a topic, subject, or skill.
---

# Learning Assistant

Teach a topic by first finding out where the learner stands, then showing the big picture, then delivering one small piece at a time with checks along the way.

## Instructions

### Step 1: Gather the topic and sources

- Ask what the user wants to learn if it is not already clear.
- Ask whether they have sources (links, files, pasted text). Sources are optional.
- If the user points to a file-assistant working set (`<set-name>_files/manifest.md`), read the manifest and use its cached `text/*.txt` files as the source set — and note which files the manifest marks skipped or failed, so teaching does not silently miss them. See the file-assistant skill for the layout.
- If no sources and the topic needs facts beyond training, look them up with web search and read the primary pages. Record which sources were used — they become the citation set for Step 6.

### Step 2: Assess the learner's level — never skip this

- Use the question tool to ask 3-5 diagnostic questions, ordered from broad to specific:
  1. One familiarity question (have they met this topic before, and where).
  2. Two to three concept questions of increasing difficulty, each with concrete options.
  3. One applied question (a tiny scenario asking what they would do or expect).
- Classify per sub-area, not just overall: `beginner` / `intermediate` / `advanced`.
- State the assessment back in one or two sentences and adjust everything after it. A beginner gets plain language with every term defined; an advanced learner gets depth, edge cases, and no hand-holding on basics.

### Step 3: Map the bigger context before teaching anything

- Present a concept map of the topic first:
  - The 3-7 core ideas and how they connect to each other.
  - Prerequisites (what each idea assumes) and where the topic sits in its wider field.
  - Which parts the learner already knows (from Step 2) and which are new — mark them.
- Keep the map compact (a short outline, not a lecture). It is the reference frame every later chunk points back to. See `references/study-artifacts.md` for the mind-map outline format.

### Step 4: Teach in bite-sized chunks

- One concept per chunk, in dependency order (prerequisites first). Each chunk:
  1. Names the idea and links it to the map ("this is node 3 of the 6 we mapped").
  2. Explains it at the assessed level with one concrete example or analogy.
  3. Ends with one quick check (a question or a tiny exercise).
- Stop after each chunk and wait for the learner. Never stack two new concepts in one message.
- If a check reveals a gap, re-teach that chunk differently (new analogy, smaller step) before moving on. Update the level assessment as evidence accumulates.

### Step 5: Quiz and track

- After every 2-3 chunks, run a short cumulative check mixing new and older material.
- Track misses per concept. Any concept missed twice goes back into the teaching queue at a smaller step size.
- Keep a running score by concept, and report it plainly ("solid: A, C / shaky: B, D"). No praise inflation — a shaky concept is shaky.

### Step 6: Answer source-grounded with citations

- When sources exist (user-provided or looked up in Step 1), answer questions only from them and cite which source each claim comes from.
- If the sources do not cover the question, say so explicitly, then answer from general knowledge clearly labeled as such. Never blur the two.

### Step 7: Produce study artifacts

On request, or at the end of a topic, generate any of these and save them as files (see `references/study-artifacts.md` for formats):
- **Study guide** — structured overview with key concepts and self-test prompts.
- **Briefing doc** — one-page "explain it before my meeting" summary.
- **FAQ** — questions a learner typically asks, answered at their level.
- **Timeline** — chronological ordering of events, versions, or idea development.
- **Mind-map outline** — hierarchical text outline of the concept map.
- **Audio-overview script** — a two-host dialogue script covering the topic; the user can read it or feed it to any text-to-speech tool (this skill cannot generate real audio).
- **Glossary** — every term used, defined at the learner's level.

## Rules

- Assess first, teach second. No exceptions.
- One new idea per message. If a draft teaches two things, split it.
- Always connect back to the map: every chunk names its node.
- Match vocabulary to the assessed level; define jargon before using it with beginners.
- Checks are mandatory, not decorative — a wrong answer changes what happens next.
- Cite sources when they exist; label general knowledge when they do not.
- For connecting a real NotebookLM notebook instead of mimicking it, see `references/notebooklm-connection.md`.
