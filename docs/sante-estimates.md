# Estimations Santé — 10 octobre 2026

Les trois anneaux restent des repères de suivi, avec leurs définitions accessibles au clic.
Cette révision conserve la séparation des sources, les graphiques quotidiens visibles et
l'absence de nouveau score sans une composante nocturne exploitable réellement mesurée ce jour-là.
Elle ne constitue pas une validation clinique ou une calibration sur des résultats de récupération mesurés.

## Base de sommeil

La base initiale est de 8 h. L'adaptation demande au moins 14 nuits comparables réparties
sur 21 jours, parmi les 60 jours précédant le bilan, avec la même source de sommeil.
La nuit évaluée et les nuits futures ne déterminent jamais la durée de sa propre cible.
Un changement de montre reprend la référence de cette montre, sans fusionner les historiques.

Les nuits de moins de 7 h ou de plus de 10 h sur 24 h, les contextes perturbés connus
(effort important, décalage horaire, maladie signalée, etc.) et les nuits dont les signaux
cardiaques ou respiratoires sont anormaux selon une référence antérieure établie sont exclus
de cet apprentissage. Elles restent visibles dans les graphiques et comptent dans le suivi.
L'annotation rétrospective d'un épisode d'alerte n'intervient pas dans cette sélection :
les mesures et références connues ce jour-là décident, pour conserver un historique cohérent.
Les siestes utilisent l'attribution existante au réveil : elles ne sont comptées qu'une fois.

La cible est le troisième quartile des durées retenues, borné à 7–9 h, puis rapproché de
8 h avec un poids `n / (n + 14)`, arrondi à 10 minutes. Le choix du quartile, les seuils,
les bornes et ce poids sont des heuristiques de produit. Même un historique long ne mesure
pas le besoin biologique : des nuits apparemment normales peuvent rester insuffisantes.
L'interface indique soit « apprentissage en cours », soit « base personnelle estimée ».

Le besoin du jour ajoute à cette base l'effort et une part du manque de sommeil estimé.
La dette utilise la base que chaque jour pouvait connaître, sans les nuits futures, et
sans réintroduire la dette dans le calcul de la dette. Les jours manquants ne valent pas zéro.

## Précautions de récupération

Le score pondéré conserve ses composantes VFC, FC nocturne et sommeil. Les limites
cardiaques et respiratoires restent en place.

La limite liée au sommeil devient continue, relative à la base estimée (sans dette) :
0 à 0 h, 40 à la moitié de la base, 65 aux trois quarts, puis 100 aux sept huitièmes
avec un minimum de 7 h. Interpolation linéaire entre ces points. La limite redondante
de 69 liée à la couleur rouge du sommeil est supprimée. À base de 8 h, 5 h 59 et
6 h 01 ne provoquent donc plus un saut à travers le seuil de 6 h.

Les catégories et fenêtres de prudence après effort sont conservées. Leurs anciens
paliers deviennent des points d'interpolation : ultra ordinaire, 35 à J+1, 65 à J+4,
100 à J+11 ; ultra traversant la nuit ou durant au moins 24 h, dernier point à J+14.
Pour une très longue sortie : 45 à J+1, 65 à J+3, 100 à J+6.
Pour une longue sortie : 65 à J+1 puis 100 à J+4, ou J+6 si effort de course.
J+0 conserve la précaution de J+1. Si plusieurs efforts se chevauchent, la limite
effective la plus basse est retenue. Aucun compte à rebours ne promet une date de récupération.
Ces coefficients restent des choix prudents de produit, sans précision médicale démontrée.

Un nouveau calcul peut augmenter **ou diminuer** un résultat antérieur. Les résultats
historiques affichés sont recalculés avec la version actuelle du modèle, sans données futures.

## Graphiques

- Les points et barres correspondent uniquement aux mesures reçues.
- Les moyennes sur sept jours calendaires restent tracées avec au moins trois nuits
  mesurées de la même source, même si la nuit du jour manque. La VFC conserve sa moyenne géométrique.
- La ligne s'interrompt quand la fenêtre manque de mesures ou quand la source change.
  Une moyenne isolée reste visible sous forme de court trait, jamais comme une fausse mesure.
- La sélection donne la moyenne et son nombre de nuits, distincts de la mesure de la nuit.
- Les graphiques quotidiens ajoutent une médiane des sept jours **écoulés**, avec au moins
  quatre jours de la même source. Le jour en cours est exclu, les zéros mesurés sont conservés.
  Cette référence est moins sensible à un jour de course extrême qu'une moyenne ; ce n'est
  ni un objectif, ni une composante de récupération. Les anciennes données restent datées.

## Sources et limites

- [Consensus AASM/SRS sur la durée de sommeil](https://aasm.org/resources/pdf/adultsleepdurationconsensus.pdf) :
  au moins 7 h régulièrement chez l'adulte, variations individuelles ; aucune formule personnelle.
- [Sargent et al., 2021](https://doi.org/10.1123/ijspp.2020-0896) : la durée habituelle
  peut être inférieure au besoin ressenti chez les athlètes ; elle ne prouve pas le besoin biologique.
- [Fazackerley et al., 2019](https://pubmed.ncbi.nlm.nih.gov/31321510/) : les signaux
  autonomes et les symptômes après ultra n'évoluent pas nécessairement au même rythme.
- [Explication officielle WHOOP du besoin de sommeil](https://www.whoop.com/us/en/thelocker/how-much-sleep-do-i-need/) :
  référence de présentation (base, effort, dette, siestes), pas une formule publiée à reproduire.

Ces sources motivent les précautions et la présentation. Elles ne valident pas les seuils,
pondérations ou pourcentages propres à PaceForge. Les tests vérifient la cohérence logicielle,
les transitions, l'absence de fuite temporelle et la séparation des sources.
