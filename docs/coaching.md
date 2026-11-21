# Coaching (bêta)

Un plan d’entraînement en temps, construit par des règles (aucune IA), que l’athlète suit séance après séance et
qu’un coach peut suivre avec elle. Demande du propriétaire, 21 novembre 2026 : « super interactif, avec un retour
sur le ressenti, quelque chose de simple, graphique, efficace, avec un calendrier synchronisé ».

Tout est déterministe : les mêmes réponses, les mêmes activités et le même jour donnent le même plan. Les règles
sont des constantes de `app/services/coaching_plan.py` (fonctions pures), épinglées une à une dans
`tests/test_coaching_plan.py`. **(H)** marque une heuristique de PaceForge : un choix raisonnable, pas un seuil
publié. Les sources citées sont en bas de page.

## Qui y a accès

`COACHING_BETA_USERS` : les e-mails des comptes qui ont la bêta, séparés par des virgules (la casse ne compte pas).

- **Vide (par défaut) : la fonction n’existe pas.** Toutes les routes `/coaching…` répondent 404 (le flux calendrier
  aussi), aucun onglet, aucune carte, aucune ligne dans la politique de confidentialité, aucune tâche planifiée.
- Un compte de la liste voit un quatrième onglet « Coaching » sur le site (en-tête et barre du bas) et une carte
  d’entrée en haut d’Activités.
- Un coach doit lui aussi être dans la liste. Chaque requête d’un coach revérifie le lien athlète → coach et la
  présence de l’athlète dans la liste : sinon 404.
- La variable se lit au démarrage du worker pour la tâche quotidienne (comme tout réglage : un redéploiement), et à
  chaque requête pour le reste.

Dans l’app iOS (une WebView avec trois onglets natifs fixes), on entre par la carte d’Activités. `/coaching` n’est
associé à aucun onglet natif (`tabForUrl` renvoie `null`) : la page s’ouvre dans la WebView, l’onglet « Activités »
reste sélectionné, et le toucher à nouveau ne fait rien (c’est déjà l’onglet courant). La page porte donc son propre
retour « ‹ Activités », visible seulement dans l’app. Aucun changement natif dans cette livraison.

## Les réponses de départ

Un écran, trois questions et une valeur calculée :

1. **Objectif** : une course à venir de l’athlète (un parcours à pied avec une date), ou « Progresser, sans course »
   (un bloc glissant de 12 semaines (H) qui se prolonge de lui-même).
2. **Jours disponibles** : de 2 à 6.
3. **Jour de la sortie longue** : parmi eux ; dimanche par défaut, sinon le dernier jour disponible.
4. **Volume de départ** : la médiane des minutes de course des 4 dernières semaines complètes (famille « course » de
   `sante_training.load_family` : route, trail, tapis ; semaines du lundi au dimanche à l’horloge de l’athlète),
   arrondie à 5 min, modifiable. Sans historique : cinq choix, 1 h, 2 h, 3 h, 4 h, 5 h et plus (H).

« Refaire le plan » repose ces questions : la semaine en cours redevient la semaine 0, ce que le ressenti avait
changé est oublié ; les séances faites, passées ou modifiées par une personne restent.

## Les règles du plan

Le plan est en **minutes** (arrondies à 5), jamais en kilomètres.

| Règle | Valeur | Source |
|---|---|---|
| Semaines | Du lundi au dimanche, à l’horloge de l’athlète ; de la semaine en cours à celle de la course, 24 semaines au plus (une course plus lointaine : des semaines de base d’abord). | (H) `HORIZON_WEEKS` |
| Phases | Base → Spécifique (les 6 dernières semaines avant l’affûtage, 8 quand le plan en compte 16 ou plus avant lui) → Affûtage → le jour de la course, une séance « Course » liée au parcours. Sans course : « Progression ». | (H) `SPECIFIC_WEEKS`, `SPECIFIC_LONG_FROM` |
| Affûtage | Les semaines qui contiennent J-14 → J-1. Volume : les bandes de la page de course (`race_prep.week_target`), J-14 → J-8 à 55–70 % de la base, J-7 → J-1 à 35–50 %, au milieu de la bande ; la base est la médiane des 4 semaines qui précèdent. L’intensité reste : un rappel court par semaine. | Bosquet 2007 ; Mujika 2003 (comme `race_prep`) |
| Progression | Volume de départ, puis +10 % par semaine de charge. | (H) `GROWTH` |
| Semaine légère | Une semaine sur quatre à 75 % de la dernière semaine de charge ; la suivante reprend la progression. | (H) `LIGHT_EVERY`, `LIGHT_FACTOR` |
| Plafond | 150 min + 70 % du temps de course attendu (l’objectif du parcours, sinon l’estimation de `race_prep.expected_s` à partir de la distance et du D+), entre 3 h et 12 h. Sans course : 1,5 × la base, entre 3 h et 8 h. Jamais sous la base. | (H) `CEILING`, `CEILING_RANGE`, `FREE_CEILING` |
| Plancher | Une semaine d’entraînement ne descend pas sous 1 h (hors affûtage). | (H) `WEEK_FLOOR` |
| Facile / dur | Au plus 20 % du temps de la semaine en efforts durs (les répétitions des séances) ; le reste en aisance. | Seiler 2010 pour la répartition ; la compter en minutes est (H) `HARD_SHARE` |
| Sortie longue | Environ 35 % de la semaine (au moins autant que les autres séances), plafonnée à 55 % du temps de course attendu, ce plafond tenu entre 2 h et 3 h 30 ; sans course : 2 h 30. Aucune à partir de J-3. | (H) `LONG_SHARE`, `LONG_RACE_SHARE`, `LONG_CAP_RANGE`, `FREE_LONG_CAP`, `NO_LONG_BEFORE_RACE` |
| Séances de qualité | Une par semaine à partir de 3 jours disponibles ; deux à partir de 5 jours et d’une base de 4 h, hors semaine légère et hors affûtage. | (H) `QUALITY_FROM_DAYS`, `TWO_QUALITY` |
| Jours durs | Jamais deux de suite : une séance de qualité n’est jamais la veille ni le lendemain de la sortie longue, d’une autre séance ou de la course (dimanche et lundi sont voisins). | (H) `place` |
| Durée minimale | Aucune séance de moins de 30 min ; une semaine trop courte pour tous ses jours en garde moins. | (H) `MIN_SESSION` |
| Jours | Uniquement les jours disponibles ; le lendemain d’un jour dur, le footing devient une « Récup » (très facile). | (H) |

### Les séances de qualité

Échauffement 15 min, les répétitions (pas de récupération après la dernière), retour au calme 10 min au moins
(arrondi pour un total multiple de 5). Une répétition de plus toutes les 2 semaines de la phase, sans jamais
dépasser le budget d’efforts durs de la semaine (H) ; le minimum en semaine légère et en affûtage.

| Phase | Séance | Répétitions |
|---|---|---|
| Base | Côtes courtes : *n* × 1 min en côte, récup en descente | 6 à 10 |
| Base | Fartlek : *n* × 2 min vite, récup 2 min | 5 à 8 |
| Spécifique | Seuil : *n* × 8 min soutenu, récup 2 min | 2 à 4 |
| Spécifique (trail) | Côtes longues : *n* × 4 min en côte, récup en descente | 3 à 6 |
| Spécifique (route) | Intervalles : *n* × 3 min soutenu, récup 2 min | 4 à 8 |
| Affûtage | Rappels : *n* × 2 min soutenu, récup 2 min | 3 à 5 |

Les semaines alternent les deux séances de leur phase ; sans course : fartlek, côtes courtes, seuil à tour de rôle.
Tout ce tableau est (H) (`TEMPLATES`, `BY_PHASE`).

### L’intensité

Quatre niveaux, dits d’abord par la sensation (le test de la parole : Persinger 2004 ; Foster 2008), puis par une
plage de fréquence cardiaque quand une FC maximale est connue (confirmée dans Réglages, sinon le repère observé de
Santé, `heart_rate_reference`). Les plages reprennent les cinq zones de l’app (`heart_rate_zones`), leur
correspondance avec les niveaux est (H) :

| Niveau | Sensation | Plage de FC |
|---|---|---|
| 1 Très facile | Tu parles sans y penser | Zones 1–2 (50–70 % de la FC max) |
| 2 Facile | Tu peux tenir une conversation | Zones 2–3 (60–80 %) |
| 3 Soutenu | Quelques mots, pas des phrases | Zone 4 (80–90 %) |
| 4 Vite | Parler devient impossible | Aucune : sur un effort court la FC est en retard |

Le flux calendrier, public, ne lit que la FC maximale confirmée.

## Le ressenti et l’adaptation

Après une séance faite : « C’était comment ? », cinq niveaux (Très facile, Facile, Bien, Dur, Très dur),
enregistrés au toucher, modifiables. C’est l’effort ressenti de la séance, rien d’autre : aucun champ de douleur,
de blessure ni de maladie n’est collecté.

Sur une séance **facile** (footing, sortie longue, récup), « Dur » ou « Très dur » est un signal. Toutes ces règles
sont (H) (`adaptation`, `lighter_reason`, `week_missed`) :

| Ce qui s’est passé dans la semaine | La semaine suivante | Ligne affichée |
|---|---|---|
| Un « Très dur » sur une séance facile, ou deux signaux en 7 jours | −20 %, la séance de qualité devient un footing | « Semaine allégée : tes séances faciles t’ont paru dures. » |
| 2 séances manquées ou plus (pas toutes) | −20 %, la séance de qualité devient un footing | « Semaine allégée : plusieurs séances manquées la semaine dernière. » |
| Toute la semaine manquée (des séances prévues, aucune faite, aucune sortie enregistrée) | Le même volume : la progression est décalée d’une semaine | « Même volume que la semaine dernière, restée sans séance. » |
| Deux semaines manquées de suite | On repart de la base, recalculée sur les activités réelles (plancher 1 h) | « Reprise en douceur après deux semaines sans séance. » |
| Rien de tout cela | La progression prévue continue | — |

En affûtage, le volume n’est pas réduit davantage ; seul le rappel devient un footing. La semaine où le plan est créé
(ou refait) reste telle que choisie.

L’adaptation de la semaine en cours et de la suivante est relue à chaque synchronisation ; celle des semaines
passées est figée. Elle est gardée dans le plan (`coaching_plans.adaptations` : facteur et raison par semaine) :
le calendrier dit pourquoi une semaine est allégée, même après coup.

## Les séances et les activités

- Une sortie de la famille « course » le jour local de la séance la marque **faite** et lui est liée ; plusieurs le
  même jour : la durée la plus proche d’abord. Les minutes faites sont celles de l’activité.
- Une sortie sans séance ce jour-là apparaît « hors plan » dans le calendrier et compte dans le temps fait de la
  semaine.
- Sans montre : « Fait » (durée réelle modifiable) et « Pas faite ». Un « Pas faite » coché à la main n’est jamais
  défait par une sortie ; un « Fait » coché à la main prend la sortie quand elle arrive.
- Une séance prévue dont le jour est passé sans sortie devient **pas faite**.
- Le jour de la course et l’activité qui lui est liée ne comptent pas dans les temps de la semaine (H).

**Où cela se déclenche.** Rien n’est branché dans les synchronisations Strava, COROS, Garmin ou des connecteurs
officiels. `services/coaching.sync` relit le plan à l’affichage de la page (celle de l’athlète, celle du coach),
après chaque changement (ressenti, statut, modification), et une fois par jour pour chaque plan (tâche Celery
`paceforge.coaching_daily`, 03:27 UTC, inscrite seulement quand la liste bêta n’est pas vide). Une nouvelle activité
est donc prise en compte à la prochaine ouverture de la page, au plus tard le lendemain matin.

**Régénération.** Elle ne réécrit que les séances **à venir** (à partir d’aujourd’hui), générées, que personne n’a
modifiées, faites ni manquées. Une séance modifiée ou déplacée par l’athlète ou le coach est épinglée ; déplacée,
elle garde le jour que le plan lui avait donné (`auto_day`), qui n’est donc pas rempli à nouveau ; supprimée par un
coach, elle reste en base comme annulée pour la même raison.

## Les écrans

- `/coaching` : l’objectif et le compte à rebours, la phase ; la semaine en sept jours (une pastille par séance,
  pleine avec une coche quand elle est faite, en pointillés quand elle est manquée), les flèches, la vue « Mois » ;
  une barre « fait / prévu » ; la carte du jour (le profil d’intensité dessiné, la consigne, « Fait », « Pas
  faite », « Déplacer », « Modifier ») ; une fois faite : l’activité, le ressenti, la note, le commentaire du coach ;
  un graphique des semaines (prévu en contour, fait en plein, phases, semaine en cours) ; un pli « Comment ça
  marche » ; « Ajouter à mon calendrier », « Partager avec un coach », « Refaire le plan », « Arrêter le coaching ».
- « Déplacer » : on touche le bouton puis un jour (à partir d’aujourd’hui) ; un jour qui porte déjà une séance
  prévue l’échange. Tout se fait au clavier (ce sont des boutons). Pas de glisser-déposer.
- `/coaching/athletes` : la liste du coach (la barre de la semaine, les dernières séances avec statut et ressenti) et
  le champ du code. `/coaching/athletes/<id>` : le même calendrier, au prénom de l’athlète ; le coach ajoute,
  déplace, modifie, supprime des séances (épinglées, origine « coach ») et écrit un commentaire. Il voit le plan, les
  statuts, le ressenti, les notes et le résumé de l’activité liée ; **jamais les données de Santé**, ni le détail
  d’une activité.
- Le lien : l’athlète touche « Partager avec un coach » → un code de 8 caractères affiché une fois, valable 7 jours et
  une seule fois, stocké par son empreinte → le coach le saisit. L’athlète retire l’accès, le coach arrête de
  suivre : immédiat dans les deux sens. « Arrêter le coaching » supprime le plan, ses séances, les liens et les codes.

## Le calendrier synchronisé

`GET /coaching/cal/<jeton>.ics` : un flux iCalendar, sans session.

- Un événement d’une journée entière par séance (`VALUE=DATE`), des 30 derniers jours à la fin du plan ;
  `SUMMARY` « Footing 45 min » ; `DESCRIPTION` la structure et la consigne.
- `UID` stable (l’identifiant de la séance : une régénération réécrit la ligne en place), `SEQUENCE` son nombre de
  changements, `DTSTAMP` et `LAST-MODIFIED` son dernier changement : une modification arrive dans le calendrier
  abonné à son prochain rafraîchissement. Une séance supprimée ou annulée n’est plus dans le flux.
- Lignes CRLF, pliées à 75 octets, texte échappé (RFC 5545).
- La page propose le lien `webcal://` (« S’abonner au calendrier ») et l’adresse à copier ; « Régénérer le lien » en
  crée un nouveau, l’ancien ne répond plus.
- 404 quand la bêta est vide, quand le jeton est inconnu ou régénéré, ou quand son propriétaire a quitté la liste.

**Le jeton dans l’adresse est l’exception documentée.** L’API (`/api/v1`) refuse toute clé dans une URL
(`token_in_url`) ; une application de calendrier, elle, ne peut pas envoyer d’en-tête. Ce jeton ne donne que ce
flux, en lecture : 32 octets aléatoires, stocké chiffré (`EncryptedString`, pour le réafficher à son propriétaire)
et par son empreinte SHA-256 (c’est par elle qu’une requête le retrouve, comme les clés d’API) ; il est masqué dans
les journaux d’accès (`log_redaction.FEED_TOKEN`) et limité à 120 requêtes par 10 minutes et par adresse.

Dans l’app iOS, un lien `webcal://` n’est pas suivi par la WebView : le bouton d’abonnement n’y est pas affiché,
l’adresse se copie (Réglages iOS › Calendrier › Comptes › Ajouter un calendrier avec abonnement).

**L’envoi des séances à la montre n’est pas construit.** Il demande un accès en écriture chez les marques (Garmin
Training API, écriture COROS côté partenaire) que PaceForge n’a pas. Aucun écran ne le propose.

## Les données

Migration `m4b5c6d7e8f9` (après `l3a4b5c6d7e8`) : `coaching_plans`, `coaching_sessions`, `coaching_links`,
`coaching_invites`. Tout part avec le compte (`ON DELETE CASCADE`) et sort dans l’export de Réglages
(`coaching.json`, `coaching_seances.json` : jamais le jeton du flux ni un code).

Politique de confidentialité, ligne affichée seulement quand la liste bêta n’est pas vide : « Coaching (bêta) » —
le plan, les séances et leur statut, le ressenti (l’effort perçu, de 1 à 5), les notes, les commentaires du coach ;
base « Contrat » ; partagé avec le coach que l’athlète choisit ; gardé jusqu’à l’arrêt du coaching ou la suppression
du compte.

## Mise en service

1. Coolify : `COACHING_BETA_USERS=<e-mail de l’athlète>,<e-mail du coach>`, redéployer (web et workers lisent la même
   variable).
2. L’athlète ouvre Activités › Coaching, répond aux questions.
3. Elle touche « Partager avec un coach », donne le code ; le coach le saisit dans Coaching › Suivre un athlète.
4. Pour tout éteindre : vider la variable, redéployer. Les données restent en base, invisibles ; elles partent avec
   « Arrêter le coaching » (une fois la variable remise) ou avec le compte.

## Sources

- Bosquet L., Montpetit J., Arvisais D., Mujika I. (2007). Effects of tapering on performance: a meta-analysis.
  *Medicine & Science in Sports & Exercise*, 39(8), 1358–1365.
- Mujika I., Padilla S. (2003). Scientific bases for precompetition tapering strategies. *Medicine & Science in
  Sports & Exercise*, 35(7), 1182–1187.
- Seiler S. (2010). What is best practice for training intensity and duration distribution in endurance athletes?
  *International Journal of Sports Physiology and Performance*, 5(3), 276–291. Il décrit environ 80 % de séances à
  basse intensité chez les athlètes d’endurance ; PaceForge en tire une borne en minutes, qui est une heuristique.
- Persinger R., Foster C., Gibson M., Fater D. C. W., Porcari J. P. (2004). Consistency of the talk test for exercise
  prescription. *Medicine & Science in Sports & Exercise*, 36(9), 1632–1636.
- Foster C., Porcari J. P., Anderson J. et al. (2008). The talk test as a marker of exercise training intensity.
  *Journal of Cardiopulmonary Rehabilitation and Prevention*, 28(1), 24–30.

Aucune étude n’est citée pour la progression de 10 % par semaine, la semaine légère sur quatre, les plafonds, la
part de la sortie longue ni l’adaptation au ressenti : ce sont des usages d’entraînement, marqués (H).
