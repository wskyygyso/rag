#!/bin/sh
# 备份 RAgent PostgreSQL 元数据库，并生成可校验的自定义格式归档。
set -eu

: "${PGHOST:?PGHOST 未配置}"
: "${PGDATABASE:?PGDATABASE 未配置}"
: "${PGUSER:?PGUSER 未配置}"
: "${BACKUP_DIR:?BACKUP_DIR 未配置}"

BACKUP_KEEP="${BACKUP_KEEP:-7}"
case "${BACKUP_KEEP}" in
  ''|*[!0-9]*)
    echo "BACKUP_KEEP 必须为正整数" >&2
    exit 1
    ;;
esac
if [ "${BACKUP_KEEP}" -lt 1 ]; then
  echo "BACKUP_KEEP 必须为正整数" >&2
  exit 1
fi

mkdir -p "${BACKUP_DIR}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
final_file="${BACKUP_DIR}/ragent-${timestamp}.dump"
partial_file="${final_file}.partial"

cleanup_partial() {
  # 仅清理本次备份尚未完成的临时文件。
  if [ -f "${partial_file}" ]; then
    rm -f "${partial_file}"
  fi
}
trap cleanup_partial EXIT INT TERM

pg_dump \
  --format=custom \
  --no-owner \
  --no-acl \
  --file="${partial_file}" \
  "${PGDATABASE}"

# 通过列出归档目录确认备份文件可以被 pg_restore 读取。
pg_restore --list "${partial_file}" >/dev/null
mv "${partial_file}" "${final_file}"
sha256sum "${final_file}" > "${final_file}.sha256"

# 只清理本目录内超过保留数量的 RAgent 备份及对应校验文件。
ls -1t "${BACKUP_DIR}"/ragent-*.dump 2>/dev/null \
  | tail -n +"$((BACKUP_KEEP + 1))" \
  | while IFS= read -r old_file; do
      rm -f "${old_file}" "${old_file}.sha256"
    done

echo "backup_created=${final_file}"
echo "backup_sha256=$(cut -d ' ' -f 1 "${final_file}.sha256")"
