# Recovery

| Result | Read or do |
|---|---|
| Failed apply or dirty library | [Inspect partial writes](dirty-state.md) before any next apply |
| Only ambiguous existing greetings | [Narrow and retry once](greetings.md) |
| Any other error | Preserve the result, stop dependent applies and report it |
| A provider or agent refusal | Respect it; do not change agents or framing to bypass it |

No recovery step authorizes deleting unrelated or uncommitted library content.
