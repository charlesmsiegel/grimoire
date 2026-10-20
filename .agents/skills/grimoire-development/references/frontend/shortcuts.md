Source: [useHotkeys.ts](../../../../../frontend/src/shortcuts/useHotkeys.ts), [registry.ts](../../../../../frontend/src/shortcuts/registry.ts).

## Frontend: keyboard bindings go in the registry, never on `window`

`src/shortcuts/` owns every key the app answers, and `no-restricted-syntax` in
`eslint.config.js` fails a `keydown`/`keyup`/`keypress` listener added anywhere
else (`registry.ts` is exempt — it installs the only one). Bind with
`useHotkeys(keys, { modal })`:

- The array is read at dispatch, so it needs no memoization and its `enabled`
  is never a render behind the screen. Mirror the control the binding stands
  for, and carry the condition that control is disabled by — a shortcut that
  reaches past a disabled button is a second copy of the guards.
- `whileTyping` is what survives the caret being in prose, and almost nothing
  should ask for it: a bare letter that did would eat the word being typed.
  `global` survives an open overlay, and only the palette and the shortcuts
  sheet may claim it.
- An overlay passes `modal`, which makes Escape reach it rather than the view
  underneath and holds that view's bindings off while it is up. "On top" is
  whichever modal registered last, and opening re-registers — so it follows
  what the reader sees, not the mount order.
- A binding with a `label` and a `group` is listed by the `?` sheet, which
  reads the registry rather than a hand-kept list. That is the whole
  discoverability story, so label anything a reader could want to find.
