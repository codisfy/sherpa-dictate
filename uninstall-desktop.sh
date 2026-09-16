#!/bin/sh
set -eu

data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
config_home=${XDG_CONFIG_HOME:-"$HOME/.config"}

rm -f \
    "$data_home/applications/io.sherpa.Sherpa.desktop" \
    "$data_home/icons/hicolor/scalable/apps/io.sherpa.Sherpa.svg" \
    "$config_home/autostart/io.sherpa.Sherpa.desktop"

echo "Removed the Sherpa launcher. Models and preferences were kept."
