#!/bin/sh
# On-device Termux install for termux-agent-dispatcher.
# Usage:
#   curl -sL https://raw.githubusercontent.com/DSamuelHodge/termux-agent-dispatcher/main/setup.sh | bash
#
# No ADB. Run this inside Termux (not as a foreign root/proot if you can
# avoid it — Termux:API talks to the Termux app user).

set -eu

REPO_URL="${REPO_URL:-https://github.com/DSamuelHodge/termux-agent-dispatcher.git}"
REPO_REF="${REPO_REF:-main}"
INSTALL_DIR="${INSTALL_DIR:-${HOME}/agent}"
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

echo "-> installing python (pkg)"
if ! pkg install -y python; then
  command -v python >/dev/null 2>&1 || die "python is required"
  echo "-> pkg install python failed; using existing $(command -v python)"
fi

command -v python >/dev/null 2>&1 || die "python not found after pkg install"
command -v git >/dev/null 2>&1 || pkg install -y git || die "git is required"

echo "-> pip install pyyaml"
python -m pip install --user pyyaml >/dev/null

WORKDIR="${TMPDIR:-/tmp}/termux-agent-dispatcher-src"
rm -rf "$WORKDIR"
git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$WORKDIR"

echo "-> installing into ${INSTALL_DIR}"
mkdir -p "$INSTALL_DIR"
# Copy dispatcher files only; do not wipe unrelated trees already in $HOME/agent.
for item in daemon.py verbs.yaml requirements.txt AGENTS.md README.md setup.sh; do
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
echo "boot:   Termux:Boot will run ~/.termux/boot/01-start-agent after reboot"
echo "token:  cat ${INSTALL_DIR}/.agent-token   (created on first start)"
echo
echo "smoke:"
echo "  TOKEN=\$(cat ${INSTALL_DIR}/.agent-token)"
echo "  curl -H \"X-Agent-Token: \$TOKEN\" http://127.0.0.1:8477/health"
echo "  curl -H \"X-Agent-Token: \$TOKEN\" http://127.0.0.1:8477/verbs"
