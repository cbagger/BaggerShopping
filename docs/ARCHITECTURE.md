# Architecture

Samsung Food / Family Hub → QNAP backend → shopping.chewbagger.dk → Bagger Shopping iOS.

The verified baseline is backend v0.5.1 and iOS v0.2.3.

The Samsung Food integration uses a persistent browser profile on QNAP.
The iOS app uses MapKit for store search and Core Location region monitoring for geofence notifications.
