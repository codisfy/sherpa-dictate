#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
version=${1:-0.1.2}
architecture=$(dpkg --print-architecture)
bundle="$project_dir/dist/sherpa"
package_root="$project_dir/build/deb-root"
output="$project_dir/dist/sherpa_${version}_${architecture}.deb"

if [ ! -x "$bundle/sherpa" ]; then
    echo "Release bundle not found. Run ./build-release.sh first."
    exit 1
fi

rm -rf "$package_root"
mkdir -p \
    "$package_root/DEBIAN" \
    "$package_root/opt/sherpa" \
    "$package_root/usr/bin" \
    "$package_root/usr/share/applications" \
    "$package_root/usr/share/doc/sherpa-desktop" \
    "$package_root/usr/share/icons/hicolor/scalable/apps"

cp -R "$bundle/." "$package_root/opt/sherpa/"
ln -s /opt/sherpa/sherpa "$package_root/usr/bin/sherpa"
cp "$project_dir/assets/sherpa.svg" \
    "$package_root/usr/share/icons/hicolor/scalable/apps/io.sherpa.Sherpa.svg"
cp "$project_dir/LICENSE" \
    "$package_root/usr/share/doc/sherpa-desktop/copyright"
cp "$project_dir/THIRD_PARTY_NOTICES.md" \
    "$package_root/usr/share/doc/sherpa-desktop/THIRD_PARTY_NOTICES.md"
sed \
    -e 's|@SHERPA_EXEC@|sherpa|g' \
    -e 's|@SHERPA_ICON@|io.sherpa.Sherpa|g' \
    "$project_dir/packaging/io.sherpa.Sherpa.desktop.in" \
    > "$package_root/usr/share/applications/io.sherpa.Sherpa.desktop"

cat > "$package_root/DEBIAN/control" <<EOF
Package: sherpa-desktop
Version: $version
Section: sound
Priority: optional
Architecture: $architecture
Depends: libportaudio2
Recommends: ydotool, wl-clipboard | xclip
Maintainer: Sherpa contributors
Description: Private local dictation and text-to-speech app
 Sherpa runs speech recognition and text-to-speech models locally and
 provides a desktop window, status icon, and shortcut-friendly commands.
EOF

dpkg-deb --root-owner-group --build "$package_root" "$output"
echo "$output"
