#!/bin/sh
# On-device Termux install for termux-agent-dispatcher.
# Usage:
#   curl -sL https://raw.githubusercontent.com/DSamuelHodge/termux-agent-dispatcher/main/setup.sh | bash
#
# Installs into ~/termux-agent-dispatcher by default. Never ~/agent:
# that name collides with other CoS/agent trees and overwrites consumers.
# Override: INSTALL_DIR=/path/to/checkout
# Emergency: FORCE_INSTALL_DIR=1 INSTALL_DIR=$HOME/agent bash setup.sh
#
# No ADB. Run this inside Termux (Termux:API talks to the Termux app user).

set -eu

REPO_URL="${REPO_URL:-https://github.com/DSamuelHodge/termux-agent-dispatcher.git}"
REPO_REF="${REPO_REF:-main}"
INSTALL_DIR="${INSTALL_DIR:-${HOME}/termux-agent-dispatcher}"
BOOT_DIR="${HOME}/.termux/boot"

die() {
  echo "setup.sh: $*" >&2
  exit 1
}

if [ ! -d /data/data/com.termux ]; then
  die "this installer is for Termux on Android"
fi

if ! command -v pkg >/dev/null 2>&1; then
  die "pkg not on PATH — run from a Termux shell"
fi

# ~/agent was the original default. It is a mixed CoS tree on this phone
# and a footgun for anyone else who already has ~/agent.
if [ "$INSTALL_DIR" = "${HOME}/agent" ] && [ "${FORCE_INSTALL_DIR:-}" != 1 ]; then
  die "refusing INSTALL_DIR=~/agent (clobbers unrelated files). Use the default ~/termux-agent-dispatcher, or FORCE_INSTALL_DIR=1 if you mean it"
fi

echo "-> installing python (pkg)"
if ! pkg install -y python; then
  command -v python >/dev/null 2>&1 || die "python is required"
  echo "-> pkg install python failed; using existing $(command -v python)"
fi

command -v python >/dev/null 2>&1 || die "python not found after pkg install"
command -v git >/dev/null 2>&1 || pkg install -y git || die "git is required"

WORKDIR="${TMPDIR:-/tmp}/termux-agent-dispatcher-src"
rm -rf "$WORKDIR"
git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$WORKDIR"

echo "-> installing into ${INSTALL_DIR}"
mkdir -p "$INSTALL_DIR"
# Copy dispatcher files only. Do not wipe unrelated files already in INSTALL_DIR.
for item in daemon.py verbs.yaml requirements.txt AGENTS.md README.md setup.sh SKILL.md; do
  if [ -e "${WORKDIR}/${item}" ]; then
    cp -a "${WORKDIR}/${item}" "${INSTALL_DIR}/${item}"
  fi
done
for dir in dispatch boot docs; do
  if [ -d "${WORKDIR}/${dir}" ]; then
    mkdir -p "${INSTALL_DIR}/${dir}"
    cp -a "${WORKDIR}/${dir}/." "${INSTALL_DIR}/${dir}/"
  fi
done

# Keep the existing token if this machine used the old ~/agent install.
if [ ! -f "${INSTALL_DIR}/.agent-token" ] && [ -f "${HOME}/agent/.agent-token" ]; then
  cp -a "${HOME}/agent/.agent-token" "${INSTALL_DIR}/.agent-token"
  chmod 600 "${INSTALL_DIR}/.agent-token"
  echo "-> adopted token from ~/agent/.agent-token"
fi

echo "-> pip install -r ${INSTALL_DIR}/requirements.txt"
if ! python -m pip install --user -r "${INSTALL_DIR}/requirements.txt"; then
  echo "setup.sh: pip install of PyYAML/libsql failed." >&2
  echo "setup.sh: PyYAML is required. libsql on Android often needs a from-source build" >&2
  echo "setup.sh:   (see tursodatabase/libsql-python; ANDROID_API_LEVEL + matching rust-std)." >&2
  python -c "import yaml" 2>/dev/null || die "PyYAML is required"
  python -c "import libsql" 2>/dev/null || echo "setup.sh: libsql missing — dispatch/store.py will not import until it is installed" >&2
fi

mkdir -p "${INSTALL_DIR}/logs"
mkdir -p "$BOOT_DIR"
cp "${INSTALL_DIR}/boot/01-start-agent" "${BOOT_DIR}/01-start-agent"
chmod +x "${BOOT_DIR}/01-start-agent"

(
  cd "$INSTALL_DIR" || die "cannot cd ${INSTALL_DIR}"
  python -c "from dispatch.catalog import Catalog; c=Catalog.load('verbs.yaml'); print('catalog', len(c.verbs), 'verbs')"
) || die "catalog load failed"

echo
echo "installed to ${INSTALL_DIR}"
echo "start:  cd ${INSTALL_DIR} && python daemon.py"
echo "boot:   Termux:Boot runs ~/.termux/boot/01-start-agent (TERMUX_AGENT_DISPATCHER_HOME or ~/termux-agent-dispatcher)"
echo "token:  cat ${INSTALL_DIR}/.agent-token   (created on first start, or adopted from ~/agent)"
echo
echo "smoke:"
echo "  TOKEN=\$(cat ${INSTALL_DIR}/.agent-token)"
echo "  curl -H \"X-Agent-Token: \$TOKEN\" http://127.0.0.1:8477/health"
echo "  curl -H \"X-Agent-Token: \$TOKEN\" http://127.0.0.1:8477/verbs"
