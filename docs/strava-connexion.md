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
d’inscriptions. Le code conserve le mécanisme existant de reprise des imports.

Un webhook de l’app commune est optionnel : le polling existant assure la reprise.
Configurer une seule souscription pour cette app vers
`https://paceforge.fr/webhook/strava`, avec `STRAVA_WEBHOOK_VERIFY_TOKEN` défini
comme secret aléatoire côté serveur. La connexion et la déconnexion d’un athlète
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
