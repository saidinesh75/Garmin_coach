#!/bin/sh
# Permanent development launcher. Always runs the code beside this launcher.
set -eu
launcher_path="$0"
while [ -L "$launcher_path" ]; do
    launcher_dir=$(CDPATH= cd -- "$(dirname -- "$launcher_path")" && pwd)
    launcher_path=$(readlink "$launcher_path")
    case "$launcher_path" in
        /*) ;;
        *) launcher_path="$launcher_dir/$launcher_path" ;;
    esac
done
project_dir=$(CDPATH= cd -- "$(dirname -- "$launcher_path")" && pwd)
cd "$project_dir"
umask 077
if ! /usr/bin/python3 scripts/develop.py; then
    printf '\nPress Return to close… '
    read -r ignored
    exit 1
fi
