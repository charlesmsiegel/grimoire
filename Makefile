# grimoire — Android APK build orchestration.
#
# `make android-bootstrap` provisions the build toolchain (JDK 17, Android SDK
# + licenses, android/local.properties) — idempotent, per-user, no admin
# rights. `make apk` then builds the debug APK. On Windows recipes run under
# cmd.exe (pinned below), so make works from PowerShell, cmd, or Git Bash
# (`winget install ezwinports.make`).

ANDROID     := android
BUILD_DIR   := build
APK_DEBUG   := $(BUILD_DIR)/grimoire-debug.apk
APK_RELEASE := $(BUILD_DIR)/grimoire-release-unsigned.apk
GRADLE_APK_DEBUG   := $(ANDROID)/app/build/outputs/apk/debug/app-debug.apk
GRADLE_APK_RELEASE := $(ANDROID)/app/build/outputs/apk/release/app-release-unsigned.apk

# --no-daemon (both platforms): a cold gradle-daemon start hangs under make's
# sub-shell on Windows/Git Bash (daemon comes up but the client never
# connects). In-process builds are reliable; they cost ~15s of JVM startup per
# build.
ifeq ($(OS),Windows_NT)
  # Pin the recipe shell to cmd.exe: which shell make picks up otherwise
  # depends on the invoking environment (sh from Git Bash, cmd from
  # PowerShell), and recipes can't satisfy both.
  SHELL := cmd.exe
  .SHELLFLAGS := /C
  JAVA_HOME ?= $(LOCALAPPDATA)/Android/jdk-17
  SDK_DIR   := $(LOCALAPPDATA)/Android/Sdk
  BOOTSTRAP := powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/android-bootstrap.ps1
  # .\ prefix: NoDefaultCurrentDirectoryInExePath (set on some machines)
  # stops cmd from resolving bare gradlew.bat out of the current directory.
  GRADLEW = cd $(ANDROID) && .\gradlew.bat --no-daemon
  MKDIR_BUILD = if not exist $(BUILD_DIR) mkdir $(BUILD_DIR)
  COPY = copy /Y
  fixpath = $(subst /,\,$(1))
  # The check-* targets. PY defaults to the venv CLAUDE.md documents; a worktree
  # has no venv of its own, so run those with PY=../../backend/.venv/Scripts/python.exe.
  PY ?= $(CURDIR)/backend/.venv/Scripts/python.exe
  VENV_PY = $(BUILD_DIR)/venv-pydantic1/Scripts/python.exe
  # backend/.venv holds an editable install whose .pth points at whichever
  # checkout created it, so inside a worktree a bare pytest imports the *other*
  # tree's sources. PYTHONPATH sorts ahead of site-packages, so this shadows it.
  WITH_SRC = set "PYTHONPATH=$(call fixpath,$(CURDIR)/backend/src)" &&
  # Idempotent: bare `rmdir /s /q` fails when the directory is absent, and `rm`
  # is not a cmd.exe builtin.
  rm_rf = if exist "$(call fixpath,$(1))" rmdir /s /q "$(call fixpath,$(1))"
else
  JAVA_HOME ?= $(HOME)/.local/share/grimoire-android/jdk-17
  SDK_DIR   := $(HOME)/.local/share/grimoire-android/Sdk
  BOOTSTRAP := sh scripts/unix/android-bootstrap.sh
  GRADLEW = cd $(ANDROID) && ./gradlew --no-daemon
  MKDIR_BUILD = mkdir -p $(BUILD_DIR)
  COPY = cp
  fixpath = $(1)
  # Mirrors the Windows default: scripts/unix/install.sh creates backend/.venv
  # and installs -e ".[dev,desktop]" into it, so a bare `python3` would miss
  # every project dependency on an otherwise correctly set-up machine. CI passes
  # PY=python explicitly, where the deps really are global to the runner.
  PY ?= $(CURDIR)/backend/.venv/bin/python
  VENV_PY = $(BUILD_DIR)/venv-pydantic1/bin/python
  WITH_SRC = PYTHONPATH="$(CURDIR)/backend/src"
  rm_rf = rm -rf "$(1)"
endif
# The backend coverage floor, in percent: the measured total (93.07% of 23529
# statements and 7504 branches) rounded down to the integer below it. On the
# Makefile rather than in pyproject.toml's addopts so that the *other* pytest
# run -- check-pydantic1, which exists to answer a different question -- is not
# slowed down measuring it. Measuring costs this suite about 8 minutes on
# 3.11, which is why it is not also folded into that one.
COV_FLOOR ?= 93

# Passed by the APK CI job as the Chaquopy build-machine interpreter (<= 3.12);
# empty locally, where android/local.properties supplies it instead.
BUILD_PYTHON ?=
export JAVA_HOME
# The bootstrap installs platform-tools but doesn't put adb on PATH; call it
# by its SDK location.
ADB = $(call fixpath,$(SDK_DIR)/platform-tools/adb)

.PHONY: apk apk-release apk-install android-bootstrap android-clean \
        check check-py check-web check-lint check-mypy check-eslint \
        check-templates check-pydantic1 check-apk web-dist frontend-deps \
        baseline sync-phone sync-phone-apply

apk:
	$(GRADLEW) :app:assembleDebug $(if $(BUILD_PYTHON),-Pgrimoire.buildPython="$(BUILD_PYTHON)",)
	@$(MKDIR_BUILD)
	@$(COPY) $(call fixpath,$(GRADLE_APK_DEBUG)) $(call fixpath,$(APK_DEBUG))
	@echo APK: $(APK_DEBUG)

apk-release:
	$(GRADLEW) :app:assembleRelease
	@$(MKDIR_BUILD)
	@$(COPY) $(call fixpath,$(GRADLE_APK_RELEASE)) $(call fixpath,$(APK_RELEASE))
	@echo Unsigned APK: $(APK_RELEASE)

apk-install: apk
	$(ADB) install -r $(call fixpath,$(APK_DEBUG))

android-bootstrap:
	$(BOOTSTRAP)

android-clean:
	$(GRADLEW) clean


# ---- the CI gate. One target per guard; .github/workflows/ci.yml calls one
# target per job, so a CI failure reproduces locally with the same command.

# check-apk is deliberately excluded: it needs a per-machine
# `make android-bootstrap`, so folding it in would break `make check` on any
# un-bootstrapped machine. CI runs the two as separate jobs.
#
# Cheapest first, and that ordering is the point: serial `make check` is now
# half an hour, most of it the two full test runs at the end, and there is no
# reason to spend eighteen minutes of it before finding out that a nine-second
# lint is red. CI runs these as parallel jobs, so the order only matters here.
check: check-lint check-mypy check-templates check-eslint check-web check-py check-pydantic1

# --cov-fail-under is a floor, not a target: it sits just under the number the
# suite actually reaches, so it fails on a change that *drops* coverage and
# says nothing about one that does not raise it. Raise it when the number
# rises; lowering it needs the same justification as any other gate being
# weakened.
# The xml report is what external readers want (Codecov, the coverage gutters,
# the code-visualization atlas) and CI uploads it as `backend-coverage`; the
# term report is for the human already reading this output.
# One line, no backslash continuation: on Windows the recipe shell is cmd.exe
# (pinned above), which continues lines with `^` and would take a trailing `\`
# as an argument.
check-py:
	$(WITH_SRC) "$(call fixpath,$(PY))" -m pytest backend -q --cov=grimoire --cov-config=backend/pyproject.toml --cov-report=term:skip-covered --cov-report=xml:backend/coverage.xml --cov-fail-under=$(COV_FLOOR)

# `test:coverage`, not `test`: same suite, same pass/fail, plus it drops
# frontend/coverage/lcov.info. Measuring in the gate rather than in a separate
# job is deliberate -- a coverage target nobody runs reports on a tree nobody
# has, and the istanbul provider costs a few seconds on this suite.
check-web: frontend-deps
	cd frontend && npm run typecheck && npm run test:coverage

# The install both frontend gates need, as a prerequisite of each rather than
# a line in both recipes. That is what makes them safe under `make -j`: make
# runs a target once per invocation however many things depend on it, whereas
# two recipes each running `npm ci` in the same directory at the same time
# would be two processes deleting and repopulating one `node_modules`. It also
# stops `make check` installing the frontend twice.
#
# `npm ci`, never `npm install`: install rewrites resolution and would defeat
# the committed lockfile.
frontend-deps:
	cd frontend && npm ci

# The three ratcheted gates. Each runs its tool and compares the findings to
# `lint-baselines/<tool>.json`; `scripts/ratchet.py` explains why they land
# against a baseline rather than report-only. `make baseline` rewrites all
# three -- run it when a change *fixes* findings, and commit the smaller
# baseline alongside. It will not write one that permits *more* than the
# current file: that direction needs `--accept-regressions` typed out, so the
# regenerate step cannot quietly become the way a red gate goes green.
check-lint:
	"$(call fixpath,$(PY))" scripts/ratchet.py ruff

check-mypy:
	"$(call fixpath,$(PY))" scripts/ratchet.py mypy

check-eslint: frontend-deps
	"$(call fixpath,$(PY))" scripts/ratchet.py eslint

# `frontend-deps` for the same reason `check-eslint` takes it: regenerating the
# eslint baseline against a `node_modules` that is not the lockfile's would
# write counts the gate then cannot reproduce -- and the whole file is only
# worth anything if the number in it is the number CI gets.
#
# `make baseline ACCEPT=1` passes --accept-regressions, for the rise that is
# not a regression: a rename, a widened rule set, or a merge bringing code the
# gate has not seen. One entry point either way, because the alternative is a
# documented longhand that people reach for by muscle memory and stop reading.
BASELINE_FLAGS = $(if $(ACCEPT),--accept-regressions,)

baseline: frontend-deps
	"$(call fixpath,$(PY))" scripts/ratchet.py ruff --update $(BASELINE_FLAGS)
	"$(call fixpath,$(PY))" scripts/ratchet.py mypy --update $(BASELINE_FLAGS)
	"$(call fixpath,$(PY))" scripts/ratchet.py eslint --update $(BASELINE_FLAGS)

check-templates:
	"$(call fixpath,$(PY))" scripts/verify_templates.py

# Proves the suite passes against the *Android* dependency set: pydantic 1.10,
# no desktop extra (no uvicorn[standard], no tiktoken). In its own venv, wiped
# first -- installing 1.10 into backend/.venv would silently downgrade the
# development environment and make target order significant. One resolver
# invocation, so pip cannot satisfy the pin and then upgrade past it while
# resolving FastAPI. The FastAPI bound is mirrored in
# android/app/build.gradle.kts; raise both together.
check-pydantic1:
	$(call rm_rf,$(BUILD_DIR)/venv-pydantic1)
	"$(call fixpath,$(PY))" -m venv $(BUILD_DIR)/venv-pydantic1
	"$(call fixpath,$(VENV_PY))" -m pip install -q --upgrade pip
	"$(call fixpath,$(VENV_PY))" -m pip install -q -e "./backend[dev]" "pydantic==1.10.*" "fastapi>=0.110,<0.116"
	"$(call fixpath,$(VENV_PY))" -m pip check
	"$(call fixpath,$(VENV_PY))" -c "import pydantic; assert pydantic.VERSION.startswith('1.10.'), pydantic.VERSION"
	"$(call fixpath,$(VENV_PY))" -m pytest backend -q

# A recipe action rather than a prerequisite list: as prerequisites, parallel
# make could start gradle before the frontend bundle it packages exists.
check-apk: web-dist
	$(MAKE) apk BUILD_PYTHON="$(BUILD_PYTHON)"

web-dist: frontend-deps
	cd frontend && npm run build

# ---- store sync (PC <-> USB-connected phone) ----
#
# `sync-phone` only reports; `sync-phone-apply` copies. Split into two targets
# rather than one with a flag because the half that writes should have to be
# typed. Neither ever deletes: see scripts/grimoire_sync.py, which also
# explains why the freshness test is a recorded baseline rather than mtimes.
sync-phone:
	"$(call fixpath,$(PY))" scripts/grimoire_sync.py --adb "$(ADB)"

sync-phone-apply:
	"$(call fixpath,$(PY))" scripts/grimoire_sync.py --adb "$(ADB)" --apply
