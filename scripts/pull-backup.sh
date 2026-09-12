#!/usr/bin/env bash
set -euo pipefail
remote=${1:?Usage: pull-backup.sh user@host /absolute/remote/var/backups ./local-backups [ssh-key]}
remote_dir=${2:?Remote backup directory required}
local_dir=${3:?Local destination required}
[[ "$remote" =~ ^[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+$ && "$remote_dir" =~ ^/[a-zA-Z0-9_./-]+$ ]] || exit 1
mkdir -p "$local_dir"
rsync_args=(-a --protect-args)
if [[ ${CHEMBIG_REMOTE_SUDO:-0} == 1 ]]; then rsync_args+=(--rsync-path="sudo -n rsync"); fi
if [[ $# -ge 4 ]]; then
    # rsync's -e is shell-parsed: reject shell metacharacters in key paths.
    [[ "$4" =~ ^/[a-zA-Z0-9_./-]+$ ]] || exit 1
    rsync "${rsync_args[@]}" -e "ssh -i $4" "$remote:$remote_dir/" "$local_dir/"
else
    rsync "${rsync_args[@]}" "$remote:$remote_dir/" "$local_dir/"
fi
# No --delete: a local independent copy survives loss or deletion on the server.
