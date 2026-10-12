# Récupération : effet cumulé des entraînements

Révision du 12 octobre 2026, modèle `cumulative-1`.

## Problème corrigé et priorités

Les séances ordinaires alimentaient Entraînement, mais pas directement la
précaution de récupération. À signaux nocturnes identiques, quatre séances
rapprochées pouvaient donc produire le même bilan qu'une période légère.

1. Intégrer leur accumulation et leur diminution sur plusieurs jours.
2. Comparer les efforts au propre historique de l'athlète ; séparer la course
   et la musculation du seul coût cardiaque.
3. Empêcher les doublons, données futures et changements de couverture FC de
   créer artificiellement une baisse de récupération.
4. Conserver les trois cadrans de même taille. Un seul résumé « Effort
   accumulé » apparaît dans le détail Récupération ; les limites restent dans
   « Mesures utilisées » et l'aide.

## Appui scientifique et portée

La [revue narrative de Rebelo et al., 2026](https://doi.org/10.1007/s40279-026-02417-4)
distingue charge imposée, état de l'athlète et réponse à l'entraînement.
Elle soutient les références individuelles et la lecture de plusieurs domaines,
sans fournir une formule validée de récupération universelle.

L'[étude de récupération après musculation, 2019](https://doi.org/10.3390/sports7100225)
ne permet pas d'assimiler l'évolution de la VFC à celle de la fonction
neuromusculaire. La [revue trail de 2026](https://doi.org/10.3390/muscles5010009)
décrit notamment les contraintes excentriques des descentes. La durée de course
ou de musculation fournit un contexte d'exposition, pas la mesure de ces dommages.
Le D+ reçu ne devient donc pas un D− inventé ; les kilos soulevés, répétitions,
courbatures et pertes de force ne sont pas déduits du pouls.

Les [limites des modèles forme-fatigue](https://doi.org/10.1186/s40798-022-00426-x)
interdisent de présenter leurs constantes comme des délais individuels établis.
Ici, décroissance, seuils de disponibilité et conversion en limite du score sont
des hypothèses explicites de produit. Aucune de ces publications ne valide leurs
valeurs numériques ni le score PaceForge.

## Calcul livré

Le bilan lit les activités terminées avant le réveil sélectionné. Sans horaire
de réveil, il s'arrête à minuit. Quand le fuseau nocturne est connu, les fins
d'activités sont comparées en UTC ; les anciennes données sans fuseau utilisent
leur heure locale. Une activité toujours en cours ou terminée ensuite ne
modifie pas rétroactivement ce matin. La précaution existante pour un effort
exceptionnel reçu dans la journée est conservée.

Quatre expositions restent séparées dans le calcul : charge cardiaque d'endurance,
durée totale, durée de course, durée de musculation. Les copies d'activités
sont éliminées par le chargeur commun ; un même identifiant ne compte qu'une fois.
Les sports sont classés d'après le type réellement reçu, pas le nom de l'activité.

Chaque contribution décroît exponentiellement depuis la fin de l'activité.
Constantes : 2 jours pour la charge cardiaque et la durée totale, 3 jours pour
les volumes course/musculation. La queue, négligeable, est progressivement
retirée entre 27 et 28 jours. Ce ne sont pas des promesses de récupération
en 2 ou 3 jours. Aucune bonne nuit ne remet ce cumul à zéro.

La référence comprend les 42 jours précédant les sept derniers jours, avec
14 jours d'amorçage. Elle exige au moins 28 jours exploitables, huit activités
et quatre semaines actives, pour chaque domaine. Un domaine insuffisamment
documenté n'applique aucune limite ; « Repères en cours » reste visible.
Les repères cardiaques sont ceux disponibles avant les sept derniers jours,
appliqués aux deux périodes pour conserver les mêmes unités.

La comparaison utilise le troisième quartile des expositions de référence.
L'excès est divisé par le maximum entre ce quartile et la dose médiane d'une
activité, ce qui évite un dénominateur presque nul dans les historiques peu actifs.
La limite vaut `100 − A × (1 − exp(−excès))`. `A=40` pour la charge cardiaque,
`A=20` pour les durées seules. Les diminutions inférieures à un demi-point ne
créent pas de précaution. Le signal de charge ne peut donc pas créer seul un
état rouge. Ces bornes limitent l'influence d'une estimation incertaine ; elles
ne signifient pas qu'une fatigue sévère serait impossible après l'entraînement.

La FC doit couvrir au moins 80 % des minutes de référence des sports où elle
est exploitable. Sinon, seuls les volumes interviennent. Pour la période
récente, la charge cardiaque réellement enregistrée reste une borne basse :
ajouter un entraînement sans FC ne doit jamais effacer une précaution existante.
Sous 80 % de couverture récente, l'intensité est indiquée partielle. La
musculation reste hors de cette comparaison cardiaque. Une nouvelle ceinture,
une FC interrompue ou la musculation à faible pouls ne sont ainsi pas confondus
avec un changement mesuré de charge cardiaque. La durée seule reste une
approximation : deux séances de musculation aussi longues peuvent être très
différentes.

Le score nocturne garde ses composantes et ses limites existantes. La limite
cumulative peut le réduire même lorsque les signaux nocturnes sont habituels.
Les limites ne sont pas additionnées : le minimum s'applique, pour éviter
d'empiler plusieurs pénalités corrélées. Sans nouvelle composante nocturne
exploitable, aucun score n'est créé à partir des activités seules.

L'absence d'activité importée ne prouve jamais un repos. Un volume habituel ne
prouve pas non plus qu'il est bien toléré. Le modèle ne diagnostique ni fatigue
musculaire, ni surentraînement, ni risque de blessure. Le poids et le lactate
n'y entrent pas.

## Vérification et validation

Les scénarios logiciels couvrent : séances ordinaires rapprochées avec les
mêmes nuits, diminution après arrêt, absence de nuit, historique court,
FC manquante ou nouvellement enregistrée, musculation, vélo, doublons,
franchissement de minuit, fuseaux, activités futures et cumul des précautions.
L'ordre « plusieurs efforts > un seul » et la diminution restent vérifiés
avec des constantes de 1,5 à 4 jours ; cela ne valide pas la valeur des scores.

Contrôles de cette livraison : 962 tests serveur passent, dont 18 scénarios
spécifiques au cumul. Les parcours de rafraîchissement, défilement, sélection,
pagination et erreurs passent dans Chromium (320/393 px) et WebKit (393 px).
Les trois cadrans et l'absence de débordement sont vérifiés à 320/393/1024 px.
Ces vérifications utilisent des données synthétiques ; aucun test sur l'iPhone
physique ni aucune mesure de fatigue réelle n'est revendiqué.

L'historique est recalculé avec les données actuellement enregistrées et le
modèle actuel, sans événement daté après le bilan. Un import tardif peut le
modifier : il ne s'agit pas de prédictions conservées à l'époque.

La validation physiologique exige un suivi prospectif distinct : figer version
et bilan avant les efforts, recueillir un résultat indépendant (performance
sous-maximale standardisée, fonction neuromusculaire ou ressenti facultatif),
prévoir les critères avant analyse, puis tester sur une période ultérieure et
sur d'autres athlètes/appareils. Comparer ce modèle au modèle nocturne seul,
publier les erreurs et les faux signaux, et analyser les données manquantes.
Les tests et les exemples synthétiques livrés ici ne remplacent pas ce suivi.
