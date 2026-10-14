# Connexion Strava de PaceForge

Le bouton « Continuer avec Strava » ouvre directement l’autorisation Strava
quand l’application commune est configurée. Aucune clé personnelle n’est demandée.
Les utilisateurs ayant déjà une app personnelle valide conservent leur connexion.

## Configuration serveur

Configurer les mêmes valeurs pour le serveur web et les workers Celery :

- `STRAVA_CLIENT_ID` et `STRAVA_CLIENT_SECRET` : les accès de l’application
  Strava PaceForge, dans le gestionnaire de secrets du déploiement.
- `BASE_URL` : `https://paceforge.fr` ; le callback OAuth est
  `https://paceforge.fr/auth/strava/callback` et le domaine autorisé côté Strava
  doit correspondre.
- `STRAVA_SHARED_APP_ENABLED=true` uniquement après vérification de la capacité
  d’athlètes accordée par Strava. La valeur par défaut est `false` : la présence
  d’anciens identifiants globaux ne suffit pas à activer le service public.

Les Compose de production transmettent le drapeau aux deux services. Aucun
secret n’est ajouté au dépôt, au lien OAuth ou au cookie. Les tentatives OAuth
sont à usage unique, expirent après dix minutes et lient le compte à l’app
choisie. Un changement de configuration pendant l’autorisation impose de
recommencer. Les tokens historiques sans ID d’app ne sont jamais renouvelés
avec des identifiants arbitraires : leur utilisateur doit reconnecter Strava.

## Synchronisation et exploitation

L’import initial, la synchronisation manuelle et le polling fonctionnent avec
l’app commune. Les quotas de requêtes sont partagés entre ses athlètes ; surveiller
les réponses 429 et dimensionner la capacité Strava avant d’ouvrir davantage
d’inscriptions. Un import initial coûte une requête par activité des 24 derniers
mois (son détail) : avec le quota par défaut (1 000 lectures par jour), un nouvel
athlète peut en consommer l’essentiel.

Le webhook est la voie principale : chaque activité arrive à son envoi. Le polling
n’en est que le secours : une tournée par heure, pour les seuls athlètes que le
webhook n’a pas atteints depuis 24 h. À 80 % d’une fenêtre de quota Strava (15 min
ou jour), ou sur une réponse 429, les synchronisations de fond (tournée, détails de
l’import initial, D+ des courses) attendent la fenêtre suivante ; le webhook et le
bouton « Synchroniser » (une fois par minute au plus) gardent le reste. Un import
initial en échec est relancé par la tournée après 1 h, 2 h, 4 h… (24 h au plus).
Ces états vivent dans Redis (aucune colonne).

Configurer une seule souscription pour l’app commune vers
`https://paceforge.fr/webhook/strava`, avec `STRAVA_WEBHOOK_VERIFY_TOKEN` défini
comme secret aléatoire côté serveur, puis renseigner `STRAVA_SUBSCRIPTION_ID`
(l’`id` que Strava renvoie à sa création, ou `GET /push_subscriptions`) pour le
serveur web : un événement n’est traité que s’il vient d’une souscription connue
pour l’app de l’athlète (la sienne pour une app personnelle, celle-ci pour l’app
commune), et chaque événement est vérifié auprès de Strava avant toute
modification (création, modification, suppression, révocation). Sans cette
variable, les événements de l’app commune sont ignorés et seul le polling horaire
les rattrape. Une révocation sur Strava oublie le lien (jetons, identifiant
athlète) ; les activités déjà reçues restent, comme pour une déconnexion depuis
Réglages. La connexion et la déconnexion d’un athlète
ne doivent pas créer ou supprimer ce webhook partagé. Les apps personnelles
continuent de gérer leur propre souscription. Les autorisations OAuth demandées
restent celles du service existant, y compris `activity:write` pour la fonction
existante de mise à jour des descriptions ; cette livraison ne change pas ce
comportement.

## Validation avant activation

Les tests locaux simulent l’autorisation, le retour au compte, le renouvellement
via la bonne app, les erreurs et l’import manuel. Avant diffusion, vérifier le
parcours avec deux comptes Strava autorisés, l’import initial et la reconnexion
sur l’iPhone. La livraison du code ne constitue ni une approbation de Strava,
ni une activation de la configuration en production.
