# Architecture

## Data flow

Samsung Food / Family Hub → QNAP backend (`backend/`) → public mobile API at `shopping.chewbagger.dk` → Bagger Shopping iOS (`ios/`).

The iOS app never stores Samsung Food credentials. It authenticates to the mobile API with a bearer token stored in Keychain.

## Authentication

Samsung Food uses a persistent browser profile on the QNAP. Manual browser login is the proven baseline. The backend extracts/refreshes the usable Samsung session/token from that profile.

## Shopping mutations

The private Samsung Food gRPC-web `SyncItems` contract has been reverse engineered and verified for add, check, uncheck and delete.

## Geofencing

Stores are found with MapKit and persisted locally in the iOS app. Core Location monitors enabled circular regions. On region entry, the app obtains background execution time, fetches the current shopping list, and posts a local notification. A cached list is used as fallback if the live background fetch fails.

## Verified baseline

- backend v0.5.1
- iOS v0.2.3
