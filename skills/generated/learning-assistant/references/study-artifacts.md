# Study Artifact Formats

Templates for the artifacts Step 7 of the skill can produce. Keep each artifact tight — it is a study aid, not a textbook.

## Study guide (`<topic>_study-guide.md`)

```markdown
# Study Guide: <topic>
Level: <beginner|intermediate|advanced> | Date: <date>

## The big picture (3-6 sentences)
...

## Core concepts
### 1. <concept> [solid|shaky — from quiz tracking]
- What it is (2-3 sentences at the learner's level):
- Example:
- Connects to: <other concept numbers>

## Self-test prompts
- [ ] <question covering concept 1>
- [ ] ...

## Sources
- <source> — used for <claims>
```

## Briefing doc (`<topic>_briefing.md`)

One page max: what the topic is, why it matters, the 3-5 things to know, one-line glossary of unavoidable terms. Written so the learner can read it 10 minutes before a meeting.

## FAQ (`<topic>_faq.md`)

5-10 questions a learner at the assessed level typically asks (plus any the learner actually asked), each answered in 2-4 sentences at their level. Wrong-but-common beliefs get a "common misconception" callout.

## Timeline (`<topic>_timeline.md`)

Chronological bullet list: date/version → what happened → why it mattered for the topic. Only for topics where order matters (history, tech evolution, a standard's versions). Skip otherwise rather than forcing it.

## Mind-map outline (`<topic>_mindmap.md`)

Nested outline of the Step 3 concept map, marking known vs new per the assessment:

```markdown
- <topic>
  - [known] <concept> — one-line reminder
  - [new] <concept>
    - [new] <sub-concept>
```

## Audio-overview script (`<topic>_overview-script.md`)

A two-host dialogue (Host A curious, Host B expert) walking through the topic in 800-1200 words, conversational, no headers inside the dialogue. Note at the top: paste into any TTS tool for a podcast-style listen. The skill itself cannot generate audio.

## Glossary (`<topic>_glossary.md`)

Alphabetical term → one-sentence definition at the learner's level. Every term the lessons used, nothing extra.
