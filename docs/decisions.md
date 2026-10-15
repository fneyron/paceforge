# Journal des décisions

Les règles de Santé et du modèle de course vivent dans le code, avec leur source scientifique et la marque (H)
des choix sans étude (`tests/test_heuristics_marked.py` les épingle). Ce journal garde ce que le code ne doit pas
porter : qui a décidé quoi, quand et pourquoi. Une entrée par décision — date, décision, raison, source — dans
l’ordre chronologique. Les citations sont celles du propriétaire.

## 7 octobre 2026

| Décision | Raison | Source |
|---|---|---|
| Des repères « provisoires » dès 7 nuits mesurées (complets à 14), tracés en pointillé et dits tels. | Une montre neuve ne doit pas laisser Santé muet trois semaines. | Propriétaire, 7 oct. — `nights.MIN_PROVISIONAL_NIGHTS` |

## 8 octobre 2026 (Santé v4 à v4.4)

| Décision | Raison | Source |
|---|---|---|
| Trois cadrans — Sommeil, Récupération, Entraînement — et leurs cartes dessous. | « Fais comme WHOOP, ça doit rester simple ». | Propriétaire, 8 oct. — `sante.py`, `sante_score.dial` |
| Toute nuit mesurée compte dans les repères, les moyennes 7 nuits et les horaires ; les étiquettes de contexte (séance longue, soirée intense, altitude, décalage, après effort) retiennent seulement l’alerte FC de nuit. | « Tous les relevés VFC doivent compter en fait, pareil pour la FC » ; repères glissants de WHOOP et d’Oura. | Propriétaire, 8 oct. — `nights.py`, `sante_training.NIGHT_TAGS` |
| La récupération se juge sur les activités passées seulement : ni course prévue, ni check-in. | « tu te bases que sur les activités passées pour juger de la récupération ». | Propriétaire, 8 oct. — `sante_training.efforts`, `nights.py` |
| Un jour sans mesure n’a ni barre ni point sur la carte 14 jours ; son libellé le dit. | « s’il n’y a pas de mesure tu ne mets rien, pas de point ». | Propriétaire, 8 oct. — `sante_score.NO_MEASURE`, `history_card` |
| (v4.3) Plus de modificateur « dénivelé inhabituel » (M1) ni de jours enchaînés (M3) dans les fenêtres d’effort ; chaque activité, ou chaîne d’activités à moins de 30 min, est un effort. | « je ne comprends pas » : une règle qu’on ne peut pas expliquer n’est pas gardée. | Propriétaire, 8 oct. — `sante_training.py` (efforts) |
| (v4.3) Le pli « Comment je calcule » en quelques phrases simples, sans citation ni « (H) » (les sources sur /sante/sources) ; la carte 14 jours ne lit que la date, l’indice et l’état. | « c’est trop d’explication, simplifie et synthétise, ne mets pas les citations » ; « mets juste les scores ». | Propriétaire, 8 oct. — `sante_score.METHOD`, `history_card` |
| (v4.4) Des pourcentages plutôt que des valeurs : l’indice (« 65 % ») et le statut des cartes (« −8 % », « comme d’habitude » à 0 %). | « Mets des pourcentages plutôt que des valeurs (comme WHOOP / Oura) ». | Propriétaire, 8 oct. — `sante_score.pct`, `sante.card_status` |
| (v4.4) Les explications en français : phrases de 15 mots au plus, « tes valeurs habituelles », « d’habitude », jamais « ta normale ». | « les explications en français ne sont pas claires ». | Propriétaire, 8 oct. — `sante.STATUS`, `sante_score.METHOD` |
| (v4.4) La légende des cartes : chaque nuit mesurée est un point plein ; la bande pleine, ou pointillée tant qu’elle est provisoire. | « comment matérialiser que c’est en cours de construction dans le graphique ? ». | Propriétaire, 8 oct. — `sante.LEGEND_*` |
| (v4.4, R3) Une Longue courue comme une course garde sa fenêtre plus longtemps, pour tout utilisateur : marquée course sur Strava, notée 8/10 ou plus, ou FC moyenne ≥ 80 % de la réserve. | Un effort plus dur abîme plus (Martínez-Navarro 2021b ; Bernat-Adell 2021) ; RPE de séance : Foster 2001. | research_data.md R3 — `sante_training.INTENSE_RPE`, `nights.VIGOROUS_HRR` |
| (v4.4, R4) Une sortie chaude : 25 °C ressentis au lieu et à l’heure du départ (Open-Meteo), sinon la température de la montre. | La chaleur élève la FC à allure donnée (Racinais 2015) ; une règle pour tout utilisateur, pas seulement les montres qui mesurent la température. | research_data.md R4 — `sante_training.HOT_C`, `is_hot` |
| (v4.4) Aucune valeur de marque : le Relative Effort de Strava et le modèle de charge bâti dessus sont retirés. | Rien ne le dessinait depuis v4.1 ; une charge propre à PaceForge (FC) le remplace le 9 oct. | 8 oct. — `sante_training.py` |

## 9 octobre 2026

| Décision | Raison | Source |
|---|---|---|
| Le jour Santé tourne au réveil, pas à minuit : avant midi, tant que la nuit n’est pas arrivée, la page lit encore hier. | Réveillé à 1 h dans un avion : « J’ai sommeil vide ». | Propriétaire, 9 oct. — `sante.cycle_day` |
| Chaque sieste est comptée par un seul matin. | Signalement : une sieste d’après-midi comptée dans les 24 h de deux matins. | Propriétaire, 9 oct. — `nights.day_naps` |
| Une « sieste » qui chevauche la nuit ou finit ≤ 30 min avant son début est le début de la nuit. | Signalement : la « sieste » COROS 23:03 → 00:09 d’une nuit 23:57 → 04:58 affichée « + sieste ». | Propriétaire, 9 oct. — `health.fold_naps`, `nights.build_nights` |
| La respiration nocturne entre dans l’alerte maladie : ligne à la médiane + max(1 /min, 2 SD), SD rétrécie vers 0,5 /min. | « utilise la respiration aussi si tu l’as » ; SD nuit à nuit faible (Miller 2020). | Propriétaire, 9 oct. ; research_ind_sleep_resp.md B3 — `nights.RESP_UP_*` |
| Le cadran Entraînement lit la charge cardiaque (Banister 1991) ; sa carte dit « Intensité », selon la maquette approuvée. | « Oui vas-y ». | Propriétaire, 9 oct. ; research_ind_train.md — `sante.training`, `sante.INTENSITY_*` |
| Le lien de la carte Entraînement vers Activités filtre sur les 7 jours, pas sur la semaine. | « Ça ne filtre pas sur la semaine ? ». | Propriétaire, 9 oct. — `sante.training` (`since`) |
| Les cartes de nuit disent en mots que les repères se construisent, et quand ils seront prêts. | « Tu ne mets pas les fourchettes pour VFC et FC repos dans les graphiques ? ». | Propriétaire, 9 oct. — `sante.BUILDS`, `nights_to_normal` |
| Une séance compte le jour local où elle finit. | La Transjeju, partie le 2 oct. à 21:00, a couru 14 de ses 17 h le 3. | Propriétaire, 9 oct. — `sante_training.Session.end_day` |
| Un seul pli de méthode (« Comment je calcule »), 5 puces au plus : les trois cadrans dans leur ordre, les phases, la marge. | L’audit du 9 oct. | Audit, 9 oct. — `sante_score.METHOD` |

## 14 octobre 2026 (révision « base scientifique » et revues statistiques)

| Décision | Raison | Source |
|---|---|---|
| L’état du jour remplace l’indice pondéré 0–100 (VFC 25 · FC 25 · sommeil 30) et ses 8 plafonds : règles (tendance 7 jours, alerte FC, respiration, moins de 6 h, fenêtre d’effort), chaque raison listée ; l’« indice nocturne » ne contredit jamais l’état. | Un score composite n’a pas de validation (Doherty 2025) ; la lecture guidée par la VFC en a (Plews 2013 ; Vesterinen 2016 ; Javaloyes 2019 ; Nuuttila 2022). | Propriétaire, 14 oct. ; docs/sante-estimates.md — `sante_score.py` |
| Les repères s’arrêtent 7 jours avant le jour lu (jours −67 à −8). | La semaine jugée n’entre pas dans la moyenne qui la juge ; choix de conception, pas une règle publiée. | Propriétaire, 14 oct. — `nights.BASELINE_LAG` |
| La bande à 0,5 SD aussi pour la FC de nuit (c’était ± 3 bpm fixes), élargie à 1,28 erreur-type d’une moyenne de 7 nuits corrélées (ρ = 0,3), c’était 1,28/√n7. | 17 % des matins d’un athlète stable lisaient « en dessous » ; ≈ 10 % attendus. | Deuxième revue statistique, 14 oct. — `nights.SWC`, `SWC_Z`, `TREND_RHO`, `threshold` |
| Pour la FC de nuit, la tendance compare une moyenne : centre = moyenne, dispersion = SD rétrécie vers 4 % (c’était médiane et MAD) ; l’alerte garde la médiane. | Contre la médiane d’une FC asymétrique, 26–32 % des matins ordinaires lisaient « au-dessus ». | 14 oct. — `nights._band` |
| La série lue : parmi les montres portées ces 7 jours, celle qui a le plus de nuits sur 28 (égalités : la plus grande référence, la série de la veille, un ordre fixe) — jamais la dernière nuit. | Deux montres en alternance changeaient de série, de repère et de tendance une nuit sur deux. | Propriétaire, 14 oct. — `nights._series_source`, `SERIES_DAYS` |
| L’historique 14 jours relit le choix de série de la page ; une égalité est lue jusqu’à 400 jours en arrière. | Après 200 jours de deux montres en alternance, l’historique lisait COROS où la page lisait Garmin. | Deuxième revue statistique, 14 oct. — `nights.share_series`, `SERIES_LOOKBACK` |
| L’alerte FC de nuit finit sur une nuit ≤ médiane + 3 bpm, pas au haut de la bande de tendance. | L’étiquette « FC de nuit haute » courait 14 jours sur des nuits déjà revenues à l’habitude. | Revue, 14 oct. — `nights.ALERT_END_BPM`, `alert_episodes` |
| La respiration est un signal de maladie, une raison de l’état — plus un plafond du score à 69. | Même base que l’état : une raison, pas un plafond. | Propriétaire, 14 oct. — `nights.resp_up_nights` |
| Une série plus jeune que sa fenêtre décalée donne un repère provisoire (60 nuits jusqu’à la veille), lu par l’état : le score s’affiche pendant que les repères se construisent. | « il y a bien un score » : le COROS du propriétaire ne traçait aucune bande et l’état restait muet trois semaines. | Propriétaire, 14 oct. — `nights.drawn_band`, `sante_score.usable` |
| L’alerte maladie s’affiche même quand la semaine est trop mince pour une tendance. | Elle était cachée derrière « trop peu de nuits pour comparer ». | 14 oct. — `sante.card_status` |
| Le texte des repères en construction dit « dès {n} nuits mesurées avant les 7 derniers jours ». | Une nuit entre dans les valeurs habituelles une semaine après ; « dès 7 nuits mesurées » seul était devenu faux. Correction minimale, la formulation reste une proposition d’interface. | 14 oct. — `sante.BUILDS` |
| Le besoin de sommeil vaut 8 h pour tous (ni base apprise, ni ajout par l’effort ou la dette) ; « suffisant » dès 7 h ; le manque sur 7 nuits est dit à part. | Aucune base validée pour un besoin adapté. | Propriétaire, 14 oct. ; docs/sante-estimates.md — `sante_sleep.NEED_DEFAULT`, `sante.sleep_word` |
| Une sieste après le réveil passe au matin suivant dès qu’il existe ; un jour passé de la carte 14 jours ne la lit pas dans les nuits ultérieures. | Le passé ne doit pas changer avec les nuits qui suivent. | 14 oct. — `sante._assess`, `nights.day_tst24` |
| Une seule charge (FC) pour le cadran Entraînement, la Charge accumulée et Activités ; elle ne plafonne plus l’état. | La VFC lit déjà ce que la charge fait à la nuit. | Propriétaire, 14 oct. ; docs/recovery-cumulative.md — `sante_load`, `sante.training_context` |
| « Comme d’habitude » = entre la plus légère et la plus lourde des semaines actives habituelles (c’était ± 20 %). | ± 20 % n’avait pas de base ; l’étendue des semaines de l’athlète en a une. | Revue statistique, 14 oct. — `sante.training`, `sante_load.USUAL_*` |
| Les fenêtres d’effort sont une raison de l’état, sans plafond (les plafonds 35/45/65 n’avaient pas de base) ; longueurs lues dans les études : Longue D+1, courue comme une course D+2 (c’était D+5), Très longue D+3, Ultra D+5, ≥ 24 h D+7 (c’était D+13) ; rouge D+0 et D+1 après un Ultra. | Petersen 2007 ; Fazackerley 2019 ; Hoffman 2017. | Propriétaire, 14 oct. — `sante_training.EFFORT_WINDOWS`, `LONG_RACE_LAST`, `ULTRA_LONG_LAST`, `effort_window` |
| Les semaines d’Activités et de Santé sur l’horloge de l’athlète (c’étaient des lundis UTC). | Une sortie à 00:30 un lundi est de cette semaine-là. | 14 oct. — `sante_training.local_monday` |
| FC en footing : les km splits après les 10 premières minutes, pente ± 2 %, 2 km au moins. | Vesterinen 2014 : FC à vitesse sous-maximale fixe sur le plat ; l’échauffement fait monter la FC. | Propriétaire, 14 oct. — `sante_training.STEADY_AFTER_MIN` |
| Activités 7 / 28 jours : les mêmes séances dédupliquées que Santé, par jour local, jusqu’à l’« aujourd’hui » de l’athlète. | Ils sommaient chaque activité stockée, doublons compris, sur des jours UTC comptés depuis « maintenant ». | Propriétaire, 14 oct. — `training_load.py` |
| Le pli de méthode ne décrit plus l’ancien indice (besoin adapté, efforts accumulés). | Ces puces disaient quelque chose de faux. | Propriétaire, 14 oct. — `sante_score.METHOD` |
| Modèle de course : un seul exposant de population k = 1,28 pour tous les niveaux, écart-type du prior 0,08. | Vérifié par niveau (level_benchmark.py k_by_level) sur 48 640 finishers : l’exposant ne baisse pas chez les forts ; 242 coureurs suivis n’ont pas d’exposant propre au-delà du bruit. | 14 oct. — `race_calibration.K_PRIOR`, `K_PRIOR_SD` |

## 15 octobre 2026 (PR #7 : contexte, imports et lecture sur iPhone)

| Décision | Raison | Source |
|---|---|---|
| « Signaux nocturnes » est un indice sur 100, descriptif : ni un pourcentage de récupération, ni une autorisation de reprise ; la FC nocturne élevée reste un signal même quand la VFC est habituelle. | Un indice composite n’a pas de validation clinique ; aucun indicateur isolé ne résume la récupération (Kellmann 2018). | docs/health-context-2026-10-15.md — `sante_score.py` |
| Les fenêtres après effort ne sont plus une raison ni un plafond de l’indice ; l’effort reste un contexte séparé, daté depuis la fin locale réelle, affiché 28 jours (une limite de présentation, pas un délai de guérison). | VFC et symptômes ne suivent pas le même calendrier après un ultra (Fazackerley 2019). | docs/health-context-2026-10-15.md — `sante_score.score_of`, `sante_context.py`, `sante_training.effort_window` |
| Le repère de 8 h de sommeil est dit comme tel, sans prétendre mesurer le besoin individuel ; l’écart sur 7 jours précise les nuits documentées, les nuits absentes ne valent pas zéro. | Aucune mesure du besoin individuel. | docs/health-context-2026-10-15.md — `sante.py`, `sante_sleep.py` |
| Le ressenti déclaré (fatigue, douleur, tolérance du dernier effort) est enregistré à part, daté au jour local du compte, et ne change ni l’indice ni les anciens indicateurs. | Mesures, activités et ressenti restent distincts. | docs/health-context-2026-10-15.md — `sante_checkin.py` |
| Le contexte après-course exige une activité reçue, éventuellement liée comme résultat d’un parcours ; une course seulement prévue ne suffit pas ; l’association suit la copie conservée au dédoublonnage. | Aucun contexte sans mesure. | docs/health-context-2026-10-15.md — `activity_identity.py`, `sante_context.py` |

## 15 octobre 2026 (revue de fix2/cleanup)

| Décision | Raison | Source |
|---|---|---|
| Les cinq tables de l’ancienne chaîne IA et des simulations (analyses, chat_messages, weekly_digests, generated_plans, simulations) restent une version de plus, non mappées (leurs modèles sont supprimés, `alembic check` les ignore) ; elles seront supprimées après la première sauvegarde. | chat_messages et generated_plans peuvent contenir des lignes saisies par l’athlète (routeurs coach et séance livrés de juin à septembre) ; la suppression est irréversible et la production n’a pas encore de sauvegarde ; pendant la fenêtre de déploiement, l’ancien worker charge encore `analyses` à chaque lecture complète d’Activity. | Revue de fix2/cleanup, 15 oct. — `alembic/env.py` (include_object), docs/ops-runbook.md |
