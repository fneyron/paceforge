# PaceForge mobile

Application iOS / Android avec **Santé comme tableau de bord**, puis Activités et Courses.
Les connexions restent accessibles par le bouton « Réglages », avec Apple Santé / Health Connect en premier. Les écrans du compte partagent une seule WebView HTTPS ; sa barre
de navigation web et son bandeau sont masqués dans l’application. Le compte
PaceForge fonctionne sans Strava. Garmin, COROS et Strava restent des connexions
serveur accessibles depuis Connexions. COROS et Strava ouvrent directement leur
page dédiée dans le navigateur externe, où tout le parcours OAuth conserve la
même session. Le compte PaceForge est affiché avant validation ; après une
connexion au compte dans le navigateur, la destination choisie est conservée.
Garmin se connecte dans l’application. Le profil, l’apparence et les données personnelles restent disponibles
dans « Compte, apparence et confidentialité ». Les connexions web ne sont pas
dupliquées dans cette page lorsqu’elle est ouverte dans l’application.

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
Cette vérification utilise un `fetch` JSON dans le document déjà chargé, avec
les mêmes cookies et un nonce à usage unique. Elle ne remplace plus la page par
un pont, puis par un second chargement au retour. Revenir à l’onglet précédent
réaffiche son document, sa position et ses graphiques immédiatement. Une lecture
lente peut être quittée par un onglet ; sa réponse tardive est ignorée. Si la
session a expiré, le formulaire de connexion reprend le parcours. Si le compte
a changé, les destinations et copies de l’ancien compte sont invalidées et une
nouvelle page est chargée. Les anciens compagnons gardent leur pont HTML.

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

Sur iPhone, chaque catégorie vérifie d’abord si sa demande d’autorisation a
déjà été présentée, notamment après une mise à jour ajoutant le sommeil ou la
VFC. Une catégorie encore à demander n’est pas interrogée : le reçu indique
« Réglages → Apple Santé → Revoir les autorisations ». Les autres catégories
restent lisibles. Ce contrôle ne révèle pas l’accord ou le refus de lecture ;
une lecture vide après la demande reste une absence de mesure, jamais un zéro.
La synchronisation automatique n’ouvre aucune demande de permission.
Le conseil de déverrouiller l’iPhone est réservé aux données protégées
indisponibles ; les délais, erreurs de lecture et erreurs de traitement des
nuits ont des messages distincts. Un contrôle d’autorisation expiré ne lance
pas de lecture native tardive.

À l’ouverture, au retour et lors d’une actualisation de Santé/Activités, la
vérification du compte utilise un `fetch` authentifié dans la WebView existante,
sans quitter la page. Origine, chemin, nonce et compte associé sont contrôlés
avant toute lecture santé. Une modification de période dans l’URL est permise.
L’association et la synchronisation explicite depuis Réglages utilisent la même
WebView, masquée derrière la progression native pendant leur pont. La page renvoie sa réponse
jusqu’à l’accusé de réception (30 s maximum). Le natif vérifie origine, route
et nonce/challenge, puis consomme la demande avant tout travail asynchrone.
Une réponse répétée ne relance ni l’association ni la synchronisation. La fin,
l’échec ou l’expiration ramènent à un écran utilisable ; les pages techniques
ne deviennent jamais une destination de retour. Une session expirée affiche
toujours le formulaire de connexion avant de reprendre la vérification.
Le document réellement chargé est suivi séparément de la destination demandée.
Une réponse de navigation tardive vers un pont terminé force le retour à un
écran, même si React demandait déjà cette adresse. Le pont reste masqué pendant
ce chargement. Après accusé de réception, les anciennes versions reçoivent aussi
une redirection de la page vers Santé ; un lien « Ouvrir Santé » reste disponible.

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
ligne qui nomme le service concerné et ouvre son détail ; les causes et le reçu
restent accessibles. Le détail Apple Santé ouvre directement le reçu local, même sans réseau ;
l’alerte indique la catégorie ou l’étape en échec. Un refus serveur mentionne son
code et, si disponible, le champ concerné sans recopier les données de santé
de sa réponse. Les erreurs du téléphone et des services sont indépendantes :
la réussite de l’un n’efface pas l’échec de l’autre. Une réussite serveur efface
explicitement son ancienne alerte. Aucun message de succès n’est ajouté. Les sections
ouvertes, le point sélectionné et la position sont conservés. Le remplacement
attend la fin du geste et du défilement ; les anciennes semaines d’activités
déjà ouvertes restent consultables. Les mises à jour automatiques utilisent le
même remplacement discret. Les requêtes sont bornées et une double demande
rejoint celle en cours. Voir [audit et limites des tests](../docs/mobile-fluidite.md).

Santé conserve trois cadrans de même importance : Sommeil, Récupération et
Entraînement. Chacun ouvre son détail, dans le même ordre. Les graphiques
de la dernière nuit et de récupération restent visibles ; l’historique détaillé
du sommeil, les mesures nocturnes, les mesures quotidiennes et la méthode se déplient au toucher. Les bilans partiels et références
provisoires restent signalés, avec les mesures utilisées dans le détail de
récupération. Ce changement d’interface ne modifie ni les mesures reçues ni les
coefficients des calculs.

Les cartes partagent une même surface, une typographie système et des espacements
réguliers. Le thème du site est transmis à l’en-tête et à la navigation natifs,
y compris après une modification dans Réglages. Les trois cadrans restent de
même taille, sans mettre la récupération au-dessus des deux autres.

## Plans hors connexion

Dans une course enregistrée, le menu ⋯ propose « Garder hors connexion ».
Les modifications sont d’abord sauvegardées sur le compte ; le serveur produit
une copie des horaires, points de passage, ravitaillements et barrières horaires.
La copie native est ensuite consultable depuis « Plans hors connexion » dans
Courses, ou depuis l’écran d’erreur réseau, même après redémarrage de l’app.
La date de copie est affichée. Répéter l’action remplace la copie ; il n’y a pas
de mise à jour automatique. Carte, édition et export restent en ligne.
Fermer une copie réaffiche la course encore chargée sans nouvelle requête.
Un écran ayant échoué ou une page différente reste rechargé normalement.

La bibliothèque locale utilise `expo-file-system` 57.0.7 dans le répertoire des
documents de l’application, avec deux fichiers alternés et un index de compte
commis dans SecureStore seulement après écriture. La déconnexion explicite et
le changement de compte invalident l’index avant de remplacer les fichiers.
Les réponses sont liées à l’origine HTTPS, à la page courante, au nonce et à la
course demandée. Les champs sont sélectionnés et validés avant stockage ;
aucun HTML authentifié, jeton ou relevé Santé n’est enregistré dans ces copies.
Les limites sont vingt plans, mille points par plan, 256 ko par réponse et un
million de caractères pour la bibliothèque. Les anciennes versions du compagnon
ne montrent pas ces actions.

`tests/browser/native/offline.cjs` couvre fermeture/réouverture sans page web,
passage après minuit, réponses répétées, échecs d’écriture disque et SecureStore,
changement de compte et déconnexion. Les API natives y sont simulées : vérifier
également le mode avion et la persistance sur un iPhone avec la nouvelle build.

## Synchronisation livrée

- Lecture volontaire des **30 derniers jours** : poids, masse grasse, pas, FC et
  respiration sur la journée. Dernière pesée du jour ; totaux de pas calculés
  par HealthKit / Health Connect ; moyennes quotidiennes pour FC / respiration.
  Les totaux de pas HealthKit, fournis en nombres réels, sont arrondis au pas
  entier avant envoi pour respecter le contrat serveur.
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
  incomplet est relu entièrement à la reprise. Avec cette option, la
  synchronisation continue aussi en arrière-plan (section suivante) ; une
  tentative restée en attente est reprise dès l’ouverture, sans délai de 15 min.
- Mesures quotidiennes envoyées avant la lecture du sommeil. Les lectures
  natives ont un délai maximal ; un échec n’efface pas les imports confirmés.
  Sur iOS, les cinq catégories d’un jour sont lues en parallèle, puis le jour
  précédent ; les trois catégories cardiaques d’une nuit suivent le même principe.
  L’application distingue délai dépassé et lecture indisponible, sans en déduire
  un refus d’autorisation. Une réponse vide reste une lecture vide, jamais un zéro.
  Une nuit rejetée n’empêche pas l’envoi des suivantes ; les erreurs réseau ou
  de compte interrompent l’envoi et la reprise est idempotente.
- Réglages conserve un reçu dans SecureStore, lié au compte : tentative complète
  ou partielle, dernier envoi confirmé, dernière pesée lue et dernière pesée
  retenue par le serveur. Un ancien relevé ignoré n’est pas compté comme reçu.
  Ni une réponse HTTP réussie ni une lecture vide ne prouvent une mesure récente.
- Senssun Health iOS doit écrire ses pesées dans Apple Santé. Aucun accès direct
  à EXZACT/Senssun n'est supposé. Senssun → Health Connect sur Android reste à
  vérifier sur le téléphone concerné.

## Synchronisation en arrière-plan

Exigence du propriétaire : les pesées de la balance (Senssun Health → Apple Santé)
et les nuits arrivent dans PaceForge sans ouvrir l’application. Elle suit
l’interrupteur « Actualisation automatique », une fois le téléphone associé ;
elle est désactivée avec lui, à la dissociation, si la session du site montre un
autre compte, à la déconnexion du site et sur un jeton refusé (401, avec la
demande de reconnexion dans Réglages).

- **iPhone.** Après l’autorisation Apple Santé, l’application enregistre la
  livraison immédiate en arrière-plan du poids, de la masse grasse, du sommeil et
  de la VFC (`configureBackgroundTypes`, entitlement
  `com.apple.developer.healthkit.background-delivery`). iOS réveille alors
  l’application après une nouvelle pesée ou une nuit enregistrée par une montre ;
  les observateurs sont réenregistrés nativement à chaque lancement, avant le
  JavaScript. Il n’y a pas de JavaScript sans écran sur iOS : un réveil lance
  l’application entière, mais la WebView ne se charge qu’au premier passage au
  premier plan. HealthKit garde l’application éveillée environ 25 s : chaque
  réveil lit 3 jours et 3 nuits en 18 s au plus (8 s et 10 s de lecture, 3 s par
  requête, 8 s par envoi), une fois les réveils regroupés (1,5 s), une
  synchronisation à la fois. Une tâche `expo-background-task` (BGProcessingTask,
  réseau requis, au plus toutes les 60 min selon iOS) rattrape les envois manqués.
- **Android.** WorkManager (`expo-background-task`, environ toutes les 60 min)
  exécute la lecture sans écran, avec la permission
  `READ_HEALTH_DATA_IN_BACKGROUND`, demandée en activant l’interrupteur.
  Un jeton de changements Health Connect par catégorie indique ce qui a changé :
  seules ces catégories sont relues sur 3 jours ; un jeton expiré relit 7 jours ;
  un refus de quota arrête la lecture jusqu’à l’heure suivante.
- **Envois.** Un condensat des valeurs déjà envoyées évite les renvois identiques
  (oublié chaque jour) ; le serveur ignore aussi un renvoi identique, sans
  changer la version des données de Santé. L’expiration du jeton avance à chaque
  utilisation, y compris en arrière-plan. Le serveur limite chaque téléphone à
  200 requêtes par 10 min (429, réessai plus tard) et la taille des envois.
- **Trousseau.** L’association, son reçu et l’index des plans hors connexion
  sont lisibles après le premier déverrouillage suivant un redémarrage
  (`AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY`, migration unique des anciens éléments).
  Un trousseau verrouillé n’est jamais lu comme « non associé », et les plans
  hors connexion ne sont plus remplacés par une bibliothèque vide.

Limites, sans contournement possible :

- **iPhone verrouillé** : les données Santé sont chiffrées, aucune lecture n’est
  possible. Le reçu passe « en attente » ; la synchronisation se fait au réveil
  suivant (nouvelle mesure, tâche périodique) ou dès l’ouverture.
- **Application fermée de force** (balayée dans le sélecteur) : iOS ne la réveille
  plus jusqu’à sa prochaine ouverture.
- **Mode économie d’énergie, « Actualisation en arrière-plan » désactivée** :
  iOS peut retarder ou supprimer les réveils, et la tâche périodique peut ne pas
  s’exécuter.
- **Avant le premier déverrouillage après un redémarrage** : ni Santé ni le
  trousseau ne sont lisibles.
- **Sans réseau** : la lecture a lieu, l’envoi échoue ; HealthKit ne renvoie pas
  lui-même la notification. La tâche périodique (réseau requis), le réveil
  suivant ou l’ouverture reprennent l’envoi.
- **Android** : économies de batterie du fabricant, Doze et quotas Health Connect
  en arrière-plan peuvent espacer les lectures.

Après `expo prebuild`, `python3 scripts/check-background-sync.py` vérifie
l’entitlement, le mode « processing », `BGTaskSchedulerPermittedIdentifiers` et
la permission Android (la CI le fait). Le profil de provisionnement doit contenir
l’entitlement de livraison en arrière-plan : le workflow TestFlight fige les
accès (`--freeze-credentials`), il faut donc régénérer le profil une fois
(voir `docs/testflight-without-expo-build-quota.md`). La vérification de l’IPA
s’arrête sinon.

`tests/browser/native/background.cjs` simule un lancement sans écran, des
réveils groupés, un trousseau verrouillé, un jeton refusé et la déconnexion du
site (API natives simulées). À vérifier sur appareil, avec la nouvelle build :

1. Pesée Senssun, iPhone déverrouillé, application en arrière-plan : la pesée
   arrive dans PaceForge (Santé sur le site) sans ouvrir l’application ; noter
   le délai.
2. Même pesée, iPhone verrouillé : rien n’est lu ; après déverrouillage, au réveil
   suivant ou à l’ouverture, la pesée arrive ; Réglages n’affiche jamais
   « Non connecté ».
3. Nuit enregistrée par une montre, application non ouverte : la nuit arrive le
   matin, après la synchronisation de la montre.
4. Après une fermeture forcée : aucun envoi jusqu’à l’ouverture (limite iOS),
   puis reprise normale.
5. En mode économie d’énergie, puis avec l’actualisation en arrière-plan coupée.
6. En mode avion : pesée lue, envoi repris une fois le réseau revenu.
7. Après un redémarrage, avant puis après le premier déverrouillage.
8. Plans hors connexion toujours présents après ces lancements en arrière-plan.
9. Android : activer l’interrupteur (permission en arrière-plan de Health Connect),
   pesée Senssun/balance dans Health Connect, application fermée : envoi à la
   tâche suivante (jusqu’à environ une heure) ; refus de la permission : seule
   l’actualisation à l’ouverture reste ; mode avion et redémarrage comme sur iPhone.

## Association au compte

L'utilisateur se connecte au site dans l'application puis confirme le compte.
Code aléatoire valable 2 minutes, échange unique avec preuve SHA-256 d'un secret
conservé côté natif (PKCE), jeton limité aux imports, à sa révocation et à son
renouvellement, valable 180 jours après sa dernière utilisation. Au premier plan,
l’application le renouvelle tous les 30 jours ; l’ancien reste valable 7 jours,
jusqu’à la première utilisation du nouveau. Seuls les condensats sont conservés
en base ; le jeton natif est dans SecureStore. Avant chaque lecture/envoi au
premier plan, une page du compte signe implicitement son identité via la session
HTTPS ; le pont vérifie URL exacte et nonce et refuse un compte différent. En
arrière-plan, sans page affichée, seul le jeton du téléphone sert : un autre
compte constaté au premier plan, une déconnexion du site ou un jeton refusé
arrêtent la synchronisation en arrière-plan. Aucune donnée ni aucun jeton n'est
placé dans une URL.

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
8. Synchronisation en arrière-plan : liste de la section dédiée.

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
