# grimoire for Android

A thin Kotlin shell around the real grimoire: the APK packages
`backend/src` (via Chaquopy) and the freshly built `frontend/dist` (as assets),
runs the FastAPI app on `127.0.0.1:<port>` inside the app process — a port
drawn once per install and kept, falling back to one the OS assigns — and
shows it in a full-screen WebView. There is no Android copy of any grimoire
code — see `docs/android-architecture.md` for the full design.

## Building

From the repo root (GNU make; on Windows run from Git Bash — `winget install
ezwinports.make` if needed):

```
make android-bootstrap   # once per machine: JDK 17 + Android SDK + licenses
make apk                 # debug APK -> build/grimoire-debug.apk
make apk-release         # unsigned release APK -> build/grimoire-release-unsigned.apk
make apk-install         # adb install to a connected device
```

APKs are staged into `build/` at the repo root (gitignored); gradle's own
outputs stay under `android/app/build/`.

`android-bootstrap` is idempotent and needs no admin rights: it downloads a
portable Temurin JDK 17 and the Android cmdline-tools (platform 34,
build-tools 34) into a per-user directory, accepts the SDK licenses, and
writes `android/local.properties` (`sdk.dir` plus `grimoire.buildPython`).
Chaquopy 17 requires that build-machine Python to be the **same minor version**
as the runtime it packages into the APK — 3.12, pinned by `version =` in the
`chaquopy` block — and fails the build on a mismatch; the runtime itself is
still downloaded by the plugin. The only other prerequisites are Node 18+
(`npm run build` runs as a Gradle task, so `frontend/` must have had
`npm install`) and Python 3.12.

Without make, the same build is:

```
cd android
./gradlew :app:assembleDebug     # or open android/ in Android Studio (AGP 8.5, JDK 17)
```

The build runs `npm run build` in `frontend/`, stages `dist/` and the repo's
`templates/` into APK assets, pip-installs the backend's base dependencies for
Android, and packages `backend/src` as Python source.

### If pip fails on `pydantic-core`

FastAPI pulls pydantic v2, whose core is a Rust wheel that may not be available
for Android in Chaquopy's repository. Two documented fallbacks
(`docs/android-architecture.md` §7, risk 1):

1. Build the wheel once with maturin against the Android NDK and add
   `options("--find-links", "wheels/")` to the `pip` block in
   `app/build.gradle.kts`.
2. Pin the pure-python line instead: `install("pydantic==1.10.*")` plus a
   FastAPI version that accepts it. The backend is v1/v2-agnostic — the only
   v2-specific API call is wrapped in `routes.common._dump`.

## Runtime layout on the device

| What | Where |
|---|---|
| Store (worlds, campaigns, config) | `<external app dir>/.grimoire` — visible over USB at `Android/data/app.grimoire/files/.grimoire` |
| Bootstrap pointer | `<external app dir>/.grimoire.json` |
| Extracted frontend + templates | `<internal files>/web/`, re-extracted per install/update |

The shell sets `HOME` (not `GRIMOIRE_HOME`), so the Storage-location page in
Configuration still works; pointing it at a shared folder is the Phase 3
synced-library flow (requires All-files access).

## First launch

Cold start shows a spinner for roughly the interpreter + import time (budget
≤2.5 s mid-range; measure per device class), then the regular grimoire UI.
Add the OpenRouter key under Configuration, exactly as on desktop — or point a
connection at an LLM server on your network (see
[Using a local LLM server](#using-a-local-llm-server)).

## Using a local LLM server

The app can talk to an LLM server running on a computer on the same network —
Ollama, llama.cpp, LM Studio, vLLM, KoboldCpp, text-generation-webui, or
anything else with an OpenAI-compatible endpoint. Set it up as an
**OpenAI-compatible** connection exactly as on desktop (see "Local and
OpenAI-compatible backends" in the top-level README), with three differences:

1. **Use the computer's LAN address, not `localhost`.** On the phone,
   `localhost` and `127.0.0.1` are the phone itself — that is where the app's
   own embedded server lives. Find the computer's address (for example
   `192.168.1.20`) and use `http://192.168.1.20:11434/v1`, with that server's
   port. The Base URL presets fill in `localhost`; replace the host before
   saving.
2. **Make the server listen on the network.** Most bind to loopback by default
   and so refuse the phone:

   | Server | How to listen on the network |
   |---|---|
   | Ollama | set `OLLAMA_HOST=0.0.0.0` before starting it |
   | llama.cpp | `llama-server --host 0.0.0.0` |
   | LM Studio | enable *Serve on Local Network* in the server settings |
   | vLLM | listens on all interfaces unless `--host` says otherwise |
   | KoboldCpp | listens on all interfaces unless `--host` says otherwise |
   | text-generation-webui | start it with `--api --listen` |

   Then allow the port through the computer's firewall.
3. **Same network, no client isolation.** The phone and the computer have to
   be able to reach each other: a guest Wi-Fi or a hotspot that isolates
   clients will not work, and neither will mobile data.

Plain `http://` is fine for a server on your own network. The app's network
security config restricts cleartext only for traffic that goes through
Android's Java networking — the WebView, which loads the app from its own
loopback server. LLM calls are made by the embedded Python backend (`httpx`
over its own sockets), which that policy does not cover, so no HTTPS proxy is
needed in front of the local server.

Use **Test connection** on the Connections page to check the phone can reach
it; a `network` failure there almost always means one of the three points
above. Since a local model can be slow to start a reply, raise **No-reply
timeout** under Settings → Timeouts if turns time out — a turn keeps running
with the screen off, because the app holds a foreground service while a run is
live. The context inspector's token counts are always marked as estimates on
Android: the APK ships no tokenizer and counts by length.

## Syncing the store with a phone

`make sync-phone` reports what a sync would do; `make sync-phone-apply` does
it. Both drive `scripts/grimoire_sync.py`, which finds the store on each side
the way the app does (`GRIMOIRE_HOME` → the `data_dir` in the `.grimoire.json`
pointer → `~/.grimoire`) rather than assuming a path, so a library moved from
the Storage-location page still syncs.

Freshness is decided against a *baseline* — the hash each file had when the two
sides last agreed, kept per device under `~/.grimoire-sync/` — not against
mtimes, which `adb push` rewrites as it copies. That is what distinguishes
"changed on the phone" from "changed on the PC" from "changed in both places".

Two rules make it safe to run without reading the diff first:

- **It never deletes.** A file on one side and not the other is copied, never
  removed, because "deleted here" and "created there" are the same observation
  and only one reading can be undone. Removing a record means removing it in
  both places yourself.
- **It never resolves a conflict.** When both sides moved, the phone's copy
  lands beside the PC's as `<name>.sync-conflict-<stamp>-<serial><ext>` and
  both originals stay put. That name matches `store/external.py`'s Syncthing
  rule, so the Configuration page lists them alongside any other sync client's.

`--apply` also git-commits the PC store first (it is a repo) and force-stops
the app on the phone, so nothing is being written while files move.

This needs the phone's store to be readable over USB, which is what
`android_entry._open_store_to_usb` arranges: Android runs apps with
`umask 0077`, so files landed `0600` and every `adb pull` failed. It relaxes
the umask and walks the existing tree once. **Open the app on the phone at
least once after installing a build with that change**, or the sync reports the
older files as unreadable.
