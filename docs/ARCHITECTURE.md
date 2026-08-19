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
| `control-center` | Local-only, read-only observability, architecture and operations UI | 8092 |

The Control Center never mounts the Docker socket and exposes no write/control endpoints. Worker wrappers only emit best-effort heartbeat/event telemetry under `/data/control-center`; telemetry failure cannot stop a production worker.

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

## Control Center Operations v2

Operations v2 separates **live status**, **meaningful events**, **quality**, and **capacity** instead of treating every poll as activity.

- Runtime heartbeats remain live but are not copied into the Activity feed every few seconds.
- A Luna/OpenAI event is written only when the persistent OpenAI request counter actually increases. The event contains request delta, token delta and estimated DKK delta, never credentials.
- End-to-end status is read-only evidence across provider, APIs, Luna coverage, family state and Samsung. It deliberately does not create/delete a test shopping item.
- Freshness is workload-aware: an idle Luna worker with zero pending coverage and an APNs worker with nothing new to send remain healthy.
- Alert lifecycle records first seen, duration, resolution and recurrence episodes; dashboard refreshes do not increment the recurrence count.
- Trend history is sampled every five minutes and retained locally for approximately seven days.
- Directory-size measurement is cached and never runs at the three-second SSE cadence.
- Deploy drift compares the non-secret build commit embedded in the Control Center image against `/data/deployed-commit.txt`.
- Backup status is a registry of the last verified deployment backup; it is metadata only and never performs a restore from the UI.

### Storage scope

Storage is intentionally split into two different concepts:

1. **Kurv persistent data** = actual recursive size of Kurv's mounted `/data` directory.
2. **QNAP volume capacity** = total/free/host-used capacity reported by the filesystem backing `/data`.

The second value describes the whole underlying QNAP volume and must never be labelled as Kurv's own usage. Control Center presents QNAP free/total/used only as host infrastructure context.

## Control Center security

Control Center is designed for LAN access only:

- no domain / reverse-proxy configuration is part of the service;
- port 8092 is exposed directly by QNAP Docker Compose;
- requests require both a private/loopback source address and a local IP/`localhost`/`.local` Host header;
- the UI and JavaScript are served locally with no CDN or third-party assets;
- CSP, no-store, frame denial and no-referrer headers are applied;
- the Control Center container receives no production `.env`, OpenAI key, Samsung credential, APNs secret, mobile API token or Docker socket;
- snapshots expose operational summaries, not tokens, token hashes, recovery secrets, credentials or raw family identifiers;
- observability history is stored only under `/data/control-center` and never mutates shopping/family business state;
- there are no start/stop/retry/delete/configuration endpoints in Operations v2.

The authoritative component catalog and dataflow used by the UI live in `backend/app/control_center_catalog.py`.
