# La charge d'entraînement : un seul calcul, en contexte de la récupération

Révision du 14 octobre 2026. Elle remplace le modèle `cumulative-1` du 12 octobre, qui limitait
le score de récupération d'après l'accumulation des séances ordinaires.

## Ce qui change et pourquoi

`cumulative-1` ajoutait au score nocturne une limite tirée de quatre expositions (charge
cardiaque, durée totale, course, musculation), avec des décroissances de 2 et 3 jours, un troisième
quartile de référence et une limite `100 − A × (1 − exp(−excès))`. Il est retiré pour trois raisons.

1. **Aucune base validée.** Rien ne relie une charge accumulée à une limite de récupération. Les
   constantes des modèles forme-fatigue sont des constantes de lissage, pas des délais individuels
   (Imbach 2022). Les ratios de charge ont des écueils de construction connus (Lolli 2019 ;
   Impellizzeri 2020).
2. **Deux fois la même séance.** La lecture validée de la récupération est la réponse à
   l'entraînement, pas la dose : la tendance nocturne de la VFC et de la FC (Plews 2013b ;
   Vesterinen 2016 ; Javaloyes 2019 ; Nuuttila 2022). Une séance qui fatigue se lit déjà dans la
   nuit. L'ajouter comme limite la comptait deux fois.
3. **Quatre calculs qui ne s'accordaient pas.** Le cadran Entraînement, la Charge accumulée, la
   limite cumulative et les 7 / 28 jours d'Activités lisaient la charge de quatre façons : bornes
   cardiaques, décroissances et doublons différents.

La récupération ne lit donc plus la charge. Le pourcentage du cadran Entraînement s'affiche à côté,
comme contexte. Les gros efforts gardent leurs fenêtres (estimations, C7), une raison de l'état du
jour qui dit « fenêtre indicative jusqu'à J+n ».

## Le calcul unique (`app/services/sante_load.py`)

- **Charge d'une séance L.** Minutes pondérées par x·0,64·e^(1,92·x), x étant la part de la réserve
  cardiaque, tour par tour, sinon par kilomètre (Morton, Fitz-Clarke et Banister 1990). C'est une
  charge interne (Impellizzeri 2019) : une sortie à vélo et une course à la même FC pèsent autant.
- **Bornes.** Les mêmes pour toutes les séances de toutes les vues, celles du jour : FC de repos
  = médiane des FC de nuit sur 60 jours (sinon 50) ; FC max = 98e centile des pics sur 12 mois
  (sinon 190).
- **Séance estimée.** Sans FC lisible, et toujours pour la musculation, la séance compte sa durée
  × la charge par minute médiane de sa famille (course, vélo, ou son sport) sur 12 mois, 5 séances
  lues au moins ; sinon de toutes les séances lues ; sinon 1,2 **(H)**. Ajouter un capteur ne
  fait donc pas croire à plus d'entraînement dès que 5 séances sont lues : les semaines d'avant
  empruntent leur médiane.
- **Charge du jour D.** Σ L des séances dédoublonnées (un même identifiant une seule fois, une
  durée invalide écartée), sur le jour local où chacune s'est terminée. Aucune séance future.
- **Charge accumulée.** A et C, moyennes exponentielles de τ = 7 et 42 jours : chaque jour,
  x ← x + α·(D − x) avec α = 1 − e^(−1/τ), la décroissance du modèle forme-fatigue (Busso 2003 ;
  la moyenne de Williams 2017 utilise λ = 2/(N + 1)). Initialisées à la moyenne de D sur les
  28 premiers jours, 180 jours de chauffe avant les 90 jours tracés. La courbe habituelle apparaît
  après 28 jours d'historique et 4 semaines actives.
- **Entraînement %.** Σ D des 7 jours ÷ moyenne hebdomadaire de Σ D des 11 semaines précédentes,
  avec au moins 8 semaines actives **(H)**. La semaine en cours reste hors de sa référence (ratio
  découplé). « Comme d'habitude » de la plus légère à la plus chargée des semaines actives
  parmi ces 11 **(H)**, « plus » ou « moins que d'habitude » au-delà. Une semaine ordinaire sort
  de la plage de n_a semaines actives avec une probabilité 2/(n_a + 1) : 40 % à 4 semaines, 22 %
  à 8 ; en dessous de 8, le cadran donne la durée des 7 jours. Une référence où tout pèse zéro
  (toutes les séances à la FC de repos) se lit en minutes.
- **Activités 7 j / 28 j.** Les mêmes séances, la même charge D, les mêmes jours locaux ;
  kilomètres par sport, jamais ceux d'un vélo ajoutés à une course.

## Simulations (données synthétiques)

Plan stable de 6 séances par semaine sur 220 jours, 10 % des séances sautées, durées ± 20 %,
20 tirages ; lecture des jours 150 à 219. « Avant » désigne le commit `0f276af`.

- **Récupération.** `cumulative-1` abaissait la limite sur 28 % des matins d'un plan stable (sous
  90 sur 5 %, minimum 81) et sur 45 % des matins autour d'une semaine à + 30 %. Désormais jamais.
- **Entraînement %.** Quartiles inchangés : 89, 103 et 113 %. Part des jours d'un plan stable
  dits « plus » ou « moins que d'habitude » : 26 % avec ± 20 %, 57 % avec les centiles 25–75 (une
  semaine ordinaire tombe hors de l'intervalle interquartile une fois sur deux), 16 % avec la
  plage des semaines actives retenue. Le dernier jour d'une semaine à + 30 %, le cadran dit « plus
  que d'habitude » 75 % du temps (médiane 137 %).
- **Charge accumulée.** Rapport récente/habituelle : quartiles 0,92 ; 1,00 ; 1,07. La courbe
  habituelle lisait 24 % trop bas au 56e jour quand elle partait de 0 ; elle est à 2 % près
  désormais.
- **Cohérence.** La charge des 7 jours d'Activités égale celle du cadran 100 % des jours.

## Limites

L'absence d'activité importée ne prouve pas un repos. Un volume habituel ne prouve pas qu'il est
bien toléré. La charge ne mesure ni les dommages musculaires des descentes, ni les kilos soulevés,
ni les courbatures : une FC basse en musculation ne signifie pas un faible effort musculaire. Le
modèle ne diagnostique ni fatigue, ni surentraînement, ni risque de blessure.

## Validation à mener

La validation physiologique exige un suivi prospectif distinct : figer la version et le bilan
avant les efforts, recueillir un résultat indépendant (performance sous-maximale standardisée,
fonction neuromusculaire ou ressenti facultatif), fixer les critères avant l'analyse, puis tester
sur une période ultérieure et sur d'autres athlètes et appareils. Les tests et les simulations
livrés ici ne remplacent pas ce suivi.

## Sources

- Morton, Fitz-Clarke et Banister 1990 — [Modeling human performance in running](https://pubmed.ncbi.nlm.nih.gov/2246166/), J Appl Physiol.
- Busso 2003 — [Variable dose-response relationship between exercise training and performance](https://doi.org/10.1249/01.MSS.0000074465.13621.37), MSSE.
- Williams 2017 — [Better way to determine the acute:chronic workload ratio?](https://doi.org/10.1136/bjsports-2016-096589), BJSM.
- Imbach 2022 — [The use of fitness-fatigue models for sport performance modelling](https://doi.org/10.1186/s40798-022-00426-x), Sports Med Open.
- Lolli 2019 — [Mathematical coupling causes spurious correlation within the ACWR](https://doi.org/10.1136/bjsports-2017-098110), BJSM.
- Impellizzeri 2019 — [Internal and external training load: 15 years on](https://doi.org/10.1123/ijspp.2018-0935), IJSPP.
- Impellizzeri 2020 — [Acute:chronic workload ratio: conceptual issues and fundamental pitfalls](https://doi.org/10.1123/ijspp.2019-0864), IJSPP 15:907–913.
- Plews 2013b — [Training adaptation and heart rate variability in elite endurance athletes](https://doi.org/10.1007/s40279-013-0071-8), Sports Med.
- Vesterinen 2016 — [Individual endurance training prescription with HRV](https://doi.org/10.1249/MSS.0000000000000910), MSSE.
- Javaloyes 2019 — [Training prescription guided by HRV in cycling](https://doi.org/10.1123/ijspp.2018-0122), IJSPP.
- Nuuttila 2022 — [Individualized endurance training based on recovery and training status](https://pmc.ncbi.nlm.nih.gov/articles/PMC9473708/), MSSE.
- Rebelo 2026 — [Monitoring training effects in athletes: a multidimensional framework](https://doi.org/10.1007/s40279-026-02417-4), Sports Med : charge, état et réponse se lisent séparément.
