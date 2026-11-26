# Coaching (bêta)

Un plan d’entraînement en temps, construit par des règles (aucune IA), que l’athlète suit séance après séance et
qu’un coach peut suivre avec elle. Demande du propriétaire, 21 novembre 2026 : « super interactif, avec un retour
sur le ressenti, quelque chose de simple, graphique, efficace, avec un calendrier synchronisé ».

Tout est déterministe : les mêmes réponses, les mêmes activités et le même jour donnent le même plan. Les règles
sont des constantes de `app/services/coaching_plan.py` (fonctions pures), épinglées une à une dans
`tests/test_coaching_plan.py`.

**Chaque règle porte son étude, ou dit qu’elle n’en a pas** (propriétaire, 22 novembre 2026 : « Toute les séances
étude choix que tu fais doivent être basé sur des études scientifiques »). Trois marques, dans cette page comme
dans le code :

| Marque | Ce qu’elle dit |
|---|---|
| Une étude seule (« Frandsen 2025 ») | La valeur est celle de l’étude. |
| **(H) chiffre** | Un chiffre de PaceForge. Soit une étude appuie le principe et aucune ne fixe le chiffre : l’étude est citée, et la ligne dit pourquoi ce chiffre. Soit aucune étude n’existe : la ligne dit « aucune étude ». |
| **choix pratique** | Pas une décision d’entraînement : jusqu’où écrire le plan, un arrondi, une lecture stable, une marge dans l’agenda. |

Les études d’entraînement fixent rarement un chiffre : beaucoup sont petites, courtes, faites sur des cyclistes ou
des athlètes de haut niveau. Cette page dit ce que chacune a trouvé et sur qui, pas davantage. Les références
complètes, avec leur DOI, sont en bas de page (« Sources ») ; chacune a été ouverte et lue le 22 novembre 2026.

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

**Sur le site** : un onglet « Coaching », le deuxième, après Santé et avant Courses et Activités (en-tête et barre
du bas ; ordre du propriétaire, 25 nov. 2026).

**Dans l’app** (à partir de la version 1.1.0) : un onglet natif « Coaching », dans le même ordre que le site (le
quatrième dans les versions installées avant le 25 novembre 2026), qui ouvre `/coaching`. `tabForUrl` lui associe `/coaching` et `/coaching/…` ; il garde son dernier écran (la
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

Demande du propriétaire, 22 novembre 2026 : « Regarde l’ux de fond en comble pour le coaching fait quelque chose de
visuel out of the box facile à utiliser et simple ».

**Un écran, un plan déjà proposé.** Toutes les réponses arrivent remplies et « Créer mon plan » est actif tout de
suite ; l’athlète ajuste si elle veut.

| Réponse | Ce qui est proposé | Comment elle se change |
|---|---|---|
| **Intention** | « Préparer une course » quand une course est à venir, sinon « Progresser » ; sur « Refaire le plan », l’intention du plan (voir « Les profils »). | Trois cartes, un toucher : « Garder la forme », « Progresser », « Préparer une course ». |
| **Objectif** (pour « Préparer une course ») | La prochaine course à venir de l’athlète (un parcours à pied avec une date) ; sans course : « Progresser, sans course » (un bloc glissant de 12 semaines (H) qui se prolonge de lui-même). | Une carte par course, « Ajouter une course » (une course sans fichier GPX, voir plus bas), et « Progresser, sans course ». |
| **Jours disponibles** (de 2 à 6) | Les jours où l’athlète court vraiment (voir plus bas) ; sans habitude : mardi, jeudi, dimanche (H, `DEFAULT_DAYS` : à 2 h par semaine, chaque jour porte une séance). | Sept pastilles, **sous les barres de la semaine** : un toucher ajoute ou enlève un jour. |
| **Jour de la sortie longue** | Le jour qui porte le plus souvent sa plus longue sortie de la semaine, s’il est parmi les jours proposés ; sinon dimanche, sinon le dernier jour proposé. | Une liste, qui ne propose que les jours choisis. |
| **Volume de départ** | La médiane des minutes de course des 4 dernières semaines complètes (famille « course » de `sante_training.load_family` : route, trail, tapis ; semaines du lundi au dimanche à l’horloge de l’athlète), arrondie à 5 min. Sans historique : 2 h (H). | Une liste (de 1 h à 15 h) ; sans historique, cinq choix : 1 h, 2 h, 3 h, 4 h, 5 h et plus (H). |

**Le niveau n’est pas une réponse** : il est lu dans l’historique, jamais affiché (depuis le 26 novembre 2026) ;
sa règle est dite dans « Comment ça marche » (voir « Les profils »).

**De 2 à 6 jours**, donc un jour de repos au moins. (H) chiffre. Le principe : chez 446 athlètes d’endurance de haut niveau, moins de 2 jours de repos par semaine allait avec 5,2 fois plus de blessures de surmenage (Ristolainen 2014 — une seule étude, rétrospective, par questionnaire). Son seuil est deux jours ; PaceForge en impose un et laisse le sixième jour au choix de l’athlète : c’est moins strict que l’étude, et dit ici. Le minimum de 2 jours est un choix pratique (une sortie longue et une autre séance) ; l’ACSM recommande 3 jours ou plus à intensité soutenue (Garber 2011).

« Quand peux-tu courir ? » (Matin, Midi, Soir) n’est plus demandé ici : la question ne sert qu’avec un agenda relié,
et elle vit dans « Mon agenda ».

**Les jours habituels** (`app/services/coaching_start.py`, pur ; tout est (H)). Sont lues les sorties de course des
8 semaines avant aujourd’hui (`HABIT_WEEKS`) ; deux sorties le même jour comptent pour un jour.

| Règle | Valeur |
|---|---|
| Une habitude | Au moins 4 jours de course sur la période (`HABIT_MIN_RUNS`), sur au moins deux jours de la semaine différents. Sinon : la semaine par défaut. |
| Combien de jours | Le nombre habituel de jours de course dans une semaine (la médiane des semaines où l’athlète a couru), tenu entre 3 et 5 (`HABIT_DAYS`) ; moins si elle n’a couru que sur moins de jours différents. |
| Lesquels | Les jours de la semaine le plus souvent courus ; à égalité, celui qui porte le plus de minutes, puis le premier de la semaine. |
| La sortie longue | Le jour qui a le plus souvent porté la plus longue sortie de sa semaine, s’il est parmi les jours retenus ; sinon dimanche, sinon le dernier jour retenu. |

Ce n’est pas une règle d’entraînement : seulement ce qui est coché à l’ouverture. Le plan reste fait par
`coaching_plan` à partir des jours que l’athlète valide.

**L’aperçu : « Ta première semaine ».** Sept colonnes, une barre par séance, haute comme sa durée (écrite au-dessus),
de la couleur de son type, et une légende des types présents. C’est la **première semaine complète** du plan : la
semaine en cours un lundi, la suivante les autres jours (la seule semaine qui reste avant une course cette
semaine-là). Sous le bouton, la première séance à venir : « Première séance aujourd’hui : Séance 45 min ».

- **Il dit vrai.** Les séances sont celles du générateur lui-même (`coaching_view.first_week` appelle
  `coaching_plan.build`, l’appel que fait `sync` pour un plan créé à l’instant), sur le même temps de course que le
  plan (`coaching.first_goal` : la lecture de la page de la course). Un jour choisi qui ne porte aucune séance cette
  semaine-là (un volume trop court pour tous ses jours) est dit : « Samedi reste libre cette semaine-là. »
- **Il réagit.** À chaque changement (un jour, la sortie longue, le volume, l’objectif), la semaine est redessinée
  sur place (htmx, `GET /coaching/apercu`).
- **Il n’écrit rien.** La route ne fait que lire : aucun plan, aucune séance, rien sur un plan existant. La
  prédiction d’une course, qui lit tout le tracé, est celle que le plan a déjà gardée pour la journée, sinon elle est
  calculée une fois par processus, course et jour.
- Toujours répondue 200 : des réponses qui ne font pas un plan le disent à la place du dessin. Hors de 2 à 6 jours,
  le bouton attend et la ligne sous les jours dit pourquoi (« Garde un jour de repos : 6 jours au plus. », « Choisis
  au moins 2 jours. »). Un formulaire refusé répond lui aussi 200 : l’app remplace tout document répondu 4xx par
  « Écran indisponible ».
- Sans JavaScript, le formulaire s’envoie tel quel ; l’aperçu reste celui de l’ouverture.
- **« Ma séance fixe »** : sous les jours, une ligne discrète, « J’ai déjà une séance chaque semaine », qui
  ouvre le jour (les puces des jours, un seul choix), la durée (proposée pour le jour choisi, avec sa raison : « Lu
  sur tes mercredis. ») et le nom (facultatif). Le jour choisi est coché parmi les jours ; la semaine dessinée la
  suit, et une ligne dit « Séance fixe est ta séance de qualité de la semaine : le plan écrit le reste autour. ».
  Décochée, aucune séance fixe. Un écran ouvert avant ce changement ne la demande pas : celle du plan reste.
- Sur « Refaire le plan », l’aperçu montre ce que le générateur ferait des nouvelles réponses : les séances faites,
  passées ou modifiées par une personne restent, et un agenda relié peut encore déplacer une séance.

Ouvert depuis la page d’une course (« Préparer cette course avec Coaching »), l’écran arrive avec cette course
choisie.

« Refaire le plan » rouvre cet écran avec les réponses du plan (jamais celles de l’habitude) et le volume recalculé :
la semaine en cours redevient la semaine 0, ce que le ressenti avait changé est oublié ; les séances faites, passées
ou modifiées par une personne restent.

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
- Les règles lisent ce temps **au quart d’heure** (choix pratique, `RACE_TIME_STEP`) : une estimation qui bouge
  d’une minute d’un jour à l’autre ne réécrit pas les semaines. La séance « Course » garde le temps exact.

**Sur la page de la course**, une ligne discrète : « Plan de coaching · semaine 5 sur 12 » quand cette course est
l’objectif du plan (vers `/coaching`) ; sinon, pour une course à pied à venir, « Préparer cette course avec
Coaching » (vers l’écran de départ, cette course choisie). Rien pour une course passée, un autre sport, ou un
compte sans Coaching.

**Le dénivelé d’un trail** (`climb_target`). Le principe : courir en côte est une capacité à part. Chez des
coureurs de montagne, le coût énergétique en côte ne se déduit pas de celui du plat (Balducci 2016, 10 coureurs) ;
sur un trail de 27 km, l’économie de course sur une pente de 10 % et l’endurance musculaire locale prédisent la
performance bien mieux que le modèle classique sur le plat (Ehrström 2018, 9 coureurs). Aucune étude ne fixe
combien de dénivelé viser à l’entraînement : la règle qui suit est (H) chiffre — celui de la course elle-même,
approché par paliers. Pour une course de trail, la sortie longue et la séance de côtes
longues portent un dénivelé à viser : le D+ par heure de la course (D+ ÷ temps attendu, tenu à 500 m/h au plus,
`CLIMB_RATE_CAP`) × la durée de la séance × la part de la semaine. Cette part part de 60 % (`CLIMB_START`) la
première semaine et monte par pas égaux jusqu’à 100 % la dernière semaine avant l’affûtage ; elle reste entière
ensuite. Arrondi à 50 m ; rien sous 100 m (`CLIMB_MIN`). Affiché « Vise +600 m » sur la carte de la séance, « Vise
+600 m de dénivelé. » dans la description du flux calendrier, et, une fois la sortie enregistrée, contre son propre
dénivelé (« +470 m sur +600 m visés »). Une course sur route, ou un plan sans course : aucun.

**Une course sans fichier** (22 novembre 2026). Le propriétaire, pour sa compagne : « prépare aussi le plan pour
stella qui veut s’entrainer deux jours par semaines … pour le 10km brook marathon annecy comme moi ». Elle n’a pas
le tracé : l’écran de départ propose « Ajouter une course » — **un nom, une date, une distance en km, et, si elle
veut, un temps visé** (heures et minutes) ; rien d’autre. Les champs s’ouvrent sous l’option une fois choisie
(toujours visibles sans JavaScript) ; le bouton attend un nom, une date et une distance ; un refus répond 200, les
champs remplis à nouveau. Une date passée, à plus de deux ans (une faute de frappe sur l’année), une distance hors
de 1 à 400 km ou un temps visé de moins de 10 min sont refusés, chacun avec sa phrase.

| Ce que la course a | Le temps sur lequel le plan se règle | L’en-tête dit |
|---|---|---|
| Un temps visé | Ce temps | « Objectif 52 min » |
| Pas de temps visé | L’approximation de `race_prep.expected_s` — la même que pour un parcours sans prédiction : 11 km/h sur route (10 km : 55 min) | « Environ 55 min » |

- **Aucun second estimateur** : c’est le repli qu’un parcours sans prédiction avait déjà (`coaching.custom_goal`
  appelle `race_prep.expected_s`).
- **Aucun dénivelé n’est connu** : une course sans fichier est une course sur route pour le plan, sans dénivelé à
  viser.
- **Partout comme un parcours** : l’en-tête (le nom, le compte à rebours, le temps, **sans lien** : il n’y a pas de
  page de course), la séance « Course » le jour J (sans « Voir le plan de course »), le flux calendrier
  (« Course 55 min », le nom dans la description), la page de la personne qui suit le plan et sa liste.
- **Passée**, elle laisse le plan « Progresser, sans course » dès le lundi suivant, comme un parcours (ci-dessous) ;
  ses champs sont vidés.
- **Le tracé importé plus tard** : « Refaire le plan » propose la course sans fichier (cochée) et, à côté, le
  parcours importé ; choisir le parcours vide les champs de la course sans fichier. La page du parcours propose
  aussi « Préparer cette course avec Coaching ».
- Stockée sur le plan (`coaching_plans.race_name`, `race_date`, `race_km`, `race_target_s`), elle sort dans l’export
  (`coaching.json`) et part avec « Arrêter le coaching » ou le compte.

**La course supprimée, ou passée.** Quand la course du plan est supprimée (ou perd sa date), ou que son jour est
derrière, le plan devient « Progresser, sans course » **à partir de la semaine suivante** : le lundi après la
course, ou après la semaine où la suppression est vue. Ce lundi devient la semaine 0 (le volume de départ, puis la
progression) ; les séances de la semaine en cours restent telles quelles, la séance « Course » d’une course
supprimée est retirée. Une ligne le dit jusqu’à la fin de cette première semaine : « Trail des Crêtes est derrière
toi : dès lundi, le plan continue sans course. » ou « … n’est plus dans tes courses : … ».

## Les profils

Demandes du propriétaire, 22 novembre 2026 : « Toute les séances étude choix que tu fais doivent être basé sur des
études scientifiques » ; « il faut également adaptés les études au profils des athlètes quelqu’un qui cherche à
courir pour garder la forme ou pour performer par exemple ». La revue qui fonde cette section a été faite le même
jour ; chaque référence est dans « Sources » avec son DOI.

**Ce que la littérature permet, dit d’abord.** Aucune étude lue n’a comparé des profils d’athlètes sur une décision
d’entraînement. Ce qu’elle donne : des recommandations de santé pour qui court pour la santé ; une épidémiologie des
blessures qui sépare les débutants des autres ; quelques essais chez des coureurs amateurs, souvent petits ; beaucoup
d’essais chez des coureurs entraînés — la population sur laquelle les règles d’avant avaient été construites. Les
règles d’avant sont donc, en fait, celles de « performer ». Chaque règle de profil ci-dessous **retire ou retient de
la charge** par rapport à elles ; aucune n’en ajoute sur une preuve mince.

### Trois intentions, un toucher

L’écran de départ propose trois cartes, une icône et un mot chacune ; la phrase de celle qui est choisie s’affiche
dessous. Rien d’autre n’est demandé.

| Intention | La phrase affichée | Les règles |
|---|---|---|
| **Garder la forme** | « On garde ton rythme : le même volume chaque semaine, et une séance un peu rythmée. » | Voir le tableau suivant. Aucune course. |
| **Progresser** | « Ton volume monte peu à peu, une semaine sur quatre plus légère, avec une séance de qualité par semaine. » | Les règles du plan, avec **une** séance de qualité par semaine au plus. Aucune course. |
| **Préparer une course** | « Le plan mène à ta course : il monte, puis s’allège les deux dernières semaines. » | La course choisie (un parcours, ou une course sans fichier). **Pour finir** quand elle n’a pas de temps visé, **pour performer** quand elle en a un : lu, jamais demandé (ci-dessous). |

Par défaut : « Préparer une course » avec la prochaine course quand il y en a une, sinon « Progresser ». « Préparer
une course » sans course ouvre directement « Ajouter une course ».

**« Garder la forme »** — `HOLD`, `rule_set`. Population : des adultes de la population générale ; les études de
maintien portent sur de jeunes adultes qui venaient de s’entraîner fort, pas sur un joggeur de longue date.

| Règle | Valeur | Source |
|---|---|---|
| Le volume | Celui de l’athlète, **chaque semaine**, sans hausse ; sous 75 min, +10 % par semaine jusqu’à 75 min, puis tenu. Jamais sous la base, pas de plafond. | (H) chiffre `FORME_FLOOR`, `FORME_GROWTH`. L’OMS recommande 150 à 300 min d’activité modérée ou 75 à 150 min d’activité intense par semaine, « un peu vaut mieux que rien » (Bull 2020, texte intégral) : 75 min est la lecture basse de cette bande, et le choix est le nôtre. Les coureurs avaient une mortalité plus basse quelle que soit la dose, déjà sous 51 min ou à 1–2 sorties par semaine (Lee 2014, 55 137 adultes) ; aucune relation dose-réponse parmi les coureurs (Pedisic 2020, méta-analyse, 232 149 adultes). Rien ne justifie donc de pousser vers plus de volume pour la santé. La forme se maintient avec moins de volume tant que l’intensité reste : VO2max tenu 15 semaines à 2 jours par semaine (Hickson 1981) ou avec des séances raccourcies de 40 à 26 ou 13 min (Hickson 1982), **pas** quand l’intensité baisse d’un tiers (Hickson 1985) ; revue narrative : jusqu’à 15 semaines avec 2 séances par semaine « tant que l’intensité est maintenue », données insuffisantes chez les athlètes (Spiering 2021). |
| Semaine allégée | Aucune. | (H) chiffre `FORME_LIGHT`, **aucune étude** : la charge ne monte pas, il n’y a rien à absorber. Une simplification, pas un résultat. |
| Séance de qualité | Une par semaine (à partir de 3 jours ; 2 jours : voir « Deux jours par semaine »), **à son plus petit nombre de répétitions**, sans « une de plus toutes les 2 semaines » : fartlek, côtes courtes, seuil à tour de rôle. | (H) chiffre `FORME_REPS`. Garder l’intensité est ce qui tient la forme (Hickson 1985 ; Spiering 2021) ; pour un joggeur, ce que ces études ne disent pas, c’est s’il lui faut des intervalles : la séance reste courte. Elle peut devenir un footing sans conséquence. |
| Sortie longue | La plus longue séance reste l’habitude : 1 h 30 au plus. | (H) chiffre `FORME_LONG_CAP`, **aucune étude** : l’OMS n’exige aucune séance longue (toute durée compte, Bull 2020). Elle reste comptée comme un jour dur pour le placement (la revue proposait de ne plus la compter ; la garder rend « jamais deux jours durs de suite » vrai pour tous les plans, et n’ajoute rien). |
| Phase | « Entretien », un bloc glissant de 12 semaines. | choix pratique |
| Ce qui ne change pas | La règle « une séance » (Frandsen 2025), les 20 % d’efforts durs au plus, le ressenti, la forme, jamais deux jours durs de suite. | — |

**« Pour finir » ou « pour performer »** — `aim`. Une course est préparée « pour performer » quand elle a un temps
visé (l’objectif de sa page, ou le temps donné avec une course sans fichier), « pour finir » sinon (l’estimation du
simulateur n’est pas un objectif). L’écran le dit sous la course : « Sans temps visé, une seule séance de qualité par
semaine : le reste en aisance. » ou « Avec un temps visé : jusqu’à deux séances de qualité par semaine, à partir de
5 jours et 4 h. »

| Règle | Pour finir, et « Progresser » | Pour performer | Source |
|---|---|---|---|
| Séances de qualité | **Une** par semaine au plus, même à 5 jours et 4 h | Jusqu’à deux (5 jours et 4 h), comme avant | **Aucune étude** ne compare « finir » et « performer » : c’est un choix de PaceForge, dans le sens qui retire de la charge. Le raisonnement : sur 151 813 marathons, le temps passé en intensité est à peu près le même à tous les niveaux, les plus rapides ajoutent du volume facile (Muniz-Pumares 2025, observationnel, marathon) ; sur 100 km, le volume prédit mieux l’allure que la vitesse à l’entraînement (Tanda & Knechtle 2015). Une seconde séance dure est l’ajout le moins appuyé pour qui n’a pas de temps à viser. |

**Ce qui n’est pas fait** (la revue le déconseille) : pas de plafond de volume « pour la santé » (la courbe en U
repose sur une poignée de coureurs, Schnohr 2015 ; aucun excès de risque à dix fois le minimum, Arem 2015) ; pas de
« 80/20 » comme cible (les répartitions testées chez des amateurs font aussi bien les unes que les autres) ; pas de
séance de renforcement, de consigne de descente ni de phrase sur le sommeil pour l’instant (hors du périmètre de ce
changement).

### Le niveau, lu dans l’historique

**Jamais demandé** (`read_level`, pur). PaceForge connaît le temps de course, la fréquence et la plus longue sortie,
pas la VO2max : le niveau est un niveau d’**habitude d’entraînement**. Il lit les 12 dernières semaines complètes
(du lundi au dimanche, la semaine en cours exclue) : les sorties enregistrées et les séances cochées « Fait » sans
sortie ; deux sorties le même jour comptent pour un jour.

| Niveau | Règle | Source |
|---|---|---|
| **Débutant** | Moins de 8 des 12 semaines avec une sortie, **ou** une semaine médiane sous 75 min | (H) chiffre `NOVICE`. Les seuils sont les nôtres ; ils visent les « débutants » et les « reprises » des études de blessures : les débutants se blessent environ deux fois plus par heure de course que les coureurs amateurs (Videbæk 2015, méta-analyse de 13 études : 17,8 contre 7,7 pour 1 000 h ; Kemler 2018, 4 621 coureurs, débutants = moins de 12 mois de course : 8,78 contre 4,24) ; « 0 à 2 ans d’expérience » et « reprise » vont avec plus de blessures chez les hommes (van der Worp 2015, revue de 15 cohortes). 75 min : le bas de la bande intense de l’OMS (Bull 2020). |
| **Entraîné** | Au moins 10 des 12 semaines avec 3 sorties ou plus, **et** une semaine médiane de 4 h ou plus, **et** une sortie d’1 h 30 ou plus | (H) chiffre `TRAINED`. Au-dessus d’une VO2max de 50, moins de 3 séances par semaine ne l’améliorent plus (Wenger & Bell 1986, revue ancienne) ; 4 h est le seuil déjà utilisé pour deux séances de qualité (`TWO_QUALITY`), environ 40 km par semaine à 6 min/km : le bas de la bande du milieu des marathoniens de Fokkema 2020, près des 45 km moyens de Muniz-Pumares 2025. |
| **Régulier** | Tout le reste | Le « coureur amateur » des essais. |
| Aucune sortie enregistrée | Le volume annoncé : moins de 2 h par semaine → débutant, 2 h ou plus → régulier ; **jamais** « entraîné » sans semaines enregistrées | choix pratique `NOVICE_NO_HISTORY` |

**Ce qu’il faut savoir de ces mots.** L’« entraîné » de PaceForge est le « amateur à modérément entraîné » des
études, pas leur « bien entraîné » (VO2max de 58–60 et plus), où la plupart des essais d’intervalles ont été faits :
un effet mesuré chez eux peut être plus petit, ou autre, ici. Un cadre en six niveaux existe (McKay 2022) ; ses
critères n’ont pas pu être lus (texte intégral inaccessible) : il n’est pas utilisé.

**Ce que le niveau change** (`novice`, `days_used`) — il ne fait **qu’enlever** :

| Niveau | Règle | Source |
|---|---|---|
| Débutant | **Les 8 premières semaines du plan en aisance seulement** (aucune séance de qualité), puis **une** séance de qualité par semaine au plus, à son plus petit nombre de répétitions ; **4 jours de course au plus** parmi les jours choisis (la sortie longue d’abord, puis chaque fois le jour le plus éloigné de ceux gardés) ; une course est préparée **« pour finir »**, même avec un temps visé. Toute intention. | (H) chiffre `NOVICE_EASY_WEEKS`, `NOVICE_MAX_DAYS`. Le principe : l’OMS demande d’augmenter « progressivement la fréquence, l’intensité et la durée » (Bull 2020) ; les adultes peu entraînés progressent déjà par l’endurance continue seule (+4,9 mL/kg/min ; Milanović 2015, 28 essais, 723 adultes de 25 ans en moyenne, peu de coureurs). **Aucune étude ne montre que retenir l’intensité protège un débutant** : progresser en intensité n’a pas blessé plus que progresser en volume (Ramskov 2018, 447 coureurs amateurs). Les 8 semaines copient la phase commune de cet essai — un choix de protocole, pas un résultat ; aucune étude ne fixe 4 jours. Le +10 % par semaine reste le même pour tous : un programme plus lent n’a pas blessé moins de débutants (Buist 2008). |
| Régulier | Rien de plus que l’intention. | — |
| Entraîné | « Progresser » suit les règles complètes (deux séances de qualité à partir de 5 jours et 4 h) ; le reste comme l’intention. | Aucune étude ne distingue régulier et entraîné sur une décision d’entraînement : à tous les niveaux, le temps dur est le même et les plus rapides ajoutent du facile (Muniz-Pumares 2025). Le seuil de 5 jours et 4 h fait déjà ce travail. |

**Ce que voit l’athlète.** Depuis le 26 novembre 2026 (« supprime le max de texte »), **plus rien à l’écran** : le
niveau est lu et appliqué, sa règle est dite dans « Comment ça marche » (« Ton niveau est lu sur tes 12 dernières
semaines, jamais demandé. Si tu débutes : … »). Avant, une ligne sous l’objectif et en haut de la page disait :
« **Tu débutes** : huit semaines en aisance d’abord, quatre jours de course
au plus. » et dessous ce qui a été lu (« Lu sur tes 12 dernières semaines : 5 avec une sortie, 40 min par
semaine. » ; « Aucune sortie enregistrée ; lu sur ton volume, 1h00 par semaine. ») ; après les 8 semaines : « une
séance de qualité par semaine au plus, courte, et quatre jours de course au plus. ». « **Tu cours régulièrement** : lu
sur tes 12 dernières semaines : 11 avec une sortie, 2h10 par semaine. » ; « **Tu t’entraînes beaucoup** : … 11 avec 3
sorties ou plus … ». **La personne qui suit le plan ne voit jamais cette ligne** : elle voit le plan lui-même.

**Quand il change.** Le niveau est lu, et gardé sur le plan (`coaching_plans.level`, `level_read` : ce qui a été lu
et le jour), **quand le plan est créé et à chaque « Refaire le plan »** ; jamais entre les deux. L’écran de départ
montre la ligne avant que l’athlète confirme : après douze semaines de course régulière, « Refaire le plan » dit
« Tu cours régulièrement » et retire les règles « débutant ». La revue proposait de le relire à chaque lecture du
plan pour qu’il ne puisse que **baisser** de lui-même ; ce n’est pas fait : un historique partiel juste après la
création (une montre reliée depuis trois semaines, des séances cochées à la main) se lirait « débutant » et changerait
d’un coup un plan qui se passe bien, alors que l’adaptation (semaine allégée au ressenti, reprise après deux semaines
manquées) retire déjà la charge quand l’habitude se perd. choix pratique.

### Deux jours par semaine

Le cas du propriétaire : sa compagne veut s’entraîner **deux jours par semaine** pour un 10 km sur route,
« quelquechose de bien et flexible ». Avant ce changement, deux jours donnaient un footing et une sortie longue, rien
d’autre (`QUALITY_FROM_DAYS` valait 3). **Aucun essai « deux sorties par semaine vers un 10 km » n’a été trouvé**, ni
d’essai publié des programmes à fréquence réduite : chaque règle ci-dessous est construite à partir d’études voisines,
et marquée.

| Qui | Séance A | Séance B | Source |
|---|---|---|---|
| Régulier, entraîné (une habitude) | **Une séance avec du rythme** : la séance de qualité de la phase, à son plus petit nombre de répétitions au départ (45 min environ : 15 min faciles, les efforts, 10 min faciles), une répétition de plus toutes les 2 semaines comme ailleurs (sauf « Garder la forme ») | **Une sortie plus longue et facile**, qui grandit d’un pas par semaine de charge | (H) chiffre `QUALITY_FROM_DAYS`, `QUALITY_MIN_WEEK`. Deux séances par semaine améliorent la VO2max des moins entraînés ; au-delà d’une VO2max de 50, il en faut trois (Wenger & Bell 1986, revue ancienne). À faible fréquence, c’est l’intensité qui porte l’effet : 4 × 4 min une, deux ou trois fois par semaine pendant 6 semaines améliorent VO2max et temps d’épuisement à chaque fréquence, le plus à deux et trois, sans bénéfice clair de la troisième (Lenk 2025, 26 adultes actifs, exploratoire, précision limitée selon les auteurs). 132 coureurs amateurs qui remplacent deux de leurs trois sorties par des intervalles courts gagnent 38 s sur 5 km en 8 semaines (Gliemann 2015) ; 18 coureurs modérément entraînés, volume −54 %, 48 s sur 5 km (Gunnarsson 2012) — aucun n’a mesuré un 10 km. La sortie longue reste : quand les séances sont raccourcies, la VO2max tient mais un effort de 2 h baisse de 10 % (Hickson 1982) ; chez des semi-marathoniens, une plus longue sortie au-delà de la distance va avec un meilleur temps (Fokkema 2020). Progresser en intensité n’a pas blessé plus que progresser en volume (Ramskov 2018). |
| Débutant | Footing | Footing un peu plus long ; après 8 semaines, A devient la séance avec du rythme | Comme « Le niveau » : les peu entraînés progressent par l’endurance continue (Milanović 2015) ; les 8 semaines sont les nôtres. |

**Les conditions de la séance avec du rythme**, en constantes : deux séances dans la semaine au moins (**la course
compte pour une** la semaine où elle a lieu) ; une semaine de **75 min ou plus** (les minutes de la course comptent
dans sa semaine) ; pas « débutant » dans ses 8 premières semaines ; et **jamais la veille ni le lendemain d’un autre
jour dur** (la règle « Jours durs » ci-dessus, dimanche et lundi voisins : au moins 48 h après un effort intense,
Stanley 2013). Les 20 % d’efforts durs tiennent d’eux-mêmes : le plus petit nombre de répétitions fait 6 à 16 min
d’effort (la séance « seuil », 16 min, demande une semaine de 80 min ; les autres tiennent dans 75). Un plan fait
avant les profils garde le seuil de 3 jours (`QUALITY_FROM_DAYS_BEFORE`).

**Deux jours voisins** (samedi et dimanche, ou dimanche et lundi). Le générateur n’y met pas de séance avec du
rythme : deux footings la semaine, et une ligne le dit, sur l’écran de départ et en haut de la page de l’athlète :
« Deux jours de suite : la séance rythmée devient un footing. »

**Quand elle déplace une séance à côté de l’autre** (« Déplacer » prend n’importe quel jour) : **rien n’est refusé
ni réécrit** — ce qu’une personne décide reste. Comme pour tout jour dur : pendant le choix, un point marque les jours
voisins d’une autre séance dure ; puis la semaine le dit, et quand l’une des deux est la séance avec du rythme, elle
dit : « Mardi et mercredi : deux jours durs de suite. » (que la séance avec du rythme peut se courir en footing est
dans « Comment ça marche » depuis le 26 novembre 2026). Le générateur, lui, n’en fait jamais.

**Le volume.** À deux séances, c’est la **règle « une séance »** qui commande (Frandsen 2025) : la sortie longue
grandit d’un pas de 5 min par semaine de charge (de 30 à 65 min : 7 semaines de charge, 9 à 10 semaines avec la
semaine légère) — l’arithmétique de la règle adoptée, pas le chiffre d’une étude. **Pour une course courte, la sortie longue s’arrête à 1,5 fois le temps de course attendu** (1 h 30 pour un
10 km en une heure ; jamais sous 1 h ; à partir d’une course d’1 h 20, le plafond habituel de 2 h s’applique) : elle
couvre la distance de la course et un peu plus en aisance, ce que Fokkema 2020 associe à un meilleur temps (semi,
transposé) ; le facteur est (H) chiffre `SHORT_RACE_LONG`, **aucune étude** ne le fixe, et il ne vaut que pour un plan
fait avec les profils. À deux jours, **la semaine plafonne à ce que ses deux séances peuvent contenir** (le plafond
de la sortie longue et une séance de 55 min, (H) chiffre `TWO_DAYS_QUALITY`) : sans cela la semaine légère et
l’affûtage ne seraient pas plus légers que les autres. Quand la sortie longue atteint son plafond, la semaine
**demande moins que son objectif** plutôt que de retirer la séance avec du rythme. Une semaine manquée ne coûte rien : la fenêtre de 30 jours garde la dernière sortie longue. Quand la
course est plus proche que ce que la sortie longue peut atteindre, l’écran de départ le dit : « Ta course arrive
vite : ta plus longue sortie atteindra 50 min, moins que le temps de ta course (55 min). »

**L’affûtage** est celui de tous (Bosquet 2007 ; Wang 2023 : des affûtages d’une semaine ou moins améliorent aussi le
temps, davantage après une surcharge) : la semaine J-14 → J-8 garde ses deux séances, plus courtes (les rappels et
une sortie facile, plus courte que la course quand elle tombe dans les 7 derniers jours : 45 min à J-6 pour le 10 km
d’Annecy) ; la semaine de la course garde **une** séance, les rappels, 3 ou 4 jours avant, et la course est
la seconde : c’est ainsi que la fréquence est gardée. Elle demande alors moins que sa bande (les rappels gardent leur
longueur). À moins de 2 h par semaine, **aucune étude ne mesure un affûtage** : on arrive frais, sans promettre de
gain. (H).

**Ce que le plan dit honnêtement**, dans « Comment ça marche » (l’écran de départ le disait aussi jusqu’au
26 novembre 2026) :

- « Deux séances par semaine suffisent pour préparer un 10 km et progresser quand on court depuis peu. » (Wenger &
  Bell 1986 ; Lenk 2025 ; Gliemann 2015 — (H))
- « Pour aller plus loin, c’est une troisième séance, facile, qui aide le plus. » (l’ACSM recommande l’effort
  intense 3 jours par semaine ou plus, Garber 2011 ; au-delà d’un certain niveau il faut trois séances, Wenger &
  Bell 1986 ; les plus rapides sont ceux qui courent plus, surtout en facile : Muniz-Pumares 2025 ; Doherty 2020 —
  observationnels)

Et ce qu’il ne dit ni ne fait : pas « aussi bien que trois ou quatre » (aucun essai ne le montre pour un 10 km) ;
jamais une seconde séance avec du rythme à deux jours (deux séances dures sans rien de facile n’est ce qu’aucun essai
cité n’a fait vers un 10 km, et la sortie longue porte l’effort d’une heure). Pour la santé, rien ne manque : deux
sorties par semaine en portent le bénéfice (Lee 2014 ; O’Donovan 2017). Le jour de la semaine ne compte pas, d’après
ce qui a été lu (pour la santé, une activité en 1 ou 2 séances va avec la même mortalité plus basse : O’Donovan 2017 ;
pour la performance, aucune étude).

### La séance fixe

Demande du propriétaire, 23 novembre 2026 : « Moi j’ai des séances vma club le mercredi, tu vas le voir dans mon
historique, comment articuler le plan autour du coup ? ». Jusque-là le plan décidait seul où allaient les séances de
qualité et ce qu’elles contenaient. **« Ma séance fixe »** : une séance hebdomadaire que l’athlète apporte (un club,
une piste, une séance qu’il fait seul), un jour, une durée, un nom facultatif (« Séance fixe » par défaut) ; le plan
s’écrit autour
(`Fixed`, `week_sessions`, `coaching_plans.fixed`). **Aucune étude** n’a été trouvée sur la façon d’écrire un plan
autour d’une séance imposée : chaque règle ci-dessous applique une règle du plan déjà sourcée, ou porte sa marque.

| Règle | Valeur | Source |
|---|---|---|
| Ce qu’elle est | Une séance de qualité dont le contenu est celui de l’athlète : le plan n’écrit **aucune répétition**, seulement son nom, sa durée et une ligne, « Son contenu est le tien. ». Un jour dur comme toute séance de qualité. Une par plan. Son jour s’ajoute aux jours disponibles s’il n’y était pas (six au plus avec lui : un jour de repos reste). | choix pratique `FIXED_KEY`, `FIXED_NAME`, `FIXED_TEXT` ; le jour de repos : la règle « 2 à 6 jours » (Ristolainen 2014, (H)) |
| C’est la séance de qualité de la semaine | Chaque semaine où les règles permettent au moins une séance de qualité, elle prend cette place : la première séance de qualité du plan n’est pas écrite. Où elles en permettent deux (`TWO_QUALITY` : course « pour performer » ou « Progresser » entraîné, 5 jours, base de 4 h), la seconde reste celle du plan, placée par `place` — jamais la veille ni le lendemain d’un jour dur, la séance fixe comprise (Stanley 2013) — et c’est la séance de la phase qui **n’est pas** des intervalles courts en vitesse : seuil ou côtes longues d’abord, puis côtes courtes, fartlek et intervalles en dernier. | Deux au plus : Seiler & Tønnessen 2009. L’ordre est (H) chiffre `BESIDE_FIXED`, **aucune étude** : la séance de club est déjà le travail court en vitesse (VMA). |
| Une semaine sans séance de qualité | Là où les règles n’en écriraient aucune (les 8 premières semaines d’un « débutant », une semaine allégée par le ressenti ou la forme, une semaine trop courte), et aussi en semaine légère et chez un « débutant » ensuite : **elle reste** — l’athlète l’a décidée — et le plan n’en ajoute aucune. Une ligne dit quoi en faire au lieu de la retirer : « Semaine légère : la moitié des répétitions suffit. » (« Pour l’instant, … » chez un débutant). Ses minutes dures comptées sont alors divisées par deux. « Garder la forme » la garde entière : c’est l’intensité qui tient la forme. | (H) chiffre `FIXED_HALF`, `FIXED_NOTE` : le principe, garder l’intensité quand le volume baisse, est celui de l’affûtage (Bosquet 2007) ; **aucune étude** pour la semaine légère. Garder la forme : Hickson 1985. |
| Le temps dur | Elle compte pour **un quart de sa durée** dans les 20 % d’efforts durs (`HARD_SHARE`) : 19 min pour 1 h 15 (16 × 300 m à VMA dans une séance de club de 75 min font 16 à 19 min d’effort). La seconde séance du plan prend ce qui reste du budget (moins de répétitions), jamais l’inverse. | (H) chiffre `FIXED_HARD_SHARE`, **aucune étude** ; les 20 % : Seiler 2010, Muñoz 2014. |
| La sortie longue et les voisins | Le plan n’écrit jamais une de ses séances dures à côté du jour fixe. Le jour fixe **ne peut pas** être celui de la sortie longue : refusé là où l’option est réglée (« Ta sortie longue est ce jour-là : choisis un autre jour pour l’une ou l’autre. ») — rien n’est déplacé en silence. Voisin de la sortie longue : accepté, une ligne le dit là où l’option est réglée (« Ta séance fixe est la veille de ta sortie longue : deux jours durs de suite. »), et l’avertissement des semaines s’affiche comme pour deux jours durs déplacés à la main. | choix pratique : la solution la plus simple qui ne réécrit rien de ce que l’athlète a choisi ; Stanley 2013 pour l’avertissement. |
| Affûtage, semaine de la course | Dans les semaines d’affûtage, elle reste, avec « Affûtage : la moitié des répétitions suffit. ». La semaine de la course, le rappel du plan reste et la séance fixe **n’est pas écrite** ; la semaine le dit : « Semaine de la course : ta séance fixe laisse la place au rappel du plan. » | (H) chiffre `FIXED_NOTE`, l’affûtage garde l’intensité et baisse le volume (Bosquet 2007) ; la semaine de la course : (H), **aucune étude**. |
| La règle « une séance » | Elle s’applique toujours : la durée de la séance fixe est celle de l’athlète, **jamais raccourcie**, mais elle compte comme une sortie pour les jours qui suivent, comme toute séance prévue. Les autres règles (plancher, plafond, sortie longue, ressenti, forme) restent ; à deux jours, la semaine plafonne au plafond de la sortie longue plus la durée de la séance fixe (au lieu des 55 min de `TWO_DAYS_QUALITY`). | Frandsen 2025 `SPIKE` ; le plafond à deux jours : (H) chiffre, comme `TWO_DAYS_QUALITY`. |
| La durée proposée | La médiane des sorties de l’athlète ce jour de la semaine sur les **8 dernières semaines complètes** (deux sorties le même jour : un jour), arrondie à 5 min, entre 30 min et 3 h, avec sa raison (« Lu sur tes mercredis. ») ; sans sortie ce jour-là : 1 h 15. Le jour, lui, est choisi par l’athlète, jamais deviné. | (H) chiffre `FIXED_WEEKS` (comme les 8 semaines des jours habituels), `FIXED_DEFAULT` (**aucune étude**) ; `FIXED_RANGE` choix pratique |
| Le reste | Une matinée de récupération basse ne la change pas (elle est à l’athlète : « Alléger aujourd’hui » reste à son toucher) ; un agenda relié lui donne une heure mais ne la change jamais de jour. La sortie enregistrée ce jour-là s’y lie comme à toute séance, et « Fait autrement » lit sa durée. La personne qui suit le plan la voit comme toute séance, avec son nom ; le flux calendrier et l’export la portent ; « Arrêter le coaching » la supprime. | choix pratique |

**Un plan sans séance fixe ne change pas** : sur 3 840 plans (chaque intention et niveau, 2 à 6 jours, quatre
volumes, sans course, course sur route, trail, avec et sans sorties réelles et une semaine allégée), le générateur
écrit octet pour octet ce qu’il écrivait avant (`tests/test_coaching_fixed.py`). **La régler, la changer ou la
retirer** passe par le seul chemin de régénération (`coaching.set_fixed` → `sync`) : les séances à venir que le plan
peut réécrire suivent, à partir d’aujourd’hui ; ce qu’une personne a épinglé, déplacé, modifié ou fait reste.

**Ses mots ne supposent pas de club** (26 novembre 2026 : l’app ne sait pas ce qu’est la séance). L’option dit
« Une séance qui revient chaque semaine » (au lieu de « Une séance de ton club »), l’écran de départ « J’ai déjà une
séance chaque semaine » (sans « (club) »), le nom par défaut est « Séance fixe » (au lieu de « Séance club » ; un
plan gardé avec l’ancien nom par défaut se lit avec le nouveau), sa ligne « Son contenu est le tien. » (au lieu de
« Le contenu est celui de ton club. »), le mot de son jour dans le tableau est le premier mot de son nom
(« Séance », au lieu de « Club »), et une sortie au lieu d’elle dit « au lieu de ta séance fixe de 1h15 ».

**Posée toute seule, d’après l’habitude** (26 novembre 2026). Le 25, après une sortie lue comme une séance dure, la
carte demandait « Tu cours tous les mercredis avec ton club ? ». Le propriétaire : « On ne sait pas si c'est une
sortie club du coup je ne comprends pas la question si tu cours tous les mercredi on ne peut pas faire mieux ou
autrement ? ». La question est retirée ; le plan décide seul (`coaching_runs.habit`). Quand une sortie vient d’être
lue comme une séance dure (voir « Une sortie qui n’est pas la séance prévue ») et que l’historique montre **une
séance dure ce jour de la semaine comme une habitude**, le plan garde une séance dure ce jour-là chaque semaine :

| Règle | Valeur | Source |
|---|---|---|
| L’habitude | Sur les **8 semaines complètes** qui précèdent la semaine de la sortie, une sortie ce jour de la semaine dans **au moins 5** (`RECUR_FROM`, `weekly_habit`) **et**, dans **au moins 3** de ces semaines, une sortie lue comme une séance dure par la même lecture (`reading` : tours, cardio, allure ; `HABIT_HARD`). Moins de trois : pas d’habitude, rien n’est posé — un footing tranquille chaque mercredi n’est pas une séance dure. | choix pratique, **aucune étude** : la plupart des semaines, sur la fenêtre de la durée proposée |
| Ce qui est posé | « Ma séance fixe » ce jour-là, exactement comme l’option la pose (`clean_fixed`, `fixed_state`, puis le plan relu et écrit autour comme par `set_fixed`) : la durée est la **médiane des sorties dures** de l’habitude, arrondie comme l’option (5 min, de 30 min à 3 h) ; le nom par défaut, « Séance fixe ». Les changements ponctuels que la lecture aurait faits cette semaine ne sont pas écrits : la règle de la séance fixe prend la semaine. | choix pratique |
| Jamais | Quand une séance fixe existe ; un jour de la semaine refusé (`coaching_plans.fixed.declined`) ; là où l’option la refuserait (le jour de la sortie longue, un septième jour). | choix pratique |
| Le coût | L’historique n’est lu que lorsqu’une sortie de ce jour vient d’être lue dure, sans séance fixe, et que 5 semaines sur 8 ont une sortie ce jour-là : une seule requête pour les tours de ces sorties (`facts`), aucun flux. | — |
| Une fois | Lu à chaque lecture du plan (la page, celle de la personne qui suit le plan, la tâche quotidienne), posé une fois et gardé sur la lecture de la sortie (`answer.recur`, l’état d’avant compris) : une lecture suivante ne le refait pas. Les sorties encore à lire le sont juste après, le plan écrit autour. | — |

Les chemins du retour, tous sans question :

- **« Pas tous les mercredis »** (le lien discret de la carte) : la séance fixe part, la semaine redevient ce que la
  lecture ponctuelle en faisait (le plan relu sans elle, puis les règles de la séance fixe pour cette semaine
  seulement), et ce jour de la semaine est refusé pour de bon (`declined`) ;
- **« C’était une sortie tranquille »** : la lecture est reprise avec la séance fixe qu’elle avait posée (le jour
  n’est pas refusé : relue dure, l’habitude tient de nouveau) ;
- **« Ma séance fixe »** (Options du plan) la montre comme toute séance fixe ; la modifier y reste possible. La
  **retirer** y fait ce que fait « Pas tous les mercredis » ; la **changer de jour** refuse le jour qu’elle quitte.
  Sans cela, la séance dure du mercredi suivant la reposerait.

Avant le 26 novembre, « Non, juste cette fois » gardait le jour dans `declined` : ces jours restent refusés. Une
réponse « ask » laissée par la question ne dit plus rien.

**Le cas du propriétaire** (mardi, mercredi, vendredi, samedi, dimanche ; mercredi fixe 1 h 15 ; sortie longue le
dimanche ; trail avec temps visé ; base 5 h) : chaque semaine la séance club le mercredi, un footing le mardi, la
seconde séance du plan le vendredi (côtes courtes en base, seuil et côtes longues à tour de rôle en spécifique), une
récup le samedi, la sortie longue le dimanche ; rien de dur le mardi ni le jeudi. Semaines légères et affûtage : la
séance club seule, « la moitié des répétitions suffit » ; semaine de la course : le rappel du plan, pas de séance
club.

### Les plans faits avant les profils

Un plan créé avant le 22 novembre 2026 n’a ni intention ni niveau (`coaching_plans.intent`, `level` vides). **Il
garde exactement ses règles** (`BEFORE` : deux séances de qualité à partir de 5 jours et 4 h, une séance de qualité à partir de 3 jours),
et rien de ses séances ne bouge à sa lecture suivante : aucune règle de profil ne s’y applique, aucune ligne de profil
ne s’affiche. « Refaire le plan » rouvre l’écran avec l’intention qu’il est déjà (« Préparer une course » s’il vise
une course, sinon « Progresser ») ; c’est en le refaisant que l’athlète passe aux règles de profil. Un écran ouvert
avant ce changement, qui n’envoie pas d’intention, fait de même un plan aux règles d’avant.

## Les règles du plan

Le plan est en **minutes** (arrondies à 5), jamais en kilomètres.

| Règle | Valeur | Source |
|---|---|---|
| Semaines | Du lundi au dimanche, à l’horloge de l’athlète ; de la semaine en cours à celle de la course, 24 semaines au plus (une course plus lointaine : des semaines de base d’abord). | choix pratique `HORIZON_WEEKS` |
| Phases | Base → Spécifique (les 6 dernières semaines avant l’affûtage, 8 quand le plan en compte 16 ou plus avant lui) → Affûtage → le jour de la course, une séance « Course » liée au parcours. Sans course : « Progression ». | (H) chiffre `SPECIFIC_WEEKS`, `SPECIFIC_LONG_FROM`. Aucune étude ne fixe la durée d’une phase : les modèles de périodisation tiennent plus de la tradition que de la preuve (Kiely 2012). 6 à 8 semaines : la durée sur laquelle les séances de la phase ont été étudiées (6 semaines : Barnes 2013 ; 7 : Seiler 2013 ; 8 : Helgerud 2007 ; 10 : Esfarjani 2007). |
| Affûtage | Les semaines qui contiennent J-14 → J-1. Volume : les bandes de la page de course (`race_prep.week_target`), J-14 → J-8 à 55–70 % de la base, J-7 → J-1 à 35–50 %, au milieu de la bande ; la base est la médiane des 4 semaines qui précèdent. L’intensité reste : un rappel court par semaine. | Bosquet 2007 (2 semaines, volume réduit de 41 à 60 %, intensité gardée) ; Mujika 2003 (comme `race_prep`) |
| Progression de la semaine | Volume de départ, puis +10 % par semaine de charge. | (H) chiffre `GROWTH`. Aucune étude ne rend 10 % protecteur : un programme gradué à 10 % n’a pas blessé moins qu’un programme plus rapide (20,8 % contre 20,3 %, Buist 2008). Les hausses trouvées avec plus de blessures sont plus fortes : plus de 30 % sur deux semaines (Nielsen 2014, non significatif), 20 à 60 % d’une semaine à l’autre (Damsted 2019, à 21 jours seulement). 10 % reste sous ces seuils. Une revue systématique conclut à des preuves « très limitées » d’un lien entre hausse de charge et blessure (Damsted 2018, 4 études). |
| **Progression d’une séance** | **Aucune séance ne dure plus de 10 % de plus que la plus longue sortie des 30 jours qui la précèdent.** Pour les jours passés : les sorties réelles de l’athlète (toute sortie de course enregistrée, dans le plan ou non, et les séances cochées « Fait » à la main). Pour les jours à venir : les séances que le plan écrit. Sans aucune sortie enregistrée sur 30 jours, il n’y a rien à comparer : les séances du plan tiennent lieu de sorties. Arrondi à 5 min vers le bas ; un pas de 5 min reste toujours permis (sous 50 min c’est plus de 10 % : la limite de l’arrondi, pas celle de l’étude) ; jamais sous 30 min. En affûtage, aucune séance ne grandit : pas plus longue que la plus longue des 30 jours précédents (le volume baisse : Bosquet 2007). Une semaine qui ne tient pas dans des séances aussi courtes **demande moins que son objectif** : les séances de qualité gardent leur place, les séances faciles rendent les minutes. | Frandsen 2025 `SPIKE`, `SPIKE_DAYS`, `session_limit` : 5 205 coureurs, 18 mois. Une séance dépassant de plus de 10 % la plus longue des 30 derniers jours allait avec plus de blessures de surmenage (+64 % de 10 à 30 %, +128 % au-delà du double) ; la hausse d’une semaine à l’autre, elle, n’y était pas liée. Limites dites par les auteurs : étude d’observation, blessures déclarées, mesure en **distance** — PaceForge compte en **temps**, c’est sa transposition. |
| Semaine légère | Une semaine sur quatre à 75 % de la dernière semaine de charge ; la suivante reprend la progression. | (H) chiffre `LIGHT_EVERY`, `LIGHT_FACTOR`. Le principe : une charge élevée et sans variation va avec plus de maladies bénignes (Foster 1998, 25 athlètes) ; « éviter une monotonie excessive » (Meeusen 2013). Aucune étude ne fixe le cycle ni la baisse. |
| Plafond | 150 min + 70 % du temps de course attendu (l’objectif de la course, sinon l’estimation du simulateur : voir « L’objectif et le temps de course »), entre 3 h et 12 h. Sans course : 1,5 × la base, entre 3 h et 8 h. Jamais sous la base. | (H) chiffre `CEILING`, `CEILING_RANGE`, `FREE_CEILING`. **Aucune étude.** Plus de volume va avec un meilleur temps sur marathon (Doherty 2020 ; Fokkema 2020) ; aucune ne donne un maximum. Ce plafond est un garde-fou : il arrête la hausse, il ne retire jamais rien à la base de l’athlète. |
| Plancher | Une semaine d’entraînement ne descend pas sous 1 h (hors affûtage). | choix pratique `WEEK_FLOOR` : deux séances de 30 min. |
| Facile / dur | Au plus 20 % du temps de la semaine en efforts durs (les répétitions des séances) ; le reste en aisance. | Seiler 2010 (environ 80 % de séances faciles, 20 % dures, chez des athlètes de haut niveau) ; compté **en temps** par Muñoz 2014 `HARD_SHARE` : 30 coureurs amateurs, 10 semaines, un groupe à 77 % du temps en aisance et 20 % au-dessus du second seuil a progressé de 5,0 % sur 10 km (3,6 % pour le groupe « entre les seuils » ; écart non significatif). Une répartition polarisée fait mieux qu’une répartition au seuil (Rosenblat 2019, 3 essais). 20 % est ici un plafond, pas une cible. |
| Sortie longue | Environ 35 % de la semaine (au moins autant que les autres séances), plafonnée à 55 % du temps de course attendu, ce plafond tenu entre 2 h et 3 h 30 ; sans course : 2 h 30. Course courte, plan fait avec les profils : 1,5 fois le temps de course, jamais sous 1 h (voir « Deux jours par semaine »). Aucune à partir de J-3. | (H) chiffre `LONG_SHARE`, `LONG_RACE_SHARE`, `LONG_CAP_RANGE`, `FREE_LONG_CAP`, `NO_LONG_BEFORE_RACE`. Le principe : plus la plus longue sortie de la préparation est longue, meilleur est le temps sur semi et marathon, sans plus de blessures (Fokkema 2020 ; Doherty 2020). **Aucune étude** ne fixe sa part de la semaine ni son plafond. J-3 : l’affûtage baisse le volume (Bosquet 2007) ; le jour est le nôtre. Sa progression suit « Progression d’une séance ». |
| Séances de qualité | Une par semaine à partir de deux séances dans la semaine (la course comptant pour une) et de 75 min, jamais à côté d’un autre jour dur (voir « Deux jours par semaine » ; un plan fait avant les profils : à partir de 3 jours disponibles) ; deux à partir de 5 jours et d’une base de 4 h, hors semaine légère et hors affûtage, seulement pour une course « pour performer » ou « Progresser » entraîné (voir « Les profils »). Jamais plus de deux. | Deux au plus : Seiler & Tønnessen 2009 (« environ deux séances par semaine suffisent pour progresser sans stress excessif »). Les seuils (3 jours ; 5 jours et 4 h) sont (H) chiffre `QUALITY_FROM_DAYS`, `TWO_QUALITY` : les essais cités font 2 séances d’intervalles parmi 4 à 5 séances par semaine (Esfarjani 2007 ; Seiler 2013). |
| Jours durs | Jamais deux de suite : une séance de qualité n’est jamais la veille ni le lendemain de la sortie longue, d’une autre séance ou de la course (dimanche et lundi sont voisins). | Stanley 2013 `place` : la récupération du système nerveux autonome demande au moins 48 h après un effort intense, 24 à 48 h après un effort au seuil, jusqu’à 24 h après un effort facile. Compter la sortie longue comme un jour dur est (H) chiffre : la même revue note, sur peu de données, que la durée pèse moins que l’intensité. |
| Durée minimale | Aucune séance de moins de 30 min ; une semaine trop courte pour tous ses jours en garde moins. | Garber 2011 `MIN_SESSION` : la recommandation de l’ACSM, 30 min par jour ou plus à intensité modérée. Une recommandation de santé, pas un essai d’entraînement. |
| Jours | Uniquement les jours disponibles ; le lendemain d’un jour dur, le footing devient une « Récup » (très facile). | Stanley 2013 (même constat : le lendemain d’un effort intense, la récupération n’est pas finie). |

### Les séances de qualité

Échauffement 15 min, les répétitions (pas de récupération après la dernière), retour au calme 10 min au moins
(arrondi pour un total multiple de 5). Une répétition de plus toutes les 2 semaines de la phase, sans jamais
dépasser le budget d’efforts durs de la semaine ; le minimum en semaine légère et en affûtage.

| Phase | Séance | Répétitions | Source |
|---|---|---|---|
| Base | Côtes courtes : *n* × 1 min en côte, récup en descente | 6 à 10 | Barnes 2013 : chez 20 coureurs entraînés, 6 semaines d’intervalles en côte, quelle que soit leur durée, améliorent le temps sur 5 km d’environ 2 % ; aucune durée ne se détache. Des efforts d’1 min chargent moins que ceux de 2 min et plus (82 % contre 92 % de VO2max, Seiler & Sjursen 2004) : la séance de la phase de base. Le nombre est (H) chiffre. |
| Base | Fartlek : *n* × 2 min vite, récup 2 min | 5 à 8 | Seiler & Sjursen 2004 : des efforts de 2 min avec une récupération égale atteignent environ 92 % de VO2max (12 coureurs entraînés, 12 × 2 min). 5 à 8 : moins que l’étude, (H) chiffre. |
| Spécifique | Seuil : *n* × 8 min soutenu, récup 2 min | 2 à 4 | Seiler 2013 : 4 × 8 min à 90 % de la FC max, deux fois par semaine pendant 7 semaines, font progresser plus que 4 × 4 min et 4 × 16 min (35 **cyclistes** amateurs entraînés). Commencer à 2 est (H) chiffre. |
| Spécifique (trail) | Côtes longues : *n* × 4 min en côte, récup en descente | 3 à 6 | Seiler & Hetlelid 2005 : 6 × 4 min sur une pente de 5 %, la séance même de l’étude (9 coureurs très entraînés) ; Barnes 2013. Commencer à 3 est (H) chiffre. |
| Spécifique (route) | Intervalles : *n* × 3 min **vite**, récup 2 min | 4 à 8 | Seiler & Sjursen 2004 : des efforts de 3 à 5 min sont ceux qui chargent le plus. Courus à 90–95 % de la FC max, 4 × 4 min font progresser VO2max plus que le même travail au seuil (Helgerud 2007, 40 hommes modérément entraînés) ; 8 efforts à la vitesse de VO2max, avec une récupération égale, améliorent le 3 000 m de 7,3 % en 10 semaines (Esfarjani 2007, 17 coureurs). **Changé le 22 novembre 2026** : ces efforts étaient « soutenus » (80–90 % de la FC max), une intensité qu’aucune de ces études n’utilise pour des efforts de 3 min ; ils sont « vite ». |
| Affûtage | Rappels : *n* × 2 min soutenu, récup 2 min | 3 à 5 | Le principe : l’affûtage garde l’intensité (Bosquet 2007 ; Mujika 2003). Le format est (H) chiffre. |

Les semaines alternent les deux séances de leur phase ; sans course : fartlek, côtes courtes, seuil à tour de rôle
(`TEMPLATES`, `BY_PHASE`). **Avec une séance fixe** (voir « La séance fixe »), elle est la séance de qualité de la
semaine, sans répétitions écrites ; la seconde, quand il y en a deux, est celle de la phase qui n’est pas des
intervalles courts en vitesse (`BESIDE_FIXED`, (H)).

| Autour des efforts | Valeur | Source |
|---|---|---|
| Récupération entre les efforts | 2 min (en descente pour les côtes) | Seiler & Hetlelid 2005 : entre des efforts de 4 min, environ 2 min suffisent ; de 1 à 4 min, la vitesse tenue change peu. |
| Échauffement | 15 min faciles | Le principe : un échauffement améliore la performance dans 79 % des critères étudiés (Fradkin 2010, 32 études). 15 min est (H) chiffre `WARM_UP`. |
| Retour au calme | 10 min au moins, très faciles | (H) chiffre `COOL_DOWN`. **Aucun bénéfice démontré** : un retour au calme actif n’améliore guère la récupération, ne prévient pas les blessures et ne freine pas l’adaptation (Van Hooren & Peake 2018). Il est gardé court, comme du volume facile. |
| Une répétition de plus toutes les 2 semaines | de la plus petite à la plus grande valeur du tableau | (H) chiffre `REP_EVERY`. **Aucune étude** ne fixe ce rythme. |

### L’intensité

Demande du propriétaire, 23 novembre 2026, devant le ticket : « Tu ne met pas les zones cibles ? HR vitesse ou
autre ? ». Chaque séance a **une cible** lue dans les données de l’athlète : une allure (depuis sa vitesse critique),
une plage de fréquence cardiaque (depuis sa FC maximale), ou les deux. **Une cible chiffrée remplace la phrase de
sensation** : dès que la cible porte un nombre (une allure ou une FC), la phrase du test de la parole
(Persinger 2004 ; Foster 2008 ; « Efforts vifs : parler devient impossible. ») n’est plus dite, ni sous le ticket ni
dans le flux calendrier. Elle reste seulement quand la séance n’a aucun nombre (ni allure ni FC connue, la séance
fixe du club, les côtes courtes) : personne ne reste sans consigne. **Sur le ticket, depuis le 26 novembre 2026**,
les valeurs sont nues (« 3:50–4:00 /km », « max 5:15 /km », « 171–181 bpm »), sans titre ni étiquette, leur sens
entier dit au lecteur d’écran ; les lignes « À la sensation… » ne sont plus que dans le flux calendrier ; sans
aucune donnée, le lien « Confirme ta FC max ». **Changé le 24 novembre
2026** (propriétaire : « Retire effort vif parler devient impossible on veut de vrai cible ») : la phrase était dite
d’abord, la cible la précisait. **Quand les données ne suffisent pas, aucun chiffre n’est affiché** : jamais
d’allure ni de FC par défaut.

**La FC maximale** : confirmée dans Réglages (`#max-heart-rate`), sinon le repère observé de Santé
(`heart_rate_reference`). Les plages des niveaux 1 à 3 reprennent les cinq zones de l’app (`heart_rate_zones`). Leur
correspondance : le moment où parler devient difficile est le seuil ventilatoire, mesuré à 86–89 % de la FC de pointe
sur tapis chez 16 volontaires (Persinger 2004) ; « Facile » (60–80 %) reste dessous, « Soutenu » (80–90 %) le
contient — c’est aussi la FC des 4 × 8 min de Seiler 2013 (90 %). Les bornes exactes sont celles des zones de l’app :
(H) chiffre.

**La vitesse critique** (`services/coaching_pace.py`) sépare le domaine « lourd », où un état stable s’installe, du
domaine « sévère », où il ne s’installe pas (Jones & Vanhatalo 2017). Elle se calcule comme Smyth & Muniz-Pumares
2020 la calculent sur des données d’entraînement brutes :

| Étape | PaceForge | Source |
|---|---|---|
| Fenêtre | Les 16 semaines qui précèdent aujourd’hui ; au moins 24 sorties de course (Run, TrailRun) dedans, sinon rien. | Smyth & Muniz-Pumares 2020 (16 semaines avant le marathon, 24 activités au moins). Le tapis (VirtualRun) est laissé de côté : sa distance est celle de la machine ou d’un capteur — choix pratique `RUN_SPORTS`. |
| Efforts | Le temps le plus rapide sur 400, 800, 1000, 1500, 3000 et 5000 m dans la fenêtre : les meilleurs efforts de Strava (400 m, 1/2 mile, 1 km, 1 mile, 2 miles, 5 km, chacun à sa vraie distance, comptés pour la cible la plus proche à 10 % près), sinon les efforts glissants les plus rapides calculés sur les flux distance et temps d’une sortie. | Les distances : Smyth & Muniz-Pumares 2020. (H) : l’étude lit l’allure ajustée à la pente tous les 100 m ; PaceForge n’ajuste pas la pente — un effort en descente est écarté à la place (ci-dessous) ; les distances de Strava et la tolérance de 10 % `BUCKET_TOLERANCE` sont les nôtres. |
| Modèle | La droite distance = D′ + VC × temps (moindres carrés) ; les trois distances du meilleur modèle de l’étude, 400, 800 et 5000 m, quand elles y sont ; sinon toutes les distances trouvées, trois au moins, de 1000 m ou moins à 3000 m ou plus. | Smyth & Muniz-Pumares 2020 (la pente est la VC ; 400–800–5000 m prédisait le mieux le marathon). Le repli est (H) `SHORT_MAX`, `LONG_MIN` : l’étude a essayé toutes les combinaisons de trois distances ou plus, de VC voisines (3,74 ± 0,08 m/s). |
| Descente | Un effort dont la descente nette atteint 1 % de sa distance est écarté, quand l’altitude est connue. | (H) `DOWNHILL`. Une montée ne fait que ralentir un effort : elle ne peut que baisser la VC. |
| Erreurs | Un effort plus rapide que le record du monde de sa distance ; dans un flux, un trou de plus de 10 s ou un pas plus rapide que 10,5 m/s dans l’effort ; une droite dont D′ n’est pas positif ou dépasse 400 m ; une VC hors de 1,5–6,5 m/s : rien. | Les records : choix pratique `RECORD_S`. (H) `MAX_GAP_S`, `MAX_STEP_SPEED`, `D_PRIME_MAX` (l’étude trouve D′ = 136 ± 39 m), `CS_RANGE`. |

Elle est lue **une fois par page** (et une fois par flux calendrier, sur les seuls meilleurs efforts de Strava : pas
de lecture lourde sur une adresse publique). Un flux est un gros JSON : seuls ceux qu’il faut sont lus — les
sorties sans meilleurs efforts, et les sorties qui portent un effort le plus rapide (pour écarter celui couru en
descente ; le suivant est alors vérifié à son tour), par paquets de 12 (`STREAM_BATCH`, choix pratique). Rien
n’est stocké en base : le résultat est gardé dans le processus tant que les sorties de la fenêtre ne changent pas
(`_seen`, 512 athlètes au plus, choix pratique), et recalculé sinon.

**Les cibles**, séance par séance. Une allure n’est donnée que **sur le plat** (dit à côté) : les séances de qualité
hors côtes, les sorties faciles, une sortie longue sans dénivelé à viser. Une séance structurée a la cible de ses
efforts (« pendant les efforts ») ; la récupération reste dans la phrase de la structure.

| Séance | Allure | FC | Source |
|---|---|---|---|
| Footing, récup, sortie longue sans dénivelé à viser (niveaux 1–2) | **Pas plus vite que** 82,3 % de la VC | Zones 2–3 (60–80 %) ; récup : zones 1–2 (50–70 %) | Allure : le premier seuil (la frontière modéré–lourd) se trouve en moyenne à 82,3 % de la VC (IC 95 % 81,1–83,6 ; Hunter 2024, méta-analyse, 26 études, 527 coureurs ; les auteurs recommandent un test pour plus de précision). Une borne haute seulement : rien ne fixe une borne basse. FC : les zones de l’app, (H). |
| Sortie longue avec un dénivelé à viser (trail) | Aucune (pas sur le plat) | Zones 2–3 | Les zones de l’app, (H). |
| Seuil : *n* × 8 min soutenu | 93–97 % de la VC | Zone 4 (80–90 %) | Nixon 2021 (10 coureurs bien entraînés) : l’état stable maximal de lactate à 15,2 km/h pour une VC de 16,4 (92,7 %), la V̇O2 encore stable 0,5 km/h sous la VC (97 %). Les bornes arrondies : (H) `THRESHOLD`. FC : Seiler 2013 (90 %), les zones de l’app (H). |
| Intervalles : *n* × 3 min vite | La vitesse de V̇O2max sur la droite de l’athlète : VC + D′ / t, t de 505 s à 303 s | 90–95 % de la FC max, atteinte en fin d’effort | Allure : la vitesse de V̇O2max se tient 404 ± 101 s (Billat 1994, 8 coureurs de bon niveau) ; des efforts à cette vitesse durant 60 % de ce temps ont amélioré V̇O2max et le 3000 m (Esfarjani 2007) — un effort de 3 min est 60 % de 5 min ; la durée tenue à une vitesse au-dessus de la VC est D′ / (v − VC) (Jones & Vanhatalo 2017). Prendre la moyenne ± 1 écart-type de Billat pour tous : (H) `VO2MAX_TIME_S`. FC : Helgerud 2007 (4 × 4 min à 90–95 %, 40 hommes modérément entraînés) `HR_FAST`. |
| Côtes longues : *n* × 4 min en côte | Aucune (côte) | 90–95 % de la FC max, en fin d’effort | Helgerud 2007 ; en côte, une transposition. |
| Fartlek (2 min vite), rappels (2 min soutenu) | L’allure du niveau de l’effort, sur le plat : celle des intervalles pour « vite », celle du seuil pour « soutenu » | Aucune : « Cardio : à la sensation, l’effort est trop court. » | La FC monte en retard sur un effort court ; le seuil de 3 min `LAG_MIN` est (H) : la durée de nos intervalles (ceux de Helgerud 2007 duraient 4 min). L’allure, elle, ne tarde pas : celle du niveau est reprise telle quelle, (H) — **aucune étude** ne la fixe pour un effort de 2 min. **Changé le 23 novembre 2026** : les rappels (niveau 3) affichaient la zone 4. |
| Côtes courtes (1 min) | Aucune (côte) | Aucune : « À la sensation : l’effort est trop court pour le cardio. » | Comme ci-dessus pour la FC ; en côte, pas d’allure. |
| Séance fixe | Aucune | Aucune | Son contenu est celui de l’athlète. |
| Course sur route avec un objectif | L’allure moyenne : le temps visé divisé par la distance | Aucune | Arithmétique. Aucune séance du plan n’est prévue à l’allure de course (les rappels sont « soutenus ») : aucun type de séance n’est ajouté, l’allure est sur le ticket de la course seulement. FC : l’intensité d’une course dépend de sa longueur, **aucune étude** ne donne une plage commune (**changé le 23 novembre 2026** : la zone 4 n’est plus dite le jour de la course). |
| Trail, course sans objectif (une estimation) | Aucune | Aucune | Une estimation n’est pas une cible. |

**Qui voit quoi.** L’athlète : l’allure et la FC. **La personne qui suit le plan ne lit jamais une FC** — une FC
maximale est une donnée physiologique, parfois le repère de Santé — : elle voit l’allure seulement (tirée des
sorties dont elle lit déjà le résumé), jamais la ligne qui mène à la FC maximale. **Changé le 23 novembre 2026** :
la plage de FC lui était montrée.

Le flux calendrier, public, ne lit que la FC maximale confirmée.

## Le ressenti et l’adaptation

Après une séance faite : « C’était comment ? », cinq niveaux (Très facile, Facile, Bien, Dur, Très dur) sur une
échelle de cinq marches, enregistrés au toucher, modifiables. C’est l’effort ressenti de la séance, rien d’autre : aucun champ de douleur,
de blessure ni de maladie n’est collecté.

Sur une séance **facile** (footing, sortie longue, récup), « Dur » ou « Très dur » est un signal.

Le principe : ce que l’athlète rapporte suit la charge d’entraînement mieux que les mesures objectives courantes
(Saw 2016, revue de 56 études) ; l’effort perçu d’une séance est une mesure valide de sa charge (Foster 2001) ; le
consensus sur le surentraînement demande d’« ajuster l’intensité ou le volume, ou d’accorder un jour de repos,
quand l’athlète se plaint d’une fatigue excessive » (Meeusen 2013). Après un arrêt, les adaptations se perdent vite
(Mujika 2000) et une séance trop longue au retour est ce qui va avec les blessures (Frandsen 2025) : d’où la
reprise depuis les activités réelles. **Aucune étude ne fixe les chiffres de ce tableau** (−20 %, deux signaux en
7 jours, deux séances manquées, une puis deux semaines) : ils sont (H) chiffre, choisis pour n’enlever que de la
charge (`adaptation`, `lighter_reason`, `week_missed`).

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

**Une option, explicite et séparée.** Dans les options de Coaching, un interrupteur « Adapter à ma forme », seul
(depuis le 26 novembre 2026, ce qu’il lit — VFC, sommeil, FC de repos — et que la personne qui suit le plan ne voit
jamais pourquoi sont dits dans « Comment ça marche »), **éteint par défaut**. Il n’est proposé que si l’accord sur les données de santé est donné
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
remplace la séance intense par du facile ou du repos. L’effet mesuré est modeste : deux méta-analyses trouvent un
gain de performance petit et non significatif face à un plan fixe, avec moins de séances intenses et moins
d’athlètes qui ne progressent pas (Düking 2021 ; Manresa-Rocamora 2021). Les durées, le déplacement et la règle
des trois matins sont (H) chiffre.

| Ce matin | Ce que fait le plan | Source |
|---|---|---|
| Indice bas, séance dure du jour (séance de qualité, côtes, sortie longue) générée et jamais modifiée | Elle devient un **footing** facile : 60 % de sa durée, entre 30 et 45 min, et pas plus long que le footing dont elle prend la place. La séance dure **se décale** au prochain jour disponible de la même semaine (jusqu’au dimanche) qui est libre ou porte un footing que le plan peut réécrire, et qui garde « jamais deux jours durs de suite » (une sortie longue jamais à partir de J-3). Aucun jour : elle est **abandonnée**. | Principe : Kiviniemi 2007 ; Vesterinen 2016 ; Javaloyes 2019. Le footing de 30 à 45 min : (H) chiffre `FORM_FOOTING_SHARE`, `FORM_FOOTING_CAP`, autour des 40 min de la séance facile de Kiviniemi 2007. Le déplacement (`move_day`) : chez Vesterinen 2016 la séance dure se fait le jour où la VFC est revenue ; le garder dans la semaine est (H) chiffre. |
| Indice bas, séance dure **épinglée** (modifiée ou déplacée par une personne) ou **écrite par le coach** | Rien n’est changé. L’athlète lit « Récupération basse ce matin. » et un bouton « Alléger aujourd’hui » : son toucher en fait le même footing (rien n’est déplacé). | choix pratique `lighten_by_hand` : ce qu’une personne a décidé n’est jamais réécrit. |
| Indice bas, semaine d’affûtage | Seulement le passage en footing, à la même durée : ni déplacement, ni baisse de volume. | Comme le ressenti |
| Trois matins bas en cinq jours | La semaine suivante est allégée comme par le ressenti (−20 %, la séance de qualité devient un footing), par le même mécanisme (`adaptations`, raison `forme`). **Jamais deux baisses cumulées** : avec le ressenti, une seule s’applique, la plus forte (à égalité, celle du ressenti) ; une semaine répétée ou une reprise reste telle quelle. | (H) chiffre `FORM_LOW_COUNT`, `FORM_LOW_DAYS`, `with_form`. Kiviniemi 2007 allège sur une baisse d’un jour ou une tendance de deux jours, pour la séance du jour ; **aucune étude** n’allège une semaine entière sur trois matins. |
| Pas d’indice ce matin (pas de nuit reçue, repères en construction) | Rien ne change. | — |
| Indice habituel | Rien ne change. **Un bon indice n’ajoute jamais de charge.** | — |
| Jour de course | Jamais touché. | — |

**Quand.** À l’ouverture de la page, après chaque toucher, à la tâche quotidienne, et toutes les heures à :12
(`paceforge.coaching_morning`) pour les plans dont l’option est allumée, pendant la matinée de l’athlète (de 5 h à
midi à son horloge, choix pratique) et tant que l’indice du matin n’a pas été lu : la nuit arrive quand la montre se
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

**D’où viennent les heures occupées : des connexions directes, rien à taper.** Demande du propriétaire, 24
novembre 2026 : « il faut virer dans mon calendrier ou mon agenda on ne va pas rentrer d'URL à la main donc tu
connecte directement Outlook ou Google etc », puis « iCloud aussi ». Dans Coaching › « Mon agenda », trois sources,
chacune en un ou deux touchers :

- **Google Agenda** et **Outlook**, connectés par OAuth (ci-dessous) — chacun montré seulement quand son connecteur
  est configuré (`GOOGLE_CALENDAR_*`, `MICROSOFT_CALENDAR_*` : drapeau, identifiant et secret ; sans eux, pas de
  bouton et les routes répondent 404). Une connexion de chaque au plus ; se reconnecter renouvelle la même ;
- **« Calendrier de l’iPhone »** : iCloud et tous les comptes que l’iPhone synchronise, lus **sur le téléphone** par
  l’app (« Le calendrier de l’iPhone », plus bas). Apple n’offre aucune connexion web aux calendriers iCloud : le seul
  chemin direct est l’app. Le bouton n’existe que dans l’app iPhone qui l’annonce (`.pf-native-calendar`) ; sur le
  site et dans une app plus ancienne, une seule ligne discrète : « iCloud se relie depuis l’app iPhone. » (dans une
  app plus ancienne : « … depuis la dernière version de l’app iPhone. »).

Sous les boutons, une phrase : « PaceForge lit seulement quand tu es occupé. Jamais les titres. », puis les sources,
chacune avec l’état de sa dernière lecture et « Retirer ». Sans aucune source, rien ne change : pas d’heure, les
séances restent des journées entières.

**Les adresses collées avant le 24 novembre 2026.** Le champ « adresse iCal privée », son pli « Où trouver
l’adresse » et sa route (`POST /coaching/agenda`) sont retirés : c’était aussi le seul endroit où un utilisateur
donnait au serveur une adresse à aller lire — moins de surface. Les adresses déjà enregistrées **restent** : listées
masquées (leur hôte et leurs 4 derniers caractères) avec leur état et « Retirer », lues comme avant par le worker
(`safe_fetch`, ci-dessous), chiffrées (`EncryptedString`). Aucune migration ne les supprime. Code :
`app/services/coaching_agenda.py`, `app/services/coaching_accounts.py`.

| | Google Agenda | Outlook (Microsoft) |
|---|---|---|
| Connexion | OAuth 2.0, code d’autorisation + PKCE (S256), `access_type=offline`, `prompt=consent` | Plateforme d’identité Microsoft v2, `common` (comptes pro et personnels), code + PKCE, `prompt=select_account` |
| Autorisation demandée | `https://www.googleapis.com/auth/calendar.freebusy` (« voir tes disponibilités »), seule | `offline_access` et `MICROSOFT_CALENDAR_SCOPE` : `Calendars.ReadBasic` par défaut, `Calendars.Read` en repli (toute autre valeur est lue comme `Calendars.ReadBasic`) |
| Ce qui est lu | `freebusy.query` sur l’agenda principal : des intervalles occupés, aucun événement | `GET /me/calendarView` de l’agenda par défaut, `$select=start,end,showAs,isAllDay,isCancelled`, paginé (100 par page, 15 pages au plus, une page suivante sur l’hôte de Graph seulement), `Prefer: outlook.timezone="UTC"` |
| Occupé | ce que Google dit occupé | `showAs` « busy » et « oof » (absent) ; « free », « tentative » (pas encore une réunion), « workingElsewhere » (on travaille, ailleurs) et « unknown » ne le sont pas ; un événement annulé non plus |
| Journée entière | un intervalle d’un minuit local à un autre : 2 jours ou plus, « absent » ; un seul jour, ignoré (la règle de l’adresse : Google ne dit pas « journée entière », un jour complet d’occupations collées serait lu de même) | `isAllDay` : la règle de l’adresse, sur les jours de l’athlète |
| Retirer | le jeton est révoqué chez Google (`/revoke`) | rien à révoquer pour une seule application chez Microsoft : l’athlète le retire dans son compte Microsoft ; les jetons sont effacés |

Ce qui est stocké d’un compte connecté : son service, ses jetons chiffrés (`EncryptedString`), leur échéance. Jamais
son e-mail, son identifiant, le nom d’un agenda, ni rien d’un événement au-delà d’un début, d’une fin et « absent ».
Un jeton expiré est renouvelé dans la lecture ; un renouvellement refusé (`invalid_grant` : retiré, ou expiré — les
7 jours d’une application Google « en test ») donne « À reconnecter : l’accès a expiré ou a été retiré. » et un lien
« Reconnecter », jamais une page d’erreur : les jetons sont effacés, les heures de la dernière lecture réussie restent
jusqu’à leur échéance. Une lecture refusée (401/403) renouvelle le jeton une fois, puis vaut « à reconnecter » (le
journal le dit pour `MICROSOFT_CALENDAR_SCOPE`). Ni un jeton, ni un code, ni un `state`, ni les mots du fournisseur ne
sont journalisés : seulement le fournisseur et un code d’erreur.

Au retour du fournisseur, l’athlète retrouve « Mon agenda » ouvert, le résultat en une ligne : « Google Agenda
connecté : lecture en cours. », « Connexion annulée. », « La connexion a expiré. Réessaie. », « Outlook a refusé la
connexion. Réessaie. », « … ne répond pas pour l’instant. Réessaie dans quelques minutes. », et pour un compte pro dont
l’administrateur n’a pas donné son accord : « Ton organisation n’autorise pas cette connexion : demande à ton
administrateur. » Le résultat voyage en deux codes dans l’adresse
(`?outil=agenda&connexion=google&resultat=ok`), jamais en message. Le `state` et le vérificateur PKCE voyagent scellés
dans le cookie de session signé (10 minutes) : un retour dans un autre navigateur, ou avec un autre `state`, est
refusé.

**Dans l’app iPhone.** Google refuse sa connexion dans une WebView (`disallowed_useragent`). Le bouton demande au
serveur (`POST /coaching/agenda/{google|microsoft}/app`) le lien de la feuille de connexion du système, signé par le
ticket à usage unique que COROS et Strava utilisent déjà (2 minutes, dans le fragment du lien) ; l’app ouvre
`/mobile/oauth/agenda/{google|microsoft}` dans `ASWebAuthenticationSession`, la page montre le compte PaceForge avant
le consentement, et la feuille se ferme sur `paceforge://agenda?connexion=…&resultat=…` ; l’app rouvre « Mon agenda »
avec sa ligne. Une app installée plus ancienne (sans `.pf-native-agenda`) ne montre pas les boutons (depuis le 24
novembre 2026, plus d’adresse à coller non plus) : ses sources se connectent depuis paceforge.fr ou une app à jour.

**Le calendrier de l’iPhone** (24 novembre 2026). Code : `mobile/src/phone-calendar.ts` (les règles, pur, testé),
`mobile/src/phone-calendar-native.ts` (EventKit par `expo-calendar`, SDK 57), `mobile/App.tsx` (le pont),
`app/static/js/coaching-agenda.js` (la page), `coaching_agenda.phone_intervals` et `POST /coaching/agenda/iphone`
(le serveur).

| Étape | Ce qui se passe |
|---|---|
| Le toucher | La page demande à l’app (`postMessage` `{type: "phone-calendar", action: "connect"}`, accepté seulement depuis `/coaching` de ce site : `phoneRequest`). L’app montre l’invite du système (iOS 17+ : accès complet, `NSCalendarsFullAccessUsageDescription` ; la lecture ne marche pas avec l’accès en écriture seule), texte : « PaceForge lit quand tu es occupé dans tes calendriers pour placer tes séances en dehors. Jamais les titres, les lieux ni les participants : seules ces heures occupées sont envoyées à ton compte. » Les rappels ne sont jamais demandés. |
| Ce qui est lu | Les événements des 21 prochains jours (l’horizon des autres sources) de tous les calendriers du téléphone, sauf celui qui s’appelle « PaceForge · Coaching » (le flux du plan, si l’athlète s’y est abonnée sur l’iPhone). Pour chaque événement, seulement : début, fin, journée entière, disponibilité, statut ; pour une invitation (un organisateur), la réponse du propriétaire du téléphone (150 invitations au plus, choix pratique `MAX_ATTENDEE_READS` ; au-delà, comptées occupées). Le titre, le lieu, les notes, les participants et le nom d’un calendrier ne quittent jamais le téléphone et ne sont pas journalisés. |
| Occupé | Les règles des autres sources : « disponible » (`free`) et « provisoire » (`tentative`, disponibilité ou statut) ne comptent pas ; un événement annulé ou refusé non plus ; « occupé », « absent » (`unavailable`) et **une disponibilité non donnée** comptent — iCloud n’en donne le plus souvent aucune. Journée entière : un seul jour, ignoré ; 2 jours ou plus (`AWAY_DAYS`), envoyé d’un minuit local à l’autre, que le serveur lit « absent » comme les intervalles de Google. Une absence déjà commencée est coupée à maintenant et lue comme du temps occupé, les mêmes jours. |
| Ce qui quitte le téléphone | **Seulement** `{"intervals": [{"start": s, "end": s}, …]}` : des secondes UTC, fusionnées, 1 500 au plus. L’app ne fait pas la requête elle-même : elle remet la lecture à la page (`phoneSendScript`, seulement sur `/coaching` de ce site), qui l’envoie dans la session de la WebView (`fetch` même origine : le middleware CSRF exige l’origine du site). Un refus envoie `{"refused": true}`. |
| Le serveur | `POST /coaching/agenda/iphone` : 128 Kio au plus ; un objet avec la seule clé `intervals` (ou exactement `{"refused": true}`) ; chaque intervalle un objet aux deux seules clés `start` et `end`, des nombres (ni texte, ni booléen, ni NaN), `start < end`, dans l’horizon (maintenant − 1 h … + 21 jours + 1 h, choix pratique `PHONE_SKEW` pour l’horloge du téléphone), coupé à l’horizon ; 1 500 au plus. Un refus est un code (`shape`, `number`, `order`, `window`, `count`, 422 ; 413 ; 429). Les intervalles qui tombent exactement sur une séance du plan avec une heure sont ignorés (le flux vu par l’iPhone). 60 lectures par heure et par compte (`coaching_agenda_phone`). Gardé comme la source `iphone` (une par athlète), ses intervalles dans `coaching_busy` remplacés à chaque lecture ; les séances retrouvent aussitôt leur créneau. Le worker ne la lit jamais (rien à aller chercher). |
| Relue sans rien demander | Chaque fois que la page Coaching se charge dans l’app et que la dernière lecture a plus de 3 heures (choix pratique `PHONE_STALE`, le rythme de la tournée des autres sources) : la page le dit (`data-phone-calendar-state="stale"`), l’app relit sans invite (`getCalendarPermissions`) et envoie en silence. La ligne de la source dit « Lu aujourd’hui à 14 h 05 » comme les autres. |
| Refusé, ou retiré ensuite | La source lit « À reconnecter : l’iPhone ne donne plus accès à son calendrier. » avec « Ouvrir les réglages » (`Linking.openSettings`, la page de PaceForge dans Réglages, où se trouve l’accès aux calendriers) ; au retour, la page se recharge et relit. Les heures de la dernière lecture restent jusqu’à leur échéance, comme pour un compte connecté ; une source refusée avant toute lecture n’empêche pas les autres de placer les séances. Un refus n’est envoyé qu’une fois (une source déjà « À reconnecter » n’est pas réécrite). |
| Retirer | Supprime la source et ses intervalles aussitôt ; les séances retrouvent leurs heures sans elle. |

Ce bouton n’est pas sur Android (le drapeau n’est annoncé que sur iPhone) ; `READ_CALENDAR` et `WRITE_CALENDAR`,
ajoutées d’office par le module, sont bloquées dans `app.json`. Rien de ceci n’a été essayé sur un vrai iPhone ni
contre un vrai calendrier iCloud : les règles sont testées en Node (`mobile/src/phone-calendar.test.ts`), le pont dans
le harnais natif (`tests/browser/native/check.cjs`), le serveur en Python (`tests/test_coaching_phone.py`).

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

**Quand.** Dans Celery seulement (`app/tasks/coaching.py`) — le calendrier de l’iPhone excepté, lu par l’app : à
l’enregistrement d’une connexion (`paceforge.coaching_agenda`), toutes les 3 heures à :47 pour les comptes qui ont un plan et une adresse ou un compte
connecté (`paceforge.coaching_agenda_round`), et sur « Actualiser » (une fois par minute au plus, `coaching_agenda`) ;
un compte connecté est compté comme une adresse (`coaching_agenda_fetch`). Après
chaque lecture, le plan est relu : chaque séance retrouve son créneau.

**Ce qui est lu d’un événement** (`app/services/coaching_ical.py`, pur) : `DTSTART`, `DTEND`, `DURATION`, `RRULE`,
`EXDATE`, `RECURRENCE-ID`, `TRANSP`, `STATUS`, `UID`. Rien d’autre : un titre, une description, un lieu ou un
participant n’est jamais lu.

| Dans l’agenda | Lecture |
|---|---|
| Un événement avec une heure | Occupé de son début à sa fin : heure UTC, `TZID` (nom IANA, ou nom Windows d’Outlook parmi les plus courants : `WINDOWS_ZONES`), ou heure « flottante » lue à l’horloge de l’athlète (un `TZID` inconnu aussi). |
| `TRANSP:TRANSPARENT` (affiché « disponible ») ou `STATUS:CANCELLED` | Ignoré. |
| Un événement d’une journée entière | Ignoré : c’est le plus souvent un rappel ou un anniversaire. |
| Un événement de journée entière de 2 jours ou plus | L’athlète est « absent » ces jours-là : aucune séance n’y est placée, rien n’est demandé. choix pratique `AWAY_DAYS` |
| Un événement répété (`RRULE`) | Développé avec `python-dateutil` (déjà installé : Celery en dépend, épinglé dans les verrous) pour `FREQ=DAILY`, `WEEKLY`, `MONTHLY`, `YEARLY`, à l’horloge de l’événement (un 9 h hebdomadaire reste à 9 h au changement d’heure), moins ses `EXDATE` et les occurrences qu’une exception (`RECURRENCE-ID`) remplace. Une règle à l’heure, à la minute ou à la seconde, une règle illisible et `RDATE` ne sont pas développées : l’événement compte une fois. |
| Le flux de PaceForge lui-même | Jamais du temps occupé : un calendrier dont le `PRODID` est le nôtre est refusé en entier, un événement dont l’`UID` est celui d’une de nos séances est ignoré (l’athlète qui s’est abonnée au flux dans l’agenda qu’elle a donné). |

**Ce qui est gardé** (`coaching_busy`) : pour les 21 prochains jours, des intervalles occupés — un début, une fin,
« absence » ou non — fusionnés quand ils se touchent, remplacés à chaque lecture réussie (une lecture ratée garde
ceux de la dernière réussie). **Jamais un titre, une description, un lieu ni un participant.**

**Les moments.** « Quand peux-tu courir ? » — Matin, Midi, Soir, par plan, dans « Mon agenda » (l’écran de départ
ne le demande plus depuis le 22 novembre 2026) ; les trois par défaut. Choix pratique `WINDOWS` : 6 h–9 h, 11 h 45–14 h, 17 h 30–21 h, à l’horloge de l’athlète.

**Le placement** (`app/services/coaching_slots.py`, pur). Ce sont des choix pratiques : où une séance tient dans
une journée, pas ce qu’elle est. Une seule règle touche à l’entraînement, l’heure de la séance de qualité : les
adaptations sont plus fortes à l’heure où l’on s’entraîne d’habitude, et les performances courtes et intenses sont
meilleures en fin d’après-midi que tôt le matin — pour l’endurance, l’effet de l’heure est incertain (Chtourou &
Souissi 2012, revue). D’où « l’heure habituelle, sinon le soir ».

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
qu’une source n’a jamais été lue, rien n’est placé : une heure donnée sur la moitié des agendas serait un pari (un
calendrier de l’iPhone refusé avant toute lecture n’en est pas une). Retirer la dernière source efface les heures et
remet à leur jour les séances que l’agenda avait déplacées.

**Ce que voit l’athlète.** Sur la carte du jour, le créneau : « 12 h 15 – 13 h 05 » (une heure s’écrit « 12 h 15 »,
une durée « 1h05 »), et « Autre créneau ». Les barres de la semaine restent telles quelles.

**Ce que voit le coach.** L’heure des séances, et « Séance déplacée de mardi à mercredi. » ; jamais les heures
occupées, ni qu’un jour est plein, ni « Journée chargée », ni les adresses, ni les comptes connectés, ni le
calendrier de l’iPhone, ni « Autre créneau ».

**Export et suppression.** « Télécharger mes données » donne chaque adresse par son hôte et chaque compte connecté
par son service (« Google Agenda », « Outlook », « Calendrier de l’iPhone »), avec leurs dates et leur état — jamais une adresse ni un jeton —, et
les heures occupées. « Arrêter le coaching » et la suppression du compte révoquent l’accès Google et effacent tout ;
le retrait du consentement aux données de santé n’y touche pas (l’agenda n’en est pas).

## Les séances et les activités

- Une sortie de la famille « course » le jour local de la séance la marque **faite** et lui est liée ; plusieurs le
  même jour : la durée la plus proche d’abord. Les minutes faites sont celles de l’activité.
- Une sortie liée dont la durée s’écarte de plus de 20 % de la durée prévue ne ressemble pas à la séance
  (`coaching.FIT`, choix pratique, aucune étude) : elle reste liée — la sortie du jour est la charge du jour — mais la
  séance dit « **Fait autrement** » et l’écart (« 3h17 au lieu de 45 min »), au lieu de « Fait », et le plan la lit
  tout seul, séance dure ou sortie tranquille, puis se met à jour (voir « Une sortie qui n’est pas la séance
  prévue »). Jamais pour la course. Sous toute
  séance faite avec une sortie, « **Ce n’est pas cette séance** » la détache : la sortie redevient
  « hors plan », la séance « Pas faite » (elle peut être reportée), et une lecture suivante ne la reprend pas.
- Une sortie sans séance ce jour-là apparaît « hors plan » dans le calendrier et compte dans le temps fait de la
  semaine ; le plan la lit de la même façon (lue tranquille, elle reste hors plan).
- Sans montre : « C’est fait » (durée réelle modifiable) et « Pas faite ». Un « Pas faite » coché à la main n’est
  jamais défait par une sortie ; un « C’est fait » coché à la main prend la sortie quand elle arrive. « Annuler »
  défait l’un comme l’autre.
- Une séance prévue dont le jour est passé sans sortie devient **pas faite**.
- Le jour de la course et l’activité qui lui est liée ne comptent pas dans les temps de la semaine (choix pratique).

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

**Quand une règle change** (22 novembre 2026 : « Progression d’une séance », les intervalles « vite »). Aucun plan
n’est réécrit d’un coup, aucune donnée n’est migrée : à sa lecture suivante (la page, celle du coach, la tâche
quotidienne), les séances à venir que personne n’a touchées sont recalculées **en place** — la même ligne, le même
événement dans un calendrier abonné (`SEQUENCE` + 1). Les séances faites, manquées, passées, modifiées, déplacées,
allégées par la forme ou écrites par un coach restent telles quelles. Ce qu’un plan existant voit : une sortie
plus courte quand elle dépassait de plus de 10 % la plus longue sortie réelle des 30 derniers jours (et une
semaine qui demande un peu moins), puis des séances qui remontent de 10 % au plus par semaine ; pour une course
sur route en phase spécifique, « *n* × 3 min vite » à la place de « soutenu ».

### Une sortie qui n’est pas la séance prévue

Message du propriétaire, 25 novembre 2026 : « Pour la partie coaching je suis dsl mais ce n'est pas user friendly on
ne sait pas où cliquer et quoi faire exemple simple je n'ai pas fait ma séance du jour mais une vma club je fais quoi
il se passe quoi ? C'est recalculé automatiquement ? ». Une première version posait une question (« C’était quoi ? »,
deux boutons). Sa réponse, le même jour : « Oui mais simplifie encore ou alors ne fait pas en préventif pas d'actions
utilisateurs quand tu vois que ce n'est pas la séance attendu car tu ne peux pas la rapprocher alors tu met à jour ».
**Maintenant rien n’est demandé : le plan lit la sortie tout seul, se met à jour, puis la page dit ce qu’il a fait**
(`services/coaching_runs.py`).

**Quelles sorties.** Une sortie enregistrée dans les `DEBRIEF_DAYS` = 7 derniers jours, à partir du premier jour du
plan, jamais la course, jamais une sortie déjà lue ou détachée par « Ce n’est pas cette séance » :

| Cas | Ce que la sortie est |
|---|---|
| (a) Liée à la séance du jour sans lui ressembler (`misfit` : plus de 20 % d’écart de durée) | « au lieu du footing de 55 min » |
| (b) Liée à une séance facile (footing, récup) dont la durée colle, mais lue comme une séance dure | « plus soutenu que le footing prévu » |
| (c) Le jour d’une séance cochée « Pas faite » à la main, la sortie arrivée ensuite | « au lieu du footing de 55 min » |
| (d) Un jour sans séance (« hors plan ») | « rien n’était prévu » |

Une seconde sortie un jour dont la séance est faite n’est pas lue : le jour avait sa séance (elle reste « hors
plan », comme avant).

**La lecture : séance dure ou sortie tranquille** (`coaching_runs.reading`, une fonction pure). Une séance dure dès
qu’un de ces trois critères tient — chacun reprend une définition du plan, aucune physiologie nouvelle :

| Critère | Règle | Source |
|---|---|---|
| Les tours | Des efforts vite répétés : la vitesse de chaque tour (distance sur temps de déplacement ; les tours de moins de 100 m ou 20 s, un tour pressé deux fois, laissés de côté), la vitesse typique de la sortie (la médiane de ses tours) ; un tour « vite » va 20 % plus vite qu’elle (`FAST_LAP`), il faut trois blocs de tours vite séparés par des tours plus lents (`FAST_LAPS`), sur six tours au moins (`looks_like_intervals`). Prudent : des tours automatiques d’un kilomètre le montrent rarement, des tours pressés à chaque effort le montrent. | choix pratique |
| Le cardio | La fréquence cardiaque moyenne de la sortie atteint la zone que le plan donne à ses efforts « soutenus » : niveau 3 = zone 4 (`coaching_plan.HR_ZONES`), soit 80 % de la FC max connue de l’athlète (`hr_floor` ; les zones de l’app, `heart_rate_zones`), la référence que la page lit déjà pour ses fourchettes de cardio (confirmée, sinon corroborée par les sorties). Rien de cardiaque n’est affiché : la règle de la personne qui suit le plan ne change pas. | « L’intensité » : Persinger 2004 pour le niveau, les zones de l’app (H) |
| L’allure, sur le plat | La vitesse moyenne de la sortie atteint l’allure seuil du plan : la borne basse de `coaching_pace.THRESHOLD` (93 %) × la vitesse critique de l’athlète, quand elle est connue, et seulement si la sortie a peu de dénivelé : **10 m de montée par kilomètre au plus** (1 % en moyenne, `FLAT_CLIMB`, choix pratique : le plan ne donne pas d’allure en côte non plus ; une sortie plus vallonnée se lit par ses tours et son cardio). | Nixon 2021 pour le seuil ; le dénivelé : choix pratique |

Sans tours, sans cardio et sans allure utilisable : une **sortie tranquille** — rien n’est reconstruit, le côté
prudent. La FC max et la vitesse critique ne sont lues que si une sortie en a encore besoin (une seule requête pour
les tours).

**Ce que fait le plan, aussitôt** — à chaque lecture du plan (`coaching.sync` : la page de l’athlète, celle de la
personne qui suit le plan, la tâche quotidienne), avant tout affichage ; puis le plan est relu une fois, comme après
tout changement. Les sorties se lisent de la plus ancienne à la plus récente (les règles d’une semaine voient ce qui
précède). Chaque règle reprend une règle du plan déjà sourcée, ou porte sa marque :

| Lecture | Ce que fait le plan | Source |
|---|---|---|
| Séance dure | La sortie devient la séance dure du jour (« Séance dure », sa durée réelle, la forme de la séance fixe : aucune répétition écrite, `declared_structure`) et compte pour **une des séances de qualité de la semaine** : les règles de la séance fixe, pour cette semaine seulement (`after_hard`). La séance du jour (liée, ou cochée « Pas faite ») devient cette séance ; une sortie hors plan devient une séance du jour (faite, liée). Quand ce jour de la semaine montre une habitude de séance dure, le plan pose « Ma séance fixe » ce jour-là à la place des changements ponctuels (voir « La séance fixe »). | La séance fixe (ci-dessus) : choix pratique pour la forme, (H) `FIXED_HARD_SHARE` pour son poids. |
| … le nombre | Des séances de qualité du plan encore à faire cette semaine, il en reste autant que le nombre de la semaine (`week_quality` : le compte même que le générateur écrit, `TWO_QUALITY` au plus) en laisse à côté d’elle et des séances faites, fixes ou touchées par une personne ; la séance de la phase qui n’est pas des intervalles courts en vitesse d’abord (`BESIDE_FIXED`), puis la plus tôt. Les autres deviennent **un footing de leur durée**. | Deux au plus : Seiler & Tønnessen 2009 ; l’ordre (H) `BESIDE_FIXED`, **aucune étude**. |
| … le budget dur | Au plus 20 % de la semaine en efforts durs, la sortie comptée pour un quart de sa durée : une séance qui ne tient plus garde moins de répétitions (sa plus petite valeur au moins), à sa durée, le retour au calme prenant le reste (`fewer_reps`) ; sinon un footing de sa durée. | Seiler 2010 ; Muñoz 2014 `HARD_SHARE` ; le quart : (H) `FIXED_HARD_SHARE`, **aucune étude**. |
| … le lendemain | Une séance dure que le plan peut changer, le lendemain, va au jour suivant de la semaine que le plan peut utiliser — un jour disponible, vide ou portant une séance facile du plan (qui prend sa place), jamais à côté d’un jour dur, pas de sortie longue à J-3 (`move_day`, la règle du matin de récupération basse) ; sinon elle devient un footing de sa durée — une « Récup » puisque c’est le lendemain d’un jour dur (la règle « Jours » du générateur), sauf une sortie longue, qui reste un footing (une récup est courte). | Stanley 2013 (au moins 48 h après un effort intense) ; la récup du lendemain : Stanley 2013, comme le générateur. |
| … jamais | Une séance faite, manquée, passée, modifiée, déplacée à la main, écrite par la personne qui suit le plan, allégée par la forme, ou la séance fixe : rien n’y touche. | choix pratique : ce qu’une personne décide reste. |
| Sortie tranquille | Rien n’est reconstruit. Si la séance prévue ce jour-là était une séance de qualité (pas la séance fixe), donc pas faite, elle est **reportée** au premier jour que les règles de déplacement permettent (`report_day` → `move_day`, à partir de demain : un jour disponible de la semaine, vide ou portant une séance facile du plan qui lui laisse sa place, jamais à côté d’un jour dur) ; la sortie reste la séance facile du jour. Sans jour possible, elle reste non faite et la page le dit. Une sortie liée garde sa séance ; une sortie un jour « Pas faite » ou sans séance reste **hors plan** (la tuile grise, la tenir sur une séance, « C’est ma séance de … » : rien ne change pour elle). | Stanley 2013 ; le report lui-même : choix pratique (une séance prévue, pas une charge de plus). |
| … cas (b) | Une sortie de la durée d’un footing, lue tranquille, est simplement ce footing : rien n’est écrit d’autre que sa lecture, aucune carte ; le débrief demande son ressenti. | — |
| Le temps en plus | La sortie compte dans le temps fait de la semaine (`done_by_week`, comme toute sortie). Pour les semaines suivantes, deux règles la lisent déjà : la progression d’une séance (la plus longue sortie réelle des 30 derniers jours, `recent_runs`) et la reprise après deux semaines manquées. **Aucune règle de volume n’est ajoutée** : rien ne retire des minutes au reste de la semaine pour compenser. | Frandsen 2025 pour la progression d’une séance ; compenser : **aucune étude**. |

**Écrit une fois.** La lecture est gardée sur la séance qui porte la sortie (`coaching_sessions.structure` :
`answer`, avec l’état d’avant de chaque séance changée) — pour une sortie restée hors plan, sur une note (une ligne
annulée, sans sortie liée : ni le tableau, ni le calendrier abonné, ni la régénération ne la lisent ; elle disparaît
si la sortie est ensuite tenue sur une séance). Chaque séance changée porte pourquoi (`structure.why`) et aucune
lecture suivante ne la réécrit (`coaching.replaceable` l’exclut, comme une séance allégée par la forme) : la page, la
page de la personne qui suit le plan, la tâche quotidienne relisent le plan sans rien rejouer ni défaire. **Aucune
migration** : les colonnes JSON existantes suffisent (`coaching_sessions.structure`, et `coaching_plans.fixed` pour
la séance fixe posée par l’habitude et les jours de la semaine refusés) ; une réponse donnée par la version qui
posait la question (il n’y en a pas en production) se lit comme une lecture.

**La carte : un avis, pas une question.** Une seule, en haut, à la place et dans le style du débrief (le ressenti de
la sortie y est demandé, une fois : le ticket du jour ne le redemande pas). Une sortie à la fois, la plus récente
d’abord (devant un débrief plus ancien ; un lien `?d=…#debrief` vers le jour d’un débrief garde ce débrief). Pour
l’athlète seulement : la personne qui suit le plan ne voit jamais la carte, seulement les séances qui en résultent,
avec leur ligne. Ce qu’elle dit (26 novembre 2026 : « supprime le max de texte ») : **une phrase**, ce qui s’est
passé et comment c’est lu, et **une seconde, courte, seulement quand le plan a changé quelque chose** :

| Cas | La carte |
|---|---|
| Séance dure, une séance changée | « Mercredi, 1h35 au lieu du fartlek de 45 min : comptée comme une séance dure. » puis « Jeudi devient une récup. » (« La séance de jeudi passe à vendredi. », « Jeudi devient un footing. », « Jeudi garde moins de répétitions. ») |
| Séance dure, rien d’autre | « … : comptée comme une séance dure. », seule |
| Séance dure, une habitude | « … : comptée comme une séance dure. » puis « Le plan garde une séance dure chaque mercredi. » |
| Séance dure, cas (b) | « Mardi, 55 min, plus soutenu que le footing prévu : comptée comme une séance dure. » |
| Séance dure, cas (d) | « Mercredi, 50 min sans séance prévue : comptée comme une séance dure. » |
| Tranquille, séance reportée | « Mardi, 1h10 tranquille au lieu de tes intervalles : ils passent à jeudi. » (« ton fartlek : il passe », « tes côtes courtes : elles passent », « ta séance au seuil : elle passe ») |
| Tranquille, sans jour possible | « … tranquille au lieu de tes intervalles : aucun autre jour ne peut les accueillir cette semaine. » |
| Tranquille, rien d’autre | « Mercredi, 1h35 au lieu du footing de 55 min : comptée comme une sortie tranquille. » ; un jour sans séance : « Mercredi, 40 min sans séance prévue : comptée comme une sortie tranquille. » |

Le jour se dit « Aujourd’hui », « Hier », le jour de la semaine, « Jeudi dernier » il y a sept jours ; la séance se
nomme par son format (« du fartlek », « des intervalles », « des côtes courtes », « de la séance au seuil », « du
footing », « de la sortie longue », « de ta séance fixe »). Les kilomètres ne sont plus dits (le ticket et
l’activité les portent). Puis, dans la même carte :

- **le ressenti** habituel en cinq niveaux, quand la sortie est portée par une séance faite (c’est le débrief de
  cette sortie : demandé une fois, jamais deux) — une sortie restée hors plan n’en a pas, comme avant ;
- **un seul lien discret, « Corriger »**, qui déplie sur place les retours ci-dessous ;
- **le retour qui dit ce qu’il fait** : « C’était une sortie tranquille » (après une séance dure) ou « C’était
  une séance dure » (après une sortie tranquille). Il remet exactement ce que la première lecture avait changé (les
  séances changées, la séance qui porte la sortie — son type, sa durée, son lien —, une séance fixe posée par
  l’habitude, le report) puis applique l’autre lecture, avec ses règles ; le ressenti déjà dit reste. Une
  séance changée que l’athlète a déplacée, modifiée ou faite depuis est à lui : elle reste. La carte dit la
  nouvelle lecture et son lien dit l’inverse ;
- **la sortie discrète**, où elle existait : « Ce n’est pas cette séance » (cas a et b : la lecture est reprise, la
  sortie redevient hors plan, la séance « Pas faite », et rien n’est relu pour elle) ; « C’est ma séance de … » (cas
  d : la lecture est reprise, cette séance de la semaine va au jour de la sortie, la lecture suivante les lie) ;
- **« Pas tous les mercredis »**, après la phrase de l’habitude seulement, sous « Corriger » aussi : la séance fixe
  qu’elle a posée part, la semaine redevient ce que la lecture ponctuelle en faisait, et ce jour de la semaine n’est
  plus jamais posé tout seul (voir « La séance fixe ») ;
- **« × »** dans le coin de la carte (nommé « Fermer »).

**Quand il n’y a pas de carte.** Elle reste jusqu’à « × » (ou la sortie plus vieille que 7 jours) tant qu’elle a
quelque chose à dire : une séance dure, une séance changée ou reportée, un report sans jour, une séance fixe posée
par l’habitude, une lecture corrigée, une sortie restée hors plan (la carte est le seul endroit où sa
lecture se corrige). Sinon — rien n’a changé — elle ne reste que tant que le ressenti manque ; une fois dit, plus de
carte. Le cas (b) lu tranquille n’en a jamais.

**Les lignes.** Chaque séance changée le dit sur son ticket, comme « Séance reportée de jeudi » : « Récup :
lendemain de la séance dure de mardi. », « Footing : la séance dure de mardi compte pour la semaine. », « Moins de
répétitions : la séance dure de mardi compte pour la semaine. », « Séance reportée de mercredi, lendemain de la
séance dure de mardi. », « Séance déplacée de jeudi. » (la séance facile qui a pris l’autre jour), « Séance
reportée de jeudi. » (une séance de qualité reportée après une sortie tranquille). Et la semaine qu’ils concernent,
en cartouche sous sa rangée (la plus récente seule : au plus une ligne de changement automatique par semaine),
comme « Mardi est plein : séance déplacée à vendredi. » : « Mercredi devient une
récup : c’est le lendemain de la séance dure de mardi. », « Mercredi est le lendemain de la séance dure de mardi :
sa séance passe à jeudi. ». Les mêmes mots pour la personne qui suit le plan (« la séance dure », jamais « ta »).

**Le cas du propriétaire** (mardi, mercredi, vendredi, samedi, dimanche ; deux séances de qualité : le fartlek du
mercredi, les côtes courtes du vendredi) : mardi, 1h35 de VMA en club au lieu du footing de 55 min, des tours pressés
à chaque effort. À la lecture suivante, sans un toucher : la sortie est la séance dure du mardi ; les côtes du
vendredi restent (ce ne sont pas des intervalles courts en vitesse, et le budget tient) ; le fartlek du mercredi, le
lendemain, ne peut pas rester ni aller samedi (veille de la sortie longue) : une récup. La carte : « Aujourd’hui,
1h35 au lieu du footing de 55 min : comptée comme une séance dure. Mercredi devient une récup. », le ressenti,
« Corriger » (« C’était une sortie tranquille », « Ce n’est pas cette séance »), « × ». S’il fait une séance dure
presque tous les mardis (3 des 8 semaines d’avant au moins, avec une sortie le mardi dans 5), le plan garde le mardi
chaque semaine à la place de la récup du mercredi : « Le plan garde une séance dure chaque mardi. », et « Pas tous
les mardis » sous « Corriger ».

## Les écrans

Demandes du propriétaire, 22 novembre 2026 : d’abord « qu’est-ce que je fais aujourd’hui », une action principale par
séance, le reste discret, les outils repliés ; puis « le centre de l’application doit être un calendrier ». Le
23 novembre, une maquette touchable : « La maquette est top » — la route vers la course, la semaine en tableau, le
jour en ticket, les options dans une feuille. Le 26 novembre, après plusieurs tours (« pas user friendly, on ne sait
pas où cliquer et quoi faire ») : **« Repars de 0 supprime le max de texte et bouton pour la gui si pas utile »**.
La règle depuis : **un mot reste seulement si l’enlever empêcherait d’agir ou de comprendre ce que le plan a fait** ;
une commande reste seulement si son action n’existe nulle part ailleurs, et alors un toucher plus loin (« ⋯ ») plutôt
qu’en vue. Ce qui quitte un écran est dit dans « Comment ça marche », en une phrase. Aucune règle du plan n’a changé.
Tout ce qui porte un nom accessible le garde : une commande sans texte a son `aria-label`, une valeur nue garde en
`sr-only` son sens entier.

**`/coaching`, de haut en bas** (`partials/coaching_main.html`, la partie remplacée à chaque toucher, puis
`coaching.html` autour).

1. **L’en-tête** : « Coaching » et **un bouton rond** à droite (« Options du plan ») qui ouvre les options (l’athlète
   seul). Plus de badge « bêta ».
2. **La route** (`coaching_view.road`, `header`) :
   - **une ligne** : « J‑54 » en grand, puis « · Trail des Crêtes · 5h00 ». Le temps est celui sur lequel le plan
     tourne, dit une fois, lien de la page de la course ; d’où il vient (« Objectif », « Estimation », « Environ »)
     reste dit au lecteur d’écran. Sans course, le mot de l’intention seul : « Progresser », « Garder la forme » ;
     une course passée : « Course passée » ;
   - **chaque semaine du plan est une barre**, haute comme le temps qu’elle prévoit, teintée par sa phase, la semaine
     en cours à l’encre, celle de la course pleine avec un point ; 30 barres au plus (`ROAD_WEEKS`), les premières
     semaines cèdent la place, jamais la semaine en cours ;
   - **chaque barre est l’adresse de sa semaine**. Un doigt ou une souris **la balaie** : la barre sous le doigt
     s’allume et sa semaine (ses dates, son temps prévu, sa phase) s’écrit par-dessus la fin de la ligne, le temps du
     geste seulement ; **la semaine se charge une fois, au lâcher** (`coaching-calendar.js`). Au clavier : une barre
     dans l’ordre de tabulation, les flèches, Début et Fin, Entrée. Sans JavaScript ce sont des liens ;
   - **retirés** : « Semaine 4 sur 12 », les noms des phases sous les barres, la ligne du niveau, la ligne de deux
     jours voisins. **Reste une seule alerte** : la course perdue (« Trail des Crêtes est derrière toi : dès lundi,
     le plan continue sans course. »).
3. **Une carte en haut** (`#debrief`), une à la fois — l’avis d’une sortie, sinon le débrief :
   - **l’avis d’une sortie qui n’est pas la séance prévue** (voir « Une sortie qui n’est pas la séance prévue »),
     un groupe titré par **une phrase** — ce qui s’est passé et comment c’est lu : « Hier, 1h35 au lieu du fartlek
     de 45 min : comptée comme une séance dure. » — et **une seconde, courte, seulement quand le plan a changé
     quelque chose** : « Jeudi devient une récup. », « La séance de mercredi passe à jeudi. », « Le plan garde une
     séance dure chaque mercredi. » ; puis les cinq boutons du ressenti (une séance la porte), **un seul lien
     discret, « Corriger »**, qui déplie sur place les retours qui existent (« C’était une sortie tranquille » ou
     « C’était une séance dure », « Pas tous les mercredis », « Ce n’est pas cette séance » ou « C’est ma séance
     de … ») — un `<details>`, sans JavaScript aussi —, et **« × » dans le coin** (44 px, nommé « Fermer »). Après
     une correction, le focus va au titre de l’avis, qui dit la nouvelle lecture ;
   - **le débrief** : une ligne, « **Mardi · footing 42 min.** C’était comment ? » (le temps couru), et **cinq
     boutons ronds numérotés**, un toucher, enregistré sur place. Des mots sous 1 et 5 seulement (« Très facile »,
     « Très dur ») ; chaque bouton garde son mot pour le lecteur d’écran. Une question à la fois : celle du jour
     demandé, sinon la plus récente ; les autres gardent leur pastille sur leur jour (« Puis 2 autres à débriefer. »
     est retiré). Une séance faite est demandée pendant `DEBRIEF_DAYS` = 7 jours (H). Le lien
     `/coaching?d=2026-11-24#debrief` ouvre la page sur ce jour, la question en haut. `coaching_view.page` donne
     `waiting` (aucune notification n’est envoyée d’ici).
4. **La semaine en tableau** :
   - « Cette semaine » (« Semaine prochaine », « Semaine dernière » ou ses dates) avec ‹ ›, « Aujourd’hui » seulement
     quand on est ailleurs ; **une ligne**, « 2h27 sur 5h00 · Spécifique », et sa fine barre ;
   - **le tableau, inchangé** : sept colonnes, chaque séance une tuile haute comme sa durée, de la couleur de son
     type, teintée et cernée tant qu’elle est prévue, pleine avec une coche une fois faite, en pointillés manquée,
     grise hors plan, sa durée courte dessus, un mot dessous ; le lecteur d’écran a le nom complet de chaque jour ;
   - dessous, **au plus une ligne sur un changement automatique** : la plus récente qui concerne la semaine montrée —
     ce qu’une sortie déclarée dure a changé, sinon un jour plein de l’agenda (« Mardi est plein : séance déplacée à
     vendredi. »), sinon la semaine allégée et sa raison, sinon la séance fixe la semaine de la course. Les autres
     restent dites sur le ticket de leur séance. Deux jours durs de suite qu’un déplacement a faits restent dits, en
     couleur d’avertissement (« Samedi et dimanche : deux jours durs de suite. ») ; « Tu peux courir la séance en
     footing » est dans « Comment ça marche » ;
   - « Semaine prochaine · 5h30 » et sa rangée compacte (chaque case un lien, une cible où poser une séance) ;
   - **retirés** : « Maintiens une séance pour la déplacer » (le geste reste, dit dans « Comment ça marche » ;
     « Déplacer » reste dans « ⋯ »), « Semaine plus légère, pour assimiler. » (une semaine sur quatre : « Comment ça
     marche »), les lignes de la semaine suivante.
5. **Le jour** — aujourd’hui à l’ouverture, même un jour sans séance ; son titre, « Aujourd’hui », sinon
   « Vendredi 27 » (« Vendredi 27 décembre » au-delà de trois semaines), la date une fois :
   - **un ticket par séance**, un bloc de sa couleur : le nom, le statut une fois faite ou manquée (« Fait »,
     « Fait autrement », « Pas faite »), **la durée en très grand** (le temps couru une fois faite, « prévu 45 min »
     à côté s’il diffère), **l’effort dessiné**, la phrase de structure d’une séance structurée ; texte à 4,5:1 au
     moins sur sa couleur dans les deux thèmes ;
   - **la cible en valeurs nues**, dans le grand type tabulaire : « 3:50–4:00 /km », « 171–181 bpm », le plafond du
     facile « max 5:15 /km », l’allure de course « 4:30 /km ». **Plus de titre « Cible », plus d’étiquettes**
     (« Allure, sur le plat », « Cardio en fin d’effort », « pendant les efforts »), plus de « D’où viennent ces
     chiffres ? » : le groupe s’appelle « Cible » (« Cible pendant les efforts ») pour le lecteur d’écran, et chaque
     valeur garde son sens entier en `sr-only` (« Pas plus vite que, sur le plat : 5 min 15 au km », « Cardio en fin
     d’effort : 171 à 181 bpm ») ; l’explication est dans « Comment ça marche ». Les efforts courts sans chiffre :
     rien (la phrase « À la sensation… » reste celle du calendrier synchronisé). Sans aucune donnée : le seul lien
     « Confirme ta FC max ». La phrase du test de la parole sous le ticket garde sa règle : seulement quand la cible
     n’a aucun chiffre. Seulement tant que la séance est à faire (ou manquée) ;
   - sous lui, **l’heure du créneau seule** (« 12 h 00 – 12 h 30 ») : la toucher demande un autre créneau libre
     (son nom accessible : « 12 h 00 – 12 h 30 : demander un autre créneau ») ; « Journée chargée » quand l’agenda
     n’a rien trouvé ;
   - **au plus une ligne sur ce qui a changé pour cette séance** : la forme (« Séance allégée : récupération basse
     ce matin. La séance prévue passe à vendredi. », lien de Santé ; à la personne qui suit le plan « Séance adaptée
     ce matin »), sinon une réponse à une sortie (« Récup : lendemain de la séance dure de mardi. »), sinon l’agenda
     (« Séance déplacée de mardi : journée pleine. » ; à la personne qui suit le plan « Séance déplacée de mardi. »),
     sinon qui l’a modifiée (« Modifiée par Florent », discret) ; « Alléger aujourd’hui » reste une action, un matin
     bas, quand le plan ne change pas la séance seul ;
   - **une action principale, « C’est fait », et un bouton rond « ⋯ »** (nommé « Autres actions ») qui ouvre un petit
     menu sous eux, sur place, avec tout ce qui existe pour cette séance et cet état : « Pas faite », « Déplacer » ou
     « Reporter », « Modifier » (et, pour la personne qui suit le plan, « Supprimer la séance » dedans), « Ce n’est
     pas cette séance », « Annuler », « Changer mon ressenti » (une fois dit), « Ajouter une note » ou « Modifier ma
     note », « Commenter ». Rien n’est perdu, c’est un toucher plus loin. **Au toucher et au clavier** : il s’ouvre
     sur Entrée ou Espace, le focus va à sa première action ; Échap le ferme et rend le focus à « ⋯ » ; un toucher
     ailleurs le ferme ; après une action, le focus revient à « ⋯ ». **Sans JavaScript**, « ⋯ » n’est pas dessiné et
     les mêmes actions sont une liste sous le ticket (le script de la page ajoute la classe qui les replie). Une
     séance à venir n’a pas « C’est fait » : « ⋯ » seul ;
   - **une séance faite** : le statut, ce qui a été couru (l’activité liée et son lien, « 3h17 au lieu de 45 min »,
     la durée réelle d’une coche à la main), le ressenti tant qu’il n’est pas dit, la note et le commentaire s’il y
     en a ; le reste dans « ⋯ » ;
   - la course : son ticket à l’encre, son nom en grand, « Voir le plan de course » ;
   - une sortie hors plan : « Hors plan · Footing avec Léa » et ses chiffres ; cette semaine, « C’est ma séance de
     jeudi » ;
   - **un jour sans séance** : « Repos » (« Rien de prévu » quand une sortie hors plan est là) et **une rangée**,
     « Demain · Côtes 45 min » (sinon « Samedi 28 · … »), qui ouvre ce jour ; « Prochaine séance » reste dit au
     lecteur d’écran.
6. **Les options** : le bouton rond ouvre **une feuille** venue du bas (`coaching-sheet.js`, un `<dialog>` modal) :
   le titre, **« Adapter à ma forme »** en interrupteur seul (sa phrase, ce qu’il lit — VFC, sommeil, FC de repos — et
   que la personne qui suit le plan ne voit jamais pourquoi sont dans « Comment ça marche »), **six tuiles à leur
   seul titre** — « Ma séance fixe », « Dans mon calendrier », « Mon agenda », « Partager mon plan », « Suivre un
   plan », « Comment ça marche » —, « Refaire le plan » et, petit et rouge, « Arrêter le coaching ». Une tuile ouvre
   son outil dans la feuille, « ‹ Options » revient aux tuiles. Le focus reste dans la feuille (Tab tourne), Échap et
   un toucher sur le fond la ferment et rendent le focus au bouton rond ; la page derrière ne défile pas.
   - **Dans chaque outil, une phrase courte au plus au-dessus des commandes**, et toutes les phrases qui disent ce
     qui est lu ou partagé : « Ma séance fixe » (« Une séance qui revient chaque semaine », ou la séance réglée ;
     l’alerte quand elle touche la sortie longue ; « Lu sur tes mercredis. » sous la durée proposée) ; « Dans mon
     calendrier » (trois boutons d’un toucher, « Copier l’adresse », « Régénérer le lien » ; aucune adresse
     affichée) ; « Mon agenda » (« Des séances hors réunions et rendez-vous », les boutons de connexion, **« PaceForge
     lit seulement quand tu es occupé. Jamais les titres. »**, la ligne iCloud, les sources, « Actualiser », « Quand
     peux-tu courir ? ») ; « Partager mon plan » (**« La personne à qui tu donnes le code voit ton plan et peut
     l’ajuster ; jamais tes données de santé. »**, le code, « Valable 7 jours, une seule fois. ») ; « Comment ça
     marche » complet.
   - **Un formulaire d’un outil revient dans la feuille**, ouverte sur cet outil (`/coaching?outil=agenda#…`, etc.).
   - **Sans JavaScript**, les options sont une section à la fin de la page ; le bouton rond et chaque adresse
     `?outil=…#…` y mènent.
7. **L’écran de départ** (`coaching_onboarding.html`) : « Coaching », les trois intentions à leur seul mot et leur
   icône (ce que chacune fait est dans « Comment ça marche »), la course, la première semaine dessinée, les jours
   (la ligne sous eux ne parle que quand il en manque ou en trop), « J’ai déjà une séance chaque semaine », ne restent
   que les lignes sur ce que les réponses entraînent et qu’on peut vouloir changer (la séance fixe contre la sortie
   longue, deux jours voisins, une course trop proche pour la sortie longue), la note d’un jour resté libre, la
   légende, la sortie longue, le volume, « Créer mon plan », la première séance. **Retirés** : « Ton plan est prêt.
   Ajuste-le si tu veux. », la phrase de chaque intention, la ligne du niveau, « pour finir » / « pour performer »,
   les deux phrases de deux jours par semaine, « … est ta séance de qualité de la semaine », « Calculé sur tes 4
   dernières semaines de course. », « Touche un jour pour l’ajouter ou l’enlever. », « Sans fichier GPX : un nom, une
   date, une distance ». « Refaire le plan » garde « Tes séances faites restent. ».

Sur un écran large (900 px et plus), la route et la carte en travers, puis le tableau et le ticket côte à côte ; la
feuille est un panneau centré.

**Les mots.** La fonction et son onglet s’appellent « Coaching » ; l’onglet « Courses » garde son nom. **À
l’intérieur, les écrans ne disent jamais « coach »** (propriétaire, 22 novembre 2026 : « décide ce qui est le
mieux » — le plan est automatique, et le mot laissait croire qu’il fallait un coach humain). Ce document, le code,
les routes et les tables gardent « coach » pour le rôle : la personne qui suit le plan de quelqu’un d’autre.

| Ce que dit l’écran | À qui |
|---|---|
| « Partager mon plan », avec une ligne : « La personne à qui tu donnes le code voit ton plan et peut l’ajuster ; jamais tes données de santé. » | L’athlète |
| « Suivre un plan » (la tuile des options), « Suivre le plan de quelqu’un » (le lien de l’écran de départ, le champ du code) ; la liste s’appelle « Plans suivis » | Qui suit un plan |
| Un commentaire et « Modifiée par … » sont signés d’un **prénom** : « Florent », « Modifiée par Florent » ; côté coach, « Modifiée par Stella ». L’auteur d’un commentaire n’est pas enregistré : le prénom n’est écrit que si une seule personne suit le plan ; sinon « Commentaire », « Modifiée par la personne qui suit ton plan ». | Les deux |
| « La personne qui suit ton plan ne voit jamais pourquoi. » (l’option « forme ») | L’athlète |
| « la personne avec qui tu partages ton plan » : le rôle, dit en toutes lettres | La politique de confidentialité |

- **« Déplacer »**, la chose la plus simple de la page : on ouvre le jour, « ⋯ » › « Déplacer », puis le jour voulu.
  Une séance encore à faire va sur **n’importe quel jour à partir d’aujourd’hui** — pas seulement les jours donnés à
  la création du plan, cette semaine, la suivante ou plus loin (les flèches restent actives pendant qu’on choisit) —
  et y reste **épinglée** : une régénération ne la réécrit plus. Une séance manquée plus tôt cette semaine se
  « Reporte » de même (elle redevient prévue). Un jour passé de cette semaine qui porte une sortie hors plan est
  accepté aussi : la lecture suivante lie la sortie à la séance. Un jour qui porte déjà une séance prévue l’échange.
  **Deux jours durs de suite ne sont jamais refusés** : pendant le choix, un point marque les jours voisins d’une
  autre séance dure (« Un point marque un jour voisin d’une autre séance dure. »), puis la semaine le dit (« Samedi et dimanche : deux jours
  durs de suite. »). Le générateur, lui, n’en fait toujours jamais (« Jours durs » ci-dessus). Tout se fait au
  clavier : les jours du tableau et de la rangée suivante deviennent des boutons, la consigne prend le focus. La
  course ne se déplace pas.
- **À la main, le même déplacement** (`static/js/coaching-calendar.js`, chargé par la page Coaching seule ; ses
  seuils sont des choix pratiques, des gestes et non des règles d’entraînement) :
  - **un doigt maintenu 250 ms** sur un jour (sans bouger de plus de 8 px : avant, c’est le défilement de la page ou un
    balayage) soulève sa tuile, au tableau comme dans la rangée suivante ; **une souris** la soulève après 5 px. **La
    tuile elle-même se soulève** — un peu plus grande, penchée, avec une ombre — et suit le doigt **au-dessus de
    lui** ; sa place garde une copie pâle ; les cases qui la prennent sont entourées en pointillés, les autres
    pâlissent, un jour voisin d’une autre séance dure prend la couleur et le point de l’avertissement ; le jour sous
    le doigt se remplit. (La maquette échangeait deux séances posées l’une sur l’autre : ici les listes du serveur
    décident, comme avant.)
  - **lâchée sur un jour qui la prend**, elle y est montrée aussitôt et part par la même adresse que le bouton
    (`POST …/sessions/<id>/move`, `to`, avec la semaine affichée) : la réponse redessine la page et ouvre son
    nouveau jour (le tableau suit le jour dans sa semaine), où la tuile rebondit un instant ; c’est le serveur qui décide et qui dit l’avertissement ensuite. Une réponse qui
    n’arrive pas la remet à sa place (le message habituel : « Impossible de charger. Réessaie. »). Lâchée ailleurs,
    ou Échap : elle revient, rien n’est envoyé. Un doigt maintenu puis relâché sans bouger reste un toucher : le jour
    s’ouvre ;
  - **le serveur dit tout** : chaque tuile déplaçable porte les jours affichés qui le prennent et ceux qui
    avertissent (`coaching_view.drops`, les réponses mêmes du mode « Déplacer », `drop`) ; le script ne fait que les
    lire. Seules les séances que « Déplacer » prend se soulèvent ; rien pendant que « Déplacer » a fait des jours des
    cibles ;
  - **une sortie hors plan** de cette semaine (la tuile grise) se pose sur une séance de sa semaine : c’est « C’est ma
    séance de … », à la main — cette séance va au jour de la sortie, que la lecture suivante lui lie ;
  - **la personne qui suit le plan** a les droits de son bouton « Déplacer » : les mêmes séances ; jamais la sortie
    hors plan (le bouton « C’est ma séance de … » est à l’athlète) ;
  - **un balayage horizontal** (48 px au moins, deux fois plus large que haut, en moins de 0,8 s, pas à moins de
    24 px d’un bord de l’écran, gardés aux gestes du système) suit le lien ‹ ou › ; les semaines glissent de côté
    (moins de 300 ms, rien quand le système demande moins d’animations) ;
  - **le défilement reste celui de la page** : rien n’est retenu tant qu’une séance n’est pas soulevée ; seul le
    jour soulevé coupe les gestes du navigateur. Pas de menu du lien ni de sélection sur un jour (iOS : un appui
    long est le geste). Vérifié dans Chromium (vrais événements tactiles), pas sur un iPhone.
- Chaque toucher remplace la partie du milieu sur place (htmx), sans recharger la page ; le focus reste sur
  l’élément touché. Sans JavaScript, les mêmes liens et formulaires renvoient à la page.
- `/coaching/athletes` (« Plans suivis ») : la liste du coach (la barre de la semaine, les dernières séances avec statut et ressenti) et
  le champ du code. `/coaching/athletes/<id>` : **la même mise en page**, au prénom de l’athlète — la route (le
  temps visé sans le lien de la page de course), le tableau, le ticket et son « ⋯ » — sans débrief ni
  pastille (le ressenti est à l’athlète), sans bouton rond ni feuille, avec ce qu’un coach peut faire : il ajoute, déplace, modifie, supprime des séances
  (épinglées, origine « coach ») et écrit un commentaire (« Commenter »). Il ne coche jamais une séance et n’a pas
  les options. Il voit le plan, les statuts, le ressenti (la même échelle, en lecture), les notes et le résumé de
  l’activité liée, et une sortie hors plan sur son jour (son résumé seul) ; **jamais les données de Santé**, ni le
  détail d’une activité, ni les heures occupées de l’agenda ou son adresse. Une séance allégée par la forme
  lui dit « Séance adaptée ce matin », sans la cause.
- Le lien : l’athlète touche « Partager mon plan » → un code de 8 caractères affiché une fois, valable 7 jours et
  une seule fois, stocké par son empreinte → le coach le saisit. L’athlète retire l’accès, le coach arrête de
  suivre : immédiat dans les deux sens. « Arrêter le coaching » supprime le plan, ses séances, les liens et les codes.

## Le calendrier synchronisé

`GET /coaching/cal/<jeton>.ics` : un flux iCalendar, sans session.

- Un événement par séance, des 30 derniers jours à la fin du plan : **avec une heure** (`DTSTART`/`DTEND` en UTC,
  affiché « occupé ») quand la séance a un créneau (« Mon agenda »), sinon d’une journée entière (`VALUE=DATE`,
  `TRANSP:TRANSPARENT`). L’`UID` ne change pas quand une séance reçoit ou perd son heure : le calendrier la met à
  jour sur place. `SUMMARY` « Footing 45 min » ; `DESCRIPTION` la structure, le dénivelé à viser s’il y en a un et,
  pour une séance à faire, la cible seule en une ligne quand elle a un chiffre (« Cible pendant les efforts :
  4:00–4:15 /km sur le plat, 168–177 bpm. ») ; sans chiffre, la consigne (le test de la parole) comme avant.
- `UID` stable (l’identifiant de la séance : une régénération réécrit la ligne en place), `SEQUENCE` son nombre de
  changements, `DTSTAMP` et `LAST-MODIFIED` son dernier changement : une modification arrive dans le calendrier
  abonné à son prochain rafraîchissement. Une séance supprimée ou annulée n’est plus dans le flux.
- Lignes CRLF, pliées à 75 octets, texte échappé (RFC 5545).
- La page propose trois boutons, un toucher chacun, et aucune adresse visible (propriétaire, 24 novembre 2026 : « on
  ne va pas rentrer d'URL à la main ») : **« Calendrier Apple »** (le lien `webcal://`, que l’app remet au
  système), **« Google Agenda »** et **« Outlook »**, la page « ajouter par URL » de chaque fournisseur avec l’adresse
  du flux dans le lien (`routers/coaching.feed_urls`) ; puis, discret, « Copier l’adresse » pour une autre
  application de calendrier (l’adresse est copiée sans être affichée). « Régénérer le lien » en crée un nouveau,
  l’ancien ne répond plus.

| Bouton | Lien | D’où vient le format | Rafraîchissement |
|---|---|---|---|
| Calendrier Apple | `webcal://paceforge.fr/coaching/cal/<jeton>.ics` | le schéma `webcal` ouvre l’abonnement de Calendrier (iPhone, Mac) | celui du client, qui suit à peu près `REFRESH-INTERVAL` |
| Google Agenda | `https://calendar.google.com/calendar/render?cid=<webcal://… encodé>` | non documenté par Google ; publié par Simon Willison (« Providing a "subscribe in Google Calendar" link for an ics feed », til.simonwillison.net) et repris par les widgets d’ajout au calendrier ; vérifié le 24 novembre 2026 : l’adresse renvoie vers la connexion Google en gardant `cid` | celui de Google, qui n’en publie aucun ; plusieurs sources rapportent moins d’une fois par 24 h |
| Outlook | `https://outlook.live.com/calendar/0/addfromweb/?url=<webcal://… encodé>&name=PaceForge%20%C2%B7%20Coaching` | non documenté par Microsoft (« Subscribe from web » prérempli) ; décrit par « One calendar URL, five subscribe links… » (dev.to) et repris par les widgets ; Outlook.com (comptes personnels) — un compte pro ou scolaire (`outlook.office.com`) passe par « Copier l’adresse » | celui de Microsoft : « This update can take more than 24 hours, although updates should happen approximately every 3 hours » (support Microsoft, Outlook.com ; « approximately every 6 hours » pour Outlook sur le web d’une organisation) |

  Sur le site, Google Agenda et Outlook s’ouvrent dans un nouvel onglet (`target` posé par `coaching-agenda.js`) ;
  dans l’app, un lien `https://` hors de paceforge.fr n’est jamais chargé dans la WebView : l’app le remet au
  navigateur du système (`Linking.openURL`, `onShouldStartLoadWithRequest`). Ces deux pages demandent d’être connecté
  au compte Google ou Microsoft dans ce navigateur. Rien de cela n’a été essayé avec un vrai compte.
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
prend le lien (Android sans application de calendrier qui s’y abonne), l’app le dit et « Copier l’adresse » reste.
Une app plus ancienne n’affiche pas « Calendrier Apple » : Google Agenda, Outlook et « Copier l’adresse » restent
(Réglages iOS › Calendrier › Comptes › Ajouter un calendrier avec abonnement).

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
la suppression du compte retirent adresses et heures occupées. Migration `u6e7f8a9b0c1` : `coaching_calendars.provider`
et les jetons chiffrés des comptes connectés. Le calendrier de l’iPhone (24 novembre 2026) n’a demandé aucune
migration : une source `provider = "iphone"` (la colonne est un texte), ses intervalles dans `coaching_busy`, son
état « phone » quand le téléphone refuse l’accès ; rien d’autre n’est stocké (ni nom de calendrier, ni appareil).
Migration `q8f9a0b1c2d3` :
`coaching_plans.race_name`, `race_date`, `race_km`, `race_target_s` (une course sans fichier), dans l’export.
Migration `t5d6e7f8a9b0` : `coaching_plans.fixed` (la séance fixe : jour, durée, nom), dans l’export ; ses séances
sont des lignes ordinaires de `coaching_sessions`. La lecture d’une sortie qui n’est pas la séance prévue (25
novembre 2026) n’a demandé aucune migration : la lecture et l’état d’avant des séances qu’elle a changées dans
`coaching_sessions.structure` (`answer`, `why`, `not` ; pour une sortie restée hors plan, une ligne annulée qui ne
porte que sa lecture), les jours de la semaine où le plan ne pose plus de séance fixe tout seul dans
`coaching_plans.fixed` (`declined`) ; tout sort dans l’export
avec la séance ou le plan. Tout part avec le compte (`ON DELETE CASCADE`) et sort dans l’export de Réglages
(`coaching.json`, `coaching_seances.json` : jamais le jeton du flux ni un code).

Politique de confidentialité, ligne affichée tant que la fonction n’est pas éteinte : « Coaching (bêta) » —
le plan, les séances et leur statut, le ressenti (l’effort perçu, de 1 à 5), les notes, les commentaires du coach ;
base « Contrat » ; partagé avec le coach que l’athlète choisit (la page le nomme par son rôle : « la personne avec
qui tu partages ton plan ») ; gardé jusqu’à l’arrêt du coaching ou la suppression
du compte. Une seconde ligne, « Coaching (bêta) · ta forme » : seulement si l’option est activée — la date
d’activation, les matins où l’indice de récupération de Santé est bas (leur date, jamais l’indice ni une mesure),
les séances allégées ces matins-là ; base « Consentement explicite, donné à part dans Coaching » ; jamais partagé
(le coach voit qu’une séance a été adaptée, jamais pourquoi) ; gardé jusqu’à la désactivation de l’option, le
retrait de l’accord sur les données de santé, l’arrêt du coaching ou la suppression du compte. Une troisième,
« Coaching (bêta) · ton agenda » : seulement si l’athlète connecte Google Agenda ou Outlook (nommés seulement quand
leur connecteur est configuré) ou le calendrier de son iPhone — l’accès au compte (chiffré, jamais le mot de passe),
les heures occupées des 21 prochains jours (un début et une fin, jamais un titre, une description, un lieu ni un
participant), les moments où courir, l’heure donnée à chaque séance ; le calendrier de l’iPhone lu sur le téléphone,
l’app n’envoyant que ces heures ; une adresse collée avant le 24 novembre 2026 conservée, chiffrée, jusqu’à son
retrait, et plus aucune à ajouter ; base « Contrat » ; jamais partagé (le coach voit l’heure des séances, jamais
l’agenda) ; gardé jusqu’au retrait de la source (« Retirer » efface aussitôt ses heures occupées), l’arrêt du
coaching ou la suppression du compte. La page « Confidentialité de l’app » (`/mobile/privacy`) a sa section
« Calendrier de l’iPhone » : ce qui est lu, ce qui est envoyé, la relecture à l’ouverture de Coaching (au plus toutes
les 3 heures), « Retirer », et Réglages › PaceForge › Calendriers.

## Mise en service

1. Rien à régler : sans `COACHING_BETA_USERS`, chaque compte a Coaching dès le déploiement (web et workers lisent la
   même variable).
2. L’athlète ouvre l’onglet Coaching : un plan est proposé, elle l’ajuste si elle veut et touche « Créer mon plan ».
3. Elle touche « Partager mon plan », donne le code ; le coach le saisit dans Coaching › Suivre le plan de
   quelqu’un.
4. L’onglet natif arrive avec la version 1.1.0 de l’app, publiée sur TestFlight par un commit sur `main` dont le
   message contient `[testflight]` (docs/testflight-without-expo-build-quota.md). En attendant, l’app installée
   garde la carte en haut d’Activités.
5. Pour restreindre : `COACHING_BETA_USERS=<e-mails>`. Pour tout éteindre : `COACHING_BETA_USERS=off`. Redéployer.
   Les données restent en base, invisibles ; elles partent avec « Arrêter le coaching » (une fois la fonction
   rallumée) ou avec le compte.
6. « Mon agenda » › « Calendrier de l’iPhone » demande une version native de l’app (le module `expo-calendar`, son
   texte d’autorisation, le drapeau `.pf-native-calendar`), publiée sur TestFlight comme l’onglet ; d’ici là, l’app
   installée montre la ligne « … depuis la dernière version de l’app iPhone. ». Les trois boutons de « Dans mon
   calendrier » marchent déjà dans l’app installée (le lien `webcal://` et les liens `https://` qu’elle remet au
   système). App Store : le texte d’autorisation des calendriers est dans `app.json` (plugin
   `expo-calendar`, `NSCalendarsUsageDescription` et `NSCalendarsFullAccessUsageDescription`, aucun texte pour les
   rappels) ; le manifeste de confidentialité déclare « Other Data Types » (lié au compte, fonctionnement de l’app,
   jamais de suivi) — aucune catégorie de la liste d’Apple ne nomme les calendriers — et les réponses « App Privacy »
   d’App Store Connect doivent ajouter la même ligne avant la soumission. Les athlètes qui avaient collé une adresse
   n’ont rien à faire : elle reste lue.

## Sources

Chaque référence a été ouverte le 22 novembre 2026 (sa fiche PubMed, ou le texte intégral quand c’est dit) et son
résumé lu : ce qui lui est attribué plus haut est ce qu’elle dit. La phrase qui suit chacune dit sur qui elle porte.

**La progression et les blessures**

- Frandsen J. S. B., Hulme A., Parner E. T. et al. (2025). How much running is too much? Identifying high-risk
  running sessions in a 5200-person cohort study. *British Journal of Sports Medicine*, 59(17), 1203–1210.
  [10.1136/bjsports-2024-109380](https://doi.org/10.1136/bjsports-2024-109380). Texte intégral lu. Cohorte de
  5 205 coureurs suivis 18 mois ; la règle « une séance » de PaceForge en vient.
- Buist I., Bredeweg S. W., van Mechelen W. et al. (2008). No effect of a graded training program on the number of
  running-related injuries in novice runners: a randomized controlled trial. *American Journal of Sports Medicine*,
  36(1), 33–39. [10.1177/0363546507307505](https://doi.org/10.1177/0363546507307505). Essai randomisé, 532
  débutants.
- Nielsen R. Ø., Parner E. T., Nohr E. A. et al. (2014). Excessive progression in weekly running distance and risk
  of running-related injuries: an association which varies according to type of injury. *Journal of Orthopaedic &
  Sports Physical Therapy*, 44(10), 739–747. [10.2519/jospt.2014.5164](https://doi.org/10.2519/jospt.2014.5164).
  Cohorte de 874 débutants, un an.
- Damsted C., Parner E. T., Sørensen H. et al. (2019). The association between changes in weekly running distance
  and running-related injury: preparing for a half marathon. *Journal of Orthopaedic & Sports Physical Therapy*,
  49(4), 230–238. [10.2519/jospt.2019.8541](https://doi.org/10.2519/jospt.2019.8541). Cohorte de 261 coureurs, 14
  semaines.
- Damsted C., Glad S., Nielsen R. O., Sørensen H., Malisoux L. (2018). Is there evidence for an association between
  changes in training load and running-related injuries? A systematic review. *International Journal of Sports
  Physical Therapy*, 13(6), 931–942. [PMID 30534459](https://pubmed.ncbi.nlm.nih.gov/30534459/) (pas de DOI). 4
  études retenues.
- Ristolainen L., Kettunen J. A., Waller B., Heinonen A., Kujala U. M. (2014). Training-related risk factors in the
  etiology of overuse injuries in endurance sports. *Journal of Sports Medicine and Physical Fitness*, 54(1),
  78–87. [PMID 24445548](https://pubmed.ncbi.nlm.nih.gov/24445548/) (pas de DOI). Questionnaire rétrospectif, 446
  athlètes de haut niveau (ski de fond, natation, course de fond).
- Fokkema T., van Damme A. A. D. N., Fornerod M. W. J. et al. (2020). Training for a (half-)marathon: training
  volume and longest endurance run related to performance and running injuries. *Scandinavian Journal of Medicine
  & Science in Sports*, 30(9), 1692–1704. [10.1111/sms.13725](https://doi.org/10.1111/sms.13725). 997 coureurs
  amateurs.
- Doherty C., Keogh A., Davenport J. et al. (2020). An evaluation of the training determinants of marathon
  performance: a meta-analysis with meta-regression. *Journal of Science and Medicine in Sport*, 23(2), 182–188.
  [10.1016/j.jsams.2019.09.013](https://doi.org/10.1016/j.jsams.2019.09.013). 85 études.

**Les profils : santé, maintien, niveaux** (ouvertes le 22 novembre 2026 sur PubMed, résumé lu sauf mention ;
DOI vérifiés sur Crossref)

- Bull F. C., Al-Ansari S. S., Biddle S. et al. (2020). World Health Organization 2020 guidelines on physical
  activity and sedentary behaviour. *British Journal of Sports Medicine*, 54(24), 1451–1462.
  [10.1136/bjsports-2020-102955](https://doi.org/10.1136/bjsports-2020-102955). Recommandation de l’OMS, tous les
  adultes. Texte intégral lu.
- Lee D. C., Pate R. R., Lavie C. J. et al. (2014). Leisure-time running reduces all-cause and cardiovascular
  mortality risk. *Journal of the American College of Cardiology*, 64(5), 472–481.
  [10.1016/j.jacc.2014.04.058](https://doi.org/10.1016/j.jacc.2014.04.058). Cohorte de 55 137 adultes, 15 ans.
- Pedisic Z., Shrestha N., Kovalchik S. et al. (2020). Is running associated with a lower risk of all-cause,
  cardiovascular and cancer mortality, and is the more the better? *British Journal of Sports Medicine*, 54(15),
  898–905. [10.1136/bjsports-2018-100493](https://doi.org/10.1136/bjsports-2018-100493). Méta-analyse, 232 149
  adultes.
- Schnohr P., O’Keefe J. H., Marott J. L. et al. (2015). Dose of jogging and long-term mortality. *Journal of the
  American College of Cardiology*, 65(5), 411–419.
  [10.1016/j.jacc.2014.11.023](https://doi.org/10.1016/j.jacc.2014.11.023). 1 098 joggeurs ; très peu de joggeurs
  « intenses », intervalles larges : cité pour dire ce qu’il ne montre pas.
- Arem H., Moore S. C., Patel A. et al. (2015). Leisure time physical activity and mortality: a detailed pooled
  analysis of the dose-response relationship. *JAMA Internal Medicine*, 175(6), 959.
  [10.1001/jamainternmed.2015.0533](https://doi.org/10.1001/jamainternmed.2015.0533). 661 137 adultes, toutes
  activités.
- Hickson R. C., Rosenkoetter M. A. (1981). Reduced training frequencies and maintenance of increased aerobic power.
  *Medicine & Science in Sports & Exercise*, 13(1), 13–16. [PMID 7219129](https://pubmed.ncbi.nlm.nih.gov/7219129/)
  (pas de DOI). 12 jeunes adultes, vélo et course.
- Hickson R. C., Kanakis C., Davis J. R. et al. (1982). Reduced training duration effects on aerobic power,
  endurance, and cardiac growth. *Journal of Applied Physiology*, 53(1), 225–229.
  [10.1152/jappl.1982.53.1.225](https://doi.org/10.1152/jappl.1982.53.1.225). 13 jeunes adultes.
- Hickson R. C., Foster C., Pollock M. L. et al. (1985). Reduced training intensities and loss of aerobic power,
  endurance, and cardiac growth. *Journal of Applied Physiology*, 58(2), 492–499.
  [10.1152/jappl.1985.58.2.492](https://doi.org/10.1152/jappl.1985.58.2.492). 12 jeunes adultes.
- Spiering B. A., Mujika I., Sharp M. A. et al. (2021). Maintaining physical performance: the minimal dose of
  exercise needed to preserve endurance and strength over time. *Journal of Strength & Conditioning Research*,
  35(5), 1449–1458. [10.1519/JSC.0000000000003964](https://doi.org/10.1519/JSC.0000000000003964). Revue narrative ;
  population générale (les auteurs disent les données insuffisantes chez les athlètes).
- Muniz-Pumares D., Hunter B., Meyler S. et al. (2025). The training intensity distribution of marathon runners
  across performance levels. *Sports Medicine*, 55(4), 1023–1035.
  [10.1007/s40279-024-02137-7](https://doi.org/10.1007/s40279-024-02137-7). 151 813 marathons de 119 452 coureurs,
  carnets d’entraînement ; observationnel.
- Tanda G., Knechtle B. (2015). Effects of training and anthropometric factors on marathon and 100 km ultramarathon
  race performance. *Open Access Journal of Sports Medicine*, 6, 129.
  [10.2147/OAJSM.S80637](https://doi.org/10.2147/OAJSM.S80637). Observationnel, marathon et 100 km sur route
  (Crossref donne Knechtle en premier auteur, PubMed Tanda).

- Videbæk S., Bueno A. M., Nielsen R. O. et al. (2015). Incidence of running-related injuries per 1000 h of running
  in different types of runners: a systematic review and meta-analysis. *Sports Medicine*, 45(7), 1017–1026.
  [10.1007/s40279-015-0333-8](https://doi.org/10.1007/s40279-015-0333-8). 13 études prospectives ; débutants,
  amateurs, ultra, piste.
- Kemler E., Blokland D., Backx F. et al. (2018). Differences in injury risk and characteristics of injuries between
  novice and experienced runners over a 4-year period. *The Physician and Sportsmedicine*, 46(4), 485–491.
  [10.1080/00913847.2018.1507410](https://doi.org/10.1080/00913847.2018.1507410). Enquête, 4 621 coureurs dont 1 405
  débutants ; blessures déclarées.
- van der Worp M. P., ten Haaf D. S. M., van Cingel R. et al. (2015). Injuries in runners; a systematic review on
  risk factors and sex differences. *PLOS ONE*, 10(2), e0114937.
  [10.1371/journal.pone.0114937](https://doi.org/10.1371/journal.pone.0114937). 15 études longitudinales.
- Wenger H. A., Bell G. J. (1986). The interactions of intensity, frequency and duration of exercise training in
  altering cardiorespiratory fitness. *Sports Medicine*, 3(5), 346–356.
  [10.2165/00007256-198603050-00004](https://doi.org/10.2165/00007256-198603050-00004). Revue ancienne, populations
  et protocoles mêlés.
- Milanović Z., Sporiš G., Weston M. (2015). Effectiveness of high-intensity interval training (HIT) and continuous
  endurance training for VO2max improvements: a systematic review and meta-analysis of controlled trials. *Sports
  Medicine*, 45(10), 1469–1481. [10.1007/s40279-015-0365-0](https://doi.org/10.1007/s40279-015-0365-0). 28 essais,
  723 adultes en bonne santé (25 ans en moyenne), peu de coureurs.
- Ramskov D., Rasmussen S., Sørensen H. et al. (2018). Run Clever – no difference in risk of injury when comparing
  progression in running volume and running intensity in recreational runners: a randomised trial. *BMJ Open Sport
  & Exercise Medicine*, 4(1), e000333. [10.1136/bmjsem-2017-000333](https://doi.org/10.1136/bmjsem-2017-000333).
  Essai randomisé, 447 coureurs amateurs après 8 semaines communes.
- McKay A. K. A., Stellingwerff T., Smith E. S. et al. (2022). Defining training and performance caliber: a
  participant classification framework. *International Journal of Sports Physiology and Performance*, 17(2),
  317–331. [10.1123/ijspp.2021-0451](https://doi.org/10.1123/ijspp.2021-0451). Résumé seul : les critères des six
  niveaux n’ont pas été lus et ne sont pas utilisés.

- Lenk M., Matzka M., Lauber L. et al. (2025). Impact of weekly frequency of high-intensity interval training on
  cardiorespiratory, metabolic, and performance measures in recreational runners – an exploratory study.
  *Physiological Reports*, 13(18), e70573. [10.14814/phy2.70573](https://doi.org/10.14814/phy2.70573). 26 adultes
  actifs, 6 semaines ; exploratoire.
- Gliemann L., Gunnarsson T. P., Hellsten Y. et al. (2015). 10-20-30 training increases performance and lowers
  blood pressure and VEGF in runners. *Scandinavian Journal of Medicine & Science in Sports*, 25(5).
  [10.1111/sms.12356](https://doi.org/10.1111/sms.12356). 160 coureurs de clubs (132 + 28 témoins), 8 semaines.
- Gunnarsson T. P., Bangsbo J. (2012). The 10-20-30 training concept improves performance and health profile in
  moderately trained runners. *Journal of Applied Physiology*, 113(1), 16–24.
  [10.1152/japplphysiol.00334.2012](https://doi.org/10.1152/japplphysiol.00334.2012). 18 coureurs modérément
  entraînés, 7 semaines.
- O’Donovan G., Lee I. M., Hamer M. et al. (2017). Association of “weekend warrior” and other leisure time physical
  activity patterns with risks for all-cause, cardiovascular disease, and cancer mortality. *JAMA Internal
  Medicine*, 177(3), 335. [10.1001/jamainternmed.2016.8014](https://doi.org/10.1001/jamainternmed.2016.8014).
  63 591 adultes de 40 ans et plus, toutes activités.
- Wang Z., Wang Y., Gao W. et al. (2023). Effects of tapering on performance in endurance athletes: a systematic
  review and meta-analysis. *PLOS ONE*, 18(5), e0282838.
  [10.1371/journal.pone.0282838](https://doi.org/10.1371/journal.pone.0282838). 14 études, athlètes d’endurance
  entraînés.

**La répartition, la récupération, la périodisation**

- Seiler S. (2010). What is best practice for training intensity and duration distribution in endurance athletes?
  *International Journal of Sports Physiology and Performance*, 5(3), 276–291.
  [10.1123/ijspp.5.3.276](https://doi.org/10.1123/ijspp.5.3.276). Revue ; athlètes de niveau national et
  international s’entraînant 10 à 13 fois par semaine.
- Seiler S., Tønnessen E. (2009). Intervals, thresholds, and long slow distance: the role of intensity and duration
  in endurance training. *Sportscience*, 13, 32–53. [sportsci.org/2009/ss.htm](https://www.sportsci.org/2009/ss.htm)
  (revue en ligne, relue par des pairs, sans DOI). Texte intégral lu.
- Muñoz I., Seiler S., Bautista J. et al. (2014). Does polarized training improve performance in recreational
  runners? *International Journal of Sports Physiology and Performance*, 9(2), 265–272.
  [10.1123/ijspp.2012-0350](https://doi.org/10.1123/ijspp.2012-0350). Essai randomisé, 30 coureurs amateurs.
- Rosenblat M. A., Perrotta A. S., Vicenzino B. (2019). Polarized vs. threshold training intensity distribution on
  endurance sport performance: a systematic review and meta-analysis of randomized controlled trials. *Journal of
  Strength and Conditioning Research*, 33(12), 3491–3500.
  [10.1519/JSC.0000000000002618](https://doi.org/10.1519/JSC.0000000000002618). 3 essais dans la méta-analyse, de
  qualité moyenne.
- Stanley J., Peake J. M., Buchheit M. (2013). Cardiac parasympathetic reactivation following exercise:
  implications for training prescription. *Sports Medicine*, 43(12), 1259–1277.
  [10.1007/s40279-013-0083-4](https://doi.org/10.1007/s40279-013-0083-4). Revue quantitative.
- Garber C. E., Blissmer B., Deschenes M. R. et al. (2011). Quantity and quality of exercise for developing and
  maintaining cardiorespiratory, musculoskeletal, and neuromotor fitness in apparently healthy adults: guidance
  for prescribing exercise (ACSM position stand). *Medicine & Science in Sports & Exercise*, 43(7), 1334–1359.
  [10.1249/MSS.0b013e318213fefb](https://doi.org/10.1249/MSS.0b013e318213fefb). Recommandation pour adultes en
  bonne santé.
- Foster C. (1998). Monitoring training in athletes with reference to overtraining syndrome. *Medicine & Science
  in Sports & Exercise*, 30(7), 1164–1168.
  [10.1097/00005768-199807000-00023](https://doi.org/10.1097/00005768-199807000-00023). 25 athlètes.
- Meeusen R., Duclos M., Foster C. et al. (2013). Prevention, diagnosis and treatment of the overtraining syndrome:
  joint consensus statement of the European College of Sport Science and the American College of Sports Medicine.
  *European Journal of Sport Science*, 13(1), 1–24.
  [10.1080/17461391.2012.730061](https://doi.org/10.1080/17461391.2012.730061) (aussi *Medicine & Science in
  Sports & Exercise*, 45(1), 186–205). Texte lu dans sa version d’auteur (archive HAL hal-02651426).
- Kiely J. (2012). Periodization paradigms in the 21st century: evidence-led or tradition-driven? *International
  Journal of Sports Physiology and Performance*, 7(3), 242–250.
  [10.1123/ijspp.7.3.242](https://doi.org/10.1123/ijspp.7.3.242). Revue critique.
- Bosquet L., Montpetit J., Arvisais D., Mujika I. (2007). Effects of tapering on performance: a meta-analysis.
  *Medicine & Science in Sports & Exercise*, 39(8), 1358–1365.
  [10.1249/mss.0b013e31806010e0](https://doi.org/10.1249/mss.0b013e31806010e0). 27 études.
- Mujika I., Padilla S. (2003). Scientific bases for precompetition tapering strategies. *Medicine & Science in
  Sports & Exercise*, 35(7), 1182–1187.
  [10.1249/01.MSS.0000074448.73931.11](https://doi.org/10.1249/01.MSS.0000074448.73931.11). Revue.
- Mujika I., Padilla S. (2000). Detraining: loss of training-induced physiological and performance adaptations.
  Part I: short term insufficient training stimulus. *Sports Medicine*, 30(2), 79–87.
  [10.2165/00007256-200030020-00002](https://doi.org/10.2165/00007256-200030020-00002). Revue.

**Les séances de qualité**

- Seiler S., Jøranson K., Olesen B. V., Hetlelid K. J. (2013). Adaptations to aerobic interval training:
  interactive effects of exercise intensity and total work duration. *Scandinavian Journal of Medicine & Science
  in Sports*, 23(1), 74–83. [10.1111/j.1600-0838.2011.01351.x](https://doi.org/10.1111/j.1600-0838.2011.01351.x).
  Essai randomisé, 35 cyclistes amateurs entraînés.
- Seiler S., Hetlelid K. J. (2005). The impact of rest duration on work intensity and RPE during interval training.
  *Medicine & Science in Sports & Exercise*, 37(9), 1601–1607.
  [10.1249/01.mss.0000177560.18014.d8](https://doi.org/10.1249/01.mss.0000177560.18014.d8). 9 coureurs très
  entraînés.
- Seiler S., Sjursen J. E. (2004). Effect of work duration on physiological and rating scale of perceived exertion
  responses during self-paced interval training. *Scandinavian Journal of Medicine & Science in Sports*, 14(5),
  318–325. [10.1046/j.1600-0838.2003.00353.x](https://doi.org/10.1046/j.1600-0838.2003.00353.x). 12 coureurs
  entraînés.
- Helgerud J., Høydal K., Wang E. et al. (2007). Aerobic high-intensity intervals improve VO2max more than moderate
  training. *Medicine & Science in Sports & Exercise*, 39(4), 665–671.
  [10.1249/mss.0b013e3180304570](https://doi.org/10.1249/mss.0b013e3180304570). Essai randomisé, 40 hommes
  modérément entraînés.
- Esfarjani F., Laursen P. B. (2007). Manipulating high-intensity interval training: effects on VO2max, the lactate
  threshold and 3000 m running performance in moderately trained males. *Journal of Science and Medicine in
  Sport*, 10(1), 27–35. [10.1016/j.jsams.2006.05.014](https://doi.org/10.1016/j.jsams.2006.05.014). 17 coureurs.
- Barnes K. R., Hopkins W. G., McGuigan M. R., Kilding A. E. (2013). Effects of different uphill interval-training
  programs on running economy and performance. *International Journal of Sports Physiology and Performance*, 8(6),
  639–647. [10.1123/ijspp.8.6.639](https://doi.org/10.1123/ijspp.8.6.639). 20 coureurs entraînés.
- Fradkin A. J., Zazryn T. R., Smoliga J. M. (2010). Effects of warming-up on physical performance: a systematic
  review with meta-analysis. *Journal of Strength and Conditioning Research*, 24(1), 140–148.
  [10.1519/JSC.0b013e3181c643a0](https://doi.org/10.1519/JSC.0b013e3181c643a0). 32 études, tous sports.
- Van Hooren B., Peake J. M. (2018). Do we need a cool-down after exercise? A narrative review of the
  psychophysiological effects and the effects on performance, injuries and the long-term adaptive response.
  *Sports Medicine*, 48(7), 1575–1595. [10.1007/s40279-018-0916-2](https://doi.org/10.1007/s40279-018-0916-2).
  Revue narrative.

**L’intensité et le ressenti**

- Persinger R., Foster C., Gibson M., Fater D. C. W., Porcari J. P. (2004). Consistency of the talk test for exercise
  prescription. *Medicine & Science in Sports & Exercise*, 36(9), 1632–1636.
  [PMID 15354048](https://pubmed.ncbi.nlm.nih.gov/15354048/). 16 volontaires en bonne santé.
- Foster C., Porcari J. P., Anderson J. et al. (2008). The talk test as a marker of exercise training intensity.
  *Journal of Cardiopulmonary Rehabilitation and Prevention*, 28(1), 24–30.
  [10.1097/01.HCR.0000311504.41775.78](https://doi.org/10.1097/01.HCR.0000311504.41775.78). Jeunes adultes en
  bonne santé.
- Saw A. E., Main L. C., Gastin P. B. (2016). Monitoring the athlete training response: subjective self-reported
  measures trump commonly used objective measures: a systematic review. *British Journal of Sports Medicine*,
  50(5), 281–291. [10.1136/bjsports-2015-094758](https://doi.org/10.1136/bjsports-2015-094758). 56 études.
- Foster C., Florhaug J. A., Franklin J. et al. (2001). A new approach to monitoring exercise training. *Journal of
  Strength and Conditioning Research*, 15(1), 109–115. [PMID 11708692](https://pubmed.ncbi.nlm.nih.gov/11708692/)
  (pas de DOI). Vélo et basket.

**La cible : vitesse critique, allures, FC** (ouvertes le 23 novembre 2026 : fiche PubMed et résumé lus pour
chacune ; texte intégral lu pour Smyth & Muniz-Pumares 2020, Jones & Vanhatalo 2017 et Muniz-Pumares 2025)

- Smyth B., Muniz-Pumares D. (2020). Calculation of critical speed from raw training data in recreational marathon
  runners. *Medicine & Science in Sports & Exercise*, 52(12), 2637–2645.
  [10.1249/MSS.0000000000002412](https://doi.org/10.1249/MSS.0000000000002412). Plus de 25 000 coureurs amateurs,
  carnets Strava des 16 semaines avant un marathon ; la méthode de `coaching_pace`.
- Jones A. M., Vanhatalo A. (2017). The ‘critical power’ concept: applications to sports performance with a focus
  on intermittent high-intensity exercise. *Sports Medicine*, 47(Suppl 1), 65–78.
  [10.1007/s40279-017-0688-0](https://doi.org/10.1007/s40279-017-0688-0). Revue : la puissance (vitesse) critique,
  frontière des domaines lourd et sévère.
- Hunter B., Meyler S., Maunder E., Cox T. H., Muniz-Pumares D. (2024). The relationship between the moderate–heavy
  boundary and critical speed in running. *International Journal of Sports Physiology and Performance*, 19(9),
  963–972. [10.1123/ijspp.2024-0101](https://doi.org/10.1123/ijspp.2024-0101). Méta-analyse, 26 études, 527
  participants ; le premier seuil à 82,3 % de la VC (80,6 % chez les plus lents, 84,2 % chez les plus rapides).
- Nixon R. J., Kranen S. H., Vanhatalo A., Jones A. M. (2021). Steady-state V̇O2 above MLSS: evidence that critical
  speed better represents maximal metabolic steady state in well-trained runners. *European Journal of Applied
  Physiology*, 121(11), 3133–3144. [10.1007/s00421-021-04780-8](https://doi.org/10.1007/s00421-021-04780-8). 10
  coureurs bien entraînés, sur tapis.
- Billat V., Renoux J. C., Pinoteau J., Petit B., Koralsztein J. P. (1994). Reproducibility of running time to
  exhaustion at VO2max in subelite runners. *Medicine & Science in Sports & Exercise*, 26(2), 254–257.
  [10.1249/00005768-199402000-00018](https://doi.org/10.1249/00005768-199402000-00018). 8 coureurs de bon niveau ;
  404 ± 101 s à la vitesse de V̇O2max, avec une grande variabilité individuelle (CV 25 %).
- Muniz-Pumares 2025 (déjà cité plus haut) découpe l’entraînement de 119 452 marathoniens avec ces mêmes repères :
  la VC entre ses zones 2 et 3, 82,3 % de la VC entre ses zones 1 et 2 (d’après Hunter 2024).
- Helgerud 2007 et Esfarjani 2007 (« Les séances de qualité ») : relus pour la cible — 4 min à 90–95 % de la FC max ;
  8 efforts à la vitesse de V̇O2max durant 60 % du temps qu’elle se tient.

**La forme**

- Kiviniemi A. M., Hautala A. J., Kinnunen H., Tulppo M. P. (2007). Endurance training guided individually by daily
  heart rate variability measurements. *European Journal of Applied Physiology*, 101(6), 743–751.
  [10.1007/s00421-007-0552-2](https://doi.org/10.1007/s00421-007-0552-2). Essai randomisé, 26 hommes, course.
- Vesterinen V., Nummela A., Heikura I. et al. (2016). Individual endurance training prescription with heart rate
  variability. *Medicine & Science in Sports & Exercise*, 48(7), 1347–1354.
  [10.1249/MSS.0000000000000910](https://doi.org/10.1249/MSS.0000000000000910). 40 coureurs amateurs.
- Javaloyes A., Sarabia J. M., Lamberts R. P., Moya-Ramon M. (2019). Training prescription guided by heart-rate
  variability in cycling. *International Journal of Sports Physiology and Performance*, 14(1), 23–32.
  [10.1123/ijspp.2018-0122](https://doi.org/10.1123/ijspp.2018-0122). 17 cyclistes entraînés.
- Düking P., Zinner C., Trabelsi K. et al. (2021). Monitoring and adapting endurance training on the basis of heart
  rate variability monitored by wearable technologies: a systematic review with meta-analysis. *Journal of Science
  and Medicine in Sport*, 24(11), 1180–1192.
  [10.1016/j.jsams.2021.04.012](https://doi.org/10.1016/j.jsams.2021.04.012). 8 études, 198 participants.
- Manresa-Rocamora A., Sarabia J. M., Javaloyes A., Flatt A. A., Moya-Ramón M. (2021). Heart rate
  variability-guided training for enhancing cardiac-vagal modulation, aerobic fitness, and endurance performance:
  a methodological systematic review with meta-analysis. *International Journal of Environmental Research and
  Public Health*, 18(19), 10299. [10.3390/ijerph181910299](https://doi.org/10.3390/ijerph181910299).

Les trois essais guident l’entraînement par la VFC du matin : les séances intenses les jours où elle est dans ses
valeurs habituelles, du facile ou du repos sinon. PaceForge en reprend le principe sur l’indice de Santé (VFC, FC de
nuit, sommeil), qui n’est pas le critère de ces études : le seuil reste celui de Santé, et la durée du footing, le
déplacement de la séance et la règle des trois matins sont (H) chiffre. L’indice de Santé reste une lecture
descriptive, pas une autorisation de s’entraîner : la règle ne fait qu’enlever de l’intensité, jamais en ajouter.

**Le trail et l’heure de la séance**

- Balducci P., Clémençon M., Morel B. et al. (2016). Comparison of level and graded treadmill tests to evaluate
  endurance mountain runners. *Journal of Sports Science and Medicine*, 15(2), 239–246.
  [PMID 27274660](https://pubmed.ncbi.nlm.nih.gov/27274660/) (pas de DOI). 10 coureurs de montagne de haut niveau.
- Ehrström S., Tartaruga M. P., Easthope C. S. et al. (2018). Short trail running race: beyond the classic model for
  endurance running performance. *Medicine & Science in Sports & Exercise*, 50(3), 580–588.
  [10.1249/MSS.0000000000001467](https://doi.org/10.1249/MSS.0000000000001467). 9 traileurs très entraînés.
- Chtourou H., Souissi N. (2012). The effect of training at a specific time of day: a review. *Journal of Strength
  and Conditioning Research*, 26(7), 1984–2005.
  [10.1519/JSC.0b013e31825770a7](https://doi.org/10.1519/JSC.0b013e31825770a7). Revue.

### Ce qui n’a pas d’étude

Dit sans détour, pour que cette page reste vraie :

- **Aucune étude ne fixe ces chiffres ; une étude appuie leur principe** — (H) chiffre : +10 % par semaine, la
  semaine légère sur quatre à 75 %, la durée des phases, la part et le plafond de la sortie longue, les seuils des
  séances de qualité (3 jours ; 5 jours et 4 h), le nombre de répétitions au départ, l’échauffement de 15 min, les
  bornes des zones de FC, les chiffres de l’adaptation au ressenti, la durée du footing et la règle des trois
  matins de la forme, le dénivelé à viser, le jour de repos obligatoire (l’étude citée en demanderait deux).
- **Les profils** : aucune étude n’a comparé des profils ; les seuils des niveaux (`NOVICE`, `TRAINED`), le plancher
  et la tenue du volume de « Garder la forme », ses 1 h 30, les 8 semaines faciles et les 4 jours d’un débutant, la
  séance avec du rythme dès deux séances et 75 min sont (H) chiffre ; l’absence de semaine allégée de « Garder la
  forme » et la séance de qualité unique « pour finir » n’ont **aucune étude**. Toutes ces règles enlèvent ou
  retiennent de la charge par rapport aux règles d’avant, sauf la séance avec du rythme à deux jours, qui en
  ajoute une : c’est la seule, et elle est la mieux appuyée de la revue pour un coureur qui a une habitude.
- **La séance fixe** : aucune étude n’a été trouvée sur un plan écrit autour d’une séance imposée. Le quart de sa
  durée compté en efforts durs (`FIXED_HARD_SHARE`), la moitié des répétitions des semaines légères et de
  l’affûtage (`FIXED_HALF`), l’ordre de la seconde séance (`BESIDE_FIXED`), la semaine de la course sans elle et
  1 h 15 sans historique (`FIXED_DEFAULT`) sont (H) chiffre ; pour la semaine légère, **aucune étude**.
- **Une sortie qui n’est pas la séance prévue** : aucune étude n’a été trouvée sur la façon de réécrire une semaine
  après une séance dure imprévue, ni sur la façon de reconnaître une séance dure d’après une sortie enregistrée. La
  lecture reprend les définitions du plan (le niveau « soutenu » et sa zone cardiaque, l’allure seuil de Nixon
  2021) ; sur une moyenne de sortie entière, c’est un choix pratique : une séance de club avec un long échauffement
  peut rester sous la zone 4 en moyenne (ses tours la montrent alors), une longue sortie par forte chaleur peut y
  monter (la correction le reprend). L’indice des tours (20 % plus vite, trois blocs, six tours) et le plat à 10 m de
  montée par kilomètre (`FLAT_CLIMB`) sont des choix pratiques ; sans données, le côté prudent (une sortie
  tranquille). Une séance dure applique les règles de la séance fixe pour une
  semaine (Seiler & Tønnessen 2009 pour le nombre, Seiler 2010 et Muñoz 2014 pour le budget, Stanley 2013 pour le
  lendemain) ; le quart de sa durée compté en efforts durs (`FIXED_HARD_SHARE`), l’ordre de la séance gardée
  (`BESIDE_FIXED`) et un footing « de la même durée » sont (H) ; ne rien retrancher au reste de la semaine pour le
  temps couru en plus : **aucune étude** ; l’écart de 20 % (`FIT`) et les 7 jours de la carte sont des choix
  pratiques. L’habitude qui pose la séance fixe toute seule — une sortie ce jour-là dans 5 des 8 semaines d’avant,
  dont 3 lues comme une séance dure, la durée à la médiane des sorties dures — est un choix pratique, **aucune
  étude**. Le plan décide seul, à la demande du propriétaire ; un
  lien de la carte permet toujours à l’athlète de corriger la lecture.
- **Aucune étude du tout** : le plafond hebdomadaire, le rythme d’une répétition de plus toutes les 2 semaines, les
  deux séances manquées qui allègent la semaine suivante. Le retour au calme de 10 min n’a pas de bénéfice
  démontré. Ces règles ne font que limiter ou enlever de la charge ; elles restent marquées.
- **Des transpositions** : la règle « une séance » vient d’une étude en kilomètres et s’applique ici en minutes ;
  le seuil de 4 × 8 min vient de cyclistes ; la règle du matin lit l’indice de Santé, pas la VFC seule ; plusieurs
  études portent sur des athlètes très entraînés ou sur une dizaine de personnes.
- **La cible** : les bornes 93–97 % de la VC du seuil (arrondies de Nixon 2021), la moyenne ± 1 écart-type de
  Billat 1994 prise pour tous (la vitesse des intervalles), le seuil de 3 min sous lequel la FC n’est pas une cible
  (`LAG_MIN`), l’allure du niveau reprise pour les efforts de 2 min, la descente de 1 % qui écarte un effort, le repli sans les trois distances du meilleur modèle, les
  bornes de D′ (400 m) et de la VC (1,5–6,5 m/s), le trou de 10 s et le pas de 10,5 m/s d’un flux, la tolérance de
  10 % sur les distances de Strava sont (H) chiffre. L’absence de FC le jour de la course : **aucune étude** ne donne
  une plage commune à toutes les longueurs de course. Les intervalles en côte reprennent la FC d’efforts courus sur
  le plat (Helgerud 2007) : une transposition, comme les allures de Nixon 2021 (10 coureurs bien entraînés) et de
  Billat 1994 (8 coureurs de bon niveau) appliquées à des coureurs amateurs.
- **Des choix pratiques**, qui ne sont pas des décisions d’entraînement : la semaine du lundi au dimanche,
  l’arrondi à 5 min, une allure dite à 5 s près, les records du monde qui écartent un effort, le tapis laissé de
  côté, les 24 et 12 semaines écrites, les cinq volumes de départ, la lecture du temps de course au
  quart d’heure, le plancher d’une heure, et dans l’agenda les moments, la marge de 30 min, l’ordre du placement
  et l’absence à partir de 2 jours.

Une règle nouvelle ou changée arrive avec son étude, ou porte l’une de ces marques (docs/decisions.md, 22 novembre
2026 ; `tests/test_coaching_plan.py` vérifie que chaque étude nommée dans le code est dans cette liste).
