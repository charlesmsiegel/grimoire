# Retrying ambiguous greetings

## Ambiguous-greeting narrow-and-retry

A character can already have a hand-built or previously-imported greeting library whose stored
bodies no longer line up 1:1 with the current card (edited since, or partially imported). The
apply script's own guard refuses to guess in that case (`"existing greetings for this
character/version don't match the current card content — cannot safely resolve which is which"`)
rather than risk silently corrupting real content — this is a deliberate safety feature, not a
bug to route around. When every error from an attempt is exactly this reason, it's safe to
automatically drop those specific `(character, version)` pairs' `greeting_imports` (and any
`greeting_edges`/`greeting_gating` that reference them via a `"new:"` ref) and retry once,
recording the skip as a `greeting_gaps` entry instead of blocking the whole chunk's commit. Any
other error shape should be left alone and surfaced.

Before that retry, inspect and reconcile any [partial library writes](dirty-state.md).
Narrowing the manifest does not remove the CLI's clean-tree precondition.
