# Publier sur TestFlight sans quota de compilation Expo

Le quota EAS Build concerne les compilations hébergées par Expo. La même application
peut être compilée sur un Mac avec `eas build --local`, puis envoyée à Apple.
Le compte Apple Developer, la signature et le traitement TestFlight restent nécessaires.

## Depuis GitHub Actions

Le workflow `Publish iPhone to TestFlight` compile sur un runner macOS standard
de GitHub, avec Xcode 26.6 et Node 22, puis transmet le fichier `.ipa` produit à EAS Submit.
Il s'exécute manuellement depuis `main` et conserve le profil `production` existant :
bundle `Fr.paceforge.mobile`, application App Store Connect `6821579538`, numéro de
build incrémenté par EAS. Il ne lance aucune compilation sur les serveurs EAS Build.

Le dépôt PaceForge est public au moment de cette configuration : les minutes des
runners standard GitHub y sont gratuites. Cela ne supprime pas les conditions ni les
éventuels coûts de stockage de GitHub ou du compte Apple Developer.

Prérequis unique côté GitHub si les accès Apple sont déjà configurés dans EAS :

1. Créer un jeton pour le compte Expo autorisé sur le projet dans les
   [réglages Expo](https://expo.dev/accounts/fneyron/settings/access-tokens).
2. L'ajouter sous le nom `EXPO_TOKEN` dans les
   [secrets GitHub Actions de PaceForge](https://github.com/fneyron/paceforge/settings/secrets/actions).
   Ne pas le publier dans le dépôt, les journaux, une issue ou un chat.
3. Ouvrir [le workflow TestFlight](https://github.com/fneyron/paceforge/actions/workflows/ios-testflight.yml),
   choisir **Run workflow**, puis **main**. Ou pousser un tag `testflight-…` sur un
   commit de `main` (`git tag testflight-2026-10-14 origin/main && git push origin
   testflight-2026-10-14`) : le workflow démarre seul et refuse un tag hors de `main`.

La vérification initiale s'arrête si ce secret manque. La signature utilise les
certificats et profils déjà gérés par EAS ; ils ne sont pas archivés dans les artefacts
GitHub. La compilation et l'envoi sont deux commandes distinctes : `--auto-submit`
n'est pas pris en charge avec `--local` dans EAS CLI 24.12.1.

Un envoi terminé doit encore être traité par Apple. Vérifier la nouvelle version dans
[TestFlight](https://appstoreconnect.apple.com/apps/6821579538/testflight/ios) avant
d'annoncer qu'elle est installable. Cet envoi ne publie pas l'app sur l'App Store public.

## Synchronisation en arrière-plan : régénérer le profil une fois

Depuis la synchronisation en arrière-plan, l'application signe avec
l'entitlement `com.apple.developer.healthkit.background-delivery` (plugin
HealthKit `"background": true`). Le profil de provisionnement `production`
doit le contenir. Le workflow fige les accès (`--freeze-credentials`) : il ne
modifie jamais ce profil. Si le profil actuel ne le contient pas, la compilation
signée échoue, ou la vérification de l'IPA s'arrête avec « The provisioning
profile lacks com.apple.developer.healthkit.background-delivery ».

Dans ce cas, une seule fois, depuis un ordinateur personnel connecté au compte
Apple propriétaire, dans `mobile/` :

1. Dans [Apple Developer](https://developer.apple.com/account/resources/identifiers/list),
   ouvrir l'identifiant `Fr.paceforge.mobile` : la capacité HealthKit doit être
   active (cocher l'option de livraison en arrière-plan si le portail la propose),
   puis enregistrer.
2. `npx eas-cli@24.12.1 credentials --platform ios`, profil `production`, puis
   « Build Credentials: Manage everything needed to build your project » et
   « Provisioning Profile: Delete one from your project ».
3. Dans le même menu, « All: Set up all the required credentials to build your
   project » : EAS synchronise les capacités de l'identifiant avec les
   entitlements du projet et crée un nouveau profil. Cette étape ne compile rien
   et ne demande pas de Mac ; sur un Mac, une compilation `--local` sans
   `--freeze-credentials` (section suivante) fait de même.
4. Relancer le workflow **Publish iPhone to TestFlight** depuis `main` (secret
   `EXPO_TOKEN` présent) : la vérification de l'IPA contrôle désormais
   l'entitlement dans `embedded.mobileprovision`.

## Depuis un Mac personnel

Installer une version de Xcode compatible avec le projet (26.6 utilisée en CI), Node 22,
CocoaPods et fastlane, puis exécuter depuis `mobile/` :

```sh
npm ci
npx eas-cli@24.12.1 login
pf_release_dir="$(mktemp -d)"
npx eas-cli@24.12.1 build --platform ios --profile production --local --output "$pf_release_dir/PaceForge.ipa" &&
  npx eas-cli@24.12.1 submit --platform ios --profile production --path "$pf_release_dir/PaceForge.ipa" --wait
```

Conserver le compte et le projet Expo existants. Aucun abonnement de compilation Expo
supplémentaire n'est nécessaire pour compiler sur son propre Mac. Un upload manuel avec
les outils Apple est également possible à partir d'une archive correctement signée.

Références : [compilation locale Expo](https://docs.expo.dev/build-reference/local-builds/),
[envoi iOS avec EAS Submit](https://docs.expo.dev/submit/ios/),
[facturation GitHub Actions](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
