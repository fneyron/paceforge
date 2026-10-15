# Santé : contexte, imports et lecture sur iPhone

Cette évolution distingue les mesures nocturnes, les activités enregistrées et le ressenti déclaré. Elle ne transforme pas un score de montre ou une VFC habituelle en autorisation de reprise sportive.

## Ce qui est affiché

- Les trois anneaux, les tendances VFC/FC, le sommeil et son historique restent graphiques. L’hypnogramme exige des intervalles horaires réellement reçus ; des totaux par phase ne permettent pas de reconstruire leur chronologie.
- La rubrique et l’anneau « Récupération » présentent un indice des signaux nocturnes sur 100, pas un pourcentage de récupération. La FC nocturne élevée reste un signal même lorsque la VFC est habituelle. Les règles de transformation numérique restent des heuristiques PaceForge, sans validation clinique de cet indice composite.
- La synthèse montre la date des mesures et deux raisons au maximum. Les méthodes, sources et autres explications sont disponibles dans le volet de détail. La FC nocturne n’est pas assimilée à la FC de repos ; le RMSSD recalculé sur les points nocturnes COROS n’est pas assimilé à son « HRV Avg » ou à sa plage fabricant.
- Le contexte après effort vient exclusivement des activités reçues de Strava, Garmin ou COROS : le planner, facultatif, ne fournit ni nom, ni date, ni qualification de course à Santé. Un type « course » transmis par une activité reste distinct d’un effort long détecté par sa durée. J+n compte les jours calendaires depuis la fin locale réelle. Les 28 jours d’affichage sont une limite de présentation, pas un délai de guérison.
- Les fenêtres après effort ne plafonnent plus l’indice nocturne. Les informations sur l’effort restent disponibles séparément. Aucun seuil absolu VFC 70/72 ms, FC 43/46 bpm, plafond 135 bpm ou prescription de 30 minutes n’a été ajouté.
- Le repère de 8 h de sommeil est indiqué comme tel, sans prétendre mesurer le besoin individuel. L’écart sur 7 jours précise les nuits documentées ; les nuits absentes ne valent pas zéro.

## Ressenti et conservation

Le volet facultatif enregistre uniquement les réponses explicites sur fatigue, douleur et tolérance du dernier effort. Il est daté selon le jour local du compte, même lorsque le bilan nocturne affiche encore hier. Une soumission après changement de jour demande de vérifier les réponses avant d’écrire.

Les réponses ne changent ni l’indice ni les anciens indicateurs de maladie/alcool. Elles résident dans un objet versionné de la ligne `feel`, avec isolation par utilisateur, date et source. Leur effacement conserve les autres renseignements historiques. Aucun brouillon n’est écrit dans le stockage navigateur ; une actualisation peut conserver le formulaire vivant du même compte et du même jour. La suppression du compte supprime ces données.

## Fiabilité des imports

COROS distingue une réponse vide reconnue d’un format non lu et découpe les fenêtres saturées. La réponse « no official sleep HRV value for this day; raw HRV points omitted » désigne une absence de mesure, pas un format illisible ; elle ne doit pas empêcher la lecture des autres nuits du lot. Un import réussi mais partiel reste distingué d’un échec de synchronisation ou d’une connexion expirée. La progression n’est mémorisée qu’après validation de la transaction. Strava poursuit une réconciliation bornée de l’historique ; une première page déjà connue ne prouve plus que tout l’historique est complet. Une révision persistante invalide les caches après une correction, y compris à taille de JSON identique.

Les séances stationnaires courtes restent des activités. Une association course/résultat suit la copie conservée lors du dédoublonnage, sans changer le type fourni par la montre. Les vérifications portent sur le code et des données de test ; elles ne prouvent pas qu’une course précise absente des fournisseurs a été récupérée.

Le compagnon iPhone garde les préférences de compte séparées de la progression d’envoi, annule les suites d’opérations après déconnexion et actualise la page à réception de données nouvelles sans navigation complète. Un envoi interrompu reste à reprendre.

## Fondements et limites

- [Kellmann et al., 2018](https://pubmed.ncbi.nlm.nih.gov/29345524/) : le suivi de la récupération combine des informations physiologiques, subjectives et contextuelles. Aucun indicateur isolé ne résume tous les processus.
- [Fazackerley et al., 2019](https://pubmed.ncbi.nlm.nih.gov/31321510/) : dans une petite étude après 64 km, les évolutions de la VFC et des symptômes ne suivaient pas le même calendrier. Cela soutient leur séparation, sans fournir un délai universel après ultra.
- [Vesterinen et al., 2016](https://doi.org/10.1249/MSS.0000000000000910) : des tendances VFC et références individualisées ont été étudiées pour guider l’entraînement. Ce résultat ne valide ni des seuils absolus communs à tous ni l’indice composite PaceForge.

Les scénarios visuels sont synthétiques : état habituel, FC élevée avec VFC habituelle, nuit manquante et intervalles de sommeil. Les essais navigateur et le harnais du compagnon ne remplacent pas une vérification HealthKit sur un iPhone physique.
