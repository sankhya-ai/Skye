"""Which approvals root settles by itself, and which it brings to the owner.

Ported from Chotu Coder's tiered approvals (``agent_core/coder_approvals.py``):

* a shell command is classified at the token level, default-deny, into
  ``read`` (only inspects state), ``verify`` (the project's own build, test,
  lint and type checks) or ``needs_trust`` (anything that can change state,
  and anything not recognised);
* the owner's decisions feed a persistent **trust ledger**: an action class
  such as ``run command:git push`` earns automatic approval after
  :data:`PROMOTE_AFTER` consecutive owner approvals, and loses it on the first
  rejection;
* "Allow for this session" is a host-side grant for one action class. The
  runtime is told ``approve`` — never ``approve_for_session`` — because the
  runtime caches that answer per action string, and would then skip the owner
  for every later command, not just the one class the owner allowed.

Adapted for root, which runs in the owner's real checkout rather than in a
disposable worktree: git subcommands that move the checkout (commit, checkout,
merge, rebase, stash, reset…), in-place editors (``sed -i``), formatters that
rewrite files, and package installs all need trust. Chotu let those through
because its coder sessions were sandboxed.

The lexer splits shell punctuation (``;``, ``&&``, ``|``, redirections) even
without surrounding spaces and treats newlines as command separators, so
``echo hi;rm x`` is two commands, not one ``echo``.
"""

from .classify import (
    ACTION_EDIT_FILE,
    ACTION_EDIT_OUTSIDE,
    ACTION_READ_OUTSIDE,
    ACTION_RUN_COMMAND,
    TIER_READ,
    TIER_TRUST,
    TIER_VERIFY,
    Classification,
    classify_command,
    command_of,
)
from .ledger import PROMOTE_AFTER, TrustLedger
from .policy import Decision, decide

__all__ = [
    "ACTION_EDIT_FILE",
    "ACTION_EDIT_OUTSIDE",
    "ACTION_READ_OUTSIDE",
    "ACTION_RUN_COMMAND",
    "PROMOTE_AFTER",
    "TIER_READ",
    "TIER_TRUST",
    "TIER_VERIFY",
    "Classification",
    "Decision",
    "TrustLedger",
    "classify_command",
    "command_of",
    "decide",
]
