# Getting started

[Documentation](../README.md) · [Troubleshooting](troubleshooting.md)

## Before installing

Install Python 3.11 or newer, Node 24 or newer, and Git. Decide where your
library should live. The default is `~/.grimoire`; choosing a different folder
in first-run setup keeps story data outside the checkout too.

Grimoire needs a model to generate replies. You can use a hosted provider or an
OpenAI-compatible local server. Editing worlds and reading existing campaigns
does not require a working model. See [Models and providers](models.md).

## Install

```sh
git clone https://github.com/charlesmsiegel/grimoire.git
cd grimoire
```

Then use the installer for your platform:

| Platform | Command |
|---|---|
| macOS / Linux | `scripts/unix/install.sh` |
| Windows PowerShell | `scripts\windows\install.ps1` |

It checks the Python and Node version floors, creates `backend/.venv`, installs
the backend's development and desktop dependencies, installs frontend packages,
builds the UI and creates a desktop launcher. Its final output prints the
library path resolved for this installation.

The Claude subscription adapter is an optional backend extra. If using it,
install the extra into the same environment and complete the integration's
local authentication:

```sh
# macOS / Linux, from the repository root
backend/.venv/bin/python -m pip install -e './backend[claude]'
```

```powershell
# Windows PowerShell, from the repository root
backend\.venv\Scripts\python.exe -m pip install -e './backend[claude]'
```

The [provider guide](models.md#supported-providers) explains this adapter's
capabilities. It is not available in the Android dependency set.

## Launch and stop

| Platform | Normal launch | Development launch |
|---|---|---|
| macOS / Linux | `scripts/unix/run.sh` | `scripts/unix/run.sh --dev` |
| Windows PowerShell | `scripts\windows\run.ps1` | `scripts\windows\run.ps1 -Dev` |

Normal launch opens **<http://127.0.0.1:8173>**. One backend serves the built UI
and `/api`. Development launch opens **<http://127.0.0.1:5173>** and starts Vite
beside the backend on 8173. Use normal launch for playing and development launch
when changing source code.

Keep the terminal open. Press **Ctrl+C** to stop the running app. The shutdown
helpers are `scripts/unix/shutdown.sh` and `scripts\windows\shutdown.ps1`.

## Complete first-run setup

1. **Storage:** choose the library directory. Pointing at an existing library
   uses its records instead of starting another collection.
2. **Provider:** save an account or local endpoint. Its check verifies
   connectivity; checks that generate ask for confirmation.
3. **Models:** assign Primary. Fast and Decision can inherit their parent roles.
   Embedding is optional and can remain off.
4. **Look:** choose the interface theme.
5. **World:** create a setting, then continue to campaign creation.

Each step saves as you complete it. Going back navigates; it does not undo earlier
saves. Configuration offers **First-run setup** to revisit the wizard.

## Play your first scene

1. Open the world in **Library → Worlds**. Add a character, a PC and a location.
   The [worldbuilding guide](worldbuilding.md) explains the record types.
2. Create a campaign using that world and choose the PC you will play.
3. Open the campaign's scene list and create a scene. Choose a PC scene, select
   the cast and location, and confirm the opening. Use a greeting, an idea or
   your own premise.
4. Type a contribution and send it. A configured model streams the reply.
5. Prepare the scene's wrap-up, review its proposed changes and save the result.
   This gives the next scene reviewed campaign memory.

For alternate replies, offscreen scenes and disconnected browsers, continue
with [Playing a campaign](playing.md).

## Update an installation

Stop Grimoire, run `git pull` in its checkout, then launch again. The normal
launcher checks whether the frontend bundle needs rebuilding. If dependencies
changed or a build fails, rerun the installer, then launch again.

The library is separate from the source checkout. Make a
[full backup](data.md#back-up-and-restore) before upgrading an important library.
Do not use an older build to edit settings written in a newer format.

## Implementation references

- [Unix installer](../../scripts/unix/install.sh) and [Windows installer](../../scripts/windows/install.ps1)
- [Unix launcher](../../scripts/unix/run.sh) and [Windows launcher](../../scripts/windows/run.ps1)
- [Version requirements](../../backend/pyproject.toml) and [frontend requirements](../../frontend/package.json)
- [Setup wizard](../../frontend/src/routes/SetupWizard.tsx) and [campaign wizard](../../frontend/src/routes/CampaignWizard.tsx)
