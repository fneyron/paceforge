# Coaching (bêta)

Un plan d’entraînement en temps, construit par des règles (aucune IA), que l’athlète suit séance après séance et
qu’un coach peut suivre avec elle. Demande du propriétaire, 21 novembre 2026 : « super interactif, avec un retour
sur le ressenti, quelque chose de simple, graphique, efficace, avec un calendrier synchronisé ».

Tout est déterministe : les mêmes réponses, les mêmes activités et le même jour donnent le même plan. Les règles
sont des constantes de `app/services/coaching_plan.py` (fonctions pures), épinglées une à une dans
`tests/test_coaching_plan.py`. **(H)** marque une heuristique de PaceForge : un choix raisonnable, pas un seuil
publié. Les sources citées sont en bas de page.

## Qui y a accès

**Tous les comptes**, sur le site et dans l’app, depuis le 21 novembre 2026 (propriétaire : « Non rajoute à tout le
monde »). La fonction garde sa pastille « bêta ». Elle avait d’abord été livrée le même jour en bêta fermée, pour
des comptes listés.

Un seul interrupteur reste, `COACHING_BETA_USERS` :

| Valeur | Effet |
|---|---|
| `*`, vide ou absent (**par défaut**) | Chaque compte connecté a Coaching. |
| Des e-mails séparés par des virgules (la casse ne compte pas) | Seuls ces comptes l’ont ; pour les autres, la fonction n’existe pas. Un coach doit lui aussi être dans la liste. |
| `off` (ou `0`, `false`, `non`, `no`, `none`), sans e-mail | **La fonction n’existe pas** : toutes les routes `/coaching…` répondent 404 (le flux calendrier aussi), aucun onglet, aucune carte, aucune ligne dans la politique de confidentialité, aucune tâche planifiée. |

- Une valeur vide vaut « tout le monde », partout : Coolify avait gardé la valeur vide de la bêta fermée et
  éteignait la fonction en silence (21 nov.). Pour éteindre, écrire `off`.
- Chaque requête d’un coach revérifie le lien athlète → coach et que l’athlète a toujours Coaching : sinon 404.
- La variable se lit au démarrage du worker pour la tâche quotidienne (comme tout réglage : un redéploiement), et à
  chaque requête pour le reste.
- **Un compte qui n’ouvre jamais Coaching ne coûte rien** : aucune ligne n’est créée avant « Créer mon plan », et la
  tâche quotidienne ne parcourt que les plans qui existent.

**Sur le site** : un quatrième onglet « Coaching », après Courses, Activités et Santé (en-tête et barre du bas).

**Dans l’app** (à partir de la version 1.1.0) : un quatrième onglet natif « Coaching », dans le même ordre que le
site, qui ouvre `/coaching`. `tabForUrl` lui associe `/coaching` et `/coaching/…` ; il garde son dernier écran (la
page d’un athlète suivi, par exemple) comme les autres onglets. Il se charge comme Courses, par un chargement complet
du document : la navigation « douce » de l’app n’existe qu’entre Santé et Activités, dont les pages se remplacent
sur place. Tirer vers le bas recharge la page, qui relit le plan (le jour, les nouvelles activités) ; ce geste ne
lance aucune synchronisation du téléphone ni des montres, et n’existe pas sur « Refaire le plan » (un formulaire).

**Ce que l’app annonce.** L’app injecte dans chaque page `window.paceforgeNativeCoaching = 1` et la classe
`pf-native-coaching` sur `<html>` (`mobile/src/navigation.ts`, `appShellScript`, à côté de `pf-native-refresh`). Les
pages en déduisent, en CSS seulement (`interface.css`) :

| Où la page s’affiche | Carte « Coaching » en haut d’Activités | Retour « ‹ Activités » | « S’abonner au calendrier » |
|---|---|---|---|
| Le site (un navigateur) | non : l’onglet du menu y mène | non | oui |
| L’app avec l’onglet (`pf-native` et `pf-native-coaching`) | non | non | oui : l’app passe le lien au système |
| Une app installée avant l’onglet (`pf-native` seul) | **oui** : ses trois onglets sont fixes, c’est son entrée | **oui** | non : elle ignore ce lien ; l’adresse se copie |

Une app plus ancienne continue donc de fonctionner comme à la première livraison : `/coaching` s’y ouvre dans la
WebView, l’onglet « Activités » reste sélectionné.

Éteindre la fonction, ou la restreindre à une liste, ne retire pas l’onglet natif d’une app déjà installée : il
ouvre alors « Écran indisponible » (404) pour un compte qui ne l’a pas.

## Les réponses de départ

Un écran, quatre questions et une valeur calculée :

1. **Objectif** : une course à venir de l’athlète (un parcours à pied avec une date), ou « Progresser, sans course »
   (un bloc glissant de 12 semaines (H) qui se prolonge de lui-même).
2. **Jours disponibles** : de 2 à 6.
3. **Jour de la sortie longue** : parmi eux ; dimanche par défaut, sinon le dernier jour disponible.
4. **Quand peux-tu courir ?** Matin, Midi, Soir ; les trois par défaut. Ne sert qu’à placer les séances dans la
   journée quand l’athlète relie son agenda (« Mon agenda »).
5. **Volume de départ** : la médiane des minutes de course des 4 dernières semaines complètes (famille « course » de
   `sante_training.load_family` : route, trail, tapis ; semaines du lundi au dimanche à l’horloge de l’athlète),
   arrondie à 5 min, modifiable. Sans historique : cinq choix, 1 h, 2 h, 3 h, 4 h, 5 h et plus (H).

Ouvert depuis la page d’une course (« Préparer cette course avec Coaching »), l’écran arrive avec cette course
choisie.

« Refaire le plan » repose ces questions : la semaine en cours redevient la semaine 0, ce que le ressenti avait
changé est oublié ; les séances faites, passées ou modifiées par une personne restent.

## L’objectif et le temps de course

Demande du propriétaire, 21 novembre 2026 : « Tu fais bien le lien entre les objectifs, les estimations de temps et
le coaching ? »

Le plan se règle sur le **temps de course attendu**, lu à **une seule source** : la page de la course
(`routers/simulator.race_times`, la lecture même de la page, `_page_times`). Dans l’ordre :

| Ce que la page de la course a | Ce que le plan prend | Ce que l’en-tête de Coaching dit |
|---|---|---|
| Un objectif posé par l’athlète | Cet objectif | « Objectif 6h30 » |
| Pas d’objectif | L’**estimation du simulateur** pour cet athlète : le temps d’arrivée à son niveau, avec la chaleur et les arrêts prévus, à la minute — le chiffre « Estimation » de la page | « Estimation 7h10 » |
| Aucune prédiction (une course sans tracé, ou une lecture qui échoue) | Le temps enregistré sur la course, sinon l’approximation de `race_prep.expected_s` (distance et D+) | « Objectif … », sinon « Environ 7h00 » |

- Le temps est dit **une fois**, dans l’en-tête, à côté du nom de la course, avec le lien vers sa page (pour un
  coach : les mêmes mots, sans lien). La carte du jour de la course ne le répète pas.
- Coaching n’a **aucun second estimateur**. Une prédiction lit tout le tracé : elle est gardée sur le plan pour la
  journée (`coaching_plans.goal_state`) et relue quand la page de la course a enregistré quelque chose (date,
  objectif, distance, D+, départ, dernière modification de la course) ou que le jour a changé (le niveau de
  l’athlète fait bouger l’estimation).
- **Le plan suit la course** : un objectif, une date ou une distance changés sur la page de la course déplacent les
  séances à venir que personne n’a modifiées, à la lecture suivante du plan (la page, celle du coach, la tâche
  quotidienne). Les séances modifiées, faites ou passées restent.
- Les règles lisent ce temps **au quart d’heure** (H, `RACE_TIME_STEP`) : une estimation qui bouge d’une minute d’un
  jour à l’autre ne réécrit pas les semaines. La séance « Course » garde le temps exact.

**Sur la page de la course**, une ligne discrète : « Plan de coaching · semaine 5 sur 12 » quand cette course est
l’objectif du plan (vers `/coaching`) ; sinon, pour une course à pied à venir, « Préparer cette course avec
Coaching » (vers l’écran de départ, cette course choisie). Rien pour une course passée, un autre sport, ou un
compte sans Coaching.

**Le dénivelé d’un trail** (H, `climb_target`). Pour une course de trail, la sortie longue et la séance de côtes
longues portent un dénivelé à viser : le D+ par heure de la course (D+ ÷ temps attendu, tenu à 500 m/h au plus,
`CLIMB_RATE_CAP`) × la durée de la séance × la part de la semaine. Cette part part de 60 % (`CLIMB_START`) la
première semaine et monte par pas égaux jusqu’à 100 % la dernière semaine avant l’affûtage ; elle reste entière
ensuite. Arrondi à 50 m ; rien sous 100 m (`CLIMB_MIN`). Affiché « Vise +600 m » sur la carte de la séance, « Vise
+600 m de dénivelé. » dans la description du flux calendrier, et, une fois la sortie enregistrée, contre son propre
dénivelé (« +470 m sur +600 m visés »). Une course sur route, ou un plan sans course : aucun.

**La course supprimée, ou passée.** Quand la course du plan est supprimée (ou perd sa date), ou que son jour est
derrière, le plan devient « Progresser, sans course » **à partir de la semaine suivante** : le lundi après la
course, ou après la semaine où la suppression est vue. Ce lundi devient la semaine 0 (le volume de départ, puis la
progression) ; les séances de la semaine en cours restent telles quelles, la séance « Course » d’une course
supprimée est retirée. Une ligne le dit jusqu’à la fin de cette première semaine : « Trail des Crêtes est derrière
toi : dès lundi, le plan continue sans course. » ou « … n’est plus dans tes courses : … ».

## Les règles du plan

Le plan est en **minutes** (arrondies à 5), jamais en kilomètres.

| Règle | Valeur | Source |
|---|---|---|
| Semaines | Du lundi au dimanche, à l’horloge de l’athlète ; de la semaine en cours à celle de la course, 24 semaines au plus (une course plus lointaine : des semaines de base d’abord). | (H) `HORIZON_WEEKS` |
| Phases | Base → Spécifique (les 6 dernières semaines avant l’affûtage, 8 quand le plan en compte 16 ou plus avant lui) → Affûtage → le jour de la course, une séance « Course » liée au parcours. Sans course : « Progression ». | (H) `SPECIFIC_WEEKS`, `SPECIFIC_LONG_FROM` |
| Affûtage | Les semaines qui contiennent J-14 → J-1. Volume : les bandes de la page de course (`race_prep.week_target`), J-14 → J-8 à 55–70 % de la base, J-7 → J-1 à 35–50 %, au milieu de la bande ; la base est la médiane des 4 semaines qui précèdent. L’intensité reste : un rappel court par semaine. | Bosquet 2007 ; Mujika 2003 (comme `race_prep`) |
| Progression | Volume de départ, puis +10 % par semaine de charge. | (H) `GROWTH` |
| Semaine légère | Une semaine sur quatre à 75 % de la dernière semaine de charge ; la suivante reprend la progression. | (H) `LIGHT_EVERY`, `LIGHT_FACTOR` |
| Plafond | 150 min + 70 % du temps de course attendu (l’objectif de la course, sinon l’estimation du simulateur : voir « L’objectif et le temps de course »), entre 3 h et 12 h. Sans course : 1,5 × la base, entre 3 h et 8 h. Jamais sous la base. | (H) `CEILING`, `CEILING_RANGE`, `FREE_CEILING` |
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

## La forme (VFC, sommeil, FC de repos)

Demande du propriétaire, 21 novembre 2026 : « Prendre en compte aussi la VFC, le repos, ajuster en fonction de la
forme ».

**Une option, explicite et séparée.** Dans Coaching, un interrupteur « Adapter mes séances à ma forme (VFC, sommeil,
FC de repos) », **éteint par défaut**. Il n’est proposé que si l’accord sur les données de santé est donné
(`health_consent.given`) et que Santé a un indice (un sur les 7 derniers jours : `sante.has_index`, une réponse oui
ou non, aucune valeur lue). L’allumer enregistre quand (`coaching_plans.form_on_at`). **Éteint, rien dans Coaching
ne lit de donnée de santé** : ni la page, ni un toucher, ni la page du coach, ni les tâches planifiées.

**Un seul indice : celui de Santé.** Coaching lit l’indice quotidien de Santé (« Signaux nocturnes », VFC et FC de
nuit comparées aux valeurs habituelles, sommeil court, respiration : [sante-estimates.md](sante-estimates.md), C8)
par le calcul même de la page (`sante.morning_index` : les lectures et `_assess` de `/sante`), et **ses propres
bandes** (`sante_score.HOLD`) : « bas » veut dire que l’état de Santé n’est pas « habituel » — l’indice sous 70,
« à surveiller » ou « inhabituel ». Jamais un second score, jamais un score de marque (Body Battery, Readiness…).
L’indice lu est celui **de la nuit qui s’est terminée ce matin** : avant que la montre l’ait envoyée, il n’y en a
pas (jamais celui de la veille).

**La règle du matin.** Les séances dures seulement quand la récupération est là : c’est le principe des études
d’entraînement guidé par la VFC (Kiviniemi 2007 ; Vesterinen 2016 ; Javaloyes 2019), où un matin de VFC basse
remplace la séance intense par du facile ou du repos. Tout le reste — durées, déplacement, règle des trois matins —
est (H).

| Ce matin | Ce que fait le plan | Source |
|---|---|---|
| Indice bas, séance dure du jour (séance de qualité, côtes, sortie longue) générée et jamais modifiée | Elle devient un **footing** facile : 60 % de sa durée, entre 30 et 45 min, et pas plus long que le footing dont elle prend la place. La séance dure **se décale** au prochain jour disponible de la même semaine (jusqu’au dimanche) qui est libre ou porte un footing que le plan peut réécrire, et qui garde « jamais deux jours durs de suite » (une sortie longue jamais à partir de J-3). Aucun jour : elle est **abandonnée**. | Principe : Kiviniemi 2007 ; Vesterinen 2016 ; Javaloyes 2019. Valeurs : (H) `FORM_FOOTING_SHARE`, `FORM_FOOTING_CAP`, `move_day` |
| Indice bas, séance dure **épinglée** (modifiée ou déplacée par une personne) ou **écrite par le coach** | Rien n’est changé. L’athlète lit « Récupération basse ce matin. » et un bouton « Alléger aujourd’hui » : son toucher en fait le même footing (rien n’est déplacé). | (H) `lighten_by_hand` |
| Indice bas, semaine d’affûtage | Seulement le passage en footing, à la même durée : ni déplacement, ni baisse de volume. | Comme le ressenti |
| Trois matins bas en cinq jours | La semaine suivante est allégée comme par le ressenti (−20 %, la séance de qualité devient un footing), par le même mécanisme (`adaptations`, raison `forme`). **Jamais deux baisses cumulées** : avec le ressenti, une seule s’applique, la plus forte (à égalité, celle du ressenti) ; une semaine répétée ou une reprise reste telle quelle. | (H) `FORM_LOW_COUNT`, `FORM_LOW_DAYS`, `with_form` |
| Pas d’indice ce matin (pas de nuit reçue, repères en construction) | Rien ne change. | — |
| Indice habituel | Rien ne change. **Un bon indice n’ajoute jamais de charge.** | — |
| Jour de course | Jamais touché. | — |

**Quand.** À l’ouverture de la page, après chaque toucher, à la tâche quotidienne, et toutes les heures à :12
(`paceforge.coaching_morning`) pour les plans dont l’option est allumée, pendant la matinée de l’athlète (de 5 h à
midi à son horloge, H) et tant que l’indice du matin n’a pas été lu : la nuit arrive quand la montre se
synchronise. Tant qu’elle manque, Santé n’est relue qu’une fois toutes les 10 minutes au plus. Une fois l’indice du
matin lu, la journée est fixée : relire ne change plus rien (idempotent, pas d’aller-retour). **Seul aujourd’hui est
lu** : un jour passé n’est jamais réécrit.

**Ce qui est gardé** (`coaching_plans.form_state`) : les dates des matins bas des 14 derniers jours, le jour lu en
dernier et s’il avait un indice. **Jamais l’indice, une mesure ni une raison.** Une séance allégée ou reportée
porte une marque (`coaching_sessions.form` : `allegee`, `reportee`), que la régénération laisse en place.

**Ce qui est effacé.** Éteindre l’option, retirer l’accord sur les données de santé (ou ne plus avoir l’accord en
cours : une nouvelle version du texte) et « Arrêter le coaching » effacent la date d’activation, les matins bas,
la semaine allégée par la forme et les marques des séances ; le plan redevient le sien à la lecture suivante.

**Ce que voit l’athlète.** Sur la carte du jour, une raison courte : « Séance allégée : récupération basse ce
matin », liée à Santé, et où est passée la séance (« La séance prévue passe à vendredi. »). **Le chiffre n’est pas
répété** : il est dans Santé. Sur le jour d’arrivée : « Séance reportée de mercredi ». Sur une semaine allégée :
« Semaine allégée : ta récupération est restée basse plusieurs matins. »

**Ce que voit le coach : jamais une donnée de santé.** Ni valeur, ni bande, ni raison. Il lit « Séance adaptée ce
matin » sur la séance allégée, « Séance reportée de mercredi » sur celle qui a bougé, « Semaine allégée. » sur la
semaine, et rien de l’option. Le flux calendrier dit seulement la séance telle qu’elle est devenue (« Footing
35 min »). L’export d’un coach ne contient rien de l’athlète.

## Mon agenda (les créneaux libres)

Demande du propriétaire, 21 novembre 2026 : « C’est flexible aussi en fonction du calendrier, en mettant peut-être
les créneaux hors réunion ou rendez-vous ».

**D’où viennent les heures occupées.** Dans Coaching › « Mon agenda », l’athlète colle l’adresse iCalendar privée de
son agenda : l’« adresse secrète au format iCal » de Google Agenda, le lien d’un calendrier public iCloud, un
calendrier publié par Outlook. Trois adresses au plus. Chacune est stockée chiffrée (`EncryptedString`), affichée
masquée (son hôte et ses 4 derniers caractères) et retirable. Un pli dit où trouver l’adresse dans Google, Apple et
Outlook. Sans aucune adresse, rien ne change : pas d’heure, les séances restent des journées entières.

**Lire une adresse saisie par quelqu’un** est une surface de requête forgée côté serveur (SSRF). Un seul module le
fait, `app/services/safe_fetch.py` (aucune aide de ce genre n’existait : les autres appels sortants vont à des hôtes
fixes), et jamais pendant une requête web :

- `https` seulement (`webcal://` est réécrit en `https://`), port 443, pas d’identifiants dans l’adresse, 1 024
  caractères au plus ; un nom d’hôte avec un point, jamais une adresse IP écrite, jamais un nom interne
  (`localhost`, `.local`, `.internal`…), jamais l’hôte de PaceForge ;
- le nom est résolu par ce module et **toutes** ses adresses doivent être publiques : sont refusées les plages
  privées, de bouclage, lien-local, partagées (100.64/10), multicast, réservées et non spécifiées, une IPv4
  enveloppée dans une IPv6 étant lue comme son IPv4, et 6to4, Teredo et NAT64 refusés ;
- la connexion va à **l’adresse vérifiée** : l’URL demandée porte l’IP, l’en-tête `Host` et le nom TLS (SNI) portent
  le nom, le certificat reste vérifié sur le nom ; une seconde réponse DNS ne peut pas la déplacer ;
  une adresse vérifiée qui ne prend pas la connexion (une IPv6 sur une machine sans IPv6) passe la main à la
  suivante, 3 au plus, 3 s chacune ;
- les redirections sont suivies à la main, 3 au plus, chacune revérifiée depuis le début (schéma, hôte,
  résolution) ; aucun proxy de l’environnement ;
- 10 s pour tout l’échange, 2 Mo au plus, lus en flux et coupés à la limite (après décompression) ;
- le contenu doit se lire comme un `VCALENDAR` ; un refus est un code (« adresse introuvable », « a refusé la
  lecture »…), **jamais** le corps, la ligne d’état ni les en-têtes de la réponse ;
- chaque lecture sortante est comptée par compte (`coaching_agenda_fetch`, 30 par heure).

**Quand.** Dans Celery seulement (`app/tasks/coaching.py`) : à l’enregistrement d’une adresse
(`paceforge.coaching_agenda`), toutes les 3 heures à :47 pour les comptes qui ont un plan et une adresse
(`paceforge.coaching_agenda_round`), et sur « Actualiser » (une fois par minute au plus, `coaching_agenda`). Après
chaque lecture, le plan est relu : chaque séance retrouve son créneau.

**Ce qui est lu d’un événement** (`app/services/coaching_ical.py`, pur) : `DTSTART`, `DTEND`, `DURATION`, `RRULE`,
`EXDATE`, `RECURRENCE-ID`, `TRANSP`, `STATUS`, `UID`. Rien d’autre : un titre, une description, un lieu ou un
participant n’est jamais lu.

| Dans l’agenda | Lecture |
|---|---|
| Un événement avec une heure | Occupé de son début à sa fin : heure UTC, `TZID` (nom IANA, ou nom Windows d’Outlook parmi les plus courants : `WINDOWS_ZONES`), ou heure « flottante » lue à l’horloge de l’athlète (un `TZID` inconnu aussi). |
| `TRANSP:TRANSPARENT` (affiché « disponible ») ou `STATUS:CANCELLED` | Ignoré. |
| Un événement d’une journée entière | Ignoré : c’est le plus souvent un rappel ou un anniversaire. |
| Un événement de journée entière de 2 jours ou plus | L’athlète est « absent » ces jours-là : aucune séance n’y est placée, rien n’est demandé. (H) `AWAY_DAYS` |
| Un événement répété (`RRULE`) | Développé avec `python-dateutil` (déjà installé : Celery en dépend, épinglé dans les verrous) pour `FREQ=DAILY`, `WEEKLY`, `MONTHLY`, `YEARLY`, à l’horloge de l’événement (un 9 h hebdomadaire reste à 9 h au changement d’heure), moins ses `EXDATE` et les occurrences qu’une exception (`RECURRENCE-ID`) remplace. Une règle à l’heure, à la minute ou à la seconde, une règle illisible et `RDATE` ne sont pas développées : l’événement compte une fois. |
| Le flux de PaceForge lui-même | Jamais du temps occupé : un calendrier dont le `PRODID` est le nôtre est refusé en entier, un événement dont l’`UID` est celui d’une de nos séances est ignoré (l’athlète qui s’est abonnée au flux dans l’agenda qu’elle a donné). |

**Ce qui est gardé** (`coaching_busy`) : pour les 21 prochains jours, des intervalles occupés — un début, une fin,
« absence » ou non — fusionnés quand ils se touchent, remplacés à chaque lecture réussie (une lecture ratée garde
ceux de la dernière réussie). **Jamais un titre, une description, un lieu ni un participant.**

**Les moments.** « Quand peux-tu courir ? » — Matin, Midi, Soir, par plan, dans l’écran de départ et dans « Mon
agenda » ; les trois par défaut. (H) `WINDOWS` : 6 h–9 h, 11 h 45–14 h, 17 h 30–21 h, à l’horloge de l’athlète.

**Le placement** (`app/services/coaching_slots.py`, pur ; tout est (H)).

| Règle | Valeur |
|---|---|
| Ce qu’il faut à une séance | Sa durée + 30 min (se changer, se doucher : `MARGIN`), sans rien d’occupé, dans un des moments choisis ; un départ au quart d’heure. Une séance plus longue que le moment lui-même part au début du moment et le dépasse. |
| L’ordre des moments | Sortie longue : le matin d’abord. Séance de qualité : le moment où l’athlète s’entraîne vraiment le plus souvent (les départs de ses sorties des 8 dernières semaines, 3 au moins : `USUAL_WEEKS`, `USUAL_MIN_RUNS`), sinon le soir. Séance facile : le premier moment qui a de la place. |
| Stabilité | Une séance garde l’heure qu’elle a tant que cette heure est libre et dans un moment choisi : pas d’aller-retour d’une lecture à l’autre. |
| « Autre créneau » | L’athlète fait défiler les heures libres de la journée, toutes les 30 min (`CYCLE`) ; l’heure choisie est alors la sienne, gardée quoi que dise l’agenda ensuite. |
| Un jour sans place (plein, ou jour d’absence) | La séance part sur le jour disponible le plus proche de la même semaine qui ne porte aucune séance, a un créneau et garde « jamais deux jours durs de suite » (à égalité, le plus tard). Une ligne le dit sur la semaine : « Mardi est plein : séance déplacée à mercredi. » Elle y reste tant que son créneau est libre ; à replacer, elle essaie d’abord son jour d’origine. |
| Aucun jour possible | Le jour reste, marqué « Journée chargée », sans heure. C’est le cas courant quand chaque jour disponible porte déjà une séance. |
| Ce qui peut changer de jour | Seulement une séance générée, à venir, que personne n’a modifiée. Une séance déplacée ou modifiée par une personne, ou écrite par le coach, garde son jour et reçoit seulement une heure. La séance d’aujourd’hui ne change jamais de jour ; elle reçoit une heure encore à venir. La course n’a pas d’heure. |
| Au-delà de 21 jours | Les heures occupées ne sont pas connues : pas d’heure. |

Le placement est refait à chaque lecture du plan (les heures occupées, les moments ou une durée ont pu changer). Tant
qu’une adresse n’a jamais été lue, rien n’est placé : une heure donnée sur la moitié des agendas serait un pari.
Retirer la dernière adresse efface les heures et remet à leur jour les séances que l’agenda avait déplacées.

**Ce que voit l’athlète.** Sur la carte du jour, le créneau : « 12 h 15 – 13 h 05 » (une heure s’écrit « 12 h 15 »,
une durée « 1h05 »), et « Autre créneau ». La frise de la semaine reste telle quelle.

**Ce que voit le coach.** L’heure des séances, et « Séance déplacée de mardi à mercredi. » ; jamais les heures
occupées, ni qu’un jour est plein, ni « Journée chargée », ni les adresses, ni « Autre créneau ».

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
`paceforge.coaching_daily`, 03:27 UTC, inscrite sauf quand la fonction est éteinte). Une seconde tâche,
`paceforge.coaching_morning` (toutes les heures à :12), ne relit que les plans dont l’option « forme » est allumée,
le matin de l’athlète (voir « La forme »). Une nouvelle activité est donc
prise en compte à la prochaine ouverture de la page (dans l’app : en revenant sur l’onglet ou en tirant vers le bas),
au plus tard le lendemain matin.

**Régénération.** Elle ne réécrit que les séances **à venir** (à partir d’aujourd’hui), générées, que personne n’a
modifiées, faites ni manquées. Une séance modifiée ou déplacée par l’athlète ou le coach est épinglée ; déplacée,
elle garde le jour que le plan lui avait donné (`auto_day`), qui n’est donc pas rempli à nouveau ; supprimée par un
coach, elle reste en base comme annulée pour la même raison.

## Les écrans

- `/coaching` : l’objectif et le compte à rebours, le temps visé (« Objectif 6h30 » ou « Estimation 7h10 », une
  fois, lié à la page de la course), la phase ; la semaine en sept jours (une pastille par séance,
  pleine avec une coche quand elle est faite, en pointillés quand elle est manquée), les flèches, la vue « Mois » ;
  une barre « fait / prévu » ; la carte du jour (le profil d’intensité dessiné, la consigne, « Fait », « Pas
  faite », « Déplacer », « Modifier ») ; une fois faite : l’activité, le ressenti, la note, le commentaire du coach ;
  un graphique des semaines (prévu en contour, fait en plein, phases, semaine en cours) ; un pli « Comment ça
  marche » ; « Adapter mes séances à ma forme » (si l’option est proposée), « Ajouter à mon calendrier »,
  « Mon agenda »,
  « Partager avec un coach », « Refaire le plan », « Arrêter le coaching ».
- « Déplacer » : on touche le bouton puis un jour (à partir d’aujourd’hui) ; un jour qui porte déjà une séance
  prévue l’échange. Tout se fait au clavier (ce sont des boutons). Pas de glisser-déposer.
- `/coaching/athletes` : la liste du coach (la barre de la semaine, les dernières séances avec statut et ressenti) et
  le champ du code. `/coaching/athletes/<id>` : le même calendrier, au prénom de l’athlète ; le coach ajoute,
  déplace, modifie, supprime des séances (épinglées, origine « coach ») et écrit un commentaire. Il voit le plan, les
  statuts, le ressenti, les notes et le résumé de l’activité liée ; **jamais les données de Santé**, ni le détail
  d’une activité. Une séance allégée par la forme lui dit « Séance adaptée ce matin », sans la cause.
- Le lien : l’athlète touche « Partager avec un coach » → un code de 8 caractères affiché une fois, valable 7 jours et
  une seule fois, stocké par son empreinte → le coach le saisit. L’athlète retire l’accès, le coach arrête de
  suivre : immédiat dans les deux sens. « Arrêter le coaching » supprime le plan, ses séances, les liens et les codes.

## Le calendrier synchronisé

`GET /coaching/cal/<jeton>.ics` : un flux iCalendar, sans session.

- Un événement par séance, des 30 derniers jours à la fin du plan : **avec une heure** (`DTSTART`/`DTEND` en UTC,
  affiché « occupé ») quand la séance a un créneau (« Mon agenda »), sinon d’une journée entière (`VALUE=DATE`,
  `TRANSP:TRANSPARENT`). L’`UID` ne change pas quand une séance reçoit ou perd son heure : le calendrier la met à
  jour sur place. `SUMMARY` « Footing 45 min » ; `DESCRIPTION` la structure, le dénivelé à viser s’il y en a un, et la consigne.
- `UID` stable (l’identifiant de la séance : une régénération réécrit la ligne en place), `SEQUENCE` son nombre de
  changements, `DTSTAMP` et `LAST-MODIFIED` son dernier changement : une modification arrive dans le calendrier
  abonné à son prochain rafraîchissement. Une séance supprimée ou annulée n’est plus dans le flux.
- Lignes CRLF, pliées à 75 octets, texte échappé (RFC 5545).
- La page propose le lien `webcal://` (« S’abonner au calendrier ») et l’adresse à copier ; « Régénérer le lien » en
  crée un nouveau, l’ancien ne répond plus.
- 404 quand la fonction est éteinte, quand le jeton est inconnu ou régénéré, ou quand une liste restreint la fonction
  et que son propriétaire n’y est pas.

**Le jeton dans l’adresse est l’exception documentée.** L’API (`/api/v1`) refuse toute clé dans une URL
(`token_in_url`) ; une application de calendrier, elle, ne peut pas envoyer d’en-tête. Ce jeton ne donne que ce
flux, en lecture : 32 octets aléatoires, stocké chiffré (`EncryptedString`, pour le réafficher à son propriétaire)
et par son empreinte SHA-256 (c’est par elle qu’une requête le retrouve, comme les clés d’API) ; il est masqué dans
les journaux d’accès (`log_redaction.FEED_TOKEN`) et limité à 120 requêtes par 10 minutes et par adresse.

**Dans l’app.** La WebView ne suit que `https://` : elle ignorait le lien `webcal://`. Depuis la version 1.1.0,
l’app le remet au système (`Linking.openURL`), et Calendrier sur iPhone propose l’abonnement. Une seule forme est
acceptée, vérifiée caractère par caractère (`calendarFeedUrl`, `mobile/src/navigation.ts`) :
`webcal://paceforge.fr/coaching/cal/<jeton>.ics` — cet hôte exact, sans port ni identifiants, ce chemin exact, sans
paramètre ni fragment. Tout autre lien `webcal://` et tout autre schéma restent ignorés. Si aucune application ne
prend le lien (Android sans application de calendrier qui s’y abonne), l’app le dit et l’adresse reste à copier. Une
app plus ancienne n’affiche pas le bouton : l’adresse se copie (Réglages iOS › Calendrier › Comptes › Ajouter un
calendrier avec abonnement).

**L’envoi des séances à la montre n’est pas construit.** Il demande un accès en écriture chez les marques (Garmin
Training API, écriture COROS côté partenaire) que PaceForge n’a pas. Aucun écran ne le propose.

## Les données

Migration `m4b5c6d7e8f9` (après `l3a4b5c6d7e8`) : `coaching_plans`, `coaching_sessions`, `coaching_links`,
`coaching_invites`. Migration `n5c6d7e8f9a0` : `coaching_plans.goal_state` (ce que le plan a lu de sa course).
Migration `o6d7e8f9a0b1` : `coaching_plans.form_on_at`, `form_state`, `coaching_sessions.form` (la forme) ; ces
champs sortent dans l’export de leur propriétaire et partent avec l’option, l’accord santé, le plan ou le compte.
Migration `p7e8f9a0b1c2` : `coaching_calendars` (les adresses, chiffrées), `coaching_busy` (les heures occupées),
`coaching_plans.moments`, `coaching_sessions.slot_min`, `slot_chosen`, `slot_flag`. L’export donne l’hôte de chaque
adresse (jamais l’adresse : c’est le secret d’un autre service) et les heures occupées ; « Arrêter le coaching » et
la suppression du compte retirent adresses et heures occupées. Tout part avec le compte (`ON DELETE CASCADE`) et sort dans l’export de Réglages
(`coaching.json`, `coaching_seances.json` : jamais le jeton du flux ni un code).

Politique de confidentialité, ligne affichée tant que la fonction n’est pas éteinte : « Coaching (bêta) » —
le plan, les séances et leur statut, le ressenti (l’effort perçu, de 1 à 5), les notes, les commentaires du coach ;
base « Contrat » ; partagé avec le coach que l’athlète choisit ; gardé jusqu’à l’arrêt du coaching ou la suppression
du compte. Une seconde ligne, « Coaching (bêta) · ta forme » : seulement si l’option est activée — la date
d’activation, les matins où l’indice de récupération de Santé est bas (leur date, jamais l’indice ni une mesure),
les séances allégées ces matins-là ; base « Consentement explicite, donné à part dans Coaching » ; jamais partagé
(le coach voit qu’une séance a été adaptée, jamais pourquoi) ; gardé jusqu’à la désactivation de l’option, le
retrait de l’accord sur les données de santé, l’arrêt du coaching ou la suppression du compte. Une troisième,
« Coaching (bêta) · ton agenda » : seulement si une adresse est ajoutée — cette adresse (chiffrée), les heures
occupées des 21 prochains jours (un début et une fin, jamais un titre, une description, un lieu ni un participant),
les moments où courir, l’heure donnée à chaque séance ; base « Contrat » ; jamais partagé (le coach voit l’heure des
séances, jamais l’agenda) ; gardé jusqu’au retrait de l’adresse, l’arrêt du coaching ou la suppression du compte.

## Mise en service

1. Rien à régler : sans `COACHING_BETA_USERS`, chaque compte a Coaching dès le déploiement (web et workers lisent la
   même variable).
2. L’athlète ouvre l’onglet Coaching, répond aux questions.
3. Elle touche « Partager avec un coach », donne le code ; le coach le saisit dans Coaching › Suivre un athlète.
4. L’onglet natif arrive avec la version 1.1.0 de l’app, publiée sur TestFlight par un commit sur `main` dont le
   message contient `[testflight]` (docs/testflight-without-expo-build-quota.md). En attendant, l’app installée
   garde la carte en haut d’Activités.
5. Pour restreindre : `COACHING_BETA_USERS=<e-mails>`. Pour tout éteindre : `COACHING_BETA_USERS=off`. Redéployer.
   Les données restent en base, invisibles ; elles partent avec « Arrêter le coaching » (une fois la fonction
   rallumée) ou avec le compte.

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
- Kiviniemi A. M., Hautala A. J., Kinnunen H., Tulppo M. P. (2007). Endurance training guided individually by daily
  heart rate variability measurements. *European Journal of Applied Physiology*, 101(6), 743–751.
  [10.1007/s00421-007-0552-2](https://doi.org/10.1007/s00421-007-0552-2).
- Vesterinen V., Nummela A., Heikura I. et al. (2016). Individual endurance training prescription with heart rate
  variability. *Medicine & Science in Sports & Exercise*, 48(7), 1347–1354.
  [10.1249/MSS.0000000000000910](https://doi.org/10.1249/MSS.0000000000000910).
- Javaloyes A., Sarabia J. M., Lamberts R. P., Moya-Ramon M. (2019). Training prescription guided by heart-rate
  variability in cycling. *International Journal of Sports Physiology and Performance*, 14(1), 23–32.
  [10.1123/ijspp.2018-0122](https://doi.org/10.1123/ijspp.2018-0122).

Ces trois études guident l’entraînement par la VFC du matin : les séances intenses les jours où elle est dans ses
valeurs habituelles, du facile ou du repos sinon. PaceForge en reprend le principe sur l’indice de Santé (VFC, FC de
nuit, sommeil), qui n’est pas le critère de ces études : le seuil reste celui de Santé, et la durée du footing, le
déplacement de la séance et la règle des trois matins sont des heuristiques (H). L’indice de Santé reste une lecture
descriptive, pas une autorisation de s’entraîner : la règle ne fait qu’enlever de l’intensité, jamais en ajouter.

Aucune étude n’est citée pour la progression de 10 % par semaine, la semaine légère sur quatre, les plafonds, la
part de la sortie longue, l’adaptation au ressenti, le dénivelé à viser, la lecture du temps de course au quart
d’heure, ni les moments, la marge et l’ordre du placement dans l’agenda : ce sont des usages d’entraînement ou des
choix de PaceForge, marqués (H).
