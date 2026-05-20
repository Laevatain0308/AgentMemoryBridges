#!/bin/sh
# 优先使用 TZ 环境变量设置时区；未设置则保持宿主机挂载的 /etc/localtime
if [ -n "$TZ" ] && [ -f "/usr/share/zoneinfo/$TZ" ]; then
    ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime
    echo "$TZ" > /etc/timezone
fi
exec uvicorn main:app --host 0.0.0.0 --port 3004 --workers 1 --limit-max-requests 10000
