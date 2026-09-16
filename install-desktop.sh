#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
config_home=${XDG_CONFIG_HOME:-"$HOME/.config"}
applications_dir="$data_home/applications"
icons_dir="$data_home/icons/hicolor/scalable/apps"
desktop_file="$applications_dir/io.sherpa.Sherpa.desktop"
installed_icon="$icons_dir/io.sherpa.Sherpa.svg"

mkdir -p "$applications_dir" "$icons_dir"
cp "$project_dir/assets/sherpa.svg" "$installed_icon"
sed \
    -e "s|@SHERPA_EXEC@|$project_dir/sherpa|g" \
    -e "s|@SHERPA_ICON@|$installed_icon|g" \
    "$project_dir/packaging/io.sherpa.Sherpa.desktop.in" > "$desktop_file"
chmod 644 "$desktop_file" "$installed_icon"

if [ "${1:-}" = "--autostart" ]; then
    mkdir -p "$config_home/autostart"
    sed \
        -e 's|^Exec=\(.*\)$|Exec=\1 --minimized|' \
        "$desktop_file" > "$config_home/autostart/io.sherpa.Sherpa.desktop"
    echo "Sherpa will start automatically when you sign in."
fi

echo "Installed the Sherpa application launcher."
echo "Open Sherpa from your application menu or run: $project_dir/sherpa"
