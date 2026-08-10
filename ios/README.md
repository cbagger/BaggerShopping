# Bagger Shopping iOS v0.2

Adds the two major MVP interactions requested after v0.1:

- Search real shops/places by natural language, e.g. `Rema 1000 Skørping`, using Apple MapKit (`MKLocalSearch`).
- Check/uncheck Samsung Food shopping items.
- Swipe an item to delete it.
- Separate bought items and clear bought items.
- Geofence notifications only include unchecked items.
- Store search result saves the actual MapKit coordinates/address; no manual latitude/longitude entry.
- Up to 20 enabled geofences are registered at once (iOS region-monitoring limit).

## Upgrade from v0.1

The API token remains in Keychain and existing v0.1 stores are migrated automatically.

After replacing the source folder, regenerate the Xcode project:

```bash
xcodegen generate
open BaggerShopping.xcodeproj
```

Choose your Apple Developer Team again if Xcode asks, select the physical iPhone, then Product -> Clean Build Folder and Run.

## Backend requirement

Requires Bagger Shopping backend v0.5.0 or later, which adds:

- `PATCH /api/mobile/v1/items/{id}/checked`
- `DELETE /api/mobile/v1/items/{id}`


## v0.2.1 fix

- Fixes the Stores tab so newly added stores appear immediately and remain visible after tapping Tilføj.
- The issue was observation of the nested StoreRepository, not MapKit search or persistence.


## v0.2.2 – Geofence diagnostics

Adds a diagnostic screen under `Indstillinger -> Geofence-diagnose`:

- live location authorization status
- notification authorization status
- active monitored region count
- list of every monitored store/geofence
- `requestState(for:)` state checks: INSIDE / OUTSIDE / UNKNOWN
- current/simulated coordinates and distance to each store center
- last didEnterRegion event
- last didExitRegion event
- last Core Location monitoring error
- immediate local test notification
- immediate shopping-list test notification
- foreground notification presentation so test notifications remain visible while the app is open

For an Xcode GPX test:
1. Start with an outside-GPX location.
2. Switch to the inside-GPX location.
3. Open `Indstillinger -> Geofence-diagnose`.
4. Tap `Kontrollér geofence nu`.
5. Verify the REMA region reports INSIDE and inspect whether an Enter event was delivered.


## v0.2.3 – Reliable geofence notification pipeline

This build hardens the work performed after a real region-entry event:

- obtains iOS background execution time with `beginBackgroundTask`
- records the complete geofence notification pipeline in diagnostics
- caches every successfully fetched Samsung shopping list
- uses the fresh live list whenever possible
- falls back to a cached list (maximum age 6 hours) if a background live fetch fails
- exposes:
  - last automatic notification attempt
  - last live list-fetch result
  - last notification result
- adds `Nulstil geofence-cooldown` for repeatable simulated tests

Recommended test:
1. Launch v0.2.3 once and refresh the shopping list, so a cache exists.
2. `Indstillinger -> Geofence-diagnose -> Nulstil geofence-cooldown`.
3. Simulate Outside GPX.
4. Simulate Inside GPX.
5. Inspect `Notifikations-pipeline`.
