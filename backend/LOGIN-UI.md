# Samsung Food manual login recovery

Use this only when `/api/auth/refresh` reports `requires_interaction=true` or the browser session has expired.

1. Stop API: `docker compose stop bagger-shopping`
2. Start browser: `docker compose --profile login up -d login-ui`
3. Open `https://<QNAP-IP>:3001`
4. Sign in to Samsung Food and complete/dismiss Samsung interstitials as needed.
5. Verify **Lists → Indkøbsliste** is visible.
6. Stop/remove browser:
   `docker compose --profile login stop login-ui && docker compose --profile login rm -f login-ui`
7. Start API: `docker compose up -d bagger-shopping`
8. Recover token: `curl -X POST http://localhost:8088/api/auth/refresh`

The browser and API share `./data/chromium-profile`; never use that profile from both containers at the same time.
