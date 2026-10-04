> Rapport de migration initiale. Le verrou global décrit ici a été remplacé depuis ; consulter VALIDATION_VERROUS.md et le README pour le fonctionnement courant. Les tests et résultats ci-dessous correspondent à la migration initiale.

# Validation de la migration PostgreSQL — 5 octobre 2026

## Résultat

Migration terminée sur une **véritable instance PostgreSQL 17.11** (`postgres:17-alpine`, version majeure fixée à 17). L’application locale est disponible sur `http://127.0.0.1:8000`. Les services `app` et `postgres` sont healthy. La base de développement `lipowatcher` contient **0 utilisateur et 0 batterie**, révision `0001_postgresql`. Aucun import SQLite, compte automatique ou jeu de démonstration en production. Aucun déploiement public ni modification de ressource distante.

## Changements constatés dans le code

- `backend/db.py` : SQLAlchemy Core 2.0.43 + psycopg 3.2.10, pool par worker configurable, pre-ping, recyclage, isolation explicite READ COMMITTED, paramètres liés, rollback et restitution des connexions. La révision attendue est lue depuis les fichiers Alembic de l’image.
- `backend/migrate.py`, `alembic.ini`, `backend/migrations/env.py`, `backend/migrations/versions/0001_postgresql.py` : schéma complet vide, migration explicite, DDL et marqueur atomiques, verrou advisory contre deux jobs concurrents. Aucun appel de migration au démarrage Uvicorn. Le template permet de créer des révisions manuelles futures.
- `backend/app.py`, `accounts.py`, `inventory.py`, `transfers.py` : requêtes PostgreSQL, erreurs d’intégrité SQLAlchemy, recherche ILIKE et unicité des identifiants/courriels sans distinction de casse. Formats des API, textes JSON et dates ISO conservés.
- Limites : compteurs durables PostgreSQL. Le contrôle des échecs de connexion, le hachage et l’enregistrement du résultat sont protégés dans une transaction commune ; aucune lecture obsolète des compteurs entre workers. Les demandes de courrier sont également comptées atomiquement.
- Transferts : sérialisation transactionnelle des décisions, `FOR UPDATE` sur la proposition acceptée et index unique partiel des propositions en attente. Les lectures de fiche gardent un verrou de partage sur la propriété ; les exports sont protégés pendant leur construction. Les droits, exclusions privées, historique verrouillé et annotations sont conservés.
- `Dockerfile` : image API/front unique, `npm ci`, deux workers configurables, aucun volume de base ni dépendance de test en production. `compose.yaml` : uniquement app/postgres, volume PostgreSQL persistant, healthchecks, aucun port PostgreSQL publié sur l’hôte.
- Suppression de l’ancien moteur/migrateur SQLite, du module `backend/backup.py`, des migrations SQL SQLite et des anciens helpers Docker liés à SQLite. Les anciens rapports sont signalés comme historiques.
- `.env.example`, `.env.local.example`, README : URL de connexion, pool, séparation des tests, migrations et démarrage, Coolify/PostgreSQL externe, HTTPS, SMTP, proxies fiables, sauvegarde/restauration.

## Tests exécutés et résultats réels

| Vérification exécutée | Résultat |
|---|---|
| Suite backend sur l’image applicative finale, PostgreSQL réel | **37 passed**, 28,77 s ; un avertissement de dépréciation Starlette/AnyIO |
| Tests frontend `npm --prefix frontend test` | **4 passed** |
| Compilation `npm --prefix frontend run build` | TypeScript, Vite et génération PWA réussis |
| `docker compose build app` | Image unique API + front construite ; dependencies de test absentes de l’image finale |
| Migration explicite répétée | Révision inchangée ; données de test existantes conservées |
| Deux jobs Alembic simultanés sur un schéma vide | Deux sorties 0 ; une seule révision appliquée |
| Migration volontairement défaillante | Table créée puis annulée ; marqueur précédent intact |
| Démarrage avant migration | Refus de démarrer ; aucune création automatique de schéma |
| Connexion rendue après transaction | Utilisation après fermeture rejetée |
| Santé avec révision incorrecte | HTTP 503, sans détails de connexion |
| Santé finale | HTTP 200, `{"status":"ok"}` ; Docker healthy |
| Front servi par le conteneur | GET `/` : HTTP 200 |
| Huit acceptations simultanées, **deux processus Uvicorn réels** | **1 × 200 et 7 × 409**, une seule propriété transférée |
| Douze connexions erronées simultanées, deux workers | **5 × 401 et 7 × 429**, deux compteurs partagés à 5 |
| Douze récupérations de mot de passe simultanées, deux workers | **3 × 202 et 9 × 429**, exactement 3 messages reçus par SMTP TCP de test ; compteurs à 12 sans perte |
| Recréation réelle de `app` et `postgres` avec le volume existant | Empreinte et nombres de lignes de toutes les 15 tables identiques |
| Dump PostgreSQL custom puis restauration dans une nouvelle base vide | Empreinte et nombres de lignes identiques avant tout nouvel accès API |
| API après restauration | Connexion, modèle/lot/chargeur, guide, relevé avec valeur absente, transfert accepté, annotation et retrait des accès de l’ancien propriétaire : OK |
| Nettoyage des schémas de tests | 0 schéma `test_*` restant |
| Base de développement après tous les tests | 0 utilisateur et 0 batterie ; aucun jeu de recette ajouté |

La suite conserve les parcours existants : premier compte et droits administratifs, login historique/email, inscription/vérification/récupération, jetons expirés/à usage unique, CSRF, trois utilisateurs et isolation API/QR/exports, numéro `001`, saisie simple, corrections/suppression/statistiques, calculs, limites numériques et sources fabricant, guide facultatif et cases cochées, mesures de cellules incomplètes, modèles privés et catalogue modéré avec révisions, lots et collisions atomiques, instantanés des caractéristiques, chargeurs/canaux/archivage, duplication, notes de transfert privées par défaut, annulation/refus/expiration/invalidation, collisions du destinataire, transferts successifs/provenance et annotations append-only.

Les courriels des tests de comptes et limites sont réellement reçus par `aiosmtpd` sur une socket TCP locale, avec des adresses fictives `example.com`. Il ne s’agit pas d’une validation de délivrabilité publique. Les comptes de la recette de sauvegarde ont des adresses vérifiées synthétiques ; la vérification via SMTP est couverte séparément par la suite.

## Commandes exécutées et reproduction

Après création/adaptation de `.env` à partir de `.env.local.example` :

```powershell
docker compose build app
docker compose up -d postgres
docker compose run --rm app python -m backend.migrate
docker compose up -d app
# Première création explicite de la base de tests :
docker compose exec -T postgres sh -c 'createdb -U "$POSTGRES_USER" lipowatcher_test'
docker compose run --rm --no-deps --volume "${PWD}/tests:/app/tests:ro" --volume "${PWD}/tools:/app/tools:ro" --volume "${PWD}/requirements-dev.txt:/app/requirements-dev.txt:ro" app python tools/run_tests.py
npm --prefix frontend test
npm --prefix frontend run build
.venv/Scripts/python.exe tools/check_postgres_lifecycle.py
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

Le script de cycle de vie remplace **uniquement les bases locales** `lipowatcher_test` et `lipowatcher_restore_test`, recrée les conteneurs du projet et compare les données. Il refuse d’utiliser ces noms comme base de développement. Les générateurs de recette et les tests ne sont pas copiés dans l’image de production.

Recette de dump/restauration effectivement effectuée par ce script :

```powershell
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d lipowatcher_test -Fc -f /tmp/lipowatcher-test.dump'
docker compose cp postgres:/tmp/lipowatcher-test.dump ./data/pg-validation.dump
docker compose up -d --force-recreate postgres app
docker compose cp ./data/pg-validation.dump postgres:/tmp/restore-test.dump
docker compose exec -T postgres sh -c 'createdb -U "$POSTGRES_USER" lipowatcher_restore_test'
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d lipowatcher_restore_test --no-owner --no-acl --single-transaction --exit-on-error /tmp/restore-test.dump'
```

La fixture de sauvegarde contient 2 utilisateurs, 2 sessions d’authentification, 1 batterie, 1 relevé avec cellule absente, 1 étape du guide, 1 modèle et sa révision, 1 lot, 1 chargeur, 1 annotation et 1 transfert accepté. Les tables de jetons, limites et la révision Alembic font aussi partie de la comparaison. L’archive et l’empreinte sont dans `data/pg-validation.dump` et `data/pg-expected.json`, avec des données fictives exclusivement.

## Limites et vérifications non réalisées

- Ce rapport décrit la migration initiale. Son verrou d’écriture global a depuis été retiré des parcours applicatifs ; voir [VALIDATION_VERROUS.md](VALIDATION_VERROUS.md) pour l’analyse actuelle et les tests à deux workers.
- HTTPS réel, réseau/déploiement Coolify, certificat PostgreSQL distant, fournisseur SMTP public, appareil Android, installation PWA et caméra sur appareil physique : **non vérifiés dans ce lot**. Aucun changement frontend fonctionnel ni nouvelle recette navigateur n’a été réalisé ; compilation et tests de logique frontend ont été exécutés.
- Les migrations futures sont manuelles, sans autogenerate ORM. Tester les changements et faire une sauvegarde avant chaque mise à jour. Le premier downgrade supprime le schéma ; restaurer une sauvegarde pour récupérer des données.
- Le SMTP conserve sa limite existante : aucune file de livraison durable. Un échec nécessite de renvoyer la demande. Les compteurs partagés empêchent le dépassement testé mais ne garantissent pas la remise en boîte aux lettres.
- Aucune reprise des anciens jeux SQLite n’a été effectuée, conformément à la demande. Ils ne sont plus utilisés par l’application.
