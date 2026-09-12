#!/usr/bin/env bash
# Restores into a NEW database and EMPTY directory, never overwrites the running site.
set -euo pipefail
cd "$(dirname "$0")/.."
backup_dir=$(realpath "${1:?Usage: restore.sh BACKUP_DIR NEW_DB EMPTY_DATA_DIR}")
new_db=${2:?New database name required}
target_dir=${3:?Empty destination directory required}
[[ "$new_db" =~ ^[a-z][a-z0-9_]{0,40}$ ]] || exit 1
[[ ! -e "$target_dir" ]] || { echo 'Destination must not exist'; exit 1; }
python3 scripts/verify-backup.py "$backup_dir"
docker compose exec -T db sh -c 'createdb -U "$POSTGRES_USER" "$1"' sh "$new_db"
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$1" --exit-on-error --no-owner' sh "$new_db" < "$backup_dir/database.dump"
mkdir -p "$target_dir"
tar -xzf "$backup_dir/files.tar.gz" -C "$target_dir" --no-same-owner
if [[ $(id -u) == 0 ]]; then
    chown -R 10001:10001 "$target_dir"
else
    printf 'Before starting the app: sudo chown -R 10001:10001 %q\n' "$target_dir"
fi
printf 'Restored into database %s and directory %s. Configure an isolated web instance and verify before switching.\n' "$new_db" "$target_dir"
