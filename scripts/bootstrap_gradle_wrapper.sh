#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ANDROID_DIR="$ROOT_DIR/android_client"
WRAPPER_JAR="$ANDROID_DIR/gradle/wrapper/gradle-wrapper.jar"

if [[ -f "$WRAPPER_JAR" ]]; then
  echo "Gradle wrapper jar already exists."
  exit 0
fi

GRADLE_VERSION="8.7"
DIST_NAME="gradle-${GRADLE_VERSION}-bin"
DIST_ZIP="${TMPDIR:-/tmp}/${DIST_NAME}.zip"
EXTRACT_DIR="${TMPDIR:-/tmp}/gradle-wrapper-bootstrap"
GRADLE_BIN=""

if [[ -n "${GRADLE_CMD:-}" ]]; then
  echo "Using configured Gradle command: ${GRADLE_CMD}"
  ${GRADLE_CMD} -p "$ANDROID_DIR" wrapper --gradle-version "$GRADLE_VERSION" --distribution-type bin
elif command -v gradle >/dev/null 2>&1; then
  echo "Using system Gradle from PATH: $(command -v gradle)"
  gradle -p "$ANDROID_DIR" wrapper --gradle-version "$GRADLE_VERSION" --distribution-type bin
else
  echo "Downloading Gradle ${GRADLE_VERSION} distribution..."
  curl -fsSL "https://services.gradle.org/distributions/${DIST_NAME}.zip" -o "$DIST_ZIP"

  rm -rf "$EXTRACT_DIR"
  mkdir -p "$EXTRACT_DIR"
  unzip -q "$DIST_ZIP" -d "$EXTRACT_DIR"

  GRADLE_BIN="$(find "$EXTRACT_DIR" -type f -name gradle | head -n 1 || true)"
  if [[ -z "$GRADLE_BIN" ]]; then
    echo "Failed to locate gradle binary in extracted distribution." >&2
    exit 1
  fi

  echo "Generating Gradle wrapper files..."
  "$GRADLE_BIN" -p "$ANDROID_DIR" wrapper --gradle-version "$GRADLE_VERSION" --distribution-type bin
fi

if [[ ! -f "$WRAPPER_JAR" ]]; then
  echo "Failed to generate gradle-wrapper.jar" >&2
  exit 1
fi

echo "Gradle wrapper bootstrap complete."
