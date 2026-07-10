# grimoire — Android APK build orchestration.
#
# `make android-bootstrap` provisions the build toolchain (JDK 17, Android SDK
# + licenses, android/local.properties) — idempotent, per-user, no admin
# rights. `make apk` then builds the debug APK. Recipes are POSIX sh: on
# Windows run make from Git Bash (`winget install ezwinports.make`).

ANDROID     := android
BUILD_DIR   := build
APK_DEBUG   := $(BUILD_DIR)/grimoire-debug.apk
APK_RELEASE := $(BUILD_DIR)/grimoire-release-unsigned.apk
GRADLE_APK_DEBUG   := $(ANDROID)/app/build/outputs/apk/debug/app-debug.apk
GRADLE_APK_RELEASE := $(ANDROID)/app/build/outputs/apk/release/app-release-unsigned.apk

ifeq ($(OS),Windows_NT)
  JAVA_HOME ?= $(LOCALAPPDATA)/Android/jdk-17
  BOOTSTRAP := powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/android-bootstrap.ps1
else
  JAVA_HOME ?= $(HOME)/.local/share/grimoire-android/jdk-17
  BOOTSTRAP := sh scripts/unix/android-bootstrap.sh
endif
export JAVA_HOME

# --no-daemon: a cold gradle-daemon start hangs under make's sub-shell on
# Windows/Git Bash (daemon comes up but the client never connects). In-process
# builds are reliable; they cost ~15s of JVM startup per build.
GRADLEW = cd $(ANDROID) && ./gradlew --no-daemon

.PHONY: apk apk-release apk-install android-bootstrap android-clean

apk:
	$(GRADLEW) :app:assembleDebug
	@mkdir -p $(BUILD_DIR)
	@cp $(GRADLE_APK_DEBUG) $(APK_DEBUG)
	@echo "APK: $(APK_DEBUG)"

apk-release:
	$(GRADLEW) :app:assembleRelease
	@mkdir -p $(BUILD_DIR)
	@cp $(GRADLE_APK_RELEASE) $(APK_RELEASE)
	@echo "APK (unsigned): $(APK_RELEASE)"

apk-install: apk
	adb install -r $(APK_DEBUG)

android-bootstrap:
	$(BOOTSTRAP)

android-clean:
	$(GRADLEW) clean
