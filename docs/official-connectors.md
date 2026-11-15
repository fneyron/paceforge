# Connecteurs officiels COROS et Garmin

Statut au 15 novembre 2026 : **code prêt, désactivé**. Les demandes d’accès sont envoyées (COROS Partner API ;
Garmin Connect Developer Program, accès Business « Starter »). Tant que les identifiants ne sont pas arrivés et
posés dans Coolify, rien ne change pour personne : aucune route (404), aucune page, aucune tâche planifiée, aucun
appel sortant, aucune ligne dans la politique de confidentialité.

Un connecteur est **actif** seulement si son drapeau est à `true` **et** son client id et son secret sont posés
(`settings.coros_partner_ready`, `settings.garmin_connect_ready`).

## Architecture

| Élément | Rôle |
|---|---|
| `app/connectors/__init__.py` | Qui est actif (`ready`, `visible_to`, pilote), la planification (`beat_schedule`, vide tant que rien n’est actif). |
| `app/connectors/base.py` | OAuth (state, PKCE pour Garmin, vérifieur chiffré 10 min dans la session), jetons chiffrés (Fernet), rafraîchissement avec rotation (un seul à la fois par lien : verrou de ligne + verrou de processus, nouveaux jetons commités d’abord, comme `services/coros.py`), déconnexion (désinscription chez le fournisseur), état affiché, import des activités (une ligne par sortie). |
| `app/connectors/garmin_connect.py` | Garmin : OAuth, identifiant utilisateur, permissions, désinscription, lecture des pushes, correspondance vers PaceForge. |
| `app/connectors/coros_partner.py` | COROS : OAuth, push des séances, lecture périodique (séances, données du jour), fichier FIT. |
| `app/connectors/fit.py` | Lecteur FIT minimal (tours et session), format public du SDK FIT. |
| `app/models/official_connection.py` | Table `official_connections` (migration `k2f3a4b5c6d7`, à chaîner après les autres au moment du merge). |
| `app/routers/official_connectors.py` | `/connect/{coros,garmin}`, `/connect/…/callback`, `/connect/…/disconnect`, `/webhook/coros`, `/webhook/garmin[/{type}]`. |
| `app/tasks/official_connectors.py` | Le travail dans Celery : traitement des pushes, nuits en attente, lecture COROS, tour horaire. |
| `partials/official_connection.html`, `partials/official_privacy.html` | Le bloc de Réglages et les lignes de la politique, affichés seulement connecteur actif. |

Table `official_connections` : `user_id`, `provider` (`coros` | `garmin`), `provider_user_id` (openId COROS,
identifiant API Garmin : ce que nomment les pushes), jetons d’accès et de rafraîchissement chiffrés, `expires_at`,
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

Les exemples de charges utiles des tests (`tests/official_connector_fixtures.py`) sont **étiquetés comme
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
| Jamais | Body Battery, Training Readiness, score de sommeil, statut VFC, VO2 max, charge, FC de repos de marque, SpO2 | récupération, charge, stress, score de sommeil, niveau, FC de repos de marque |

Ordre d’arrivée : une VFC ou une FC de nuit reçue avant la nuit attend (nouvel essai toutes les 30 min pendant
4 h) ; au dernier essai, la VFC est calculée sur sa propre fenêtre (étiquetée `main_window_v1`), la FC de nuit est
abandonnée.

Consentement : sans consentement aux données de santé, un push de santé Garmin est écarté dès la requête web (rien
n’est mis en file) et `health.store_daily` / `store_samples` refusent de toute façon ; la lecture COROS ne demande
même pas les données du jour. Les activités arrivent toujours.

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
- Tailles maximales (seulement connecteur actif) : `activityDetails` et la racine 32 Mo (réduits aux tours avant la file), autres types 8 Mo, désinscriptions et permissions 64 Ko, COROS 1 Mo.
- Les fichiers FIT ne sont téléchargés que depuis `COROS_PARTNER_FIT_HOSTS`, en HTTPS, 32 Mo au plus.
- Courtier Celery indisponible : 503, le fournisseur renvoie (Garmin n’envoie chaque résumé qu’une fois).

## Liste d’activation

1. Recevoir les identifiants ; lire la documentation du portail ; comparer avec « vérifié / supposé » ci-dessus et
   corriger au besoin par `*_URLS` ou dans le code (chemins COROS `daily` et `fit`, hôte des jetons Garmin).
2. Vérifier que les URLs déclarées sont exactement celles du tableau ; pour Garmin, activer les types listés en
   livraison Push.
3. Merger la branche (migration `k2f3a4b5c6d7` chaînée après les autres) et déployer : rien ne change encore.
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
8. Application iPhone/Android : livrer la petite modification native Garmin (ci-dessous) **avant** d’ouvrir Garmin
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
descendante supprime la table).

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

Rien d’autre à changer : la ligne « Activités importées de Strava, COROS ou Garmin » et la liste des données de
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
