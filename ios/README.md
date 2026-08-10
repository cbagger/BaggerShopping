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

The first classifier is deterministic and Danish-language focused. The user can change any item's category from the item menu. That correction is persisted locally and wins over the automatic classifier for future items with the same normalized name.

The shopping list is grouped by category, while bought items remain collected separately at the bottom. Existing Samsung Food add/check/uncheck/delete behavior is unchanged.

Store management is also improved:

- clearer enabled/disabled geofence state
- editable 100–500 m radius on saved stores
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

## Build

Regenerate the Xcode project after pulling source changes:

```bash
cd ios
xcodegen generate
open BaggerShopping.xcodeproj
```

Choose the Apple Developer Team if Xcode asks, select the physical iPhone, then Product -> Clean Build Folder and Run.

## Backend requirement

v0.3.0 requires the existing Bagger Shopping backend v0.5.1 or later. This release does not require a backend deployment.
