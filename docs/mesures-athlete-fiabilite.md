# Mesures utiles à l’athlète et fiabilité de PaceForge

État vérifié le **12 octobre 2026**. Complément à la
[revue WHOOP/Oura et littérature](sante-evidence-2026-10.md).
Le lactate reste mis de côté. Aucun nouvel achat n’est nécessaire pour utiliser
les fonctions décrites comme disponibles.

## Priorités pour Florent

1. **Mieux exploiter le matériel existant.** APEX 4 portée régulièrement la nuit,
   brassard COROS relié à l’appareil qui enregistre l’activité, balance EXZACT
   dans des conditions répétables. Vérifier les transmissions avant tout achat.
2. **Puissance vélo si elle manque à l’extérieur.** C’est un complément utile à
   la FC pour doser les efforts et comparer des séances. Un home-trainer mesurant
   la puissance suffit pour les comparaisons en intérieur ; il n’en fournit pas
   sur le vélo dehors. La présence d’un capteur extérieur reste à confirmer.
3. **Ceinture électrique seulement pour un besoin précis.** Par exemple des
   intervalles RR exploitables ou des décrochages récurrents du brassard.
   Pas de remplacement automatique d’un brassard qui fonctionne bien.
4. **Protocole et continuité avant multiplication des capteurs.** Ne pas
   mélanger dispositifs/méthodes dans une même référence ; garder le ressenti et
   les performances comparables pour juger ce que les chiffres décrivent.

## Utilité, limites et matériel

| Mesure | Utilité concrète | Matériel/protocole qui améliore la mesure | Exploitation réelle dans PaceForge |
|---|---|---|---|
| Sommeil : durée, horaires, régularité | Repérer les nuits courtes et l’irrégularité ; organiser le repos | APEX 4 déjà adaptée à ce suivi, portée chaque nuit avec un ajustement régulier. Bague utile surtout si mieux tolérée la nuit ; les phases restent des estimations | Nuits COROS/Garmin ou Apple Santé iOS ; durée et besoin estimé, pas de score de qualité fondé sur le sommeil profond |
| FC nocturne | Suivre les écarts personnels, en tenant compte notamment de la chaleur, de l’alcool et de l’effort | Montre portée correctement ; éviter de remplacer une mesure nocturne par une moyenne 24 h | Valeur de la nuit, tendance 7 jours et référence personnelle ; contribution seulement si mesurée cette nuit |
| VFC | Suivre la réponse autonome dans son contexte ; une baisse isolée ne mesure pas toute la fatigue | Même appareil, méthode et conditions. APEX 4 pour la nuit. Ceinture électrique type Polar H10 pour un protocole RR au repos si nécessaire | RMSSD nocturne pris en charge selon fournisseur ; SDNN Apple distinct et hors sous-score RMSSD. Pas d’import direct d’un test matinal RR/H10 dans ce bilan |
| FC pendant l’effort | Doser l’intensité interne ; comparer FC/allure ou FC/puissance dans des conditions proches | Brassard COROS actuel avec APEX/DURA/Zwift. Ceinture électrique si le signal optique décroche ; ni l’un ni l’autre ne corrige l’effet de la chaleur | Activités reçues via le compte qui les enregistre ; charge estimée et repère de FC à allure comparable en course |
| Puissance vélo | Quantifier l’effort mécanique indépendamment du vent et de la pente ; comparer les séances | Capteur de pédales/pédalier compatible DURA, correctement monté et étalonné selon le fabricant ; home-trainer en intérieur | Champs de puissance reçus selon les activités et intégrations. Pas de bilan de fatigue neuromusculaire ni de repère longitudinal FC/puissance livré dans Santé |
| Poids | Suivre une évolution de plusieurs semaines ; contextualiser les variations aiguës | EXZACT existante si répétable : même sol dur, horaire et conditions, idéalement au lever après les toilettes. Une variation peut refléter eau/contenu digestif/glycogène | Dernière pesée horodatée et tendance. EXZACT/Senssun doit d’abord écrire dans Apple Santé ; cette transmission doit être constatée sur le téléphone |
| Masse grasse par impédance | Repère grossier de long terme, sensible aux conditions ; une variation quotidienne n’est pas une variation démontrée de graisse | Standardiser l’hydratation et les conditions. Acheter une balance plus chère ne suffit pas à établir sa validité individuelle | Affichage contextuel, exclu du score de récupération. Aucune mesure de disponibilité énergétique ou de fatigue déduite du poids/BIA |
| Respiration nocturne | Repérer une modification inhabituelle à interpréter avec le reste | Montre/capteur nocturne compatible avec assez de points. Un relevé ponctuel n’équivaut pas à une nuit | Selon source, tendance nocturne et prudence après élévation répétée ; moyenne de journée conservée séparément |
| Pas, FC 24 h, stress propriétaire | Contexte d’activité et de journée ; dépendance au temps de port et à l’algorithme | Régularité du port ; aucun capteur supplémentaire prioritaire | Contexte seulement. Totaux de pas non additionnés ; stress COROS archivé masqué |
| Allure, FC à effort comparable et performances | Observer la forme sur plusieurs semaines et vérifier si les changements sont utiles en pratique | APEX 4 + brassard ; parcours/protocole comparable, conditions météo prises en compte. Puissance pour un protocole vélo | Repère de course facile et plate après assez de sorties ; il ne prouve pas seul une progression |
| Ressenti, courbatures, difficulté inhabituelle | Compléter les dimensions mal mesurées par les capteurs, notamment musculaires | Aucun achat ; protocole court et facultatif si souhaité | Pas de questionnaire quotidien ajouté et pas de donnée subjective inventée dans l’indice |

L’APEX 4 propose bien sommeil et VFC nocturne, selon
[COROS](https://support.coros.com/hc/en-us/articles/42070715941268-Set-Up-Your-COROS-APEX-4).
Le [brassard COROS](https://support.coros.com/hc/en-us/articles/17458038294292-COROS-Heart-Rate-Monitor-FAQs)
transmet par Bluetooth à un enregistreur compatible ; il ne constitue pas à lui
seul un enregistrement de séance. Aucun appairage séparé n’est ajouté à PaceForge.

Pour le DURA, COROS liste notamment **Favero Assioma Duo/Uno, Garmin Rally,
Quarq et 4iiii** dans ses
[capteurs de puissance compatibles](https://support.coros.com/hc/en-us/articles/27159331946388-Pairing-DURA-with-Power-Meters).
Choisir après vérification du vélo, du pédalier et des cales ; ce document ne
choisit pas un modèle sans ces informations. Le
[Polar H10](https://github.com/polarofficial/polar-ble-sdk/blob/master/documentation/products/PolarH10.md)
expose FC, intervalles RR et ECG via son SDK : cette capacité ne signifie pas
que PaceForge importe actuellement ces flux. Les séances Zwift peuvent passer
par [Strava](https://support.strava.com/en-us/articles/15401915-zwift-and-strava).

## Mesurable ne signifie pas automatiquement utile ou importé

Température cutanée, SpO₂, fréquence respiratoire d’un test ponctuel, ECG et VO₂max
estimé ne sont pas interchangeables avec une mesure nocturne ou un test de
performance. Les catalogues Apple Santé/Garmin ne garantissent ni leur production
par l’appareil, ni leur partage, ni leur ingestion par PaceForge. Température,
SpO₂ et ECG ne sont pas ajoutés à l’indice dans cette livraison. Une mesure de
VO₂max par analyse des gaz en laboratoire répond à une autre question qu’un
score de récupération du matin. Pas de recommandation de capteur de glucose,
de transpiration ou de nouvelle bague pour « rendre le score fiable ».

Le choix de peu de mesures adaptées et répétables est cohérent avec la revue
[Rebelo et al., 2026](https://link.springer.com/article/10.1007/s40279-026-02417-4) :
charge reçue, état du jour et réponse à l’entraînement sont trois objets
différents. La revue soutient une interprétation individuelle et contextualisée,
pas un score universel ni une preuve de performance future.
Le rôle complémentaire du ressenti est aussi documenté par
[Saw et al.](https://pubmed.ncbi.nlm.nih.gov/26423706/).
L’étude de [Frija-Masson et al., 2021](https://mhealth.jmir.org/2021/4/e22487)
distingue la mesure du poids des estimations de composition corporelle ; elle
porte sur trois balances et ne valide pas spécifiquement EXZACT.
La [revue BIA 2026](https://pubmed.ncbi.nlm.nih.gov/41718193/) compare les
estimations à un modèle à quatre compartiments ; elle ne valide pas un score
quotidien de fatigue issu de la masse grasse.

## Ce que signifie « fiable » dans cette livraison

**Chaîne des données.** Tests sur anciens relevés retardés, doublons, absence de
mesures, dates locales, panne réseau/HealthKit, compte changé ou révoqué, lecture
partielle et interruption. Un reçu constate l’enregistrement côté serveur ; une
lecture locale ou une compilation réussie ne le remplace pas. Les mesures de
poids de l’iPhone réel de Florent n’ont pas été lues depuis cet environnement.

**Calcul et affichage.** Nuits et moyennes distinguées ; manque d’un indicateur
explicite ; sources/méthodes séparées ; pas de zéro de substitution. Les
pondérations, plafonds, référence de sommeil et constantes 7/42 jours restent
des heuristiques transparentes. Le score peut amortir une variation isolée : la
valeur de la dernière nuit reste donc visible à côté de sa tendance. Aucun
nouveau seuil « aigu » n’est inventé pour masquer cette limite.

**Validation physiologique encore à mener.** Les tests logiciels et la revue
ne prouvent pas que l’indice prédit la capacité à faire une séance. Un suivi
prospectif doit enregistrer le bilan avant la séance, un résultat indépendant
(ressenti facultatif et effort sous-maximal comparable), le matériel, la chaleur,
les voyages et les manques de données. Définir le protocole et les critères avant
de regarder les résultats ; garder une période ultérieure pour l’évaluation,
sans ajuster les seuils dessus. Comparer aux repères simples (sommeil seul,
FC/VFC seules), publier erreurs et faux signaux, et séparer appareils/athlètes.
Plusieurs semaines chez une seule personne peuvent tester l’utilité individuelle,
pas établir une validation générale. Aucun résultat clinique ou prospectif n’est
revendiqué aujourd’hui.
