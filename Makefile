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
  BOOTSTRAP := powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/android-bootstrap.ps1
  # .\ prefix: NoDefaultCurrentDirectoryInExePath (set on some machines)
  # stops cmd from resolving bare gradlew.bat out of the current directory.
  GRADLEW = cd $(ANDROID) && .\gradlew.bat --no-daemon
  MKDIR_BUILD = if not exist $(BUILD_DIR) mkdir $(BUILD_DIR)
  COPY = copy /Y
  fixpath = $(subst /,\,$(1))
else
  JAVA_HOME ?= $(HOME)/.local/share/grimoire-android/jdk-17
  BOOTSTRAP := sh scripts/unix/android-bootstrap.sh
  GRADLEW = cd $(ANDROID) && ./gradlew --no-daemon
  MKDIR_BUILD = mkdir -p $(BUILD_DIR)
  COPY = cp
  fixpath = $(1)
endif
export JAVA_HOME

.PHONY: apk apk-release apk-install android-bootstrap android-clean

apk:
	$(GRADLEW) :app:assembleDebug
	@$(MKDIR_BUILD)
	@$(COPY) $(call fixpath,$(GRADLE_APK_DEBUG)) $(call fixpath,$(APK_DEBUG))
	@echo APK: $(APK_DEBUG)

apk-release:
	$(GRADLEW) :app:assembleRelease
	@$(MKDIR_BUILD)
	@$(COPY) $(call fixpath,$(GRADLE_APK_RELEASE)) $(call fixpath,$(APK_RELEASE))
	@echo Unsigned APK: $(APK_RELEASE)

apk-install: apk
	adb install -r $(APK_DEBUG)

android-bootstrap:
	$(BOOTSTRAP)

android-clean:
	$(GRADLEW) clean
