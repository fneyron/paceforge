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
- geste natif depuis le haut, avec action d’accessibilité sans bouton supplémentaire ;
- actualisation des services connectés et des lectures autorisées du téléphone ;
- contenu conservé pendant l’attente, erreurs réessayables, aucune fausse mesure récente ;
- conservation de la position, des sections et de la date sélectionnée ;
- pas de remplacement du contenu pendant un geste ou son inertie ;
- une seule demande active, nettoyage des écouteurs et observateurs remplacés ;
- aucune actualisation par geste sur les formulaires ou une course en préparation.

Ce sont les critères retenus pour l’implémentation, pas une certification Apple
ni une affirmation que PaceForge égale la fluidité d’une autre application.

## Changements livrés

**Correction des alertes persistantes.** La réussite web envoie désormais un
résultat vide explicite au natif, au lieu de ne rien envoyer après un ancien
échec. L’état Apple Santé reste indépendant : une réussite COROS ne peut pas
effacer une lecture santé partielle. Les erreurs mentionnent le service concerné
et ouvrent son détail. La demande manuelle réserve la synchronisation de la
montre avant de répondre ; le suivi ne peut plus confondre l’ancienne erreur
avec le résultat de la nouvelle tentative encore en file. Une tâche encore en
cours à la fin de l’attente n’est plus transformée en échec.

**Lecture iOS.** Cinq catégories indépendantes d’une journée, puis trois d’une
nuit, sont lues en parallèle, en conservant les budgets et le parcours du récent
vers l’ancien. Les réponses vides et les erreurs restent distinctes. Le détail
indique un dépassement de délai plutôt que d’accuser systématiquement les
autorisations. Le chemin de collecte est testé avec des réponses HealthKit
simulées ; le gain de temps sur l’iPhone du propriétaire reste à mesurer.

**Finition de Santé.** Les trois cadrans restent égaux. Fond léger, cartes sans
cadres imbriqués, chiffres plus lisibles et typographie système rapprochent
l’écran web de son en-tête natif. L’historique de sommeil se déplie au toucher,
ce qui laisse la récupération plus près de la dernière nuit. Les éléments
natifs suivent maintenant le thème clair ou sombre choisi dans Réglages.

**iPhone.** `pullToRefreshEnabled` utilise le contrôle natif de la
[WebView 13.16.1](https://github.com/react-native-webview/react-native-webview/blob/v13.16.1/docs/Reference.md).
Dans cette version, le SDK déclenche un rechargement puis termine immédiatement
l’animation de son contrôle. PaceForge intercepte ce rechargement et lance la
synchronisation ; un seul indicateur discret reste visible pendant l’action
manuelle. Le bouton de l’en-tête et le message flottant ont été retirés. Ce
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
  geste, absence de bouton supplémentaire, progression partagée, échec partiel
  et détail conservé dans Connexions, changement d’onglet pendant collecte, course non rechargée,
  session expirée, interruption réseau, une seule WebView et reprise du processus.
- Chromium mobile 320 et 393 px, et moteur WebKit Linux à 393 px, clair/sombre : balayage vertical, toucher,
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
sur iPhone n’ont pas été mesurés dans cet environnement Linux. Le test WebKit
a utilisé les bibliothèques manquantes dans un répertoire de test isolé ; il
ne remplace pas Safari ni WKWebView sur iOS. La validation finale sur
appareil doit couvrir une version TestFlight, une page riche, plusieurs gestes,
le mode économie d’énergie, le retour au premier plan et un réseau intermittent.
Si des saccades persistent : capturer une trace Instruments/Safari, puis traiter
le coût observé. Une réécriture native complète ne se justifie pas sans ce profil.

## Simplification de Santé

La revue du 12 octobre s’appuie également sur [Oura — Today et Vitals](https://support.ouraring.com/hc/en-us/articles/360058599753-How-to-Use-the-Oura-App)
et [WHOOP — organisation de l’écran principal](https://www.whoop.com/us/en/thelocker/the-all-new-whoop-home-screen/) :
trois indicateurs immédiatement lisibles, puis détails accessibles au toucher.
PaceForge conserve trois cadrans de même taille et de même importance, dans
l’ordre Sommeil, Récupération, Entraînement. Les trois pourcentages conservent
leur sens distinct : part du besoin de sommeil estimé, indice de récupération,
charge relative à l’habitude. La date est regroupée dans l’en-tête ; les cartes
gardent le même ordre. Les valeurs absentes, bilans partiels et références
provisoires restent indiqués. Les mesures utilisées se déplient dans la carte
Récupération. Aucun calcul n’a changé.

Les messages de synchronisation web sont transmis au composant natif qui possède
la seule surface de progression/erreur ; les versions anciennes gardent un repli
dans la page, sans superposition. Un lien ouvre Connexions en cas d’échec. Les
rafraîchissements réussis se terminent sans message de confirmation. Le geste
natif et l’action VoiceOver restent à vérifier sur iPhone physique.

## Reproduire les tests visuels

Depuis la racine, avec les dépendances Python de développement installées :

```sh
DEBUG=false python -m uvicorn tests.browser.preview_app:app --host 127.0.0.1 --port 8852
```

Le serveur utilise uniquement des données synthétiques et doit rester lié à
localhost. Dans un autre terminal :

```sh
npm install --prefix /tmp/pf-browser playwright@1.64.0 esbuild@0.28.2 react@19.2.3 react-dom@19.2.3 react-native-web@0.21.4
export NODE_PATH=/tmp/pf-browser/node_modules
node /tmp/pf-browser/node_modules/playwright/cli.js install chromium
node tests/browser/refresh.cjs
node tests/browser/native/build.cjs
node tests/browser/native/check.cjs
```

`PF_PREVIEW_URL`, `PF_PREVIEW_OUTPUT`, `NATIVE_PREVIEW_BUNDLE` permettent de changer
les destinations. `PF_WEBKIT=1` ajoute WebKit si le navigateur et ses dépendances
sont installés ; `PF_WEBKIT=only` exécute uniquement ce moteur, et
`PF_WEBKIT_EXECUTABLE` permet de fournir son lanceur. Les scénarios ne se
connectent jamais au compte réel de l’athlète.
