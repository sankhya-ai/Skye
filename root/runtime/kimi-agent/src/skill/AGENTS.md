# Skill Module Notes

## Scope

- `skill/mod.rs`: skill discovery, frontmatter parsing, layered roots.
- `skill/flow/*`: flow parsing for mermaid and d2.
- `utils/frontmatter.rs`: YAML frontmatter extraction.

## Compatibility Rules

- Skills are discovered from layered roots (builtin → user → project) with later roots overriding.
- Chotu profile skills use `~/.chotu/skills` and `<project>/.chotu/skills`; these are last in their layer so Chotu-specific constitutions override compatible upstream skills by name.
- Flow skills require a `mermaid` or `d2` fenced code block in `SKILL.md`.
- Invalid flow parsing falls back to `standard` skill type.
