#!/bin/sh
# 优先使用 TZ 环境变量设置时区；未设置则保持宿主机挂载的 /etc/localtime
if [ -n "$TZ" ] && [ -f "/usr/share/zoneinfo/$TZ" ]; then
    ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime
    echo "$TZ" > /etc/timezone
fi
# 通过 ROOT_PATH 支持 Nginx 反代前缀（如 /bridges）
ROOT_PATH="${ROOT_PATH:-}"
if [ -n "$ROOT_PATH" ]; then
    exec uvicorn main:app --host 0.0.0.0 --port 3004 --workers 1 --limit-max-requests 10000 --root-path "$ROOT_PATH"
else
    exec uvicorn main:app --host 0.0.0.0 --port 3004 --workers 1 --limit-max-requests 10000
fi
