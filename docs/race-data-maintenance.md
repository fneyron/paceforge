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
éditions et distances d’un même événement du même côté. Les événements apparus
après le 5 octobre 2026 (absents de `lockbox_baseline.json`) ne servent ni à
l’ajustement ni à la validation : ils forment une réserve (**lockbox**), notée
une seule fois par version du modèle avec `lockbox.py` (résultat consigné dans
`lockbox_ledger.json`), jamais par la tâche mensuelle. Les six événements de
validation fixes ont en effet servi à choisir certaines constantes (la valeur à
−20 %, l’ancrage de la fatigue, le coefficient des sentiers, le contrôle
Transjeju) : ils ne sont plus « jamais vus ».

La mesure principale est l’erreur **relative** aux points de passage, la
moyenne de |ln(prévu / réel)|, chaque arrivant planifié sur son propre temps
final, par bande de distance : en minutes, l’écart grandit avec la course
(8,4 min sur 40-60 km, 28,8 min sur 140-180 km) alors qu’il vaut 1,4 à 1,7 %
du temps final partout. Les contrôles exigent :

- au moins 8 événements d’ajustement, 4 de validation, 20 courses de validation
  et 1 000 arrivants, chaque course de validation comparée, les quatre bandes
  de distance présentes ;
- une amélioration de la mesure principale dont l’intervalle de confiance à
  95 % (4 000 rééchantillonnages des **événements** de validation) reste
  entièrement sous zéro ;
- aucune bande de distance dégradée de plus de 2 % (relatif).

Les résultats par course, les contrôles Transjeju 2025/2026, l’écart en
minutes, la part des courses améliorées et les courses LiveTrail sont publiés
comme **indicateurs**, sans veto : une course ou un coureur ne valident rien.
Le rapport donne aussi le temps final (`level_benchmark.py`) : chaque arrivant
UTMB Live prédit d’après son indice UTMB et le km-effort du parcours, un
événement laissé de côté à la fois — erreur moyenne en %, part des arrivants
entre Optimiste et Sécurité (cible 60 %) et score d’intervalle, par bande de
distance et par niveau (bande d’indice), avec l’exposant ajusté par niveau et
son intervalle de confiance : la loi ne doit pas desservir les coureurs forts.

La métrique compare les passages **à durée finale connue**, avec les arrêts
retirés lorsqu’ils sont mesurés. Elle évalue la répartition des temps sur le
parcours, pas à elle seule l’estimation du temps final d’un athlète.

Un candidat est `rejected` ou `eligible_for_review`. **Aucun paramètre de
production n’est remplacé automatiquement** : une étape du workflow échoue si
le code de l’application a changé pendant l’exécution, et un candidat
admissible doit encore passer `validate_app.py` avec son implémentation dans
le simulateur, puis la réserve (`lockbox.py`), avant d’être validé et déployé.

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
