# Settings actions that spend

Source: [llm_connections/](../../../../../backend/src/grimoire/routes/config/), [embed_space.py](../../../../../backend/src/grimoire/store/embed_space.py).

- **A settings surface never spends unasked, and the server is what holds
  that.** Play is not gated -- sending a turn *is* the request -- but a call a
  settings page starts that may cost money says what it will send and waits
  for a yes, and its route refuses the request without that yes before
  anything is built or sent. Three do today: the model test
  (`post_connection_test`, 400 without `confirm: true`; its `/test/preview`
  sends nothing and is what the dialog shows), a health check that generates
  (`GENERATING_CHECK_KINDS`, the Claude subscription, 400 without `confirm:
  true`; the free checks take none), and a change that moves the Embedding
  role's vector space -- the role changed to a selection that embeds (`PUT
  /inference/settings`), a new key or address on the provider it embeds
  through, which restamps that provider's `rev` (`PUT /llm-connections/{id}`,
  judged by `embed_space.moved_by`), or a model-facts write that turns the
  role on, such as the user's `embed: yes` over a known `no`, or changes the
  document side of the embedding options of the model it embeds with (`PUT
  /llm-connections/{id}/facts`, judged by `embed_space.facts_moved`; a
  query-prefix-only change asks nothing), or, on a
  store not yet at format 2, a legacy `embeddings_connection_id` /
  `embeddings_model` change through `PUT /config` (judged by
  `embed_space.config_moved`) -- each
  400 `confirm_embedding` without `confirm_embedding: true`, compared inside
  the hold that writes, because re-embedding a library may cost money. A
  side whose model facts cannot be judged (held, mangled, or invalid
  options) is not "embeds nothing": a provider edit that moves its `rev`, or
  a legacy key change, asks then too. Two
  things can still lift a known `no` without that question, because neither
  is a settings write the user makes to the Embedding role: a test call that
  PASSES (itself confirmed before it is sent; a failed test is `unknown` and
  lifts nothing) and a catalog refresh that starts listing the model's
  embeddings. A confirmation
  that lives only in a client dialog is not the rule: a new settings action
  that can spend adds its refusal at the route.
