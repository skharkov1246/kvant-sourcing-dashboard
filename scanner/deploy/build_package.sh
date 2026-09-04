#!/bin/sh
# Сборка деплой-пакета «КВАНТ Скан»: подставляет отпечаток Caddyfile в
# docker-compose.yml (чтобы прокси пересоздавался при смене конфига) и
# пакует backend+deploy. Запускать из каталога scanner/.
set -eu
DIGEST=$(sha256sum deploy/Caddyfile | cut -c1-16)
TMP=$(mktemp -d)
mkdir -p "$TMP/scanner"
cp -r backend deploy "$TMP/scanner/"
sed -i "s/__CADDYFILE_DIGEST__/$DIGEST/" "$TMP/scanner/deploy/docker-compose.yml"
OUT=${1:-kvant-deploy.tgz}
tar czf "$OUT" -C "$TMP/scanner" \
    --exclude='__pycache__' --exclude='*.pyc' --exclude='.env' \
    --exclude='.pytest_cache' backend deploy
rm -rf "$TMP"
echo "пакет: $OUT (caddyfile digest: $DIGEST)"
