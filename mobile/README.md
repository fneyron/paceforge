# PaceForge mobile

Compagnon iOS / Android avec trois onglets : Synchronisation native, Santé et
Activités (écrans web du compte PaceForge dans une WebView HTTPS). Le compte
PaceForge fonctionne sans Strava. Garmin, COROS et Strava restent des connexions
serveur accessibles dans Réglages.

## Synchronisation livrée

- Lecture volontaire des **30 derniers jours** : poids, masse grasse, pas, FC et
  respiration sur la journée. Dernière pesée du jour ; totaux de pas calculés
  par HealthKit / Health Connect ; moyennes quotidiennes pour FC / respiration.
- Aucune écriture dans le référentiel santé du téléphone. Pas de demande de
  permission sommeil/VFC tant que leur filtrage nocturne mobile n'est pas livré.
- Les imports vont dans `mobile_daily`, avec leur plateforme, leurs sources et
  leur date, séparément des données nocturnes utilisées pour la récupération.
  Un envoi répété remplace le résumé du jour. Un total direct Garmin/COROS a
  priorité sur sa copie mobile ; les pas de plusieurs plateformes ne sont pas
  additionnés. Le poids du profil sportif reste une saisie manuelle distincte.
- Une lecture vide n'efface pas l'historique : sur iOS, refus d'accès et absence
  de données ne peuvent pas être distingués. Une suppression dans Apple Santé
  ne supprime donc pas automatiquement un ancien résumé PaceForge. L'écran
  « Téléphones associés » permet de supprimer tous les imports et accès mobiles.
- Synchronisation manuelle, et option au retour au premier plan au plus toutes
  les 6 h. **Pas de tâche lorsque l'application est fermée** dans cette version.
- Senssun Health iOS doit écrire ses pesées dans Apple Santé. Aucun accès direct
  à EXZACT/Senssun n'est supposé. Senssun → Health Connect sur Android reste à
  vérifier sur le téléphone concerné.

## Association au compte

L'utilisateur se connecte au site dans l'application puis confirme le compte.
Code aléatoire valable 2 minutes, échange unique avec preuve SHA-256 d'un secret
conservé côté natif (PKCE), jeton limité aux imports et à sa révocation, valable
180 jours. Seuls les condensats sont conservés en base ; le jeton natif est dans
SecureStore. Avant chaque lecture/envoi, une page du compte signe implicitement
son identité via la session HTTPS ; le pont vérifie URL exacte et nonce et refuse
un compte différent. Aucune donnée ni aucun jeton n'est placé dans une URL.

## Développement et installation

```sh
cd mobile
npm ci
npm run typecheck
npm test
npx expo prebuild --no-install
npm run android # Android Studio, SDK et appareil/émulateur nécessaires
npm run ios     # macOS, Xcode et signature Apple nécessaires
```

Expo Go ne contient pas HealthKit/Health Connect : utiliser une compilation
native. `eas.json` fournit les profils de développement, de prévisualisation
(APK Android / distribution interne iOS) et de production. Configurer le projet
EAS sous le compte du propriétaire avant un build hébergé. La distribution iOS
exige les identifiants Apple Developer et, hors TestFlight, les appareils
provisionnés. L'envoi sur les stores n'est pas réalisé par un déploiement web.

```sh
npx expo export --platform ios --platform android
npx eas-cli build --platform android --profile preview
npx eas-cli build --platform ios --profile preview
```

La page de confidentialité est `https://paceforge.fr/mobile/privacy`. Les
permissions Android sont exclusivement en lecture ; le plugin local ajoute le
contrat d'autorisation et route les intentions de confidentialité vers cette
page. Les déclarations santé des stores doivent correspondre à ces cinq usages.

## Validation avant diffusion

Les tests serveur couvrent association, rejeu, révocation, isolation des comptes,
unités, dates, import idempotent et priorité des sources. TypeScript, tests de
calendrier/changement d'heure/pont et export des bundles iOS/Android complètent
la vérification statique. Ils ne remplacent pas les essais sur appareils réels :

1. Association, connexion après expiration de session, changement de compte refusé.
2. Accepter une seule catégorie, puis refuser/retirer les autres ; aucun faux zéro.
3. Pesée Senssun déjà visible dans Apple Santé ; vérifier date, poids et source.
4. Deux envois successifs et correction d'une pesée ; une valeur par jour.
5. Pas provenant de montre + téléphone ; comparer au total de santé du système.
6. Coupure réseau, révocation depuis le site, retour au premier plan, reprise.
7. Écran de confidentialité ouvert depuis les permissions Health Connect.

Aucun APK/IPA signé ni validation réelle HealthKit/Health Connect n'est produit
par l'environnement Linux de développement actuel (pas de SDK Android/Xcode).
