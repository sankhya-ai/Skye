# Skye editions

**Local (today).** Everything runs on your machine: the vendored Chamber relay (or your own), your
Claude Code / Codex installs, and optionally Root with your OpenRouter key. Nothing is hosted for
you; nothing phones home.

**Hosted (not built).** The plan is Chamber + Root on a managed VM as a paid edition: the team bus
and a worker that run while your laptop is closed, with the same contracts and policy files. No
hosted infrastructure exists yet - this document is the design placeholder, not a launched service.

Both editions share the rule that coordination stays plain code: no LLM tokens are spent by Chamber
or the dispatcher. Workers spend only their own subscriptions/keys.
