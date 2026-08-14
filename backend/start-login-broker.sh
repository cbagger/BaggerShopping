#!/bin/sh
set -eu

Xvfb :99 -screen 0 1280x820x24 -nolisten tcp &
sleep 1
x11vnc -display :99 -forever -shared -nopw -localhost -rfbport 5900 &
exec uvicorn app.login_broker:app --host 0.0.0.0 --port 8090
