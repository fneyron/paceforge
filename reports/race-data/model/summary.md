# Réévaluation du modèle

Statut : **rejected**. Paramètres publiés : **non**.

Erreur moyenne aux points de passage : 18.61 → 18.73 min.

Validation : 47 courses, 14100 arrivants, événements séparés de l'entraînement.

Les temps sont comparés à durée finale connue, arrêts retirés lorsque disponibles : cette mesure valide la répartition des temps sur le parcours.

Le candidat doit encore être vérifié avec le code de production (validate_app.py) avant adoption.

- OK — separate_events: At least 8 training events and 4 separate validation events
- OK — validation_size: At least 20 held-out races and 1,000 finishers
- ÉCHEC — overall_improvement: Mean checkpoint error must improve by at least 2% and 0.25 minutes
- OK — by_group_coverage: All four distance bands / at least four finish-time groups
- OK — by_group:0-12h: No material regression in this group
- OK — by_group:12-18h: No material regression in this group
- OK — by_group:18-24h: No material regression in this group
- OK — by_group:24-30h: No material regression in this group
- ÉCHEC — by_group:30-99h: No material regression in this group
- OK — by_band_coverage: All four distance bands / at least four finish-time groups
- OK — by_band:60-100 km: No material regression in this group
- ÉCHEC — by_band:140-180 km: No material regression in this group
- OK — by_band:40-60 km: No material regression in this group
- OK — by_band:100-140 km: No material regression in this group
- OK — race_coverage: Every held-out race must have a comparison
- ÉCHEC — races_improved: At least 60% of held-out races improve
- OK — race:utmb/chiangmai_2024/100K: No race may regress by more than 1 minute or 5%
- ÉCHEC — race:utmb/chiangmai_2024/100M: No race may regress by more than 1 minute or 5%
- OK — race:utmb/chiangmai_2024/50K: No race may regress by more than 1 minute or 5%
- ÉCHEC — race:utmb/chiangmai_2025/CHD160: No race may regress by more than 1 minute or 5%
- OK — race:utmb/chiangmai_2025/ELP100: No race may regress by more than 1 minute or 5%
- OK — race:utmb/chiangmai_2025/HMN50D: No race may regress by more than 1 minute or 5%
- OK — race:utmb/chiangmai_2025/HMN50N: No race may regress by more than 1 minute or 5%
- OK — race:utmb/kullamannen_2022/100M: No race may regress by more than 1 minute or 5%
- OK — race:utmb/kullamannen_2022/100k: No race may regress by more than 1 minute or 5%
- OK — race:utmb/kullamannen_2022/50k: No race may regress by more than 1 minute or 5%
- OK — race:utmb/kullamannen_2023/100M: No race may regress by more than 1 minute or 5%
- OK — race:utmb/kullamannen_2023/100k: No race may regress by more than 1 minute or 5%
- OK — race:utmb/kullamannen_2023/50k: No race may regress by more than 1 minute or 5%
- OK — race:utmb/kullamannen_2024/100M: No race may regress by more than 1 minute or 5%
- OK — race:utmb/kullamannen_2024/100k: No race may regress by more than 1 minute or 5%
- OK — race:utmb/kullamannen_2024/50k: No race may regress by more than 1 minute or 5%
- OK — race:utmb/kullamannen_2025/100M: No race may regress by more than 1 minute or 5%
- OK — race:utmb/kullamannen_2025/100k: No race may regress by more than 1 minute or 5%
- OK — race:utmb/kullamannen_2025/50k: No race may regress by more than 1 minute or 5%
- OK — race:utmb/puertovallarta_2024/100K: No race may regress by more than 1 minute or 5%
- OK — race:utmb/puertovallarta_2024/100M: No race may regress by more than 1 minute or 5%
- OK — race:utmb/puertovallarta_2024/50K: No race may regress by more than 1 minute or 5%
- OK — race:utmb/transjeju_2024/100k: No race may regress by more than 1 minute or 5%
- OK — race:utmb/transjeju_2024/50k: No race may regress by more than 1 minute or 5%
- OK — race:utmb/transjeju_2025/100k: No race may regress by more than 1 minute or 5%
- OK — race:utmb/transjeju_2025/100m: No race may regress by more than 1 minute or 5%
- OK — race:utmb/transjeju_2025/70k: No race may regress by more than 1 minute or 5%
- OK — race:utmb/transjeju_2026/100m: No race may regress by more than 1 minute or 5%
- OK — race:utmb/utmb_2022/ccc: No race may regress by more than 1 minute or 5%
- OK — race:utmb/utmb_2022/mcc: No race may regress by more than 1 minute or 5%
- OK — race:utmb/utmb_2022/occ: No race may regress by more than 1 minute or 5%
- OK — race:utmb/utmb_2022/tds: No race may regress by more than 1 minute or 5%
- ÉCHEC — race:utmb/utmb_2022/utmb: No race may regress by more than 1 minute or 5%
- OK — race:utmb/utmb_2023/ccc: No race may regress by more than 1 minute or 5%
- OK — race:utmb/utmb_2023/occ: No race may regress by more than 1 minute or 5%
- OK — race:utmb/utmb_2023/tds: No race may regress by more than 1 minute or 5%
- ÉCHEC — race:utmb/utmb_2023/utmb: No race may regress by more than 1 minute or 5%
- ÉCHEC — race:utmb/utmb_2024/ccc: No race may regress by more than 1 minute or 5%
- OK — race:utmb/utmb_2024/occ: No race may regress by more than 1 minute or 5%
- OK — race:utmb/utmb_2024/tds: No race may regress by more than 1 minute or 5%
- ÉCHEC — race:utmb/utmb_2024/utmb: No race may regress by more than 1 minute or 5%
- OK — race:utmb/utmb_2025/ccc: No race may regress by more than 1 minute or 5%
- OK — race:utmb/utmb_2025/occ: No race may regress by more than 1 minute or 5%
- OK — race:utmb/utmb_2025/tds: No race may regress by more than 1 minute or 5%
- ÉCHEC — race:utmb/utmb_2025/utmb: No race may regress by more than 1 minute or 5%
- OK — race:utmb/whistler_2024/50k: No race may regress by more than 1 minute or 5%
- OK — race:utmb/whistler_2024/70k: No race may regress by more than 1 minute or 5%
- OK — guard:utmb/transjeju_2025/100m: Transjeju guard must be present and not regress (0.1 minute rounding tolerance)
- OK — guard:utmb/transjeju_2026/100m: Transjeju guard must be present and not regress (0.1 minute rounding tolerance)
- OK — livetrail: At least 20 LiveTrail courses, no material regression
