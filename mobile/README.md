# PaceForge mobile

Application iOS / Android avec **Santé comme accueil**, puis Activités, Courses
et Appareils. Les écrans du compte partagent une seule WebView HTTPS ; sa barre
de navigation web est masquée dans l’application. Le compte
PaceForge fonctionne sans Strava. Garmin, COROS et Strava restent des connexions
serveur accessibles depuis Appareils. COROS et Strava ouvrent directement leur
page dédiée dans le navigateur externe, où tout le parcours OAuth conserve la
même session. Le compte PaceForge est affiché avant validation ; après une
connexion au compte dans le navigateur, la destination choisie est conservée.
Garmin se connecte dans l’application. Les réglages complets restent disponibles
dans « Mon compte et confidentialité ».

## Appareils et Bluetooth direct

L’onglet Appareils présente cinq lignes : Apple Santé / Health Connect, COROS,
Garmin, Strava et capteur Bluetooth. Chaque ligne a un état et une seule action.
Les états des comptes viennent de `/mobile/connections`, via la session de la
WebView avec vérification d’origine, de route et de nonce, sans jeton fournisseur.
Ils sont actualisés à l’ouverture de l’onglet et au retour du navigateur. Une
lecture impossible reste « État à vérifier », jamais un faux « Non connecté ».
Les options, l’aide balance et Zwift sont dans des écrans de détail.

« Connecter Apple Santé / Health Connect » enchaîne confirmation du compte
(PKCE), demande des permissions puis première lecture et envoi. Une association
existante vérifie d’abord le compte web. Une permission demandée ne signifie pas
permission accordée : « Données synchronisées » nécessite un envoi réel, avec
sa date dans le détail. « Revoir les autorisations » relance aussi la lecture.

La lecture cardiaque utilise
`react-native-ble-manager` 12.5.3 (architecture native React Native), le service
Bluetooth standard Heart Rate `180D` et la caractéristique `2A37`.

- Le bouton de recherche demande la permission Bluetooth, recherche pendant
  15 secondes puis laisse l’utilisateur choisir son capteur. Aucun appairage
  automatique, écriture de données utilisateur ou modification de firmware.
- Brassard COROS Heart Rate Monitor et APEX 4 en mode diffusion cardiaque :
  le fabricant documente la transmission vers des applications compatibles.
  Aucune connexion au matériel physique n’a été vérifiée dans l’environnement
  de développement. Le brassard accepte au plus trois connexions simultanées.
- Le pouls apparaît dans Appareils et en haut de Santé. C’est une **lecture en
  direct, sans enregistrement ni envoi au serveur** ; elle n’alimente ni la
  moyenne sur 24 h, ni le calcul de récupération, ni un enregistrement de séance.
- Une valeur sans contact, malformée, ou vieille de 15 secondes n’est jamais
  affichée comme actuelle. Arrêt à la mise en arrière-plan, déconnexion, retrait
  du Bluetooth, et délai maximal de connexion de 20 secondes. Reconnexion à
  l’initiative de l’utilisateur. Pas de lecture quand l’application est fermée.
- DURA et l’historique APEX passent par le compte COROS. Les séances Zwift peuvent
  être envoyées à COROS ou Strava, puis importées depuis le compte connecté ;
  vérifier une séance réelle. Pas de pilotage Bluetooth du home trainer.
- EXZACT : le protocole direct dépend du modèle exact, non identifié à ce jour.
  Le parcours iPhone disponible reste SenssunHealth → Apple Santé → PaceForge.

Références constructeur :
[brassard COROS](https://support.coros.com/hc/en-us/articles/17458038294292-COROS-Heart-Rate-Monitor-FAQs),
[APEX 4](https://support.coros.com/hc/en-us/articles/42070715941268-Set-Up-Your-COROS-APEX-4),
[Zwift vers COROS](https://support.coros.com/hc/fr/articles/47288305838996-Connexion-de-Zwift-avec-COROS).

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
npm run ios     # macOS et Xcode ; signature Apple pour un iPhone physique
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
permissions santé Android sont exclusivement en lecture ; le plugin local ajoute le
contrat d'autorisation et route les intentions de confidentialité vers cette
page. Les déclarations santé des stores doivent correspondre à ces cinq usages.

## Envoi iPhone sur TestFlight

La destination du profil `submit.production.ios` est la fiche App Store Connect
**6821579538**, fournie par le propriétaire, avec le bundle
**`Fr.paceforge.mobile`**. La fiche est accessible à l'équipe Apple autorisée :
<https://appstoreconnect.apple.com/apps/6821579538/testflight/ios>.

Le profil de compilation `production` produit une version iPhone destinée au
store (`distribution: store`, `simulator: false`) et incrémente son numéro.
Le projet est associé au compte Expo **`fneyron`**, sous l'identifiant EAS
**`4e22becd-175c-45e0-bf2d-3e40607a6305`** :
<https://expo.dev/accounts/fneyron/projects/paceforge>.
`app.json` conserve cette association ; il n'est pas nécessaire de recréer le
projet avec `eas init`.

Avant le premier envoi, le propriétaire doit connecter son compte Apple dans
EAS CLI pour configurer les certificats, le profil de provisionnement et l'accès
App Store Connect. Une connexion Apple dans le navigateur ne configure pas ces
accès pour EAS. Depuis un ordinateur personnel, dans `mobile/` :

```sh
npm ci
npx eas-cli@24.12.1 login
npx eas-cli@24.12.1 build --platform ios --profile production --auto-submit-with-profile production
```

Utiliser le compte Expo `fneyron`, puis le compte Apple propriétaire de la fiche
**6821579538**. Les invites de la compilation permettent de créer les accès
manquants ; saisir le mot de passe Apple et le code de validation uniquement dans
ce terminal personnel. Conserver le bundle **`Fr.paceforge.mobile`**, avec le
**F majuscule**, tel qu'enregistré dans Apple Developer : la recherche de
l'identifiant existant par EAS CLI 24.12.1 compare exactement la casse. La gestion
séparée des accès reste possible avec `eas credentials --platform ios`, profil
`production`.

L'application utilise HTTPS, le trousseau système via SecureStore et SHA-256
pour l'association, sans chiffrement personnalisé. La configuration
`ios.config.usesNonExemptEncryption: false` évite la question de déclaration de
chiffrement à chaque envoi. Réévaluer cette valeur si les usages cryptographiques
changent.

Après cette configuration, pour les envois suivants depuis `mobile/` :

```sh
npx eas-cli@latest build --platform ios --profile production --auto-submit-with-profile production
```

Cette commande compile une nouvelle version signée avec l'icône du projet et
l'envoie sur TestFlight. Après le traitement Apple, ajouter le propriétaire à
un groupe de test interne pour l'installer. La publication publique sur
l'App Store reste une étape distincte dans App Store Connect.

Pour exécuter la commande sans interaction, le compte Expo doit être accessible
via `EXPO_TOKEN` et les accès Apple doivent avoir été configurés au préalable.
Conserver les jetons et fichiers de signature dans le gestionnaire de secrets
de l'environnement de compilation ; ils ne font pas partie du dépôt.

## Validation avant diffusion

### Textes de confidentialité iOS

Le SDK HealthKit embarque des API de lecture et d'écriture. Apple exige donc
`NSHealthShareUsageDescription` et `NSHealthUpdateUsageDescription` dans le
`Info.plist`, même si PaceForge ne demande que des permissions de lecture.
Conserver les deux textes du plugin dans `app.json` ; ils ne donnent aucune
permission d’écriture. Le plugin Bluetooth ajoute aussi
`NSBluetoothAlwaysUsageDescription`, contrôlé par le même script. Après
`expo prebuild`, vérifier le fichier généré :

```sh
python3 scripts/check-ios-health-privacy.py ios/PaceForge/Info.plist
```

La CI applique aussi ce contrôle au `Info.plist` de l'application compilée pour
le simulateur. Le script accepte également le fichier extrait d'un `.ipa` signé.

### Icônes

`assets/icon.png` est l'icône carrée opaque de 1024 × 1024 pixels utilisée
pour iOS et les icônes Android classiques. Les coins sont découpés par le
système. `assets/adaptive-icon.png` conserve une marge supplémentaire pour
que le tracé reste visible dans les différents masques Android ; son fond
est assorti au `backgroundColor` de la configuration Expo.

`expo prebuild` génère les tailles natives à partir de ces fichiers. L'icône
App Store Connect / TestFlight sera transmise avec la prochaine compilation
iPhone signée : modifier les images ne met pas à jour une version déjà diffusée.

### Synchronisation

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

Le workflow mobile peut être lancé manuellement avec `platform=all`, `android`
ou `ios`. Il compile un APK Android avec SDK/NDK et/ou une application iOS native
Release pour le simulateur Apple Silicon sur macOS avec Xcode 26.6.
L'artefact `paceforge-android-preview` est signé
avec la clé de test du projet généré, pour installation de test uniquement ;
il ne remplace pas une signature Play Store. Il n'inclut pas de serveur Metro.
L'artefact `paceforge-ios-simulator` contient l'application compressée, conservée
dès que la compilation a réussi. Elle inclut son bundle JavaScript et ne
nécessite pas Metro. Un simulateur iPhone avec la version d'iOS correspondant
au SDK Xcode démarre pendant la compilation. Un contrôle distinct vérifie
ensuite que l'application reste en cours d'exécution après lancement.
Ce contrôle est limité à douze minutes ;
en cas d'échec, son journal est conservé et la compilation reste téléchargeable.
L'artefact `paceforge-ios-simulator-screenshot` contient la capture si le
lancement a réussi. Après extraction de l'application sur un Mac Apple Silicon,
installer `PaceForge.app` dans un simulateur démarré avec
`xcrun simctl install booted /chemin/PaceForge.app`, puis
`xcrun simctl launch booted Fr.paceforge.mobile`.

Cette compilation sans signature ne peut pas être installée sur un iPhone
physique ni envoyée sur TestFlight. La distribution iOS signée nécessite le
compte Apple Developer du propriétaire. Les essais réels Senssun → Apple Santé
→ PaceForge et Health Connect restent à faire sur les appareils concernés ; un
lancement réussi dans le simulateur ne valide pas ces synchronisations.
