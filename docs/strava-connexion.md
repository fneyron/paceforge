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
n’en est que le secours, par une tournée toutes les 10 minutes : chaque athlète relié
est relu au moins une fois par jour (un événement peut se perdre), et à chaque
tournée seulement quand ni lui ni son app n’ont reçu d’événement depuis 24 h — à
chaque tournée sur sa propre app Strava (son quota n’est qu’à lui), une fois par
heure au plus sur l’app commune (un quota pour tous) ; les athlètes restés le plus
longtemps sans nouvelles passent d’abord. Chaque relecture complète
aussi quelques activités importées sans leur détail (courses et sorties longues
d’abord). À 80 % d’une fenêtre de quota Strava (15 min ou jour), ou sur une réponse
429, les synchronisations de fond (tournée, détails, D+ des courses) attendent la
fenêtre suivante ; le webhook et le bouton « Synchroniser » (une fois par minute au
plus) gardent le reste. Un import initial en échec est relancé par la tournée après
1 h, 2 h, 4 h… (24 h au plus). Ces états vivent dans Redis (aucune colonne).

Configurer une seule souscription pour l’app commune vers
`https://paceforge.fr/webhook/strava`, avec `STRAVA_WEBHOOK_VERIFY_TOKEN` défini
comme secret aléatoire côté serveur. Un événement n’est traité que s’il vient
d’une souscription connue pour l’app de l’athlète (la sienne pour une app
personnelle, celle de l’app commune sinon), et chaque événement est vérifié auprès
de Strava avant toute modification (création, modification, suppression,
révocation). L’identifiant de la souscription de l’app commune est lu chez Strava
(`GET /push_subscriptions` avec les clés de l’app, gardé 12 h, relu par la tournée
horaire ; un échec n’est pas retenté avant 5 min) ; un événement arrivé avant
qu’il soit connu est mis en file et sa tâche le vérifie d’abord, sans jamais
utiliser les accès de l’athlète pour un identifiant inconnu (relu au plus toutes
les 10 min). `STRAVA_SUBSCRIPTION_ID` est facultatif : renseigné, il remplace
cette lecture. Une révocation sur Strava oublie le lien (jetons, identifiant
athlète) ; les activités déjà reçues restent, comme pour une déconnexion depuis
Réglages. La connexion et la déconnexion d’un athlète
ne doivent pas créer ou supprimer ce webhook partagé. Les apps personnelles
continuent de gérer leur propre souscription. Les autorisations OAuth demandées
sont en lecture seule (`read,activity:read_all`, depuis le 31 octobre 2026) : rien
n’écrit sur Strava. La fonction de mise à jour des descriptions reste dans le code,
inutilisée ; s’en servir demanderait de nouveau `activity:write`.

## Validation avant activation

Les tests locaux simulent l’autorisation, le retour au compte, le renouvellement
via la bonne app, les erreurs et l’import manuel. Avant diffusion, vérifier le
parcours avec deux comptes Strava autorisés, l’import initial et la reconnexion
sur l’iPhone. La livraison du code ne constitue ni une approbation de Strava,
ni une activation de la configuration en production.
