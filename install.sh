#!/bin/bash
#
# install.sh — install BoxR (boxraudio command)
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_BIN="/usr/local/bin/boxraudio"
PACKAGE_DEST="/usr/local/lib/boxraudio"
CONFIG_FILE="$HOME/.boxraudio.yaml"

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

echo ""
echo -e "${CYAN}${BOLD}BoxR installer${NC}"
echo -e "${CYAN}===============${NC}"
echo ""

# ─── Check Python ─────────────────────────────────────────────────────────────
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}ERROR:${NC} python3 not found. Install Python 3.9+ first."
    exit 1
fi
PY_VERSION=$(python3 --version | cut -d ' ' -f 2)
echo -e "  ${GREEN}✓${NC} Python ${PY_VERSION}"

# ─── Install Python dependencies ─────────────────────────────────────────────
echo ""
echo -e "${CYAN}Installing Python dependencies...${NC}"
pip3 install -r "${SCRIPT_DIR}/requirements.txt" --break-system-packages --quiet || {
    echo -e "  ${YELLOW}Warning:${NC} pip3 install had errors. Try manually:"
    echo "    pip3 install -r requirements.txt --break-system-packages"
}
echo -e "  ${GREEN}✓${NC} Python deps installed"

# ─── Check chromaprint (for audio fingerprinting) ─────────────────────────────
echo ""
if command -v fpcalc &> /dev/null; then
    echo -e "  ${GREEN}✓${NC} chromaprint (fpcalc) found"
else
    echo -e "  ${YELLOW}!${NC} chromaprint not installed (needed for audio fingerprinting)"
    echo "    Install via Homebrew: brew install chromaprint"
    echo "    (Optional — fingerprinting features will be disabled until installed)"
fi

# ─── Remove old clean_sync installation if present ────────────────────────────
if [ -f "/usr/local/bin/clean_sync" ] || [ -d "/usr/local/lib/cleansync" ]; then
    echo ""
    echo -e "${CYAN}Removing old clean_sync installation...${NC}"
    sudo rm -f /usr/local/bin/clean_sync
    sudo rm -rf /usr/local/lib/cleansync
    echo -e "  ${GREEN}✓${NC} Old installation removed"
fi

# ─── Install package files ────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}Installing package to ${PACKAGE_DEST}...${NC}"
sudo rm -rf "${PACKAGE_DEST}"
sudo mkdir -p "${PACKAGE_DEST}"
sudo cp -r "${SCRIPT_DIR}/boxraudio/"* "${PACKAGE_DEST}/"
sudo chmod -R 755 "${PACKAGE_DEST}"
echo -e "  ${GREEN}✓${NC} Package files installed and readable"

# ─── Install CLI entry point ──────────────────────────────────────────────────
echo ""
echo -e "${CYAN}Installing CLI to ${INSTALL_BIN}...${NC}"
sudo cp "${SCRIPT_DIR}/boxraudio_cli" "${INSTALL_BIN}"
sudo chmod 755 "${INSTALL_BIN}"
echo -e "  ${GREEN}✓${NC} CLI installed"

# ─── Create default config if missing ─────────────────────────────────────────
echo ""
if [ -f "${CONFIG_FILE}" ]; then
    echo -e "  ${GREEN}✓${NC} Config already exists: ${CONFIG_FILE}"
else
    cp "${SCRIPT_DIR}/default_config.yaml" "${CONFIG_FILE}"
    echo -e "  ${GREEN}✓${NC} Default config created: ${CONFIG_FILE}"
fi

# ─── Migrate old config if present ────────────────────────────────────────────
if [ -f "$HOME/.clean_sync.yaml" ] && [ ! -L "$HOME/.clean_sync.yaml" ]; then
    echo ""
    echo -e "${YELLOW}Found old clean_sync config at ~/.clean_sync.yaml${NC}"
    echo -e "${YELLOW}To migrate settings, copy them into ~/.boxraudio.yaml manually.${NC}"
fi

# ─── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}Installation complete!${NC}"
echo ""
echo -e "Try it out:"
echo -e "  ${CYAN}boxraudio --help${NC}"
echo -e "  ${CYAN}boxraudio --examples${NC}"
echo -e "  ${CYAN}boxraudio --version${NC}"
echo ""
echo -e "Edit your config at:"
echo -e "  ${CYAN}${CONFIG_FILE}${NC}"
echo ""
