# Défilement et actualisation — audit du 12 octobre 2026

## Niveau attendu

Les recommandations [Apple sur le chargement](https://developer.apple.com/design/human-interface-guidelines/loading)
et les [indicateurs de progression](https://developer.apple.com/design/human-interface-guidelines/progress-indicators)
privilégient un retour visible sans interrompre le contenu utile. Le contrôle
[UIRefreshControl](https://developer.apple.com/documentation/uikit/uirefreshcontrol)
porte le geste standard de rafraîchissement. Le guide de performance
[React Native 0.86](https://reactnative.dev/docs/0.86/performance), version utilisée
ici, rappelle le budget de 16,67 ms par image à 60 Hz et la nécessité de mesurer
une compilation de production. Une WebView peut défiler correctement, mais son
DOM, ses graphiques et ses recalculs peuvent aussi bloquer le rendu.

Pour PaceForge, cela se traduit par :

- défilement vertical confié au système, sans gestionnaire JavaScript qui le bloque ;
- geste natif depuis le haut, et bouton accessible de même effet ;
- actualisation des services connectés et des lectures autorisées du téléphone ;
- contenu conservé pendant l’attente, erreurs réessayables, aucune fausse mesure récente ;
- conservation de la position, des sections et de la date sélectionnée ;
- pas de remplacement du contenu pendant un geste ou son inertie ;
- une seule demande active, nettoyage des écouteurs et observateurs remplacés ;
- aucune actualisation par geste sur les formulaires ou une course en préparation.

Ce sont les critères retenus pour l’implémentation, pas une certification Apple
ni une affirmation que PaceForge égale la fluidité d’une autre application.

## Changements livrés

**iPhone.** `pullToRefreshEnabled` utilise le contrôle natif de la
[WebView 13.16.1](https://github.com/react-native-webview/react-native-webview/blob/v13.16.1/docs/Reference.md).
Dans cette version, le SDK déclenche un rechargement puis termine immédiatement
l’animation de son contrôle. PaceForge intercepte ce rechargement et lance la
synchronisation ; l’icône de l’en-tête reste en progression jusqu’à sa fin. Ce
n’est pas une imitation du geste par `touchmove`. Le redémarrage d’un processus
WebView interrompu reste autorisé à recharger réellement le document.

**Session.** La vérification silencieuse du compte s’effectue dans la WebView
affichée, avec les mêmes cookies et un nonce à usage unique, avant de lire Santé.
Elle ne quitte plus le tableau de bord pour montrer une page « Compte vérifié ».
Une session expirée demande une connexion ; une différence de compte empêche la
lecture. Pendant la collecte vérifiée, les onglets principaux restent utilisables.
La fin de la collecte ne renvoie pas vers un ancien onglet ni ne recharge une
course commencée entre-temps.

**Données.** La demande manuelle relance les services réellement connectés.
COROS/Garmin sont suivis jusqu’à fin/échec, avec attente bornée ; une tâche encore
active au terme du délai n’est pas déclarée terminée. Strava expose son échec au
lieu de le transformer en « déjà à jour ». Les reçus mobiles distinguent lecture,
envoi confirmé et import incomplet. Une interruption conserve les dernières
valeurs. Les anciennes semaines déjà ouvertes d’Activités restent un instantané
consultable ; le rafraîchissement vise les semaines récentes.

**Graphiques.** Les cartes compactes n’essaient plus successivement toutes les
légendes pour mesurer leur hauteur. Leur hauteur est déjà stable en CSS. Les
mouvements horizontaux sont regroupés par image ; une direction verticale prise
au début du geste ne devient pas ensuite une sélection accidentelle. Le zoom à
deux doigts, le toucher simple et le clavier restent disponibles.

## Vérifications effectuées et limites

- Régressions serveur : fraîcheur, ordre des pesées, isolation des comptes,
  données manquantes, synchronisations en attente/terminées/en erreur.
- Tests mobiles : étapes quotidiennes avant sommeil, panne et reprise, délais,
  nonce, compte différent, cadence et réponse d’authentification.
- Tests du **vrai composant App** avec interfaces natives simulées : ouverture,
  geste/bouton, changement d’onglet pendant collecte, course non rechargée,
  session expirée, interruption réseau, une seule WebView et reprise du processus.
- Chromium mobile 320 et 393 px, clair/sombre : balayage vertical, toucher,
  position et point conservés, remplacement différé pendant le geste, anciennes
  semaines conservées, compte changé, panne réseau, double demande.
- Précompilation Expo, export des deux plateformes et chaînes d’explication des
  autorisations HealthKit vérifiées. La disponibilité TestFlight doit être
  confirmée séparément après traitement Apple.

Sur une même page synthétique riche, trois chargements Chromium par variante :
**120, 121, 120 recalculs de mise en page avant**, contre **9, 8, 8 après**.
Il s’agit du chargement initial avec seul `pf-viz.js` substitué pour la comparaison.
Ce n’est ni une mesure de FPS de l’iPhone ni une preuve de disparition de toutes
les saccades. Les durées varient sur cette machine partagée et ne servent pas à
annoncer un gain de vitesse chiffré.

Le contrôle natif, les lectures HealthKit réelles, VoiceOver et les images perdues
sur iPhone n’ont pas été mesurés dans cet environnement Linux. WebKit de test
nécessite des bibliothèques système absentes ici. La validation finale sur
appareil doit couvrir une version TestFlight, une page riche, plusieurs gestes,
le mode économie d’énergie, le retour au premier plan et un réseau intermittent.
Si des saccades persistent : capturer une trace Instruments/Safari, puis traiter
le coût observé. Une réécriture native complète ne se justifie pas sans ce profil.

## Reproduire les tests visuels

Depuis la racine, avec les dépendances Python de développement installées :

```sh
DEBUG=false python -m uvicorn tests.browser.preview_app:app --host 127.0.0.1 --port 8852
```

Le serveur utilise uniquement des données synthétiques et doit rester lié à
localhost. Dans un autre terminal :

```sh
npm install --prefix /tmp/pf-browser playwright esbuild react react-dom react-native-web
export NODE_PATH=/tmp/pf-browser/node_modules
node /tmp/pf-browser/node_modules/playwright/cli.js install chromium
node tests/browser/refresh.cjs
node tests/browser/native/build.cjs
node tests/browser/native/check.cjs
```

`PF_PREVIEW_URL`, `PF_PREVIEW_OUTPUT`, `NATIVE_PREVIEW_BUNDLE` permettent de changer
les destinations. `PF_WEBKIT=1` ajoute WebKit si le navigateur et ses dépendances
sont installés. Les scénarios ne se connectent jamais au compte réel de l’athlète.
