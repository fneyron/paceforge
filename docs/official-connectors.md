# Connecteurs officiels COROS, Garmin, Wahoo et Polar

Statut au 20 novembre 2026 : **code prêt, désactivé**. Les demandes d’accès sont envoyées : COROS Partner API
(courriel à api@coros.com, 15 nov.) ; Garmin Connect Developer Program, accès Business « Starter » (formulaire
envoyé au nom de Hello Stay le 20 nov., réponse de Garmin : « Thank you for your interest, we'll be reaching out
to you soon »). Autres demandes du 20 nov., sans code de connecteur à ce jour : Suunto Partner Program avec accès
Cloud API (contrat signé au nom de Hello Stay, réponse sous deux semaines). Wahoo Cloud API, app de production
« PaceForge » en lecture seule (`user_read`, `workouts_read`, `offline_data`, webhook `/webhook/wahoo`), **approuvée
le 20 nov.** ; Polar AccessLink (libre-service), client « PaceForge » créé le 20 nov. (exercices, activité du jour,
informations physiques ; redirect `/connect/polar/callback`) : **code prêt, désactivé, le 21 nov.** (activités
seulement, voir « Wahoo et Polar » plus bas). Comptes au nom de contact@paceforge.fr ; secrets hors
dépôt, sur le Mac du propriétaire. Strava : compte « PaceForge App » créé, mais l’API Strava exige un abonnement payant
pour créer l’app. Tant que les identifiants ne sont pas arrivés et
posés dans Coolify, rien ne change pour personne : aucune route (404), aucune page, aucune tâche planifiée, aucun
appel sortant, aucune ligne dans la politique de confidentialité.

Un connecteur est **actif** seulement si son drapeau est à `true` **et** son client id et son secret sont posés
(`settings.coros_partner_ready`, `settings.garmin_connect_ready`, `settings.wahoo_cloud_ready`,
`settings.polar_accesslink_ready`). Le jeton du webhook Wahoo et la clé du webhook Polar n’activent rien : sans eux,
les pushes sont refusés (401) et seule la lecture périodique apporte les séances.

## Architecture

| Élément | Rôle |
|---|---|
| `app/connectors/__init__.py` | Qui est actif (`ready`, `visible_to`, pilote), la planification (`beat_schedule`, vide tant que rien n’est actif). |
| `app/connectors/base.py` | OAuth (state, PKCE pour Garmin, vérifieur chiffré 10 min dans la session), jetons chiffrés (Fernet), rafraîchissement avec rotation (un seul à la fois par lien : verrou de ligne + verrou de processus, nouveaux jetons commités d’abord, comme `services/coros.py`), déconnexion (désinscription chez le fournisseur), état affiché, import des activités (une ligne par sortie). |
| `app/connectors/garmin_connect.py` | Garmin : OAuth, identifiant utilisateur, permissions, désinscription, lecture des pushes, correspondance vers PaceForge. |
| `app/connectors/coros_partner.py` | COROS : OAuth, push des séances, lecture périodique (séances, données du jour), fichier FIT. |
| `app/connectors/wahoo_cloud.py` | Wahoo : OAuth (refresh avec rotation), identifiant (`/v1/user`), webhook (jeton), lecture des séances (`/v1/workouts`), fichier FIT, révocation (`DELETE /v1/permissions`). |
| `app/connectors/polar_accesslink.py` | Polar : OAuth (Basic, pas de refresh), inscription de l’utilisateur (`POST /v3/users`), webhook (signature HMAC), lecture des exercices et de leur FIT, désinscription (`DELETE /v3/users/{id}`), création du webhook du client (commande du propriétaire). |
| `app/connectors/fit.py` | Lecteur FIT minimal (tours et session), format public du SDK FIT. |
| `app/models/official_connection.py` | Table `official_connections` (migration `k2f3a4b5c6d7`, à chaîner après les autres au moment du merge) ; colonnes `activities.wahoo_activity_id`, `activities.polar_activity_id` (migration `l3a4b5c6d7e8`, juste après). |
| `app/routers/official_connectors.py` | `/connect/{coros,garmin,wahoo,polar}`, `/connect/…/callback`, `/connect/…/disconnect`, `/webhook/coros`, `/webhook/garmin[/{type}]`, `/webhook/wahoo`, `/webhook/polar`. |
| `app/tasks/official_connectors.py` | Le travail dans Celery : traitement des pushes, nuits en attente, lectures COROS, Wahoo et Polar, tour horaire. |
| `partials/official_connection.html`, `partials/official_privacy.html` | Le bloc de Réglages et les lignes de la politique, affichés seulement connecteur actif. |

Table `official_connections` : `user_id`, `provider` (`coros` | `garmin` | `wahoo` | `polar`, chaîne libre, aucune
contrainte), `provider_user_id` (openId COROS, identifiant API Garmin, id utilisateur Wahoo, `x_user_id` Polar : ce
que nomment les pushes), jetons d’accès et de rafraîchissement chiffrés, `expires_at`,
`refresh_expires_at`, `scopes`, `status` (`active` | `reauth`), `created_at`, `updated_at`, `last_push_at`,
`last_sync_at`, `sync_claimed_at`, `last_error`. Un lien par compte et par fournisseur ; un compte fournisseur
relié à un seul compte PaceForge (sinon refus explicite).

## Variables d’environnement (Coolify)

| Variable | Défaut | Rôle |
|---|---|---|
| `COROS_PARTNER_ENABLED` | `false` | Interrupteur COROS. |
| `COROS_PARTNER_CLIENT_ID` / `COROS_PARTNER_CLIENT_SECRET` | vide | Fournis par COROS. Le secret sert aussi à authentifier ses pushes. |
| `COROS_PARTNER_BASE_URL` | `https://open.coros.com` | Hôte OAuth et API (`https://opentest.coros.com` pour le bac à sable). |
| `COROS_PARTNER_URLS` | vide | JSON, un point d’accès remplacé : clés `authorize`, `token`, `refresh`, `deauthorize`, `user`, `workouts`, `daily`, `fit`. |
| `COROS_PARTNER_FIT_HOSTS` | `coros.com` | Hôtes (et sous-domaines) d’où un fichier FIT peut être téléchargé. |
| `GARMIN_CONNECT_ENABLED` | `false` | Interrupteur Garmin. |
| `GARMIN_CONNECT_CLIENT_ID` / `GARMIN_CONNECT_CLIENT_SECRET` | vide | Fournis par Garmin (consumer key / secret). |
| `GARMIN_CONNECT_AUTHORIZE_URL` | `https://connect.garmin.com/oauth2Confirm` | Page de consentement. |
| `GARMIN_CONNECT_TOKEN_URL` | `https://diauth.garmin.com/di-oauth2-service/oauth/token` | Jetons (voir « vérifié / supposé »). |
| `GARMIN_CONNECT_API_BASE_URL` | `https://apis.garmin.com` | API (identifiant, permissions, désinscription). |
| `GARMIN_CONNECT_URLS` | vide | JSON : clés `authorize`, `token`, `user_id`, `permissions`, `registration`. |
| `GARMIN_CONNECT_REQUIRE_CLIENT_HEADER` | `true` | Exige l’en-tête `garmin-client-id` sur chaque push. |
| `WAHOO_CLOUD_ENABLED` | `false` | Interrupteur Wahoo. |
| `WAHOO_CLOUD_CLIENT_ID` / `WAHOO_CLOUD_CLIENT_SECRET` | vide | Fournis par Wahoo (portail développeur). |
| `WAHOO_CLOUD_WEBHOOK_TOKEN` | vide | Le « webhook token » posé sur l’app dans le portail Wahoo (chaîne aléatoire) ; vide : tous les pushes refusés. |
| `WAHOO_CLOUD_BASE_URL` | `https://api.wahooligan.com` | Hôte OAuth et API. |
| `WAHOO_CLOUD_URLS` | vide | JSON : clés `authorize`, `token`, `user`, `workouts`, `permissions`. |
| `WAHOO_CLOUD_FIT_HOSTS` | `wahooligan.com` | Hôtes (et sous-domaines) d’où un fichier FIT Wahoo peut être téléchargé. |
| `POLAR_ACCESSLINK_ENABLED` | `false` | Interrupteur Polar. |
| `POLAR_ACCESSLINK_CLIENT_ID` / `POLAR_ACCESSLINK_CLIENT_SECRET` | vide | Fournis par Polar (admin.polaraccesslink.com). |
| `POLAR_ACCESSLINK_WEBHOOK_SECRET` | vide | `signature_secret_key` rendue à la création du webhook ; vide : tous les pushes (sauf PING) refusés. |
| `POLAR_ACCESSLINK_AUTHORIZE_URL` | `https://flow.polar.com/oauth2/authorization` | Page de consentement. |
| `POLAR_ACCESSLINK_TOKEN_URL` | `https://polarremote.com/v2/oauth2/token` | Jetons (Basic client id:secret). |
| `POLAR_ACCESSLINK_API_BASE_URL` | `https://www.polaraccesslink.com` | API v3. |
| `POLAR_ACCESSLINK_URLS` | vide | JSON : clés `authorize`, `token`, `users`, `exercises`, `webhooks`. |
| `OFFICIAL_CONNECTORS_PILOT_USERS` | vide | Ids de comptes PaceForge (séparés par des virgules) : eux seuls voient et lancent les connexions officielles. Vide : tout le monde. |

Toutes sont dans `docker-compose.prod.yml` et `.env.example`.

## URLs à déclarer chez les fournisseurs (déjà envoyées)

| Fournisseur | Usage | URL |
|---|---|---|
| COROS | Redirection OAuth | `https://paceforge.fr/connect/coros/callback` |
| COROS | Webhook (push des séances) | `https://paceforge.fr/webhook/coros` |
| Garmin | Redirection OAuth | `https://paceforge.fr/connect/garmin/callback` |
| Garmin | Pushes, un chemin par type de résumé | `https://paceforge.fr/webhook/garmin/{type}` |
| Garmin | ou tous les types sur un seul chemin | `https://paceforge.fr/webhook/garmin` |
| Wahoo | Redirection OAuth | `https://paceforge.fr/connect/wahoo/callback` |
| Wahoo | Webhook (`workout_summary`), avec son jeton | `https://paceforge.fr/webhook/wahoo` |
| Polar | Redirection OAuth | `https://paceforge.fr/connect/polar/callback` |
| Polar | Webhook du client (`EXERCISE`), créé par la commande ci-dessous | `https://paceforge.fr/webhook/polar` |

Types Garmin à activer dans le portail (« API Configuration », livraison **Push**) :
`https://paceforge.fr/webhook/garmin/activities`, `…/activityDetails`, `…/manuallyUpdatedActivities`,
`…/dailies`, `…/sleeps`, `…/hrv`, `…/bodyComps`, `…/deregistrations`, `…/userPermissionsChange`.
À laisser désactivés (reçus et ignorés s’ils arrivent, jamais stockés) : `epochs`, `stressDetails`, `userMetrics`,
`pulseox`, `allDayRespiration`, `healthSnapshot`, `skinTemp`, `bloodPressures`, `moveIQActivities`,
`activityFiles`, `mct`, `menstrualCycleTracking`.

Aucune collision : `/coros/callback` reste le rappel du lien MCP COROS, `/connections/…` et `/garmin/…` restent à
l’ancienne connexion Garmin, `/webhook/strava` à Strava.

## Ce qui est vérifié, ce qui est supposé

Sources lues le 15 novembre 2026 :
[COROS « Partner API Access »](https://support.coros.com/hc/en-us/articles/53181766856724-Partner-API-Access),
[spécification OAuth2 PKCE de Garmin](https://developerportal.garmin.com/sites/default/files/OAuth2PKCE.pdf)
(publique), [présentation du programme Garmin](https://developer.garmin.com/gc-developer-program/overview),
la liste publique des fournisseurs de Nango (`packages/providers/providers.yaml` : `coros`, `coros-sandbox`,
`garmin-oauth2`), le code ouvert d’[Open Wearables](https://github.com/the-momentum/open-wearables)
(`backend/app/services/providers/garmin/`).

### Garmin

| Élément | Statut | Source |
|---|---|---|
| OAuth 2.0 PKCE S256, autorisation `connect.garmin.com/oauth2Confirm` (`response_type`, `client_id`, `code_challenge`, `code_challenge_method`, `redirect_uri`, `state`) | Vérifié | Spécification Garmin |
| Échange du code : `grant_type`, `client_id`, `client_secret`, `code`, `code_verifier`, `redirect_uri` ; jeton d’accès 24 h, refresh ~90 j, **nouveau refresh à chaque rafraîchissement** | Vérifié | Spécification Garmin |
| Hôte du point de jetons | **Divergence** : la spécification écrit `connectapi.garmin.com`, Nango et Open Wearables utilisent `diauth.garmin.com` (défaut retenu). À trancher avec la doc du portail ; `GARMIN_CONNECT_TOKEN_URL` le change. | Spécification / Nango / Open Wearables |
| `GET /wellness-api/rest/user/id` → `{"userId": …}`, stable d’une autorisation à l’autre | Vérifié | Spécification Garmin (Open Wearables utilise `/partner-gateway/rest/user/id` : `GARMIN_CONNECT_URLS` si besoin) |
| `GET /wellness-api/rest/user/permissions` | Vérifié (format de réponse ambigu : liste ou `{"permissions": […]}`, les deux sont lus) | Spécification Garmin, Open Wearables |
| `DELETE /wellness-api/rest/user/registration`, obligatoire sur « Déconnecter » et suppression de compte | Vérifié | Spécification Garmin |
| Livraison Push seulement, pas d’historique, capacité limitée (Starter) | Vérifié | Présentation du programme |
| En-tête `garmin-client-id` sur les pushes | Supposé | Open Wearables (le vérifie en production) |
| Forme des pushes `{type: [éléments avec userId]}`, noms de types (`activities`, `activityDetails`, `dailies`, `sleeps`, `hrv`, `bodyComps`, `deregistrations`, `userPermissionsChange`…) | Supposé | Open Wearables |
| Champs des résumés (`activityId`, `startTimeInSeconds`, `startTimeOffsetInSeconds`, `durationInSeconds`, `distanceInMeters`, `averageHeartRateInBeatsPerMinute`, `sleepLevelsMap`, `hrvValues`, `timeOffsetHeartRateSamples`, `timeOffsetSleepRespiration`, `weightInGrams`…) | Supposé | Open Wearables ; noms publics de l’API Health/Activity |
| `activityId` = identifiant Garmin Connect (donc le même que l’ancienne connexion stocke) | Supposé | Open Wearables, Terra |

### COROS

| Élément | Statut | Source |
|---|---|---|
| OAuth 2.0 multi-utilisateur (authorize, access token, refresh, deauthorize, bind state) ; getUserInfo, getWorkoutRecords (30 j par appel, 3 mois en arrière), getDailyData ; getWorkoutDetailFit ; push du résumé de séance ~5 min après synchro, avec relances, client id/secret en en-têtes ; 1 000 appels/min | Vérifié | Page COROS |
| `open.coros.com/oauth2/authorize`, `/oauth2/accesstoken`, `/oauth2/refresh-token` ; `openId` dans la réponse de jetons ; pas de PKCE ni de scopes listés | Vérifié (source tierce publique) | Nango |
| Chemins `deauthorize`, `user`, `workouts` (`/oauth2/deauthorize`, `/coros/userinfosim`, `/v2/coros/sport/list`) ; jeton et `openId` en paramètres de requête ; enveloppe `{result: "0000", data}` ; un refresh qui peut répondre « 0000 » seul | Supposé | Mémoire de la documentation COROS, non relue |
| Chemins `daily` et `fit` | **Inconnus** (valeurs provisoires) : à poser dans `COROS_PARTNER_URLS` | — |
| Corps du push (`sportDataList`), noms des en-têtes (plusieurs graphies acceptées, les deux exigés) | Supposé | — |
| Champs d’une séance (`labelId`, `mode`/`subMode`, `startTime`, `endTime`, `startTimezone` en quarts d’heure, `distance` en m, `duration` en s, `avgHeartRate`, `calorie`, `fitUrl`) et la table `mode/subMode` → sport | Supposé ; **le fichier FIT, quand il est lu, l’emporte** (format public : sport, temps, distance, D+, FC, tours) | — |
| `labelId` = l’identifiant que stocke le lien MCP | Supposé (un doublon sous un autre id est de toute façon rattaché, jamais dupliqué) | — |
| Données du jour (`happenDay`, `steps`, `calorie`) | Supposé ; seuls les pas (et kcal) sont lus | — |

### Wahoo et Polar

Sources lues le 21 novembre 2026 : la documentation publique de Wahoo (cloud-api.wahooligan.com) et celle de Polar
AccessLink v3 (www.polar.com/accesslink-api), telles que résumées dans la demande du propriétaire ; rien n’a encore
été vérifié sur un vrai compte.

| Élément | Statut | Source |
|---|---|---|
| Wahoo : `api.wahooligan.com/oauth/authorize` (`client_id`, `redirect_uri`, `response_type=code`, `scope`, `state`), `/oauth/token` (formulaire avec `client_id`, `client_secret`), scopes `user_read workouts_read offline_data`, jeton d’accès ~2 h, **nouveau refresh à chaque rafraîchissement** | Doc publique, non relue ici | Doc Wahoo |
| Wahoo : `GET /v1/user` → `id` ; `GET /v1/workouts?page=&per_page=` → `{workouts: […]}` ; `DELETE /v1/permissions` | Doc publique, non relue ici (enveloppe `workouts` supposée ; une liste nue est lue aussi) | Doc Wahoo |
| Wahoo : webhook `{event_type: "workout_summary", webhook_token, user {id}, workout_summary {…, file {url}, workout {…}}}` ; autres `event_type` acquittés, ignorés | Doc publique, non relue ici ; un 2xx suffit (supposé) | Doc Wahoo |
| Wahoo : champs (`id`, `starts` ISO 8601 UTC, `minutes`, `name`, `workout_type_id` ; résumé `distance_accum` m, `duration_active_accum` / `duration_total_accum` s, `heart_rate_avg`, `ascent_accum`, `calories_accum`, `power_avg`) ; nombres possiblement en chaînes (lus des deux façons) | Supposé ; **le FIT l’emporte** | — |
| Wahoo : fichier FIT sur un hôte `wahooligan.com` | Supposé (`WAHOO_CLOUD_FIT_HOSTS`) | — |
| Wahoo : table `workout_type_id` (`WORKOUT_TYPES`) : 1 course, 3 piste, 4 trail, 5 tapis, 67 course (compétition), 71 course virtuelle ; quelques vélos, marche, rando, natation, rameur, yoga ; inconnu → « Workout » (importé quand même, comme COROS et Garmin) | Supposé. **Divergence** : la consigne du 21 nov. disait « 3 tapis, 5 trail » ; la table publique de Wahoo lue de mémoire dit 3 piste, 4 trail, 5 tapis (retenue). Sans effet sur « course ou pas » (1, 3, 4, 5 sont tous des courses) ; le FIT tranche trail/tapis quand il est lu. | Table publique de Wahoo (mémoire) |
| Polar : `flow.polar.com/oauth2/authorization` (`scope=accesslink.read_all`), jetons à `polarremote.com/v2/oauth2/token` en Basic, réponse `access_token`, `expires_in` (années), `x_user_id`, **pas de refresh** | Doc publique, non relue ici | Doc Polar |
| Polar : `POST /v3/users {"member-id"}` (409 = déjà inscrit, accepté), `DELETE /v3/users/{x_user_id}`, `GET /v3/exercises` (30 derniers jours), `GET /v3/exercises/{id}`, `GET /v3/exercises/{id}/fit` | Doc publique, non relue ici | Doc Polar |
| Polar : `member-id` = `pf-` + 32 caractères hex de HMAC-SHA256(`SECRET_KEY`, id du compte) : opaque, jamais l’e-mail ; change si `SECRET_KEY` change (409 alors impossible à relier : sans effet, Polar renvoie l’identifiant par `x_user_id`) | Choix PaceForge | — |
| Polar : webhook `POST /v3/webhooks {events: ["EXERCISE"], url}` (Basic), réponse `data.signature_secret_key` ; push `{event, user_id, entity_id, timestamp, url}`, en-tête `Polar-Webhook-Signature` = HMAC-SHA256 hex du corps brut | Doc publique, non relue ici (enveloppe `data` supposée ; un objet nu est lu aussi) | Doc Polar |
| Polar : un PING est acquitté sans vérifier sa signature (envoyé à la création, avant que PaceForge ait la clé ; il ne porte rien) ; l’`url` du push est ignorée, PaceForge reconstruit l’adresse depuis `entity_id` (lettres, chiffres, `_`, `-`, 64 au plus) | Choix PaceForge | — |
| Polar : champs d’un exercice (`id` chaîne courte, `start_time` à l’heure locale + `start_time_utc_offset` en minutes, `duration` ISO 8601, `distance` m, `sport`, `detailed_sport_info`, `heart_rate {average, maximum}`, `calories`, `ascent`) ; table des sports (`SPORTS` : RUNNING, ROAD_RUNNING, TRAIL_RUNNING, TREADMILL_RUNNING, TRACK_AND_FIELD_RUNNING, INDOOR_RUNNING… ; inconnu → « Workout ») | Supposé ; **le FIT l’emporte** | — |
| `user_id` d’un push Polar = `x_user_id` de l’échange de jetons | Supposé | — |

Les exemples de charges utiles des tests (`tests/official_connector_fixtures.py`, et ceux en tête de
`tests/test_official_connectors_wahoo_polar.py`) sont **étiquetés comme
supposés** : à remplacer par de vraies charges (doc du portail, ou premier push journalisé sur le compte du
propriétaire).

## Ce qui est stocké, et où

Mêmes tables et mêmes étiquettes de source que les anciens liens, pour que Santé, la préférence de montre
(`users.health_source` : « COROS », « Garmin ») et la frise du sommeil continuent de fonctionner. Chaque ligne de
santé porte `details.via` (`garmin_connect_api` ou `coros_partner_api`) pour la traçabilité.

| Donnée | Garmin officiel | COROS officiel |
|---|---|---|
| Activités | `Activity.garmin_activity_id` (même colonne que l’ancienne connexion) ; tours (Strava) depuis `activityDetails` ; parent multisport ignoré (les étapes arrivent seules) | `Activity.coros_activity_id` ; valeurs et tours du fichier FIT quand il est lisible |
| Sommeil | `sleep` (fenêtre, minutes endormi, phases), intervalles de phases (`HealthSample`, source Garmin), `nap` | — (le lien MCP les apporte) |
| VFC de nuit | `hrv` : exp(moyenne ln RMSSD) des relevés `hrvValues` dans les phases endormies de la nuit stockée ; jamais `lastNightAvg` | — |
| FC de nuit | `hr_night` : relevés du jour (`timeOffsetHeartRateSamples`) dans la nuit, partie après minuit (`partial`) | — |
| Respiration | `resp_night` : relevés du résumé de sommeil (`timeOffsetSleepRespiration`) ; `allDayRespiration` ignoré | — |
| Journée | `steps` (kcal, minutes d’intensité), `hr_day` (24 h), `stress` (moyenne Garmin, contexte seulement, comme l’ancienne connexion) | `steps` (kcal) |
| Pesées | `weight`, `body_fat` avec `measured_at` (lues par `athlete_weight` et Santé) | — |
| Activités Wahoo / Polar | `Activity.wahoo_activity_id` (id de la séance Wahoo, entier) / `Activity.polar_activity_id` (id de l’exercice Polar, chaîne) ; `raw_data.source` « wahoo » / « polar », `raw_data.via` `wahoo_cloud_api` / `polar_accesslink_api` ; valeurs et tours du FIT quand il est lisible ; rien d’autre (ni santé, ni charge, ni index de marque) | |
| Jamais | Body Battery, Training Readiness, score de sommeil, statut VFC, VO2 max, charge, FC de repos de marque, SpO2 | récupération, charge, stress, score de sommeil, niveau, FC de repos de marque |

Ordre d’arrivée : une VFC ou une FC de nuit reçue avant la nuit attend (nouvel essai toutes les 30 min pendant
4 h) ; au dernier essai, la VFC est calculée sur sa propre fenêtre (étiquetée `main_window_v1`), la FC de nuit est
abandonnée.

Consentement : sans consentement aux données de santé, un push de santé Garmin est écarté dès la requête web (rien
n’est mis en file) et `health.store_daily` / `store_samples` refusent de toute façon ; la lecture COROS ne demande
même pas les données du jour. Les activités arrivent toujours. Wahoo et Polar n’apportent que des activités : la
connexion ne demande pas le consentement aux données de santé (comme Strava), et le retirer ne les déconnecte pas.

Idempotence : même résumé deux fois → une ligne (identifiant d’activité unique ; une valeur par jour, métrique et
source).

## Doublons et coexistence avec les anciens liens

Règle : **un lien par marque**. Faire la connexion officielle d’une marque **remplace** son ancien lien (lien MCP
COROS révoqué, connexion Garmin oubliée), après l’échange de jetons réussi ; ce qui a été reçu reste. Si l’athlète
reconnecte ensuite l’ancien lien, les deux écrivent les mêmes lignes (même source, même jour) : jamais de double
compte, la dernière écriture gagne. Activités : même identifiant → même ligne ; identifiant différent → jumeau
retrouvé (même sport, départ à 3 min, distance à 15 %) et rattaché.

## Sécurité des pushes

- COROS : client id et secret de l’application dans les en-têtes, comparés à temps constant ; sinon 401.
- Garmin : en-tête `garmin-client-id` comparé à temps constant (désactivable par `GARMIN_CONNECT_REQUIRE_CLIENT_HEADER`) ; corps qui doit être un objet de types connus (sinon 400) ; chaque élément doit nommer un `userId` d’un lien actif (les inconnus sont ignorés, réponse identique) ; un élément dont l’identifiant ne correspond pas à son lien est écarté dans le worker ; une désinscription n’est appliquée que si Garmin ne confirme plus l’accès actuel (avis tardif d’une ancienne autorisation).
- Wahoo : `webhook_token` du corps comparé à temps constant à `WAHOO_CLOUD_WEBHOOK_TOKEN` (vide : tout est refusé) ; sinon 401. Le worker vérifie encore que le lien nomme bien l’utilisateur Wahoo du push.
- Polar : `Polar-Webhook-Signature` comparée à temps constant au HMAC-SHA256 du corps brut (clé `POLAR_ACCESSLINK_WEBHOOK_SECRET`, vide : tout est refusé) ; sinon 401. PING acquitté sans rien faire.
- Tailles maximales (seulement connecteur actif) : `activityDetails` et la racine 32 Mo (réduits aux tours avant la file), autres types 8 Mo, désinscriptions et permissions 64 Ko, COROS et Wahoo 1 Mo, Polar 64 Ko.
- Les fichiers FIT ne sont téléchargés que depuis `COROS_PARTNER_FIT_HOSTS` / `WAHOO_CLOUD_FIT_HOSTS`, en HTTPS, 32 Mo au plus ; ceux de Polar depuis l’API Polar, avec le jeton de l’athlète.
- Courtier Celery indisponible : 503, le fournisseur renvoie (Garmin n’envoie chaque résumé qu’une fois).

## Liste d’activation

1. Recevoir les identifiants ; lire la documentation du portail ; comparer avec « vérifié / supposé » ci-dessus et
   corriger au besoin par `*_URLS` ou dans le code (chemins COROS `daily` et `fit`, hôte des jetons Garmin).
2. Vérifier que les URLs déclarées sont exactement celles du tableau ; pour Garmin, activer les types listés en
   livraison Push.
3. Merger la branche (migrations `k2f3a4b5c6d7` puis `l3a4b5c6d7e8` chaînées après les autres) et déployer : rien ne
   change encore.
4. Dans Coolify : `OFFICIAL_CONNECTORS_PILOT_USERS=<id du compte du propriétaire>`, puis
   `GARMIN_CONNECT_CLIENT_ID`, `GARMIN_CONNECT_CLIENT_SECRET`, `GARMIN_CONNECT_ENABLED=true` (et/ou les variables
   COROS). Redéployer (le worker relit la planification au démarrage).
5. Sur le compte pilote : Réglages › « Garmin · connexion officielle » › Connecter. Vérifier dans les journaux :
   pushes acceptés (pas de « client header missing »), activités créées, nuit, VFC, pas. Garmin : outil
   « Partner Verification » du portail. COROS : une séance arrive ~5 min après une synchro ; vérifier ses tours, et
   que les données du jour arrivent (sinon `COROS_PARTNER_URLS` → `daily`).
6. Remplacer les exemples supposés des tests par une vraie charge (anonymisée) et ajuster les correspondances.
7. Ouvrir à tous : vider `OFFICIAL_CONNECTORS_PILOT_USERS`, redéployer. La politique de confidentialité affiche
   alors les lignes du connecteur actif (déjà le cas pendant le pilote : le traitement a lieu).
8. **Wahoo** : dans le portail développeur Wahoo, sur l’app « PaceForge », poser l’URL de webhook
   `https://paceforge.fr/webhook/wahoo` et un jeton aléatoire, le même dans `WAHOO_CLOUD_WEBHOOK_TOKEN` ; vérifier
   que la redirection déclarée est `https://paceforge.fr/connect/wahoo/callback`. Puis `WAHOO_CLOUD_CLIENT_ID`,
   `WAHOO_CLOUD_CLIENT_SECRET`, `WAHOO_CLOUD_ENABLED=true` (pilote d’abord, comme ci-dessus). Après la connexion,
   les 120 dernières séances arrivent ; vérifier `workout_type_id` et le FIT d’une vraie séance (hôte du fichier :
   `WAHOO_CLOUD_FIT_HOSTS`).
9. **Polar** : `POLAR_ACCESSLINK_CLIENT_ID`, `POLAR_ACCESSLINK_CLIENT_SECRET` posés, créer **une fois** le webhook du
   client : `docker compose exec web python -m app.connectors.polar_accesslink create-webhook` (URL par défaut
   `BASE_URL/webhook/polar` ; `show-webhook` pour relire). Copier `signature_secret_key` dans
   `POLAR_ACCESSLINK_WEBHOOK_SECRET`, puis `POLAR_ACCESSLINK_ENABLED=true`, redéployer. Sans webhook, les séances
   arrivent quand même par la lecture des 12 h.
10. Application iPhone/Android : livrer la petite modification native Garmin (ci-dessous) **avant** d’ouvrir Garmin
   à tous, sinon l’app continue d’ouvrir l’ancienne connexion Garmin.

## Bascule depuis les anciens liens (à faire par le propriétaire, pas fait ici)

1. Pendant le pilote puis l’ouverture : les deux blocs coexistent dans Réglages ; le bloc officiel dit qu’il
   remplace l’ancien lien. Un athlète passe à l’officiel en cliquant « Connecter » : son ancien lien est retiré, ses
   données restent.
2. COROS dans l’app : dès que COROS officiel est actif pour le compte, la feuille de connexion de l’app passe par le
   flux officiel (rien à livrer). Attention : tant que les données du jour COROS n’apportent pas nuits et VFC (à
   vérifier, étape 5), un athlète qui passe à l’officiel perd ses nuits COROS : garder COROS en pilote dans ce cas.
3. Prévenir les athlètes encore sur l’ancien lien (e-mail ou bandeau) ; fixer une date.
4. Ensuite, dans une livraison à part : retirer les boutons de connexion des anciens liens (`coros_connect.html`,
   `garmin_login.html`, routes `/coros/connect`, `/garmin/connect`), puis, après la date, les synchros
   (`app/services/coros.py` côté MCP, `app/services/garmin.py`, `app/tasks/garmin_sync.py`, les entrées beat
   `sync-coros` et `sync-garmin`), les tables `coros_connections`, `garmin_connections`, `oauth_clients` (migration
   qui les vide puis les supprime), la dépendance `garminconnect`, et les lignes correspondantes de la politique
   (« ton e-mail et ton mot de passe Garmin partent une fois chez Garmin »). Les colonnes `garmin_activity_id` et
   `coros_activity_id` restent (les connecteurs officiels les utilisent).

## Retour arrière

`*_ENABLED=false` (ou retirer le secret) puis redéployer : routes en 404, plus de planification, plus de lignes dans
la politique, blocs masqués. Les liens officiels restent en base, inertes ; ils sont supprimés (avec désinscription
chez le fournisseur, au mieux) à la suppression du compte ou au retrait du consentement. Les données reçues restent.
Les anciens liens n’ont jamais été touchés par le code et fonctionnent toujours ; un athlète passé à l’officiel
reconnecte l’ancien lien dans Réglages. Pour effacer complètement : vider `official_connections` (la migration
descendante supprime la table). La descente de `l3a4b5c6d7e8` supprime les séances que seuls Wahoo ou Polar avaient
apportées, les liens Wahoo et Polar, puis les deux colonnes.

## Confidentialité (texte prêt)

Affiché dans « Qui reçoit quoi » de `/privacy` **seulement** quand le connecteur est actif
(`partials/official_privacy.html`) :

- **COROS** (COROS Wearables Inc.), si tu relies COROS par sa connexion officielle : tu autorises PaceForge sur la
  page de COROS. COROS envoie ensuite à PaceForge le résumé de chaque séance quelques minutes après la
  synchronisation de ta montre, et PaceForge y lit le fichier de la séance (tours, fréquence cardiaque, dénivelé) ;
  avec ton consentement aux données de santé, il lit aussi tes pas et tes calories du jour. Les serveurs de COROS
  sont hors UE pour les comptes Monde (États-Unis) et Chine. « Déconnecter » retire l’accès de PaceForge chez COROS.
- **Garmin** (Garmin Ltd. et ses filiales, États-Unis, hors UE), si tu relies Garmin par sa connexion officielle :
  tu autorises PaceForge sur la page de Garmin, sans lui donner ton mot de passe, et tu y choisis ce que tu
  partages. Garmin envoie ensuite à PaceForge tes activités (résumé, tours, fréquence cardiaque) et, avec ton
  consentement aux données de santé, tes résumés du jour (pas, calories, minutes d’intensité, fréquence cardiaque,
  stress), ton sommeil et ses phases, ta VFC et ta respiration de nuit, tes pesées (poids, masse grasse).
  « Déconnecter » ou la suppression de ton compte retirent cet accès chez Garmin ; tu peux aussi le retirer dans
  Garmin Connect.

- **Wahoo** (Wahoo Fitness LLC, États-Unis, hors UE), si tu relies Wahoo par sa connexion officielle : tu autorises
  PaceForge sur la page de Wahoo, en lecture seule, sans lui donner ton mot de passe. Wahoo envoie ensuite à
  PaceForge le résumé de chaque séance enregistrée (date, durée, distance, dénivelé, fréquence cardiaque, puissance,
  calories), et PaceForge y lit le fichier de la séance (tours, fréquence cardiaque) ; seulement tes activités,
  aucune donnée de santé. « Déconnecter » ou la suppression de ton compte retirent cet accès chez Wahoo.
- **Polar** (Polar Electro Oy, Finlande), si tu relies Polar par sa connexion officielle : tu autorises PaceForge sur
  la page de Polar, en lecture seule, sans lui donner ton mot de passe, et PaceForge t’inscrit auprès de Polar sous
  un identifiant anonyme (jamais ton e-mail). Polar signale ensuite à PaceForge chaque nouvelle séance, que PaceForge
  lit avec son fichier (date, durée, distance, dénivelé, fréquence cardiaque, tours) ; seulement tes activités : ni
  ton activité du jour, ni ton sommeil, ni tes informations physiques. « Déconnecter » ou la suppression de ton
  compte retirent cet accès chez Polar.

La ligne du tableau « Activités importées de Strava, COROS ou Garmin » nomme aussi Wahoo et Polar, chacun seulement
quand il est actif. Rien d’autre à changer : cette ligne et la liste des données de
santé (texte du consentement, `health_consent.DATA`) couvrent déjà ces données ; le consentement n’a pas à être
redemandé (même liste). À vérifier avec les contrats : l’entité COROS et la localisation des serveurs de la Partner
API (le texte reprend celle du lien MCP), un éventuel accord de traitement à signer avec chaque fournisseur.

Export (« Télécharger mes données ») : `connexions.json` liste les liens officiels (fournisseur, identifiant chez
lui, permissions, état, dates, dernière erreur), jamais leurs jetons. Suppression du compte et retrait du
consentement : désinscription chez le fournisseur puis suppression du lien.

## Application mobile : verdict

- **COROS : rien à livrer.** L’app demande déjà le lien de sa feuille de connexion (`/mobile/oauth/coros`) ; le
  serveur fait démarrer cette feuille sur le flux officiel quand COROS officiel est actif pour le compte
  (`/connect/coros?next=app`), et le retour se fait sur le même `paceforge://devices?connection=coros&result=…`.
  L’état, « Synchroniser » et « Déconnecter » suivent le lien officiel.
- **Garmin : une petite modification native à livrer avant l’ouverture à tous.** L’app ouvre aujourd’hui la page
  `/connections/garmin` dans sa WebView (ancienne connexion), et la WebView envoie tout lien externe au navigateur
  système, où la session PaceForge n’existe pas : le flux officiel ne peut pas s’y faire. Côté serveur tout est
  prêt : `/mobile/connections` ajoute `"official": {"garmin": true}` quand Garmin officiel est visible pour le compte,
  l’action `link` est acceptée pour `garmin`, et `/mobile/oauth/garmin` (ticket, consentement, retour à l’app)
  existe. Le changement natif : dans `mobile/src/connections.ts`, lire `official` ; dans `mobile/App.tsx`
  (`connectProvider`), ouvrir la feuille (`refreshConnections({ provider: 'garmin', kind: 'link' })`) quand
  `connections.official?.garmin` est vrai, sinon garder `openSettings('/connections/garmin')`. Puis la check-list de
  `mobile/AGENTS.md` et TestFlight. Tant que ce n’est pas livré, les utilisateurs de l’app gardent l’ancienne
  connexion Garmin dans l’app et peuvent passer à l’officielle depuis paceforge.fr.

## Agendas : Google Agenda et Outlook (Coaching › « Mon agenda »)

Statut au 23 novembre 2026 : **code prêt, éteint**, aucun identifiant créé. Rien n’a été essayé contre les vrais
Google et Microsoft : tout est testé contre des faux (`tests/test_coaching_accounts.py`). Ce que ces connexions lisent
et comment : `docs/coaching.md` › « Mon agenda ». Code : `app/services/coaching_accounts.py` (pas dans
`app/connectors` : elles n’apportent aucune activité, seulement des heures occupées).

Comme les connecteurs officiels, chacun est **actif** seulement si son drapeau est à `true` **et** son client id et
son secret sont posés (`settings.google_calendar_ready`, `settings.microsoft_calendar_ready`) : sinon « Mon agenda »
ne montre pas son bouton (le calendrier de l’iPhone reste, dans l’app), les routes répondent 404, la politique de confidentialité n’en dit rien, rien n’appelle le
fournisseur.

| Variable | Défaut | Rôle |
|---|---|---|
| `GOOGLE_CALENDAR_ENABLED` | `false` | Interrupteur Google Agenda. |
| `GOOGLE_CALENDAR_CLIENT_ID` / `GOOGLE_CALENDAR_CLIENT_SECRET` | vide | Le client OAuth « Application Web » de Google Cloud. |
| `GOOGLE_CALENDAR_URLS` | vide | JSON, un point d’accès remplacé : clés `authorize`, `token`, `revoke`, `freebusy`. |
| `MICROSOFT_CALENDAR_ENABLED` | `false` | Interrupteur Outlook. |
| `MICROSOFT_CALENDAR_CLIENT_ID` / `MICROSOFT_CALENDAR_CLIENT_SECRET` | vide | L’« ID d’application (client) » et la **valeur** d’un secret client de l’inscription Entra. |
| `MICROSOFT_CALENDAR_SCOPE` | `Calendars.ReadBasic` | La permission déléguée lue : `Calendars.ReadBasic` (la moindre) ou `Calendars.Read` (repli). Toute autre valeur est lue comme `Calendars.ReadBasic`. |
| `MICROSOFT_CALENDAR_URLS` | vide | JSON : clés `authorize`, `token`, `calendar_view`. |

URLs de redirection à déclarer : `https://paceforge.fr/coaching/agenda/google/callback` (Google) et
`https://paceforge.fr/coaching/agenda/microsoft/callback` (Microsoft) — bâties sur `BASE_URL`, comme les autres.

### Chez Google (Google Cloud Console, compte contact@paceforge.fr)

1. **Projet** : console.cloud.google.com › nouveau projet « PaceForge ».
2. **API** : API et services › Bibliothèque › « Google Calendar API » › Activer.
3. **Google Auth Platform › Branding** (l’ancien « Écran de consentement OAuth ») : nom de l’application
   « PaceForge » ; e-mail d’assistance contact@paceforge.fr ; page d’accueil `https://paceforge.fr` ; règles de
   confidentialité `https://paceforge.fr/privacy` ; conditions d’utilisation `https://paceforge.fr/cgu` ; domaine
   autorisé `paceforge.fr` (déjà validé dans la Search Console : la balise `google-site-verification` est servie) ;
   contact développeur contact@paceforge.fr. Pas de logo au début (un logo déclenche une vérification de la marque).
4. **Audience** : type « Externe ». Statut de publication :
   - « En test » : 100 utilisateurs de test au plus, à lister ici (leur e-mail Google) ; leurs autorisations
     **expirent après 7 jours** — l’écran dit alors « À reconnecter » chaque semaine. Bien pour essayer sur ton
     propre compte ;
   - « En production » sans vérification : l’écran « Google n’a pas validé cette application » s’affiche et 100
     nouveaux utilisateurs au plus, mais plus d’expiration à 7 jours.
5. **Accès aux données** : « Ajouter ou supprimer des niveaux d’accès » › saisir
   `https://www.googleapis.com/auth/calendar.freebusy` (« Voir vos disponibilités dans vos agendas »), et rien
   d’autre. Regarde l’étiquette que la console lui donne (non sensible, sensible, restreint) : la documentation
   publique ne le dit pas. Si elle est « sensible », la publication demande la vérification de l’application :
   domaine validé, page d’accueil et politique de confidentialité sur ce domaine, une justification de la permission
   (« PaceForge place les séances d’entraînement de l’athlète hors de ses heures occupées ; il ne lit que les
   intervalles occupés, jamais un événement ; c’est la permission la plus étroite de l’API Agenda »), une courte vidéo
   qui montre le bouton, l’écran de consentement et les heures données aux séances ; Google annonce 3 à 5 jours
   ouvrés.
6. **Clients** : « Créer un client » › type « Application Web », nom « PaceForge web » ; URI de redirection
   autorisés : `https://paceforge.fr/coaching/agenda/google/callback` (aucune origine JavaScript). Copie l’ID client
   et le secret.
7. **Coolify** : `GOOGLE_CALENDAR_ENABLED=true`, `GOOGLE_CALENDAR_CLIENT_ID=…`, `GOOGLE_CALENDAR_CLIENT_SECRET=…`,
   puis redéploie.

### Chez Microsoft (centre d’administration Microsoft Entra)

1. **Un annuaire** : entra.microsoft.com, avec un annuaire Entra au nom de PaceForge (gratuit, créé avec un compte
   Azure pour contact@paceforge.fr ; un compte Microsoft personnel seul ne peut plus inscrire d’application).
2. **Inscriptions d’applications › Nouvelle inscription** : nom « PaceForge » ; types de comptes pris en charge :
   « Comptes dans un annuaire d’organisation (tout locataire Microsoft Entra ID – multilocataire) et comptes Microsoft
   personnels (par exemple, Skype, Xbox) » ; URI de redirection : plateforme « Web »,
   `https://paceforge.fr/coaching/agenda/microsoft/callback`.
3. **Certificats et secrets** › « Nouveau secret client » (24 mois au plus) : copie sa **valeur** (pas son ID), et
   note la date d’expiration — à renouveler avant, sinon chaque lecture échoue.
4. **Autorisations de l’API** › Ajouter › Microsoft Graph › Autorisations déléguées : `Calendars.ReadBasic` et
   `offline_access`. Retire `User.Read`, ajoutée d’office et inutile ici. Aucun consentement administrateur n’est
   demandé pour ces deux-là ; une organisation peut néanmoins l’exiger de son côté (l’athlète voit alors « Ton
   organisation n’autorise pas cette connexion… »).
5. **Personnalisation et propriétés** : page d’accueil `https://paceforge.fr`, conditions `https://paceforge.fr/cgu`,
   confidentialité `https://paceforge.fr/privacy`. **Vérification de l’éditeur** (le badge bleu, et ce que beaucoup
   d’organisations exigent pour laisser leurs employés consentir à une application multilocataire) : un identifiant
   Microsoft AI Cloud Partner Program vérifié, rattaché à l’annuaire, et le domaine d’éditeur `paceforge.fr` vérifié —
   ce dernier demande de servir `https://paceforge.fr/.well-known/microsoft-identity-association.json`, **qui
   n’existe pas encore** (une petite route à ajouter le jour où tu la demandes). Sans elle, les comptes personnels
   Outlook.com et les organisations qui le permettent se connectent quand même.
6. **Coolify** : `MICROSOFT_CALENDAR_ENABLED=true`, `MICROSOFT_CALENDAR_CLIENT_ID=<ID d’application (client)>`,
   `MICROSOFT_CALENDAR_CLIENT_SECRET=<valeur du secret>`, `MICROSOFT_CALENDAR_SCOPE=Calendars.ReadBasic`, puis
   redéploie.
7. **Si les comptes personnels sont refusés** (des fils Microsoft Q&A rapportent des 401/403 avec
   `Calendars.ReadBasic` sur Outlook.com) : la source passe à « À reconnecter » aussitôt après la première lecture et
   le journal du worker dit « refused the reading (check MICROSOFT_CALENDAR_SCOPE) ». Ajoute alors `Calendars.Read`
   dans les autorisations de l’API, pose `MICROSOFT_CALENDAR_SCOPE=Calendars.Read`, redéploie : chaque athlète
   reconnecte une fois. Aucune nouvelle version n’est nécessaire.

### Confidentialité (texte en place)

Avec un connecteur configuré, `/privacy` ajoute (`partials/agenda_privacy.html`, `partials/coaching_privacy.html`) :

- dans « Qui reçoit quoi », pour Google : « **Google** (Google LLC, États-Unis, hors UE), si tu connectes Google
  Agenda dans Coaching › « Mon agenda » : tu autorises PaceForge sur la page de Google, sans lui donner ton mot de
  passe, à voir seulement tes disponibilités. PaceForge y lit tes heures occupées des 21 prochains jours ; jamais un
  titre, un lieu ni un participant. « Retirer », l’arrêt du coaching ou la suppression de ton compte retirent cet
  accès chez Google ; tu peux aussi le retirer dans ton compte Google. » ;
- pour Microsoft : « **Microsoft** (Microsoft Corporation, États-Unis, hors UE), si tu connectes Outlook dans
  Coaching › « Mon agenda » : tu autorises PaceForge sur la page de Microsoft, sans lui donner ton mot de passe, à
  lire ton agenda. PaceForge n’en demande que le début, la fin et la disponibilité (occupé, absent) de chaque
  événement des 21 prochains jours ; jamais un titre, un lieu ni un participant. « Retirer » efface l’accès chez
  PaceForge ; pour le retirer aussi chez Microsoft, passe par ton compte Microsoft. » ;
- « Apple et Google ne reçoivent rien de PaceForge. » devient, Google configuré : « Apple ne reçoit rien de
  PaceForge, ni Google en dehors de Google Agenda si tu le connectes. » ;
- la ligne « Coaching (bêta) · ton agenda » dit « si tu connectes Google Agenda[, Outlook] ou le calendrier de ton
  iPhone », « l’accès à ce compte (chiffré, jamais ton mot de passe) ». Depuis le 24 novembre 2026, plus aucune
  adresse ne se colle : la ligne le dit, et dit que celles collées avant restent conservées, chiffrées, jusqu’à leur
  retrait. Le calendrier de l’iPhone y est toujours nommé (il ne dépend d’aucun connecteur : l’app le lit sur le
  téléphone et n’envoie que les heures occupées, docs/coaching.md › « Mon agenda »).

À vérifier avec un juriste : l’entité Google à nommer pour des utilisateurs européens (Google Ireland Limited est le
fournisseur des services Google dans l’EEE ; le texte nomme Google LLC comme pour les autres fournisseurs
américains), et les clauses de transfert hors UE.

### Application mobile

Une modification native, **livrée dans ce lot, à publier sur TestFlight** : l’app ouvre la connexion Google ou
Outlook dans la feuille de connexion du système (Google refuse les WebView), et annonce qu’elle sait le faire
(`.pf-native-agenda`). Une app installée plus ancienne ne montre pas les deux boutons ; depuis le 24 novembre 2026,
plus aucune adresse ne s’y colle non plus : une connexion faite depuis paceforge.fr ou une app à jour y apparaît dans
la liste, avec « Retirer ».
