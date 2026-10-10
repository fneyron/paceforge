# Synchronisation Garmin

Le lien Garmin et l’import sont deux états distincts. « Connecté » confirme le
lien ; Réglages affiche séparément l’attente, l’import en cours, sa réussite,
son interruption ou une récupération partielle. Les quantités et les dernières
dates disponibles sont calculées sur les seules données Garmin du compte.
Une absence de nuit ne produit pas de score de récupération.

Les imports demandés dans Réglages, après connexion ou à l’ouverture de Santé
passent par le worker Celery. La requête web réserve l’import en base puis le
met en file ; la page interroge son état toutes les trois secondes et s’arrête
une fois terminé. Fermer la page ne coupe pas le travail. Une réservation empêche
les doubles imports ; une tâche retardée ne peut pas prendre une réservation
plus récente. Une réservation sans résultat expire après quinze minutes et
l’interface propose alors de relancer. Un échec de mise en file libère la réservation.

La tâche horaire existante vérifie les comptes à synchroniser, environ toutes
les deux heures par compte. Le premier import couvre 60 dates de santé et
180 jours d’activités. Les imports suivants revérifient les sept dernières dates
et rattrapent une interruption plus longue, avec les mêmes plafonds d’historique.
Les dates de santé comprennent le lendemain UTC pour les athlètes dont la
matinée a déjà commencé. Un historique vide mais correctement vérifié n’est
pas repris intégralement à chaque passage.

Une requête échouée, une limite Garmin ou un budget d’appels atteint produit un
bilan incomplet, même lorsque d’autres données ont été enregistrées. Les données
reçues restent et l’historique est redemandé au prochain passage. Un import
dispose de 160 appels et de dix minutes de collecte au maximum. Les activités
sont limitées à dix pages de cent éléments ; atteindre cette limite est signalé
comme incomplet. Les réponses vides normales restent distinctes des erreurs.

`garmin_connections.sync_summary` conserve le résultat, les périodes demandées,
le nombre d’appels échoués, les ajouts et la nécessité de reprendre l’historique.
Cette colonne ne contient ni jeton, ni identifiant Garmin, ni mesure de santé.
La migration ajoute une colonne JSON nullable, sans modifier les données existantes.
