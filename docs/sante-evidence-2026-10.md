# Santé comme tableau de bord : WHOOP, Oura et état des connaissances

Revue ciblée au **12 octobre 2026**, confrontée au code PaceForge. Ce document
n’est pas une revue systématique. Les descriptions des fabricants expliquent
leurs produits ; elles ne valident pas scientifiquement leurs scores, ni le nôtre.

## Décision produit

**Santé devient l’écran principal**, avec trois onglets : Santé, Activités,
Courses. L’ancien Accueil faisait doublon. Les réglages gardent les connexions,
les sources et les synchronisations. Pas de nouveau questionnaire obligatoire,
pas de date ou de source répétée sous chaque mesure.

Conserver trois lectures distinctes :

| Question | Lecture utile | Horizon |
| --- | --- | --- |
| Comment sont mes signaux aujourd’hui ? | Sommeil, récupération estimée, écarts nocturnes personnels | Dernière nuit et tendances récentes |
| Quel travail ai-je accumulé ? | Durée et intensité de toutes les séances reçues, historique des semaines | Plusieurs jours à plusieurs semaines |
| Est-ce que je progresse ? | Allure ou puissance à effort comparable, performances répétées | Plusieurs semaines à plusieurs mois |

Une charge élevée peut coexister avec de bons signaux nocturnes. Une bonne nuit
ne garantit pas la récupération musculaire. Une récupération basse aujourd’hui
ne signifie pas une perte de condition physique. Il faut montrer ces informations
ensemble, sans transformer chacune en une prétendue mesure de la même fatigue.

## Comparaison avec les produits actuels

| Point | WHOOP [1] | Oura [2] | PaceForge, code actuel |
| --- | --- | --- | --- |
| Organisation | Sleep, Recovery, Strain | Sommeil, préparation du jour, activité | Sommeil, Récupération, Entraînement dans Santé |
| Lecture quotidienne | Recovery au réveil ; distinct du Strain accumulé dans la journée | Readiness combine dernière nuit et équilibres récents | Score par jour ; la tendance cardiaque sur sept nuits est lissée |
| Référence personnelle | Comparaison aux données individuelles | VFC pondérée sur 14 jours / référence de trois mois ; activité sur 14 jours / deux mois | VFC et FC nocturnes comparées à 60 jours, même source et méthode |
| Sommeil accumulé | Besoin intégrant dette et activité précédente | Sommeil récent, équilibre et régularité sur deux semaines | Nuit + siestes, besoin estimé, dette et gros efforts ; horaires habituels |
| Signaux complémentaires | Respiration, température cutanée et SpO₂ mentionnées dans la documentation Recovery | Température et dynamique du pouls nocturne, parmi neuf contributeurs | Respiration nocturne Garmin ; pas de température ou SpO₂ importées |
| Effort et condition physique | Strain distinct du score du matin | Activité récente et habituelle contribuent à Readiness | Charge de sept jours distincte ; FC à allure comparable dans Activités |
| Formule | Propriétaire | Propriétaire | Lisible dans le dépôt, mais pondérations et seuils heuristiques, sans validation prospective |

WHOOP ne fait pas descendre son Recovery au fil des activités de la journée :
il suit l’effort dans un autre indicateur. Oura associe des fenêtres courtes et
longues. Leurs interfaces inspirent la séparation des informations, pas la copie
de seuils ou d’une formule cachée.

## Ce que les études permettent de conclure

- **Approche multidimensionnelle, 2026 [3] :** cette revue narrative distingue
  charge, état de l’athlète et réponse à l’entraînement. C’est un cadre pertinent
  pour organiser l’application, pas un modèle prédictif validé prêt à importer.
- **Scores composites, 2025 [4] :** l’évaluation de 14 scores de dix fabricants
  souligne le manque de transparence et de validation indépendante complète.
  Une bonne mesure de pouls ne prouve pas la validité d’un score de récupération.
- **Performance en golf, publication 2025/2026 [16] :** une étude longitudinale
  sur 389 golfeurs professionnels rapporte des associations entre sommeil,
  signaux physiologiques, Recovery et performance. Des auteurs sont liés à WHOOP.
  Ce résultat observationnel spécifique à un sport ne valide pas une formule
  universelle de fatigue ; l’article a aussi un erratum référencé [17].
- **Mesures nocturnes, 2025 [5] :** comparaison de cinq appareils à une référence
  ECG chez 13 adultes. L’accord varie selon l’appareil et l’indicateur. Petit
  échantillon, versions précises : aucun classement universel des marques, aucune
  validation du score PaceForge.
- **VFC optique, 2026 [6] :** revue systématique de 43 études, dont dix dans la
  méta-analyse. Les résultats au repos ne démontrent pas l’interchangeabilité
  pendant l’exercice, la nuit ou en conditions libres. Conserver la méthode,
  la fenêtre de mesure et la source est indispensable.
- **Ressenti et VFC, 2026 [7] :** une étude observationnelle en population générale
  ne trouve pas d’association entre certains ressentis de stress/nervosité et
  la VFC. Ce résultat ne représente pas tous les athlètes, mais rappelle que
  le signal autonome ne remplace pas le ressenti. La revue de Saw et al. [8]
  soutient aussi l’intérêt des mesures subjectives dans le suivi d’athlètes.
- **Composition corporelle, 2026 [9] :** la revue sur l’impédancemétrie comparée
  au modèle à quatre compartiments montre des erreurs individuelles importantes.
  Elle concerne des adultes sains, pas la validation de chaque balance du marché.
  Le poids et la masse grasse doivent rester du contexte, sans pénalité automatique.
- **VO₂max et seuil, 2025 [10] :** revue systématique de 13 études. Certaines
  estimations de montres sont utiles, avec des limites de population et de
  protocole. Elles ne constituent ni une analyse sanguine, ni un dosage de fatigue.

Le signal utile est généralement une évolution personnelle reproductible,
interprétée avec le contexte. Plus de mesures ne signifie pas automatiquement
une meilleure estimation ; des indicateurs corrélés peuvent compter deux fois
la même information.

## Indicateurs : intérêt et disponibilité réelle

« Possible » ci-dessous signifie qu’un écosystème ou un appareil compatible peut
produire cette donnée. Cela ne signifie pas que le connecteur PaceForge la reçoit.
Les permissions, le modèle, le fabricant et le partage vers Apple Santé comptent.

| Indicateur | Ce qu’il apporte / limite | PaceForge aujourd’hui | Choix recommandé |
| --- | --- | --- | --- |
| VFC nocturne RMSSD | Réponse autonome ; sensible au contexte, haute ≠ toujours meilleure | COROS/Garmin, tendances et référence séparées par source | Signal principal, jamais verdict isolé |
| FC nocturne | Écart personnel utile ; moyenne nocturne ≠ minimum ≠ FC de repos | COROS/Garmin | Signal principal avec sommeil et VFC |
| VFC Apple SDNN / FC de repos Apple | Mesures possibles dans HealthKit [13], protocoles distincts | Non importées ; le pouls quotidien n’est pas une FC de repos | Intégration dédiée, aucune conversion SDNN→RMSSD arbitraire |
| Durée du sommeil, siestes | Opportunité de récupération et manque répété | COROS/Garmin | Signal principal ; besoin biologique personnel non mesuré |
| Horaires et régularité | Contexte circadien | Horaires et habitudes déjà disponibles | Montrer la tendance ; pas de nouvelle pénalité non validée |
| Phases de sommeil | Estimations de la montre | Affichées lorsqu’elles sont reçues | Pas de score musculaire dérivé du « profond » |
| Respiration nocturne | Écart à sa propre référence ; non spécifique | Garmin ; pas COROS actuellement | Complément lorsque réellement nocturne |
| Pouls et respiration sur la journée | Mélangent repos, sommeil et effort | Lecture mobile ; autres lectures selon source | Contexte uniquement, exclus de la récupération |
| Durée, FC, intensité des séances | Charge interne estimée, cumul des entraînements | COROS/Garmin/Strava, dédoublonnage ; FC par segments si disponible | Conserver tous les sports ; signaler implicitement les limites de la FC |
| Course, vélo/Zwift, D+, musculation | Contraintes musculaires différentes malgré une même FC | Activités et D+ reçus selon service ; musculation pondérée par durée | Améliorer la spécificité par sport avant une fatigue « universelle » |
| Allure, distance, puissance | Performance et charge externe | Activités ; puissance si transmise ; FC en footing comparable déjà analysée | Suivi de forme séparé, avec contexte météo/terrain |
| VO₂max, seuil estimé, FTP | Repères d’entraînement et tendances de performance | Pas de série de santé dédiée importée | Valeur et méthode du fournisseur, pas mélange avec le score du jour |
| Poids | Tendance corporelle ; eau, repas, glycogène et conditions de pesée influencent le jour | Apple Santé / Health Connect, si la balance y écrit | Tendance dans des conditions comparables ; pas diagnostic de déshydratation |
| Masse grasse, masse maigre | Estimation dépendante de la méthode et de l’hydratation | Masse grasse mobile seulement | Contexte à long terme, pas variation quotidienne de fatigue |
| Pas | Activité quotidienne hors séances | Montres + mobile, priorité sans additionner les copies | Contexte, pas double comptage avec les entraînements |
| Température cutanée | Écart nocturne ; sensible à l’environnement et au cycle | Non importée | Complément futur, distinct de la température centrale et d’un diagnostic |
| SpO₂ | Contexte respiratoire/altitude ; fiabilité dépendante de l’appareil | Non importée | Affichage contextualisé avant toute contribution au score |
| Stress, Body Battery, récupération du fabricant | Scores propriétaires, souvent calculés avec les mêmes signaux | Stress Garmin affiché ; ancien stress COROS masqué ; pas de fusion des scores | Ne pas empiler les scores avec leurs propres entrées |
| Fatigue ressentie, douleurs, motivation, RPE | Complètent les capteurs et la récupération musculaire | Pas de questionnaire quotidien ; RPE possible dans certains enregistrements d’activité | À envisager facultativement, sans réintroduire un rituel obligatoire |
| Tension, glycémie, ECG, bilans sanguins | Indications et protocoles spécifiques ; pas un score universel de forme | Non importés | Hors du score général ; aucune collecte supplémentaire dans cette version |
| Lactate sanguin, sueur, seuil lactique | Trois informations différentes, voir ci-dessous | Aucun import lactate | Tests spécialisés et unités explicites si intégration ultérieure |

Le connecteur mobile lit aujourd’hui **cinq catégories** : poids, masse grasse,
pas, pouls quotidien et respiration quotidienne. Il ne lit pas encore les nuits,
la VFC, la température ou la SpO₂ d’Apple Santé. Une autorisation HealthKit ne
crée pas les données : la balance ou l’application fabricant doit les fournir.

Les API officielles Garmin [14] et Health Connect [15] montrent d’autres
possibilités. Elles ne prouvent pas que nos connecteurs disposent déjà de leur
accès. PaceForge n’a actuellement pas de connexion de compte WHOOP/Oura, ni de
connexion directe universelle à toute marque ou à tout capteur Bluetooth.

## Lactate : oui avec une mesure adaptée, pas comme jauge de fatigue

1. **Lactate sanguin en mmol/L :** lecteur dédié, prélèvement et protocole
   d’effort identifié. Utile pour caractériser la réponse à l’intensité.
2. **Seuil estimé par une montre :** allure, puissance ou fréquence cardiaque
   estimée autour d’un seuil. Ce n’est pas la concentration sanguine [10].
3. **Lactate de la sueur :** technologies en développement. Le débit de sueur,
   la production locale et les conditions limitent la correspondance avec le
   sang ; ne pas les présenter comme interchangeables [11].

La lactatémie dépend aussi des réserves de glycogène, de la nutrition et du
protocole [12]. Une valeur isolée ne chiffre pas la fatigue restante le lendemain.
Le lactate n’est pas une « dette » musculaire stockée plusieurs jours.

## Audit du calcul et suite justifiée

**Points solides dans l’implémentation :** référence personnelle et séparation
des sources, absence de zéros inventés, activités dédoublonnées, nuits réellement
reçues, tendance distincte des points mesurés, historique calculé sans nuits
futures. Sommeil et gros efforts peuvent influencer plusieurs jours.

**Limites à ne pas cacher :**

- Le score combine des poids heuristiques (VFC 25, FC nocturne 25, sommeil 30,
  renormalisés selon les composantes disponibles). Les études ne valident pas
  ces nombres ou le pourcentage final.
- La moyenne cardiaque sur sept nuits amortit le bruit mais peut masquer un
  changement aigu. Une barre quotidienne ne signifie pas que toutes ses entrées
  concernent uniquement la dernière nuit.
- Les limites après longues sorties sont des précautions décroissantes. Plusieurs
  efforts conservent la limite la plus prudente ; ce n’est pas une addition
  physiologique de fatigue.
- Les séances ordinaires comptent dans la charge de sept jours. Elles n’ajoutent
  pas de pénalité cumulative propre à la récupération ; leurs effets peuvent
  seulement se refléter dans le sommeil et les signaux nocturnes.
- La charge hebdomadaire est une comparaison à la semaine habituelle des onze
  semaines précédentes, avec au moins quatre semaines actives. Elle ne mesure
  pas la récupération musculaire ni une probabilité de blessure.

**Cette version** fusionne la navigation dans Santé, nomme le bilan du jour,
actualise l’aide et les références. Elle ne remplace pas les poids par une autre
formule arbitraire, n’ajoute aucune permission de santé et ne prétend pas importer
les indicateurs marqués absents.

**Ordre de développement recommandé :** d’abord des imports nocturnes Apple
avec provenance, fenêtre de sommeil et méthode conservées ; ensuite des signaux
complémentaires réellement accessibles. Pour faire évoluer le calcul, comparer
séparément dernière nuit, tendance et charge accumulée, puis évaluer si ces
ajouts améliorent l’explication de performances comparables et, si accepté,
du ressenti. Utiliser des périodes futures tenues à l’écart du réglage des seuils,
étudier les changements de montre et les données manquantes. Ne pas valider
un score en le comparant uniquement à ses propres entrées ou au score d’une marque.

## Références

1. [WHOOP — documentation des trois piliers](https://developer.whoop.com/docs/whoop-101/), consultée le 12/10/2026.
2. [Oura — Readiness Contributors](https://support.ouraring.com/hc/en-us/articles/360057791533-Readiness-Contributors), mise à jour 07/10/2026.
3. [Rebelo et al., 2026 — Monitoring Training Effects in Athletes](https://doi.org/10.1007/s40279-026-02417-4), revue narrative.
4. [Doherty et al., 2025 — Readiness, recovery, and strain](https://doi.org/10.1515/teb-2025-0001), évaluation des scores composites.
5. [Dial et al., 2025 — Validation of nocturnal resting heart rate and heart rate variability](https://doi.org/10.14814/phy2.70527), étude de validation de mesures.
6. [Xu et al., 2026 — Accuracy of PPG-Derived Pulse Rate Variability](https://doi.org/10.3390/s26165192), revue systématique et méta-analyse.
7. [Disconnection Between Self-Reported Wellbeing and Heart Rate Variability from Wearables, 2026](https://doi.org/10.3390/s26041325), étude observationnelle.
8. [Saw et al., 2016 — Monitoring the athlete training response](https://doi.org/10.1136/bjsports-2015-094758), revue systématique antérieure, toujours pertinente pour le ressenti.
9. [Validity of Bioelectrical Impedance Analysis Compared to a Four-Compartment Model, 2026](https://pmc.ncbi.nlm.nih.gov/articles/PMC12922097/), revue systématique.
10. [Železnik Mežan, 2025 — Accuracy of wearables for VO₂max and lactate threshold](https://doi.org/10.3389/fspor.2025.1707991), revue systématique qualitative.
11. [Yang et al., 2024 — Wearable device for continuous sweat lactate monitoring](https://doi.org/10.3389/fphys.2024.1376801), revue narrative.
12. [Benítez-Muñoz et al., 2025 — Factors Influencing Blood Lactate Concentration During Exercise](https://doi.org/10.1111/apha.70131), revue narrative.
13. [Apple — HealthKit HRV SDNN](https://developer.apple.com/documentation/healthkit/hkquantitytypeidentifier/heartratevariabilitysdnn), définition officielle.
14. [Garmin — Health API](https://developer.garmin.com/gc-developer-program/health-api/), catalogue officiel, accès soumis au programme.
15. [Android — Health Connect data types](https://developer.android.com/health-and-fitness/health-connect/data-types), catalogue officiel.
16. [Grosicki et al., 2025/2026 — Wearable-Derived Sleep and Physiological Metrics Are Associated With Performance in Professional Golfers](https://pubmed.ncbi.nlm.nih.gov/41265431/), étude observationnelle, DOI 10.1123/ijspp.2025-0226.
17. [Erratum de l’étude chez les golfeurs](https://pubmed.ncbi.nlm.nih.gov/41417742/), DOI 10.1123/ijspp.2025-0657. Les tailles d’effet ne sont pas reprises ici.
