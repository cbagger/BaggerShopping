# Push ved nye tilbudsaviser

Kurv registrerer hver iPhone med sit eget APNs-token og sine valgte butikker.
QNAP-containeren `flyer-push-worker` kontrollerer de validerede avisfeeds hver
time og sender direkte via Apple Push Notification service. Appen behøver ikke
at være åben eller køre i baggrunden.

## Apple-opsætning

1. Aktivér **Push Notifications** for App ID `dk.chewbagger.BaggerShopping` i
   Apple Developer-portalen.
2. Opret én APNs Auth Key, download `.p8`-filen og noter Key ID samt Team ID.
3. Placér filen på QNAP som `backend/secrets/AuthKey.p8`. Den mappe og alle
   `.p8`-filer er ignoreret af Git.
4. Udfyld kun disse værdier i QNAP-installationens eksisterende `.env`:

```dotenv
APNS_KEY_ID=XXXXXXXXXX
APNS_TEAM_ID=XXXXXXXXXX
APNS_BUNDLE_ID=dk.chewbagger.BaggerShopping
APNS_PRIVATE_KEY_PATH=/run/secrets/AuthKey.p8
FLYER_PUSH_INTERVAL_SECONDS=3600
FLYER_PUSH_STORE_PATH=/data/flyer-push.json
```

## Drift

`data/flyer-push.json` indeholder enhedstokens, butikvalg og leveringshistorik.
Første kørsel registrerer de allerede kendte aviser uden at sende push. Kun nye
avis-ID'er efter denne baseline udløser notifikationer.

En fejlet APNs-levering prøves igen ved næste interval. Allerede leverede
telefoner får ikke den samme avis igen. Indstillingerne er pr. iPhone, så
familiemedlemmer kan vælge forskellige butikker.

Følg workerens status med:

```bash
docker compose logs --tail=100 flyer-push-worker
```
