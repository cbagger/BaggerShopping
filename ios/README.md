# Bagger Shopping iOS

SwiftUI companion app for the family's Samsung Food shopping list.

## v0.3.0 – Shopping v2

Shopping v2 introduces automatic local categorization without changing the Samsung Food list format. Items created on the Family Hub, Samsung Food or in the iPhone app are classified when displayed in Bagger Shopping.

Categories:

- Frugt & Grønt
- Kød
- Pålæg
- Mejeri
- Brød & Bager
- Frost
- Kolonial
- Drikkevarer
- Husholdning
- Personlig pleje
- Andet

The Danish classifier handles common products and plural forms. Manual category corrections are synchronized through the Bagger Shopping backend, so corrections can be shared across family iPhones. A local cache keeps the learned mappings available if the backend is temporarily unavailable.

The shopping list is grouped by category, while bought items remain collected separately at the bottom. Existing Samsung Food add/check/uncheck/delete behavior is retained.

### Quantity

Samsung Food quantity support is mapped end-to-end. The observed Samsung representation is:

- REST read: `item.quantity` and `item.unit`
- SyncItems write: item payload field 4 = protobuf fixed32 IEEE-754 float quantity
- SyncItems write: item payload field 5 = unit string

The iPhone list only shows a compact quantity badge when quantity is greater than one, e.g. `×3`. Quantity can be changed from the item menu and is written back to Samsung Food. Check/uncheck mutations preserve existing quantity and unit.

### Responsive mutations

Add/check/delete/quantity use optimistic local presentation so the list reacts immediately instead of waiting for Samsung's eventually-consistent read-back. Failed server mutations roll the affected UI state back and surface an error.

Store management is also improved:

- clearer enabled/disabled geofence state
- editable 100–500 m radius on saved stores
- 100 m as the default radius for newly added stores
- store deletion from the edit screen
- existing MapKit search and persisted store addresses remain unchanged

## Verified v0.2.3 baseline retained

- Samsung Food read/add/check/uncheck/delete
- MapKit store search
- persistent stores
- Core Location geofence entry
- background live shopping-list fetch
- local arrival notification
- cached-list fallback
- geofence diagnostics

A 100 m geofence has been verified in normal physical use at MENY Skørping: the arrival notification was delivered on the parking area as intended.

## Build

Regenerate the Xcode project after pulling source changes:

```bash
cd ios
xcodegen generate
open BaggerShopping.xcodeproj
```

Choose the Apple Developer Team if Xcode asks, select the physical iPhone, then Product -> Clean Build Folder and Run.

Push Notifications-capability er med i projektet. Under **Indstillinger →
Notifikation om ny avis** kan hver iPhone aktivere funktionen og vælge butikker.
QNAP sender via APNs, også når Kurv er lukket. Backend/APNs skal være sat op som
beskrevet i `backend/APNS-SETUP.md`.

## Backend requirement

v0.3.0 now requires the matching Bagger Shopping backend v0.6.0 because shared category learning and Samsung quantity mutations add new mobile/core API endpoints. Deploy backend before final device testing of those features.
