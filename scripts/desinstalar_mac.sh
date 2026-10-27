#!/usr/bin/env bash
# Quita el arranque automático de QuantSport AI. No borra tus datos (data/) ni tu .env.
set -euo pipefail
LABEL=com.quantsport.ai
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "QuantSport AI ya no arranca solo. Tus datos siguen en data/."
