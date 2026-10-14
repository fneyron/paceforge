# Estimations Santé — révision du 14 octobre 2026

Cette révision remplace les formules du 10 et du 12 octobre : base de sommeil apprise, besoin
augmenté par l'effort et la dette, score pondéré VFC 25 · FC 25 · sommeil 30 avec ses limites,
précaution cumulative `cumulative-1`. Leurs pondérations et leurs limites n'avaient pas de base
validée. Le modèle retenu (C1–C8 de l'audit) reprend des lectures publiées. Chaque seuil restant
est un choix de produit, marqué **(H)** dans le code et les tests.

Rien ici n'est une validation clinique. Les nombres décrivent ce que la page calcule. Les
simulations (avant-dernière section) décrivent son comportement sur des données synthétiques.

Code : `app/services/nights.py` (C2–C4), `sante_sleep.py` (C5), `sante_training.py` et
`sante_load.py` (C6, C7), `sante_score.py` et `sante_today.py` (C8), `sante.py` (la page).

## C2 — Valeurs habituelles

- **Fenêtre décalée.** Les valeurs habituelles d'un jour J portent sur les nuits de J−67 à J−8.
  Les 7 nuits que la tendance juge n'y entrent jamais : une semaine n'est plus comparée à une
  moyenne qui la contient (Plews 2013a ; Buchheit 2014). Avant, la référence allait jusqu'à la
  dernière nuit incluse.
- **Nombre de nuits.** Référence complète dès 14 nuits mesurées dans cette fenêtre. De 7 à 13
  nuits, une référence « provisoire » est tracée en pointillés. Elle n'alimente jamais l'état du
  jour **(H)**.
- **Centre.** Moyenne du ln RMSSD pour la VFC (moyenne géométrique). Médiane pour la FC de nuit
  et la respiration.
- **Dispersion.** σ̂ = √((n·s² + 7·σ0²)/(n + 7)) : l'écart-type de l'athlète tiré vers un a priori,
  comme une plage de référence individualisée (Hecksteden 2017 ; Kellmann 2018). s est l'écart-type
  (VFC) ou 1,4826 × MAD (FC, respiration). σ0 **(H)** : 0,10 en ln pour la VFC (CV nocturne de
  9,5 à 12,4 %, Mishica 2022 ; environ 12 %, Buchheit 2014), 4 % de la médiane pour la FC
  (Mishica 2022), 0,5 /min pour la respiration (Miller 2020). Le SDNN d'Apple n'a pas d'a priori :
  il garde sa propre référence, sans conversion, et l'état ne le lit jamais.
- **Série.** Une référence par série, c'est-à-dire par source et méthode (Dial 2025). La série lue
  est celle de l'appareil qui a le plus de nuits mesurées sur 28 jours, parmi ceux portés ces
  7 derniers jours **(H)**. Deux montres portées en alternance ne changent plus de série chaque
  nuit.

## C3 — Tendances sur 7 jours

- Moyenne des nuits mesurées des 7 derniers jours sur la série (géométrique pour la VFC).
- Exige la dernière nuit mesurée et au moins 3 nuits sur 7 (Plews 2014).
- z = (moyenne − centre)/σ̂. « Hors de tes valeurs habituelles » dès |z| ≥ T, avec
  T = max(0,5 ; 1,28/√n7). 0,5 σ̂ est le plus petit changement utile (Plews 2013a et 2013b ;
  Buchheit 2014). Le terme 1,28/√n7 élargit le seuil quand la semaine a peu de nuits : l'erreur
  d'une moyenne de n nuits vaut σ/√n, et 1,28 correspond à un test unilatéral à 90 %.
- La bande tracée sur les cartes vaut centre ± T·σ̂ : une moyenne dessinée dedans se lit « dans
  tes valeurs habituelles ».

## C4 — Signaux aigus

- **Alerte FC de nuit (rouge).** Deux nuits de suite sur la série, sans contexte connu (effort,
  décalage horaire, altitude…), chacune ≥ médiane + max(2 σ̂ ; 5 bpm) d'une référence complète
  **(H)**. Alavi 2022 rapporte + 4 bpm sur 2 nuits avant des symptômes. L'alerte est spécifique,
  pas sensible : un graphique calme ne prouve pas la bonne santé.
- **Respiration (orange).** Moyenne des deux dernières nuits ≥ médiane + max(1 /min ; 2 σ̂) d'une
  référence complète **(H)**. Miller 2020 rapporte un écart nocturne typique de 0,5 /min.

## C5 — Sommeil

- **Besoin N = 8 h pour tous.** Il n'est jamais demandé, jamais appris des nuits de l'athlète,
  jamais augmenté par un effort ou un manque. C'est le milieu de 7 à 9 h (Watson 2015a ;
  Hirshkowitz 2015). Un besoin réglable de 7 à 9 h dans Réglages reste une proposition.
- **Cadran Sommeil** = min(100 % ; TST24/N). TST24 couvre les 24 h avant le réveil, siestes
  comprises, chacune comptée une fois. Mots : « suffisant » dès 7 h, « un peu court » de 6 à 7 h,
  « court » sous 6 h. Moins de 6 h est une raison orange de l'état du jour (Craven 2022 :
  performance réduite après une privation aiguë).
- **Manque sur 7 jours.** D7 = max(0 ; (7/n) · Σ(N − TST24)) sur les n nuits mesurées des
  7 derniers matins. Une nuit non mesurée ne vaut jamais 0 h. Il n'est calculé qu'avec au moins
  4 nuits sur 7 **(H)** (Walsh 2021). Arrondi à 10 min, il s'affiche à part et ne s'ajoute
  jamais à N. Van Dongen 2003 montre que la dette d'une semaine de nuits courtes s'accumule ;
  Kitamura 2016, qu'il faut plusieurs jours pour la rembourser. Une nuit passée à courir ne
  compte que ses siestes.

## C6 — Charge d'entraînement : un seul calcul

Détails et raisons du retrait de `cumulative-1` : [recovery-cumulative.md](recovery-cumulative.md).

- **Charge d'une séance L.** Ses minutes, tour par tour (sinon par kilomètre), pondérées par
  x·0,64·e^(1,92·x), x étant la part de la réserve cardiaque (Morton, Fitz-Clarke et Banister
  1990). Constantes féminines 0,86 et 1,67 dès que le sexe sera connu. Il n'y a pas encore de
  champ : les constantes masculines s'appliquent par défaut.
- **Bornes.** Les mêmes pour toutes les séances de toutes les vues, celles du jour. FC de repos :
  médiane des FC de nuit sur 60 jours, sinon 50. FC max : 98e centile des pics sur 12 mois,
  sinon 190.
- **Séance estimée.** Sans FC lisible, et toujours pour la musculation : sa durée × la charge par
  minute médiane de sa famille de sports sur 12 mois (5 séances lues au moins), sinon de toutes les
  séances, sinon 1,2 **(H)**.
- **Charge du jour D.** Σ L des séances dédoublonnées, sur le jour local où chacune s'est terminée.
- **Charge accumulée.** Moyennes exponentielles A (τ = 7 j) et C (τ = 42 j). Toutes deux partent
  de la moyenne de D sur les 28 premiers jours, avec 180 jours de chauffe avant les 90 jours
  tracés. Démarrée à 0, la courbe « habituelle » lisait 24 % trop bas au 56e jour. Ce sont des
  constantes de lissage, pas des délais de récupération (Busso 2003 ; Williams 2017 ; Imbach 2022).
- **Entraînement %.** Σ D des 7 jours ÷ Σ D moyenne par semaine des 11 semaines précédentes, avec
  au moins 4 semaines actives. Le ratio est découplé : la semaine en cours reste hors de sa
  référence (Lolli 2019 ; Impellizzeri 2020). « Comme d'habitude » entre le 25e et le 75e centile
  de ces 11 semaines (avant : ± 20 %).
- **Activités 7 j / 28 j.** Les mêmes séances et la même charge D. Les kilomètres sont donnés par
  sport ; ceux affichés en tête sont ceux de la course à pied.
- **Récupération.** Aucune limite liée à la charge. L'Entraînement % s'affiche comme contexte.

## C7 — Fenêtres après un gros effort

Classes d'après la durée propre de l'effort, arrêts compris : longue ≥ 3 h (ou ≥ 1 500 m D+ à
pied), très longue ≥ 6 h, ultra ≥ 10 h ; un sport sans impact descend d'une classe **(H)**.
Le jour J0 de l'effort est le jour de sa fin locale, ou la veille s'il finit avant 6 h.

| Effort | Rouge | Orange jusqu'à | Appui |
| --- | --- | --- | --- |
| Longue | — | J+1 | **(H)** |
| Longue courue comme une course | — | J+2 | Petersen 2007 : puissance encore −18 % à J+2 |
| Très longue | — | J+3 | **(H)**, entre le marathon et l'ultra |
| Ultra | J0 et J+1 | J+5 | Fazackerley 2019 ; Hoffman 2017 |
| Ultra de 24 h ou plus | J0 et J+1 | J+7 | **(H)** |

Texte : « fenêtre indicative jusqu'à J+n ». Aucune date de récupération n'est promise. Quand deux
efforts sont proches, la fenêtre la plus forte nomme le jour.

## C8 — État du jour et indice nocturne

- **Conditions.** La dernière nuit est mesurée ; au moins une série cardiaque a une référence
  complète ; la semaine compte au moins 3 nuits. Sinon, la page dit pourquoi, sans nombre
  (« Pas de score ce matin : repères en construction (n/14). »). Jamais d'état à partir du seul
  sommeil ou des seules activités.
- **Marqueur principal.** La VFC (RMSSD), sinon la FC de nuit.
- **Rouge.** L'alerte FC de nuit, ou J0 et J+1 après un ultra.
- **Orange**, chaque raison listée. Tendance du marqueur principal hors du seuil T (VFC en
  dessous, FC au-dessus) ; respiration ; moins de 6 h sur 24 h ; fenêtre d'effort ouverte.
- Une FC de nuit hors de ses valeurs alors que la VFC y reste s'affiche sur sa seule ligne.
- **Indice nocturne**, le nombre du cadran : 100 × la moyenne, sur les séries utilisées, de
  s_VFC = clip((z + 2,5)/2) et s_FC = clip((2,5 − z)/2) **(H)**. Il vaut 100 jusqu'au plus petit
  changement utile et 0 à 2,5 σ. Il est ramené à 69 au plus en orange, 39 au plus en rouge, 70 au
  moins en vert **(H)** : le nombre ne contredit jamais l'état. Ce n'est pas un pourcentage de
  récupération.
- L'historique de 14 jours recalcule chaque jour avec ce qu'il savait alors, sans donnée datée
  après lui.

## FC à allure comparable (Forme, Activités)

Les kilomètres d'une sortie facile et plate, après ses 10 premières minutes, dont la pente reste
dans ± 2 % ; sinon la sortie entière, faute de découpage utilisable (Vesterinen 2014). Les
sorties chaudes (25 °C ressentis ou plus) restent hors de la référence **(H)**.

## Graphiques

- Les points et barres correspondent uniquement aux mesures reçues.
- Les moyennes sur sept jours restent tracées avec au moins trois nuits mesurées de la même
  série, même si la nuit du jour manque. La VFC garde sa moyenne géométrique.
- La ligne s'interrompt quand la fenêtre manque de mesures ou quand la série change.
- Les graphiques quotidiens ajoutent une médiane des sept jours écoulés, avec au moins quatre
  jours de la même source. Ce n'est ni un objectif, ni une composante de récupération.

## Simulations (données synthétiques, 14 octobre 2026)

Scripts hors dépôt, mêmes générateurs avant et après ; « avant » désigne le commit `0f276af`.

- **Athlète stable, port complet** (40 × 110 matins ; ln RMSSD σ 0,11, AR(1) 0,3 ; FC σ 1,9 bpm).
  Matins orange : 9,9 % avant, 22,1 % après (raison VFC 16,7 %, sommeil < 6 h 6,7 %). VFC
  « en dessous » : 13,2 % avant, 16,7 % après. Ce taux reste proche quel que soit le nombre de
  nuits de la semaine (13,5 à 18 % de 3 à 6 nuits). C'est le prix d'un seuil à 0,5 σ sur une
  moyenne de 7 nuits autocorrélées, comparée à une référence qui ne la contient plus.
- **Port de 50 %.** État disponible 45 % des matins (51 % avant) ; 21,3 % de matins orange.
- **Vraie baisse de la VFC de 10 % (1 σ) avec FC + 3 bpm**, port complet. Matins orange ou rouge
  à J0, J+2, J+4, J+6 : 26, 43, 68 et 81 % après, contre 0, 4, 10 et 33 % avant.
- **Sommeil, dormeur de 6 h 45.** Cadran médian 85 % (77 % avant, quand le besoin montait à
  8 h 50 avec la dette). Manque sur 7 jours médian 8 h 30 pour un manque réel de 8 h 33 en port
  complet, 8 h 10 avec 50 % de port au hasard, 6 h 40 si les nuits courtes sont moins portées
  (biais inévitable). Avant, la dette plafonnait à 60 min.
- **Charge.** Voir [recovery-cumulative.md](recovery-cumulative.md).

## Sources

- Plews 2013a — [Evaluating training adaptation with heart-rate measures](https://doi.org/10.1123/ijspp.8.6.688), IJSPP 8:688–691.
- Plews 2013b — [Training adaptation and heart rate variability in elite endurance athletes](https://doi.org/10.1007/s40279-013-0071-8), Sports Med 43:773–781.
- Plews 2014 — [How much compliance is needed for valid assessment?](https://doi.org/10.1123/ijspp.2013-0455), IJSPP 9:783–790.
- Buchheit 2014 — [Monitoring training status with HR measures](https://doi.org/10.3389/fphys.2014.00073), Front Physiol 5:73.
- Vesterinen 2016 — [Individual endurance training prescription with HRV](https://doi.org/10.1249/MSS.0000000000000910), MSSE 48:1347–1354.
- Javaloyes 2019 — [Training prescription guided by HRV in cycling](https://doi.org/10.1123/ijspp.2018-0122), IJSPP 14:23–32.
- Nuuttila 2022 — [Individualized endurance training based on recovery and training status](https://pmc.ncbi.nlm.nih.gov/articles/PMC9473708/), MSSE 54:1690–1701.
- Hecksteden 2017 — [A new method to individualize monitoring of muscle recovery](https://doi.org/10.1123/ijspp.2016-0120), IJSPP 12:1137–1142.
- Kellmann 2018 — [Recovery and performance in sport: consensus statement](https://doi.org/10.1123/ijspp.2017-0759), IJSPP 13:240–245.
- Mishica 2022 — [Nocturnal vs. morning measures of heart rate indices in young athletes](https://doi.org/10.1371/journal.pone.0262333), PLoS One 17:e0262333.
- Miller 2020 — [Changes in respiratory rate to predict the risk of COVID-19](https://pmc.ncbi.nlm.nih.gov/articles/PMC7728254/), PLoS One.
- Alavi 2022 — [Real-time alerting system for COVID-19 using wearable data](https://doi.org/10.1038/s41591-021-01593-2), Nat Med.
- Dial 2025 — [Validation of nocturnal resting HR and HRV in consumer wearables](https://doi.org/10.14814/phy2.70527), Physiol Rep.
- Doherty 2025 — [Readiness, recovery, and strain: composite health scores](https://doi.org/10.1515/teb-2025-0001), Transl Exerc Biomed.
- Watson 2015a — [Recommended amount of sleep for a healthy adult](https://doi.org/10.5665/sleep.4716), Sleep.
- Hirshkowitz 2015 — [National Sleep Foundation's sleep time duration recommendations](https://doi.org/10.1016/j.sleh.2014.12.010), Sleep Health.
- Craven 2022 — [Effects of acute sleep loss on physical performance](https://doi.org/10.1007/s40279-022-01706-y), Sports Med.
- Walsh 2021 — [Sleep and the athlete: 2021 expert consensus recommendations](https://doi.org/10.1136/bjsports-2020-102025), BJSM.
- Van Dongen 2003 — [The cumulative cost of additional wakefulness](https://pubmed.ncbi.nlm.nih.gov/12683469/), Sleep.
- Kitamura 2016 — [Estimating individual optimal sleep duration and potential sleep debt](https://pmc.ncbi.nlm.nih.gov/articles/PMC5075948/), Sci Rep.
- Morton, Fitz-Clarke et Banister 1990 — [Modeling human performance in running](https://pubmed.ncbi.nlm.nih.gov/2246166/), J Appl Physiol.
- Busso 2003 — [Variable dose-response relationship between exercise training and performance](https://doi.org/10.1249/01.MSS.0000074465.13621.37), MSSE.
- Williams 2017 — [Better way to determine the acute:chronic workload ratio?](https://doi.org/10.1136/bjsports-2016-096589), BJSM.
- Imbach 2022 — [The use of fitness-fatigue models for sport performance modelling](https://doi.org/10.1186/s40798-022-00426-x), Sports Med Open.
- Lolli 2019 — [Mathematical coupling causes spurious correlation within the ACWR](https://doi.org/10.1136/bjsports-2017-098110), BJSM.
- Impellizzeri 2019 — [Internal and external training load: 15 years on](https://doi.org/10.1123/ijspp.2018-0935), IJSPP.
- Impellizzeri 2020 — [Acute:chronic workload ratio: conceptual issues and fundamental pitfalls](https://doi.org/10.1123/ijspp.2019-0864), IJSPP 15:907–913.
- Petersen 2007 — [Muscle mechanical characteristics in fatigue and recovery from a marathon](https://doi.org/10.1007/s00421-007-0504-x), Eur J Appl Physiol.
- Fazackerley 2019 — [The effect of an ultra-endurance running race on HRV](https://pubmed.ncbi.nlm.nih.gov/31321510/), Eur J Appl Physiol 119:2001–2009.
- Hoffman 2017 — [Determinants of recovery from a 161-km ultramarathon](https://doi.org/10.1080/02640414.2016.1183808), J Sports Sci.
- Vesterinen 2014 — [Heart rate–running speed index](https://doi.org/10.1519/JSC.0000000000000349), J Strength Cond Res.

Ces sources motivent les règles. Elles ne valident ni les seuils (H), ni l'indice nocturne, ni
leur combinaison propre à PaceForge. Les tests vérifient la cohérence logicielle, les transitions,
l'absence de fuite temporelle et la séparation des sources.
