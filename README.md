# Bagger Shopping

Private family shopping-list companion for Samsung Food, backed by a QNAP service and an iOS app.

## Verified baseline

- Backend: v0.5.1
- iOS: v0.2.3
- Samsung Food read/add/check/uncheck/delete verified end-to-end
- Store search via MapKit verified
- Geofence entry + live shopping-list notification verified on a physical iPhone

## Repository layout

- `backend/` — FastAPI/QNAP service, Samsung Food integration, mobile API, tests
- `ios/` — SwiftUI iOS app, MapKit store search, Core Location geofencing
- `docs/` — architecture and security notes

## Secrets

Never commit `.env`, Samsung tokens/cookies, browser profiles, mobile API tokens, or Keychain exports.
Use `backend/.env.example` as a template only.
