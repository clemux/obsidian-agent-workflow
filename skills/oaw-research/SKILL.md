---
name: oaw-research
description: Preflight and hand off OAW deep-research prompts, ingest finished provider reports, preserve raw artifacts, and normalize results. Use for provider-visible handoff and result intake on OAW project research packets; the core `oaw` skill and CLI own packet scaffolding and launched-run registration.
---

# OAW Research

Use this skill for OAW project research packets under
`Projects/<project>/Research/<track>/` that are run through external providers such
as ChatGPT, Gemini, and Claude.

## Helper

Resolve `<skill-dir>` from this loaded skill's actual path; do not assume a Codex,
Claude, username, or checkout location. Use the bundled helper for deterministic
handoff and intake:

```bash
python <skill-dir>/scripts/oaw_research.py --help
```

The helper is stdlib-only and operates on filesystem paths. Prefer explicit file paths for downloaded reports; use `--latest-download` only when the user asks for the old Downloads-folder workflow.

## OAW Project Packets

For packets under `Projects/<project>/Research/<track>`, use OAW to scaffold the
packet and register each run after it is launched:

```bash
oaw research scaffold --project <project> --track <track> --title "Topic title"
oaw research start --project <project> --track <track> --source <human-label> --url <https-url>
```

Use the installed `oaw` for real-vault operations. Only while developing OAW or
testing against a temporary vault, run the checkout as `uv run python bin/oaw ...`;
bare `python bin/oaw` lacks the project environment. Scaffold owns `Prompt.md`,
`Synthesis.md`, and the shared Base. Start owns the single running result note and
prompt registration. Do not rebuild packet structure or pre-create provider
placeholders. The helper intentionally has no competing scaffold or run-registration
commands.

## Handoff Prompts

When walking the user through launching provider runs, use the copy-only format. Do not add summaries, fences, reminders, or extra commentary.

Generate the exact handoff text:

```bash
python <skill-dir>/scripts/oaw_research.py handoff <Research/track> ChatGPT
```

`handoff` extracts only the fenced `text` block under `## Deep research prompt`, without its heading or fence markers, then preflights that exact provider-visible text. It refuses Obsidian wikilinks, `obs:`
references, `Projects/` or `Agents/` paths, internal `AGT-*`/`OAW-*`/`FAB-*`/
`CDX-*`/`SR-*`/`PMX-*` IDs, and local `Consumers:` headings. Keep packet metadata
before the deep-research heading and use plain downstream artifact names in the
provider prompt.

The response to the user must be exactly:

```text
Copy to ChatGPT:

<prompt body>
```

After the launched run yields a share URL, use `oaw research start`.

## Intake Finished Reports

Ingest a finished report into its existing result placeholder:

```bash
python <skill-dir>/scripts/oaw_research.py intake <Research/track> ChatGPT --file <downloaded-report.md> --url <share-url>
```

Intake behavior:

- preserve the original input as `Raw - <Provider>.md`;
- preserve existing result-note frontmatter and replace the body with the cleaned report;
- set `status: done`;
- set `url:` when provided;
- strip ChatGPT private-use Unicode citation spans such as `...` as whole spans, then remove any stray private-use characters;
- record `pua_chars_stripped`, `source_list_present`, and `capture_quality`.

Use `--clipboard` only when the report is already on an X11 clipboard and `xclip` is
available. Prefer an explicit `--file` everywhere else.

## Guardrails

- Do not overwrite non-placeholder packet files unless the user explicitly asks for replacement or the helper is run with `--force`.
- Do not try to recover source URLs from ChatGPT/Gemini citation widgets after the fact; the source-requirements clause is the fix.
- Keep session URLs in result-note frontmatter, not in the body, so Obsidian Bases can query them.
- Preserve raw provider artifacts even when cleaned intake succeeds.
