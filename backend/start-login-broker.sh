#!/bin/sh
set -eu

# Ensure both the broker and mobile API use one persistent secret. Existing
# SAMSUNG_LOGIN_BROKER_KEY remains authoritative; otherwise broker_key() creates
# /data/samsung-login-broker.key atomically and returns it.
if [ -z "${SAMSUNG_LOGIN_BROKER_KEY:-}" ]; then
  SAMSUNG_LOGIN_BROKER_KEY="$(python -c 'from app.samsung_broker_auth import broker_key; print(broker_key())')"
  export SAMSUNG_LOGIN_BROKER_KEY
fi

# Container restarts preserve the writable layer, so a previous Xvfb crash can
# leave :99 lock/socket files behind even though the process itself is gone.
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
mkdir -p /tmp/.X11-unix

Xvfb :99 -screen 0 1280x820x24 -nolisten tcp &
XVFB_PID=$!

READY=0
for _ in 1 2 3 4 5 6 7 8 9 10
do
  if [ -S /tmp/.X11-unix/X99 ]; then
    READY=1
    break
  fi
  if ! kill -0 "$XVFB_PID" 2>/dev/null; then
    echo "Xvfb stopped before display :99 became ready" >&2
    wait "$XVFB_PID" || true
    exit 1
  fi
  sleep 1
done

if [ "$READY" -ne 1 ]; then
  echo "Xvfb display :99 did not become ready" >&2
  kill "$XVFB_PID" 2>/dev/null || true
  exit 1
fi

x11vnc -display :99 -forever -shared -nopw -localhost -rfbport 5900 &
exec uvicorn app.login_broker:app --host 0.0.0.0 --port 8090
