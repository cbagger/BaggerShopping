# Bagger Shopping Mobile API v0.4

This is the only service that should be exposed through
`shopping.chewbagger.dk`.

## Architecture

Internet / iPhone
  -> HTTPS `shopping.chewbagger.dk`
  -> Nginx Proxy Manager
  -> QNAP port 8089
  -> `mobile-api`
  -> Docker-internal `bagger-shopping:8080`
  -> Samsung Food

The existing core API on QNAP port 8088 must NOT be forwarded from the router
or exposed by Nginx Proxy Manager.

## Add a mobile API token to `.env`

Generate one on the QNAP:

```bash
openssl rand -hex 32
```

Then edit `.env` and add:

```env
MOBILE_API_TOKEN=<the generated value>
CORE_API_BASE=http://bagger-shopping:8080
```

Keep this token private. It will later be stored in the iPhone app Keychain.

## Build/start

```bash
docker compose up -d --build
docker compose ps
```

You should see:

- `bagger-shopping` on QNAP port 8088
- `bagger-shopping-mobile` on QNAP port 8089

## Local tests on QNAP

Without token (expected 401):

```bash
curl -i http://localhost:8089/api/mobile/v1/list
```

With token:

```bash
TOKEN='paste-your-mobile-token-here'

curl       -H "Authorization: Bearer $TOKEN"       http://localhost:8089/api/mobile/v1/health

curl       -H "Authorization: Bearer $TOKEN"       http://localhost:8089/api/mobile/v1/list

curl -X POST       -H "Authorization: Bearer $TOKEN"       -H "Content-Type: application/json"       -d '{"name":"Test fra mobile API"}'       http://localhost:8089/api/mobile/v1/items
```

## Nginx Proxy Manager

Create a Proxy Host:

- Domain Names: `shopping.chewbagger.dk`
- Scheme: `http`
- Forward Hostname / IP: QNAP LAN IP
- Forward Port: `8089`
- Cache Assets: Off
- Block Common Exploits: On
- Websockets Support: Off

SSL:

- Request a new Let's Encrypt certificate for `shopping.chewbagger.dk`
- Force SSL: On
- HTTP/2 Support: On
- HSTS: optional after initial testing

Do not proxy port 8088.

## Internet test

Turn Wi-Fi off on the iPhone and test using cellular data:

```bash
curl       -H "Authorization: Bearer $TOKEN"       https://shopping.chewbagger.dk/api/mobile/v1/list
```

For iPhone/Safari testing without curl, the Swift app will perform this request.

## v1 app scope

The iPhone app will use:

- `GET /api/mobile/v1/health`
- `GET /api/mobile/v1/list`
- `POST /api/mobile/v1/items`

Geofence logic and local notifications run on each iPhone independently.
