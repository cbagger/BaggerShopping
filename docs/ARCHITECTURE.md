# Kurv architecture

Kurv is split into a QNAP runtime, shared persistent state, external integrations and iOS-side intelligence. The local **Kurv Control Center** on port `8092` is the read-only operational view of that architecture.

## Runtime on QNAP

| Service | Responsibility | Local port |
| --- | --- | ---: |
| `bagger-shopping` | Core API and legacy Samsung Food / Family Hub connector | 8088 |
| `mobile-api` | Authenticated family, shopping, offers, publications, product identity and integration API | 8089 |
| `luna-enrichment-worker` | Coverage-first Luna visual audit, pricing/member verification and bounded retries | internal |
| `flyer-push-worker` | New flyer detection and quality-gated APNs delivery | internal |
| `shopping-cleanup-worker` | Midnight cleanup of checked shopping items and related offer metadata | internal |
| `samsung-login-broker` | Isolated self-service Samsung login flow | 8091 |
| `control-center` | Local-only, read-only observability and architecture UI | 8092 |

The Control Center never mounts the Docker socket and exposes no write/control endpoints. Worker wrappers only emit best-effort heartbeat telemetry under `/data/control-center/heartbeats`; telemetry failure cannot stop a production worker.

## Flyer and Luna pipeline

```text
Tjek / eTilbudsavis
        ↓
Flyer adapters
        ↓
Source fingerprint / readiness
        ↓
Luna coverage worker
        ↓
High-detail semantic page audit
        ↓
Semantic price/member safety guards
        ↓
Targeted crop when required
        ↓
Full-flyer member coverage
        ↓
Verified overlay / stable serving cache
        ↓
Mobile offers & publications API
        ↓
iOS offer search / matching / picker
```

Source publication availability is fail-open: one ambiguous advert may not block an entire flyer. Customer-visible AI price/member overrides are fail-closed and only applied after Kurv's semantic safety rules accept the exact advert. Current coverage is stored per exact source fingerprint, so a new retailer generation receives a fresh audit and retry budget.

The flyer-push worker does not announce a new flyer merely because provider data is present. `Ny tilbudsavis` is gated by the full-flyer coverage state (`complete` or `degraded`).

## Shopping and family pipeline

```text
iOS
 ↓
Mobile API
 ↓
Household / family context
 ├─ local family list
 └─ Samsung Food integration
       ↓
    Core API / Samsung client
       ↓
    Samsung Food / Family Hub
```

Family IDs, members, access tokens, recovery data, shopping-list state, offer metadata and Samsung credentials are persistent data and must never be reset during deployments.

## Product and pricing intelligence

Important logical engines include:

- Product Identity Engine: canonical product, brand, type, amount/unit and conservative same-item matching.
- Variant Extraction Engine: keeps distinct advertised products and variants selectable.
- Member Pricing Engine: ordinary campaign price remains primary; member/app/club price is a separate role.
- Luna Semantic Page Audit: visual high-detail inspection of every target on a covered page.
- Luna Semantic Safety Guards: reject unit-price leakage, neighbouring badges and ambiguous price roles.
- Luna Pricing Reader / Overlay: read-only application of previously verified AI facts; it never calls OpenAI from a customer request.
- Luna Quality & Cost Policy: prioritises price/member safety while keeping the global monthly hard budget authoritative.

## iOS runtime

The QNAP cannot truthfully observe whether a specific iOS engine is executing at this instant. Control Center therefore displays iOS modules as *deployed* with the current app release/build and shows their architectural dependencies rather than inventing a live process state.

Major client-side engines include Smart Offer Matching, Offer Price Guard, Offer Search Ranker, Offer Image Recognition, Store Geofence Engine, Member Price Geofence Reminder, Store Repository/Search and performance/cache layers.

## Control Center security

Control Center is designed for LAN access only:

- no domain / reverse-proxy configuration is part of the service;
- port 8092 is exposed directly by QNAP Docker Compose;
- requests from non-private/non-loopback source addresses are rejected;
- the UI and JavaScript are served locally with no CDN or third-party assets;
- CSP, no-store, frame denial and no-referrer headers are applied;
- snapshots expose operational summaries, not tokens, token hashes, recovery secrets, credentials or raw family identifiers;
- there are no start/stop/retry/delete/configuration endpoints in v1.

The authoritative component catalog and dataflow used by the UI live in `backend/app/control_center_catalog.py`.
