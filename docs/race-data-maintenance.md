# Actualisation des résultats publics

Le workflow [race-data.yml](../.github/workflows/race-data.yml) s’exécute chaque
lundi à **03:23 UTC**. Il est indépendant du serveur web et des synchronisations
personnelles Garmin, COROS et Strava. Un lancement manuel est également possible
depuis GitHub Actions.

## Collecte et conservation

- Le catalogue part des événements connus dans `livetrail_events.json` et
  `utmb_tenants.json`. Les éditions de l’année courante et précédente sont
  redécouvertes auprès de l’API publique : une nouvelle année ne nécessite pas
  de modifier le code. L’ajout d’une nouvelle famille d’événements au catalogue
  reste une opération distincte ; ce n’est pas un moteur couvrant tous les sites
  de résultats.
- L’API UTMB Live sert aussi les courses LiveTrail modernes. Elles utilisent le
  format de données `utmb`. Les anciennes archives XML gardent le format
  `livetrail`. Seules les courses individuelles de course à pied de 40 à 180 km
  sont retenues, avec au moins dix arrivants et des passages cohérents.
- Les courses des **90 derniers jours** sont revérifiées chaque semaine, dans
  les limites du lot. Les anciennes archives sont revérifiées progressivement
  après 90 jours ; deux places par lot leur sont réservées pour éviter de les
  oublier. Le rapport indique explicitement la file d’attente restante.
- Un lot découvre au maximum **120 éditions** et traite **24 courses**, avec
  **200 arrivants** échantillonnés par rang. Les échantillons existants plus
  grands sont conservés. Les requêtes sont espacées de 0,5 seconde, limitées à
  12 000 par lot, avec tentatives bornées et respect de `Retry-After`.
- Une revérification relit le classement, les points, le parcours et les temps
  des arrivants ; elle ne se contente pas de constater qu’un fichier existe.
  Aucun nom de participant ni coordonnée de contact n’est conservé.
- L’archive n’est remplacée qu’après téléchargement complet et validation avec
  le lecteur utilisé pour la calibration. Un échec garde l’ancienne version,
  apparaît dans le rapport et fait échouer l’exécution GitHub. Les autres
  téléchargements complets sont tout de même conservés.

Les archives, le catalogue actualisé, l’état des vérifications et les rapports
sont versionnés sur la branche **`race-data`**, sans réécriture de son historique.
Ils survivent aux redéploiements et ne dépendent pas d’un cache de CI. Le
workflow ne pousse jamais de code applicatif sur `main`. Les rapports sont
également joints à chaque exécution pendant 90 jours.

## Réévaluation mensuelle

Le premier lundi du mois, après une collecte sans erreur, les entrées du modèle
sont reconstruites avec le code applicatif courant. Le modèle de fatigue en
fonction des heures est réajusté, avec la famille et la régularisation déjà
retenues (`hours`, `lam_c=0`, `lam_t=3`). La recherche d’autres familles reste
dans `fit_model.py` ; elle n’est pas sélectionnée sur les résultats de validation.

Les événements de validation restent séparés de ceux de l’ajustement, toutes
éditions et distances d’un même événement du même côté. Les contrôles exigent :

- au moins 8 événements d’ajustement, 4 de validation, 20 courses de validation
  et 1 000 arrivants ;
- une baisse d’au moins **2 % et 0,25 minute** de l’erreur moyenne aux passages ;
- au moins 60 % des courses de validation améliorées ;
- aucune régression par groupe de durée ou de distance dépassant le plus grand
  de 2 % et 0,25 minute, ni par course dépassant le plus grand de 5 % et 1 minute ;
- aucune dégradation des contrôles Transjeju 2025/2026, avec 0,1 minute de
  tolérance d’arrondi ;
- au moins 20 courses LiveTrail de contrôle, sans dégradation dépassant le plus
  grand de 1 % et 0,2 minute.

La métrique compare les passages **à durée finale connue**, avec les arrêts
retirés lorsqu’ils sont mesurés. Elle évalue la répartition des temps sur le
parcours, pas à elle seule l’estimation du temps final d’un athlète.

Un candidat est `rejected` ou `eligible_for_review`. **Aucun paramètre de
production n’est remplacé automatiquement** : un candidat admissible doit
encore passer `validate_app.py` avec son implémentation dans le simulateur, puis
être validé et déployé. Une amélioration moyenne ne suffit pas à publier un
modèle qui dégrade certaines courses.

## Vérification et exploitation

Dans GitHub Actions, ouvrir **Refresh race results and evaluate model** : le
résumé donne le résultat de collecte et, lorsqu’elle a eu lieu, de calibration.
La branche `race-data` contient :

- `data/races/maintenance.json` : dates et états des vérifications ;
- `reports/race-data/collection.json` : mises à jour, échecs, attente et empreinte
  du corpus ;
- `reports/race-data/model/{candidate.json,validation.json,summary.md}` : candidat,
  comparaison au modèle courant et critères de décision. L’historique Git garde
  les rapports précédents.

Pour relancer un lot ciblé, saisir des identifiants séparés par des espaces
dans `only`, par exemple `utmb/transjeju_2026/100m`. Les limites s’appliquent
aussi aux lancements manuels. Cocher `reevaluate` pour demander l’analyse du
modèle après une collecte réussie ; un modèle rejeté constitue un résultat
normal, tandis qu’un calcul ou téléchargement défaillant rend le job rouge.

Les dépendances scientifiques sont isolées dans l’extra Python `research` et
ne sont pas installées dans les conteneurs de production.
