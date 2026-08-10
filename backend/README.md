# Bagger Shopping v0.3

Private QNAP connector for the Samsung Food / Family Hub shopping list.

## Proven flow

The production auth path is a persistent Chromium session. Samsung may show password-age, CAPTCHA, 2FA, device-verification or other interstitial pages, so unattended credential login is only a fallback. A successful manual login is persisted in `./data/chromium-profile` and the API recovers/validates `whisk.USER_TOKEN` from that session.

## Normal operation

```bash
docker compose up -d --build bagger-shopping
curl http://localhost:8088/api/health
curl http://localhost:8088/api/auth/status
curl http://localhost:8088/api/shopping
curl http://localhost:8088/api/home-assistant/shopping
```

Add an item:

```bash
curl -X POST http://localhost:8088/api/shopping/items \
  -H 'Content-Type: application/json' \
  -d '{"name":"Mælk"}'
```

## Home Assistant endpoint

`GET /api/home-assistant/shopping` returns a deliberately small/stable payload:

```json
{
  "ok": true,
  "list_id": "...",
  "name": "Indkøbsliste",
  "count": 2,
  "has_items": true,
  "items": ["mælk", "toiletpapir"]
}
```

This is the endpoint to use for the next Home Assistant/geofence phase.

## When Samsung requires login again

Do not run the API and login browser against the Chromium profile simultaneously.

```bash
docker compose stop bagger-shopping
docker compose --profile login up -d login-ui
```

Open `https://<QNAP-IP>:3001`, sign in, dismiss any Samsung password-age/interstitial prompt if appropriate, and verify **Lists → Indkøbsliste** is visible. Then:

```bash
docker compose --profile login stop login-ui
docker compose --profile login rm -f login-ui
docker compose up -d bagger-shopping
curl -X POST http://localhost:8088/api/auth/refresh
```

Expected: `ok=true`, `mode=browser-session`, `token_valid=true`.

## Security

- Never commit `.env`.
- Keep `./data` private; it contains the persistent authenticated browser profile and cached token state.
- Do not expose ports 8088 or 3001 directly to the public Internet.
- Port 3001 is only for temporary login on the trusted LAN.


## v0.4 Mobile API

v0.4 adds a separate Internet-facing `mobile-api` container on QNAP port 8089.
It requires a Bearer token for every endpoint and talks to the Samsung-facing
core service only over the private Docker network. See `MOBILE-API.md`.


## v0.5 item mutations

Adds the Samsung Food SyncItems operations captured from the web app for:

- check item
- uncheck item
- delete one item (same protobuf deletion operation emitted by Samsung Food's Clear action)

Public mobile endpoints:

- `PATCH /api/mobile/v1/items/{id}/checked` with `{"checked": true|false}`
- `DELETE /api/mobile/v1/items/{id}`
