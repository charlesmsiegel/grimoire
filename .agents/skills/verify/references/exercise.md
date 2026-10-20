# Seed, drive and stop

## Current model settings

A fresh library uses format-2 settings. Set a fake provider key and select a
fake model through the role API, following
[test_format2_play.py](../../../../backend/tests/test_format2_play.py):

```text
PUT /api/llm-connections/openrouter
{"api_key":"sk-or-x"}

PUT /api/inference/settings
{"roles":{"primary":{"selection":{"provider":"openrouter","model":"test/model"}}}}
```

Legacy `PUT /api/config` model/key fields do not configure a current Primary.
Use only the mocked provider; confirm Embedding is unset unless explicitly mocked.

## Seed and drive

Create a World, a character card and the needed PCs through the API, then create
a campaign and scenes. Add cast and greetings using the relevant routes.
Campaign readers resolve inherited World records through the overlay; do not
assume a physical character copy exists in the campaign folder.

Use the browser tool available in the host against the dedicated frontend URL.
Exercise the change's meaningful behavior and capture any failing response or
console message. Save screenshots and logs to an explicit ignored scratch path;
do not rely on a particular browser tool's default artifact directory.

Stop both server processes using their recorded handles when verification is
finished. Missing avatars for placeholder cards can produce harmless 404s; real
provider traffic or a non-scratch library is an isolation failure.

Source owners: [world routes/](../../../../backend/src/grimoire/routes/worlds/),
[inference routes](../../../../backend/src/grimoire/routes/inference.py),
and [overlay/](../../../../backend/src/grimoire/store/overlay/).
