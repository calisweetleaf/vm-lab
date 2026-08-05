---
applyTo: "{archive,components,extras,quarantine,docs/lineage}/**/*"
---

# Preserved and unpromoted surfaces

These paths are source evidence, donor candidates, cold addons, lineage, or
quarantine. Editing or importing them does not promote them.

- Preserve provenance and original hashes unless a new snapshot operation
  explicitly owns the change.
- Never import `quarantine/` into the live package.
- Salvage named concepts through a new bounded owner and physical gate; do not
  restore old supervisors/orchestrators wholesale.
- Consult [`../../docs/MIGRATION_MAP.md`](../../docs/MIGRATION_MAP.md) and
  [`../../FAILURE_GRAMMAR.md`](../../FAILURE_GRAMMAR.md) before promotion work.
