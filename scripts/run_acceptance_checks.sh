#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKIP_ANDROID="${SKIP_ANDROID:-0}"

resolve_android_sdk() {
  if [[ -n "${ANDROID_HOME:-}" && -d "${ANDROID_HOME}" ]]; then
    echo "$ANDROID_HOME"
    return
  fi
  if [[ -n "${ANDROID_SDK_ROOT:-}" && -d "${ANDROID_SDK_ROOT}" ]]; then
    echo "$ANDROID_SDK_ROOT"
    return
  fi
  if [[ -d "$HOME/Android/Sdk" ]]; then
    echo "$HOME/Android/Sdk"
    return
  fi
  echo ""
}

echo "[1/3] Running host unit and flow tests..."
(
  cd "$ROOT_DIR/windows_host"
  python -m unittest discover -s tests -p "test_*.py" -v
)

echo "[2/3] Protocol docs present..."
test -f "$ROOT_DIR/protocol/PROTOCOL.md"

if [[ "$SKIP_ANDROID" == "1" ]]; then
  echo "[3/3] Android build skipped (SKIP_ANDROID=1)."
  echo "Acceptance checks passed in current environment."
  exit 0
fi

echo "[3/3] Building Android debug APK..."
export GRADLE_USER_HOME="$ROOT_DIR/.gradle-user-home"
export ANDROID_USER_HOME="$ROOT_DIR/.android-user-home"
export GRADLE_OPTS="${GRADLE_OPTS:-} -Duser.home=$ANDROID_USER_HOME"
mkdir -p "$GRADLE_USER_HOME"
mkdir -p "$ANDROID_USER_HOME"
if [[ ! -f "$ROOT_DIR/android_client/gradle/wrapper/gradle-wrapper.jar" ]]; then
  "$ROOT_DIR/scripts/bootstrap_gradle_wrapper.sh"
fi
chmod +x "$ROOT_DIR/android_client/gradlew"
(
  cd "$ROOT_DIR/android_client"
  SDK_DIR="$(resolve_android_sdk)"
  if [[ -z "$SDK_DIR" ]]; then
    echo "Android SDK location not found. Set ANDROID_HOME/ANDROID_SDK_ROOT." >&2
    exit 1
  fi
  if ! ( : > "$SDK_DIR/.write_probe" ) 2>/dev/null; then
    echo "Android SDK directory is not writable: $SDK_DIR" >&2
    exit 1
  fi
  rm -f "$SDK_DIR/.write_probe"
  printf 'sdk.dir=%s\n' "$SDK_DIR" > local.properties
  ./gradlew --no-daemon :app:assembleDebug
)
echo "Acceptance checks passed in current environment."
