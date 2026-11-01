"""Skye - an autonomous engineering team in a box.

Chamber (vendored at chamber/) is the coordination layer; Root (vendored at root/) is one worker
users can opt into; Claude Code and Codex adapters drive the user's OWN local installs. The
dispatcher is plain code: no LLM in the coordination path, ever (the workers run their own).
"""
__version__ = "0.1.0"
