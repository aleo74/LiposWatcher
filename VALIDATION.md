> Rapport historique avant PostgreSQL. Les commandes de base de données ci-dessous sont obsolètes ; consulter README.md et VALIDATION_POSTGRESQL.md pour la version actuelle.

# Recette du lot modèles, lots, chargeurs et catalogue

Vérifications exécutées le 4 octobre 2026 sous Windows, Docker Desktop Linux et navigateur intégré. Aucun déploiement public effectué.

## Commandes et résultats

| Vérification exécutée | Résultat |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pytest -q` | **18 tests réussis** ; un avertissement de dépréciation Starlette/AnyIO, sans échec |
| `npm test` depuis `frontend/` | **4 tests réussis** : QR, isolation des brouillons, calcul des ampères, filtres/préremplissage des appareils et canaux |
| `npm run build` depuis `frontend/` | TypeScript et Vite réussis |
| `docker build -t lipowatcher:inventory-validation .` | Construction finale réussie ; `npm run build` exécuté dans l’image, bundle `index-BqfwKw_N.js` |
| `docker compose up --build -d` | Image unique construite et application locale recréée, même volume persistant |
| `GET http://127.0.0.1:8000/api/health` | `{"status":"ok"}` |
| `PRAGMA integrity_check`, `PRAGMA foreign_key_check` | `ok` et **0 violation**, sur les bases locale et de recette |

## Couverture API vérifiée par tests

Les scénarios de `tests/test_inventory.py` emploient une base temporaire et trois comptes distincts : administrateur, Alice et Bob. Les données de modèles y sont explicitement fictives.

- Migration depuis les deux premières migrations : comparaison de **tous les champs préexistants** des utilisateurs, batteries, sessions (dont ancien chargeur texte et mesures incomplètes) et étapes cochées ; double exécution des migrations sans duplication. Les nouvelles associations restent NULL. L’ancien test de migration vérifie aussi le rollback d’une migration défectueuse.
- Isolation en lecture et écriture des modèles privés, lots, appareils et batteries. Tentatives de lecture/modification avec un identifiant appartenant à un autre compte refusées ; association d’une batterie ou d’un chargeur étranger refusée. L’administrateur ne peut pas lire les révisions privées non soumises via l’historique du catalogue.
- Proposer, refuser avec motif, publier, modifier, refuser une modification, republier et archiver un modèle. Publication réservée à l’administrateur. Une décision portant sur une révision périmée retourne 409. Le catalogue ne divulgue ni lot, prix, numéro personnel ni session ; son historique public ne contient pas les propositions privées.
- Copie privée d’une publication avec lien/révision d’origine. La modification en attente ne remplace pas la publication et la création utilise la version publiée. Les batteries créées gardent leur capacité et révision après édition/publication/archivage du modèle. Une copie privée garde aussi son instantané.
- Aperçu `001` occupé → `002, 003, 004`, zéros conservés. Une collision ou un doublon annule **toutes** les batteries et le lot ; aucune batterie existante n’est écrasée. Un modèle changé depuis l’aperçu exige de refaire celui-ci. Les lots peuvent associer/retirer des batteries existantes ; supprimer un lot conserve batteries et sessions.
- Deux appareils physiques du même modèle possèdent des identifiants distincts. Canaux hors limites et canaux sans appareil refusés. Renommage/archivage conserve les noms et liens historiques, et la correction d’une ancienne session reste possible. Les nouvelles associations à un appareil archivé sont refusées.
- Duplication : nouveau numéro, caractéristiques et lien de modèle ; aucun historique, cycle antérieur, note historique, étape cochée ou lot copié. Collision refusée. Une charge reste possible avec seulement les mAh et sa date préremplie.
- Validation des nouveaux nombres finis/positifs, URL fabricant HTTP/HTTPS, sources documentaires, noms requis, prix et nombre de canaux. Les tests précédents d’authentification, isolation, calculs, cellules, exports CSV et sauvegarde/restauration restent verts.

## Essais réels dans un navigateur mobile simulé

Recette à **390 × 844**, dans une instance locale séparée sur le port 8003 avec son propre volume, sans modifier le parc réel :

1. Création d’un modèle fictif avec référence, capacité, connecteur et provenance.
2. Création groupée avec quantité 2, aperçu `001, 002`, lot privé, puis ouverture de la fiche `#001` depuis le regroupement.
3. Charge de 780 mAh enregistrée sans renseignement avancé ; compteur 1 et total 780 visibles.
4. Appareil à deux canaux créé. Session de 100 mAh sur le canal 2 avec résistances `5 ; ; 7` ; compteur 2 et total 880 visibles.
5. Filtre appareil/canal : une session retenue, valeurs par cellule `5`, `—`, `7` et légende visible ; aucune substitution par zéro. Réouverture du formulaire : appareil et canal 2 préremplis.
6. Duplication en `003` : zéro charge, zéro mAh et aucun historique. Collision en `002` : message « Ce numéro existe déjà » visible dans le formulaire, saisie conservée.
7. Proposition puis publication du modèle : catalogue révision 1 à 1300 mAh. Édition à 1500 mAh : fiche de travail révision 2 et proposition administrative révision 2 ; publication toujours révision 1 à 1300 mAh.
8. Rechargement après recréation du conteneur : batteries, lot, chargeur, sessions et publication encore visibles. Bundle final vérifié depuis les scripts chargés. Capture dans `docs/mobile-catalogue.png` ; largeur du document 375 px pour une fenêtre de 390 px (barre verticale incluse), sans débordement de la page. Navigation d’atelier défilable horizontalement.

Les contrôles d’accès entre comptes et les refus administratifs ont été testés côté API, pas intégralement rejoués avec plusieurs comptes dans le navigateur. Le navigateur n’est pas un téléphone Android physique.

## Conservation et persistance Docker

- Sauvegarde cohérente de la base réelle avec `python -m backend.backup backup /data/pre-inventory-update.db`, copiée vers `data/pre-inventory-update.db` avant migration. Elle est conservée et exclue de l’image.
- Avant migration réelle : 1 utilisateur, 1 batterie, aucune session, 2 migrations. Comparaison SHA-256 de tous les champs préexistants après migration : **toutes les tables préexistantes conservées**, 3 migrations, intégrité `ok`, aucune violation de clé étrangère. Les tests synthétiques ci-dessus couvrent également une ancienne base contenant des sessions.
- Base réelle après livraison : modèles, lots, chargeurs et révisions **vides**. Aucun exemple fictif ajouté au catalogue réel.
- Recréation du conteneur de recette, avec le même volume `lipowatcher_inventory_check` : **empreintes identiques pour toutes les tables**, comprenant 3 batteries, 2 sessions, 1 modèle, 1 lot et 1 chargeur. La recréation finale conserve aussi la révision en attente.
- Recréation de l’application locale avec son volume existant : **empreintes identiques pour toutes les tables** déjà migrées. Aucun `down -v` utilisé.

## Limites et recette serveur

Coolify, ses adresses de proxy, TLS/HTTPS et Android physique ne sont pas validés ici. Le README fournit la configuration Dockerfile, port 8000, endpoint, montage `/data`, permissions, cookies sécurisés, proxies de confiance, bootstrap du premier compte, sauvegarde/restauration et mise à jour. Le déploiement public reste à réaliser par l’administrateur.

Pas de collecte USB ni de pilotage de chargeur. Les nouvelles associations restent facultatives. Les brouillons restent locaux au navigateur ; aucune synchronisation hors connexion n’est ajoutée. SQLite est prévu pour une seule réplique sur stockage local persistant. Une PWA déjà ouverte peut afficher l’ancien bundle au premier rechargement : laisser installer le service worker mis à jour, puis recharger de nouveau si les nouveaux écrans n’apparaissent pas.

La recette Android/HTTPS à exécuter sur le serveur figure dans le README. La validation du catalogue est administrative et ne certifie pas les caractéristiques ni la sécurité d’une batterie.
