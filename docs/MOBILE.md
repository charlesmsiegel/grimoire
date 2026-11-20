# Playing on a phone (044)

`python -m grimoire.app` serves a V2 library and a phone-sized page on this device's loopback address. It is
the [text API](TEXT_API.md) over HTTP. Run it on the phone and on the desktop, pair the two, and sync from the
page's **Sync** button. A chat played on one device then resumes on the other.

This first slice is for one person with one phone and one desktop. Its sync runs over a forwarded loopback
port, because 028's transport is authenticated but not encrypted ([SYNC](SYNC.md)).

## The quickest phone check: the phone's browser on the desktop's app

This needs no Termux and no second library. It shows the app on a real phone screen, with touch, the on-screen
keyboard and the phone's camera, playing the desktop's library. It does not test sync: that needs a library on
each device (**Set up**, below).

On an Android phone, turn on USB debugging (Settings, About phone, tap Build number seven times, then Developer
options, USB debugging) and connect it to the desktop. On the desktop, with Android's
[platform tools](https://developer.android.com/tools/releases/platform-tools) (`adb`) installed:

```sh
python -m grimoire.app serve                         # prints: open http://127.0.0.1:8080/#token=...
adb reverse tcp:8080 tcp:8080                        # the phone's 127.0.0.1:8080 is now the desktop's
adb shell am start -a android.intent.action.VIEW -d "'http://127.0.0.1:8080/#token=...'"
```

The last line opens the printed address, token and all, in the phone's default browser, so nothing long is
typed on the phone. (The inner single quotes keep the phone's shell from reading the address.) Typing it into
the phone's browser works too. `adb reverse --remove-all` ends the forward.

An iPhone has no `adb`. An SSH app on it that forwards a local port (`-L 8080:127.0.0.1:8080` to the desktop)
does the same, if the app keeps running while the browser is in front; this has not been tried.

## Set up

On the **phone**, install [Termux](https://termux.dev) (from F-Droid) and in it:

```sh
pkg install python git
git clone https://github.com/charlesmsiegel/grimoire && cd grimoire && git checkout v2
python -m pip install -e ./backend
python -m grimoire.app init ~/grimoire-library --device phone
```

Keep the library in Termux's home folder, not on shared storage (`/sdcard`): other apps can read shared storage.

Or skip `init`: `serve --setup ~/grimoire-library`, on a folder with no library yet, opens the app's first-run
page, which makes the library there (or opens another) in the browser. Start `serve` on that folder from then on.

On the **desktop**, use your existing V2 library (or `init` a new one with `--device desktop`). An existing
library that was never given a device name gets one, once: `python -m grimoire.app identify --device desktop`.

**Pair them** with one key, printed on one device and typed on the other. Stop `serve` first: `pair` needs the
library to itself.

```sh
# desktop
python -m grimoire.app pair --peer phone            # prints: --key 3f9c...
# phone
python -m grimoire.app pair ~/grimoire-library --peer desktop --key 3f9c...
```

The key is 64 characters. With the phone on USB, type the start of the phone's command in Termux, then have the
desktop type the key into it: `adb shell input text 3f9c...` (the whole key), then press Enter on the phone.
The **Devices and sync** page (`#/sync`) can pair too: **Pair a device**, with the key in its field.

Both devices must use the same owner (`--owner`, default `human:owner`): the table's player is the library
owner on each device.

## Play

On the desktop, serve the library to the phone as well:

```sh
python -m grimoire.app serve --sync-port 8765
```

Connect the phone over USB with debugging on, and forward the port, so the phone's `127.0.0.1:8765` is the
desktop's:

```sh
adb reverse tcp:8765 tcp:8765
```

An SSH tunnel from Termux (`ssh -N -L 8765:127.0.0.1:8765 you@desktop`) works the same way. Then on the phone:

```sh
python -m grimoire.app serve ~/grimoire-library --peer desktop=127.0.0.1:8765
```

It prints `open http://127.0.0.1:8080/#token=...`. (If the desktop's app is also forwarded to the phone's 8080,
as in the quick check above, give the phone's `serve` another port: `--port 8081`.) Open that address in the phone's browser. The page is the app's [shell](APP_SHELL.md): **Chats** is in its menu. The token in the
address is this serve's key: a new one each time, kept only in that browser tab.

- **Sync** brings the desktop's chats over the first time, and every later change both ways.
- **Chats** lists every chat, latest first, or the chats of one character or scenario. A world-less card chat and an
  imported V1 chat both appear, each once.
- **Take your seat** is needed once on each device: who plays which character is kept per device.
- **Post**, **Ask for a reply**, **Keep this take** and **Pass the turn** play a turn. **Cast and details** in
  the chat's scene bar opens its side panel: **Add picture** there gives a character of the chat a new avatar,
  from the phone's camera or files, and **Sync** is under Details.
- To play **offline** on purpose, take the forward away: `adb reverse --remove tcp:8765` (or unplug the phone).
  **Sync** then fails with nothing changed on either device, and play goes on. `adb reverse tcp:8765 tcp:8765` brings it
  back.
- A chat that **forked** (both devices played it offline) waits until you keep one branch. A turn or picture
  in **conflict** (both devices changed it) waits until you keep one version. The other device must be
  reachable for that.

## Answers from a model

Without a model, everything but asking for a reply works. To have the cast answer, give `serve` a model file:

```json
{"provider_id": "openrouter", "base_url": "https://openrouter.ai/api/v1", "model": "your-model",
 "key_env": "OPENROUTER_API_KEY"}
```

```sh
OPENROUTER_API_KEY=... python -m grimoire.app serve ~/grimoire-library --peer desktop=127.0.0.1:8765 --model model.json
```

Any OpenAI-compatible endpoint works: `https`, or plain `http` on loopback for a model running on the same
device (which needs no key, so leave `key_env` out). The key is read from the environment, never from the
file or the library. `max_output_tokens` (default 1024) caps each reply. `director` (default 1) is how many
director replies a turn may make, for a scene with no character who can answer. `max_tokens_field` is
`max_tokens` or `max_completion_tokens`. `price_prompt`, `price_reply` and `price_cached` are optional prices per
million tokens, written as decimal text ([INFERENCE_UI](INFERENCE_UI.md#prices-and-costs)).

The app's **Models and costs** page (`#/providers`, [INFERENCE_UI](INFERENCE_UI.md)) writes this file too: a
`--model` file that does not exist yet, or cannot be used, starts the app without a model, and the page says why
and can set one up.

## Limits of this slice

- **Loopback and a tunnel.** The phone reaches the desktop through `adb reverse` or SSH until 028 has an
  encrypted transport. Sync runs when you press **Sync**: nothing runs in the background.
- **One request at a time.** A long answer holds the page until it finishes.
- **One person.** The owner plays. Seating is per device.
- **The page's key** is in the address while it is open. Anyone who can use this phone's browser can use it
  while `serve` runs. Every app on the phone can reach `127.0.0.1:8080` and the forwarded `8765`: the page's key
  and the pairing key are what keep them out, and an app that only connects can still hold the page busy for a
  few seconds at a time.
- **Model calls** are allowed by the app's own handle for the configured endpoint. They are not in the
  library's security ledger.
- **Chats** sorted by Recent orders chats by when the chat record last changed, not by the last post.
