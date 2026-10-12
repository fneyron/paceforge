# PaceForge mobile

Application iOS / Android avec **Santé comme tableau de bord**, puis Activités et Courses.
Les connexions restent accessibles par le bouton « Réglages ». Les écrans du compte partagent une seule WebView HTTPS ; sa barre
de navigation web et son bandeau sont masqués dans l’application. Le compte
PaceForge fonctionne sans Strava. Garmin, COROS et Strava restent des connexions
serveur accessibles depuis Connexions. COROS et Strava ouvrent directement leur
page dédiée dans le navigateur externe, où tout le parcours OAuth conserve la
même session. Le compte PaceForge est affiché avant validation ; après une
connexion au compte dans le navigateur, la destination choisie est conservée.
Garmin se connecte dans l’application. Les réglages complets restent disponibles
dans « Mon compte et confidentialité ».

Santé `/sante` est l’écran principal. L’ancien lien `/accueil` y redirige,
y compris pour les anciennes versions installées. Les sources restent dans les
réglages ; les dates se lisent en touchant les points des graphiques. Les valeurs
anciennes restent signalées. Le thème et le compte sont accessibles dans Réglages.
Les données absentes restent absentes ; une synchronisation récente n’est pas
présentée comme la preuve d’un relevé récent.

## Connexions par service

L’écran Connexions regroupe les comptes COROS, Garmin et Strava, ainsi que
Apple Santé / Health Connect. Chaque connexion a un état et une seule action.
L’utilisateur choisit ses services ; aucun modèle de montre n’est requis.
Les états des comptes viennent de `/mobile/connections`, via la session de la
WebView avec vérification d’origine, de route et de nonce, sans jeton fournisseur.
Ils sont actualisés à l’ouverture de cet écran et au retour du navigateur. Une
lecture impossible reste « État à vérifier », jamais un faux « Non connecté ».

Suunto et Amazfit (Zepp) sont signalés dans « Autres marques » comme restant à
intégrer, sans faux bouton de connexion. Si leur application partage des données
avec Strava ou le référentiel santé du téléphone, l’utilisateur peut connecter
ce service intermédiaire. Cela ne suppose pas que toutes les données sont
partagées ou importées ; les catégories mobiles restent celles décrites
ci-dessous.

Les appareils et capteurs se synchronisent avec l’application du fabricant ou
celle qui enregistre la séance. Les données disponibles dans les activités
partagées passent par ce compte. L’application ne recherche ni n’appaire de
brassard ou de ceinture séparément. La lecture Bluetooth directe des versions
1.0.0 (3) et (4), son SDK et ses permissions ont été retirés. Le pouls en direct
n’est plus proposé. Les enregistrements des fournisseurs restent conservés.

« Connecter Apple Santé / Health Connect » enchaîne confirmation du compte
(PKCE), demande des permissions puis première lecture et envoi. Une association
existante vérifie d’abord le compte web. Une permission demandée ne signifie pas
permission accordée : « Connecté » décrit l’association, pas la fraîcheur des
mesures. Le dernier envoi réel est daté dans le détail. « Revoir les autorisations » relance aussi la lecture.
L’aide explique le partage des balances et applications sportives vers un
service compatible, sans associer la connexion à un modèle précis.

À l’ouverture, au retour et lors d’une actualisation de Santé/Activités, la
vérification du compte utilise un `fetch` authentifié dans la WebView existante,
sans quitter la page. Origine, chemin, nonce et compte associé sont contrôlés
avant toute lecture santé. Une modification de période dans l’URL est permise.
L’association et les opérations de Réglages utilisent la même WebView, masquée
derrière la progression native pendant leur pont. La page renvoie sa réponse
jusqu’à l’accusé de réception (30 s maximum). Le natif vérifie origine, route
et nonce/challenge, puis consomme la demande avant tout travail asynchrone.
Une réponse répétée ne relance ni l’association ni la synchronisation. La fin,
l’échec ou l’expiration ramènent à un écran utilisable ; les pages techniques
ne deviennent jamais une destination de retour. Une session expirée affiche
toujours le formulaire de connexion avant de reprendre la vérification.

## Défilement et actualisation

Sur iPhone, tirer depuis le haut de **Santé ou Activités** utilise le contrôle
de rafraîchissement iOS. C’est le seul déclencheur visible sur ces écrans : aucun
bouton supplémentaire dans l’en-tête. L’action d’accessibilité « Actualiser les
données » du titre PaceForge propose le même parcours à VoiceOver/TalkBack.
Les opérations explicites restent disponibles dans les connexions, notamment
sur Android où ce geste WebView n’est pas proposé. Ni les formulaires ni une
course en préparation ne sont rechargés par ce geste.

L’action vérifie le compte, relit les mesures autorisées du téléphone associé,
relance COROS/Garmin et Strava connectés, puis remplace le contenu sans navigation.
Un seul indicateur discret accompagne l’action manuelle, sans bouton ni message
flottant. Les reprises automatiques restent silencieuses. Un échec affiche une
ligne « Actualisation incomplète » ouvrant les connexions ; les causes et le reçu
restent accessibles. Aucun message de succès n’est ajouté. Les sections
ouvertes, le point sélectionné et la position sont conservés. Le remplacement
attend la fin du geste et du défilement ; les anciennes semaines d’activités
déjà ouvertes restent consultables. Les mises à jour automatiques utilisent le
même remplacement discret. Les requêtes sont bornées et une double demande
rejoint celle en cours. Voir [audit et limites des tests](../docs/mobile-fluidite.md).

Santé conserve trois cadrans de même importance : Sommeil, Récupération et
Entraînement. Chacun ouvre son détail, dans le même ordre. Les graphiques restent
visibles ; la méthode se déplie au toucher. Les bilans partiels et références
provisoires restent signalés, avec les mesures utilisées dans le détail de
récupération. Ce changement d’interface ne modifie ni les mesures reçues ni les
coefficients des calculs.

## Synchronisation livrée

- Lecture volontaire des **30 derniers jours** : poids, masse grasse, pas, FC et
  respiration sur la journée. Dernière pesée du jour ; totaux de pas calculés
  par HealthKit / Health Connect ; moyennes quotidiennes pour FC / respiration.
- Sur **iPhone**, lecture du sommeil sur **60 jours**, avec SDNN, FC et respiration
  à l'intérieur des périodes de sommeil mesurées, du même auteur/appareil.
  Les intervalles sont envoyés à `/api/mobile/nights` puis réduits sur le serveur.
  Les moyennes quotidiennes restent exclues. SDNN ne devient jamais RMSSD.
  Aucune écriture dans le référentiel santé du téléphone. Android garde ses
  cinq catégories quotidiennes ; les nouvelles nuits sont propres à iOS.
- Les imports vont dans `mobile_daily`, avec leur plateforme, leurs sources et
  leur date, séparément des données nocturnes utilisées pour la récupération.
  Un envoi répété remplace le résumé du jour. Un total direct Garmin/COROS a
  priorité sur sa copie mobile ; les pas de plusieurs plateformes ne sont pas
  additionnés. Pour le poids et la masse grasse, la pesée horodatée la plus
  récente prime sur un ancien résumé journalier sans instant de mesure.
  Le poids du profil sportif reste une saisie manuelle distincte.
- Une lecture vide n'efface pas l'historique : sur iOS, refus d'accès et absence
  de données ne peuvent pas être distingués. Une suppression dans Apple Santé
  ne supprime donc pas automatiquement un ancien résumé PaceForge. L'écran
  « Téléphones associés » permet de supprimer tous les imports et accès mobiles.
- Synchronisation manuelle complète ; option à l’ouverture et au retour au
  premier plan, au plus toutes les 15 min. Lecture des 7 derniers jours avec
  recouvrement ; historique complet toutes les 6 h. Une tentative partielle
  redevient éligible après 5 min, au prochain retour/ouverture. Un historique
  incomplet est relu entièrement à la reprise. **Pas de tâche lorsque
  l’application est fermée**, ni de minuteur de synchronisation en arrière-plan.
- Mesures quotidiennes envoyées avant la lecture du sommeil. Les lectures
  natives ont un délai maximal ; un échec n’efface pas les imports confirmés.
  Une nuit rejetée n’empêche pas l’envoi des suivantes ; les erreurs réseau ou
  de compte interrompent l’envoi et la reprise est idempotente.
- Réglages conserve un reçu dans SecureStore, lié au compte : tentative complète
  ou partielle, dernier envoi confirmé, dernière pesée lue et dernière pesée
  retenue par le serveur. Un ancien relevé ignoré n’est pas compté comme reçu.
  Ni une réponse HTTP réussie ni une lecture vide ne prouvent une mesure récente.
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
page. Les déclarations santé des stores doivent correspondre aux catégories réellement lues sur chaque plateforme.

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
permission d’écriture. Après `expo prebuild`, vérifier le fichier généré :

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

## Nuits iOS et tests lactate — octobre 2026

Références de méthode et limites : `../docs/sante-evidence-2026-10.md`.
`night-samples.ts` regroupe les intervalles par auteur/appareil et fuseau de
l’échantillon (sinon fuseau actuel du téléphone). Le jour est celui du réveil.
« Au lit » et saisies manuelles sont exclus. Les éveils explicites prévalent
sur les chevauchements ; les trous ne deviennent pas du sommeil. Les durées
sont calculées en temps écoulé, y compris pendant les changements d’heure.

Le serveur exige 3 h de sommeil pour le principal, 12 relevés de FC ou
3 relevés de SDNN/respiration espacés sur au moins 2 h (heuristiques, non
critères cliniques). VFC : moyenne géométrique du SDNN reçu, sans conversion.
Les sources `Apple Santé:<empreinte>` sont distinctes des imports directs.
Les identifiants matériels sont hachés sur l’iPhone. Si HealthKit ne fournit
pas d’identifiant local, l’identité utilise le modèle matériel fourni : deux
exemplaires du même modèle peuvent alors être indiscernables. Les changements
de dispositif/méthode recommencent une référence lorsque l’identité change.

Les reprises sont idempotentes. Refus/lectures vides ne suppriment pas un
historique ; une modification de fenêtre invalide les anciens résumés cardiaques.
La suppression des imports mobiles supprime aussi ces nuits. Révoquer un
accès conserve les données. Réglages permet de préférer Apple Santé ; en
automatique, les connexions directes précèdent leurs copies Apple.

Lactate : mis de côté à la demande du propriétaire. Aucun accès dans Santé ou
l’aide ; routes privées et anciens relevés conservés, aucune permission ajoutée.
