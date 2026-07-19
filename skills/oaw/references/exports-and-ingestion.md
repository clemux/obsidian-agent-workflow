# Safe export ingestion and outbound exports

Read this before running any `oaw ingest` or `oaw export` command.

## Safe export ingestion

Use `oaw ingest safe-export` to move approved handoff notes into the vault:

```bash
OAW_INGESTION_ROOT=/path/to/ingestion-root \
  oaw ingest safe-export
OAW_INGESTION_ROOT=/path/to/ingestion-root \
  oaw ingest safe-export --write
oaw ingest safe-export --ingestion-root /path/to/handoff --destination "Imports/Reviewed"
```

- Scans markdown files in the handoff folder:
  - default: `OAW_INGESTION_ROOT`
  - fallback: `~/obsidian-ingestion`
- Reads frontmatter only; no body text is used for acceptance.
- Accepted markers (frontmatter):
  - `export-scope: personal` (preferred)
  - `export-approved: personal`
  - `safe-export-personal: true`
  - `safe-export-personal` tag
- Dry-run by default: it reports what would be ingested or rejected without writing.
- `--ingestion-root` overrides the environment/default source and `--destination` sets a vault-relative destination.
- `--write` performs the actual move:
  - accepted notes go to the vault-relative `Imports/Safe export`
  - rejected notes go to `.rejected/` in the handoff path

Safety is explicit by design: only frontmatter-marked files are ingested.

## Safe outbound exports

Use `oaw export note` only for notes that have explicit frontmatter approval:

```yaml
export-scope: work
return_ingest: true
export_artifacts:
  - scripts/run.sh
```

Export a bundle:

```bash
oaw export note OAW-TSK-export-example --target work --output-root ~/obsidian-export
```

Validate a returned or transferred bundle before trusting it on the receiving side:

```bash
oaw export validate ~/obsidian-export/OAW-TSK-export-example --target work
```

- `note` refuses unmarked notes and notes whose `export-scope` does not match `--target`. Legacy `safe_for_export: true` plus a matching `export_target` remains accepted for existing notes.
- The bundle contains `note.md`, optional copied artifacts from `export_artifacts`, and `manifest.json`.
- Existing bundles are refused unless `export note --force` is explicit; `validate --target` defaults to the manifest target when omitted.
- Manifest paths are vault-relative, not absolute local paths.
- `validate` confines manifest paths to the bundle and checks the safe marker, target, note checksum, artifact checksums, and artifact presence.
