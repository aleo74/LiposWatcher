# LipoWatcher

Carnet de suivi des batteries RC en français : FastAPI, PostgreSQL 17, SQLAlchemy Core 2.0.43, psycopg 3.2.10, Alembic 1.16.5, React/TypeScript et PWA. Chaque compte accède uniquement à ses propres données.

## Architecture et configuration

- **Production** : un seul `Dockerfile` construit le front puis sert le front compilé et l’API Python sur le port interne **8000**. PostgreSQL est un service séparé dans Coolify. L’image n’héberge aucune base et ne requiert aucun volume applicatif.
- **Développement / recette** : `compose.yaml` utilise exactement ce Dockerfile, avec deux services `app` et `postgres`. PostgreSQL utilise `postgres:17-alpine`, le réseau Compose sans port hôte, un healthcheck et le volume `postgres_data`.
- **Base vide** : aucun import des anciens jeux SQLite. Aucun compte, modèle de catalogue ou jeu de démonstration n’est créé au démarrage ni par les migrations. Le premier compte administrateur est créé manuellement via l’interface sur un accès privé.
- Toutes les connexions viennent de `DATABASE_URL`, acceptant `postgresql://` ou `postgresql+psycopg://`. Encoder les caractères réservés du mot de passe dans l’URL ; ne jamais la journaliser ni la committer. Ajouter `?sslmode=require` ou `verify-full` et le certificat requis si le serveur impose TLS.

| Variable | Défaut / usage |
|---|---|
| `DATABASE_URL` | obligatoire, PostgreSQL externe en production |
| `DB_POOL_SIZE` | 5 connexions conservées **par worker** |
| `DB_MAX_OVERFLOW` | 5 connexions temporaires supplémentaires par worker |
| `DB_POOL_TIMEOUT` | 30 secondes d’attente |
| `DB_POOL_RECYCLE` | 1800 secondes ; `pool_pre_ping` est activé |
| `WEB_CONCURRENCY` | 2 workers Uvicorn dans l’image |

Prévoir au maximum `workers × (pool_size + max_overflow)` connexions applicatives, plus migrations, sauvegardes et autres clients. Les connexions sont rendues au pool en fin de transaction ; rollback sur exception ; pool fermé à l’arrêt. Les paramètres SQL sont liés via SQLAlchemy, sans interpolation des données. Les UUID, dates ISO et contenus JSON conservent leurs formats API ; les nombres réels sont `DOUBLE PRECISION`. SQLAlchemy Core est utilisé sans ORM.

Le niveau de transaction est explicitement `READ COMMITTED`, afin de relire l’état validé après l’attente d’un verrou. Il n’existe plus de verrou d’écriture global dans les parcours normaux.

- **Batteries** : `battery_for(..., write=True)` prend `FOR UPDATE` sur la batterie avant toute écriture de caractéristiques, guide, relevés ou annotations. Les lectures de fiche prennent `FOR SHARE`. Deux batteries différentes n’utilisent pas le même verrou.
- **Transferts** : l’ordre est compte concerné (lecture partagée de son adresse), batterie `FOR UPDATE`, puis proposition `FOR UPDATE`. Propriété, statut, expiration et empreinte sont relus sous verrou. La clôture utilise aussi `WHERE status='pending'`, avec rollback de tout le transfert si aucune ligne ne change. Les contraintes d’unicité restent actives pour les numéros par compte et une proposition en attente par batterie.
- **Réconciliation** : chaque écriture technique ne revalide que les propositions de sa batterie. La liste des transferts entretient uniquement les propositions de son utilisateur, une batterie par transaction, avec `SKIP LOCKED` : une batterie occupée peut afficher temporairement son ancien statut jusqu’au prochain rafraîchissement. Toute action revalide intégralement sous verrou et ne peut accepter un consentement expiré ou modifié.
- **Limites partagées** : les compteurs restent dans PostgreSQL. Le login initialise et verrouille uniquement les buckets identifiant/IP concernés, dans un ordre stable, pendant vérification/incrément. La lecture du compte est partagée pour empêcher un reset concurrent de laisser utiliser un ancien hash. Le courrier utilise `INSERT ... ON CONFLICT DO UPDATE ... RETURNING`, avec remise à zéro atomique de la fenêtre expirée. Les buckets sont toujours acquis dans un ordre stable ; les transactions de quotas sont terminées avant les écritures de batterie.
- **Comptes, catalogue et lots** : verrou du seul compte pour les jetons et modifications de courriel ; verrou du modèle pour sa révision/modération ; lecture partagée du modèle lors de la copie ; verrou du lot et des batteries réellement concernées par une opération groupée, avec ordre stable des identifiants. Les inscriptions concurrentes sont protégées par les index uniques et `ON CONFLICT DO NOTHING`.
- **Exports** : transaction `REPEATABLE READ, READ ONLY` pour un instantané cohérent entre batteries, sessions et annotations, sans verrou d’écriture. Elle ne peut mélanger un ancien contrôle de propriété avec de nouveaux relevés d’un autre propriétaire.

Deux verrous advisory subsistent pour des opérations uniques : migrations (`73492000`) et création concurrente du premier compte (`73492004`). Ils ne sont jamais pris par les écritures usuelles de batteries ni par les quotas. L’ancienne méthode `serialize_writes()` et la clé `73492001` ont été supprimées. Voir [VALIDATION_VERROUS.md](VALIDATION_VERROUS.md) pour la preuve avant/après avec deux workers sur PostgreSQL réel et les extraits de code.

Les opérations sur une même batterie, un même modèle/lot ou un même bucket sont intentionnellement sérialisées. Une requête très longue peut retenir ces verrous et une saturation du pool peut limiter la concurrence. Le débit à forte charge n’a pas été mesuré.

Les lignes des buckets inactifs sont conservées ; leurs fenêtres sont réinitialisées lorsqu’ils sont réutilisés. Prévoir une purge de maintenance des anciens buckets, distincte des transactions d’authentification, par exemple dans un job dédié avec une rétention de 30 jours (adapter si vos fenêtres/verrous dépassent cette durée) :

```sql
DELETE FROM request_limits
WHERE started < extract(epoch FROM now() - interval '30 days');
DELETE FROM login_attempts
WHERE window_started < extract(epoch FROM now() - interval '30 days')
  AND locked_until < extract(epoch FROM now());
```

Sources techniques : [verrous PostgreSQL 17](https://www.postgresql.org/docs/17/explicit-locking.html), [UPSERT atomique](https://www.postgresql.org/docs/17/sql-insert.html).


## Démarrage local et migrations explicites

Sous PowerShell, depuis ce dossier :

```powershell
Copy-Item .env.local.example .env
# Adapter les paramètres locaux et les mots de passe fictifs de l’exemple.
docker compose build app
docker compose up -d postgres
docker compose run --rm app python -m backend.migrate
docker compose up -d app
docker compose ps
```

Ouvrir `http://127.0.0.1:8000` et créer manuellement le premier compte. Aucun identifiant par défaut. La base démarre vide. Les anciennes données de test SQLite ne sont ni lues ni importées. Les variables `POSTGRES_USER`, `POSTGRES_DB` et `POSTGRES_PASSWORD` initialisent **un volume PostgreSQL vide** : les changer ensuite ne change pas les identifiants existants.

Les migrations sont dans `backend/migrations/versions/`. Pour une évolution, créer une révision avec `alembic revision -m description`, écrire ses opérations PostgreSQL, puis les tester sur la base dédiée. Les révisions sont manuelles : aucun autogenerate ORM n’est configuré. **Le démarrage Uvicorn n’applique jamais de migration** : il vérifie la révision attendue et refuse de démarrer si elle manque. Appliquer `python -m backend.migrate` (équivalent à `alembic upgrade head`) une fois avant de lancer tous les workers. La commande est idempotente et protégée contre deux exécutions simultanées ; DDL et marqueur Alembic sont dans une transaction PostgreSQL. Pour une mise à jour : sauvegarder, arrêter l’application, construire la nouvelle image, exécuter la migration puis démarrer et vérifier.

```powershell
docker compose stop app
docker compose build app
docker compose run --rm app python -m backend.migrate
docker compose up -d app
```

`GET /api/health` vérifie la connexion réelle et la révision Alembic. Renvoie `200 {"status":"ok"}` ou `503 {"status":"unavailable"}` sans détails de connexion. Le volume nommé appartient à PostgreSQL ; recréer `app` ou `postgres` conserve les données. `docker compose down -v` les détruit.

## Tests PostgreSQL séparés

Les tests exigent `TEST_DATABASE_URL` dans `.env.local.example`, une base dédiée dont le nom finit par `_test`, distincte de `DATABASE_URL`. Chaque test reçoit un schéma aléatoire, migré puis supprimé. **Aucun test ne nettoie la base de développement.** Aucun serveur PostgreSQL n’est simulé.

Créer explicitement la base de recette (une fois par volume local), puis monter tests et dépendances uniquement dans un conteneur applicatif jetable :

```powershell
docker compose exec -T postgres sh -c 'createdb -U "$POSTGRES_USER" lipowatcher_test'
docker compose run --rm --no-deps --volume "${PWD}/tests:/app/tests:ro" --volume "${PWD}/tools:/app/tools:ro" --volume "${PWD}/requirements-dev.txt:/app/requirements-dev.txt:ro" app python tools/run_tests.py
npm --prefix frontend test
npm --prefix frontend run build
```

Le rôle de test doit pouvoir créer/supprimer des schémas **dans cette seule base de recette**. En local, l’utilisateur initial PostgreSQL possède ces droits ; en production, employer des rôles restreints et une base de recette indépendante. Les tests installent les dépendances de développement dans le conteneur jetable ; l’image de production n’inclut ni pytest, ni tests, ni générateur de données. Pour exécuter pytest sur l’hôte, installer `requirements-dev.txt` et fournir une URL vers une instance PostgreSQL de recette joignable ; le Compose ne publie pas le port de la base.

La recette complète de recréation et restauration est aussi exécutable :

```powershell
.venv/Scripts/python.exe tools/check_postgres_lifecycle.py
```

Cette commande exige le Compose local et les URL de l’exemple local. Elle **remplace uniquement** `lipowatcher_test` et `lipowatcher_restore_test`, y crée explicitement des données fictives, recrée `app` et `postgres` avec le volume existant, compare toutes les tables puis vérifie l’API sur la base restaurée. Elle laisse la base de développement intacte et ne fait pas partie du démarrage de production. L’archive est créée dans `data/pg-validation.dump`.

Le rapport de résultats et la recette de persistance/restauration sont dans [VALIDATION_POSTGRESQL.md](VALIDATION_POSTGRESQL.md). Deux workers Uvicorn réels sont lancés pendant les tests de limites partagées et de transfert concurrent.

## Production Coolify, HTTPS et SMTP

1. Créer **un service PostgreSQL 17 séparé** dans Coolify avec stockage persistant et sauvegardes. Il doit être joignable sur le réseau privé de l’application. Aucun port PostgreSQL public n’est nécessaire.
2. Créer l’application à partir de ce **Dockerfile**, racine du dépôt. `Ports Exposes` : **8000** ; endpoint de santé : `/api/health`. Aucun volume `/data` ni variable `DATABASE_PATH`.
3. Configurer dans les secrets Coolify `DATABASE_URL` vers le hostname interne réel, le rôle, le mot de passe et la base PostgreSQL. Le hostname `postgres` appartient au Compose local ; il n’est pas présumé dans Coolify. Ajuster le pool et `WEB_CONCURRENCY` à `max_connections`.
4. **Avant le premier démarrage**, exécuter un job ponctuel utilisant la nouvelle image, sur le même réseau et avec les mêmes secrets : `python -m backend.migrate`. Au besoin, désactiver le démarrage automatique initial, lancer cette commande dans un conteneur ponctuel, puis déployer l’application. Pour chaque version nécessitant une migration : arrêter les anciens workers, exécuter le job une fois, démarrer les nouveaux. Ne pas placer Alembic dans le CMD de chaque worker.
5. Associer le domaine HTTPS au port interne **8000**, définir `APP_ENV=production`, `PUBLIC_URL=https://votre-domaine` (origine exacte sans chemin), `COOKIE_SECURE=true`. Ne pas exposer un port public permettant de contourner le proxy.
6. Renseigner `FORWARDED_ALLOW_IPS` avec les seules IP/CIDR des reverse proxies fiables **vus depuis le conteneur**. Le proxy conserve `Host` et fournit `X-Forwarded-Proto` / `X-Forwarded-For`. `*` n’est acceptable que si tout accès direct non fiable est impossible. Les limites utilisent l’adresse client reconnue par Uvicorn.
7. Restreindre temporairement l’accès à l’application au niveau du proxy, créer manuellement le premier compte administrateur, puis ouvrir l’accès. Aucun compte automatique ni promotion d’un inscrit.
8. Configurer SMTP (`SMTP_HOST`, `SMTP_PORT`, `SMTP_TLS`, `SMTP_FROM`, `SMTP_USER`, `SMTP_PASSWORD`), puis `PUBLIC_REGISTRATION=true` si souhaité. Vérifier une livraison réelle, cookies Secure et connexion sur le domaine HTTPS. La configuration refuse HTTP et SMTP sans TLS en production.

Le Compose local n’est pas la méthode de déploiement production : PostgreSQL y est inclus uniquement pour développement et tests. Les commandes de job dépendent de votre installation Coolify ; aucun déploiement public ni vérification de son réseau réel n’a été effectué ici. Sources : [Dockerfile Coolify](https://coolify.io/docs/applications/builds/dockerfile), [paramètres Uvicorn](https://www.uvicorn.org/settings/), [pool SQLAlchemy](https://docs.sqlalchemy.org/en/20/core/pooling.html), [migrations Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html).

## Sauvegarde et restauration PostgreSQL

Utiliser un client PostgreSQL 17 compatible avec le serveur. `pg_dump -Fc` produit une archive cohérente pendant les écritures, à conserver chiffrée hors du serveur. Ne pas copier à chaud le répertoire `PGDATA`. L’archive contient les données privées et sessions ; ne pas la publier. Les rôles et mots de passe PostgreSQL ne sont pas inclus dans le dump d’une base ; les recréer séparément.

Exemple local sans redirection binaire PowerShell :

```powershell
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/lipowatcher.dump'
docker compose cp postgres:/tmp/lipowatcher.dump ./lipowatcher.dump
```

Pour vérifier une restauration, utiliser une **nouvelle base vide**, par exemple `lipowatcher_restore_test` :

```powershell
docker compose cp ./lipowatcher.dump postgres:/tmp/restore.dump
docker compose exec -T postgres sh -c 'createdb -U "$POSTGRES_USER" lipowatcher_restore_test'
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d lipowatcher_restore_test --no-owner --no-acl --single-transaction --exit-on-error /tmp/restore.dump'
```

Vérifier les tables, la version Alembic et les parcours API sur la base restaurée avec une application de recette ; ne pas envoyer de courriels réels pendant cette vérification. Pour restaurer la production : arrêter tous les workers, créer une nouvelle base avec le rôle approprié, restaurer, mettre à jour `DATABASE_URL`, appliquer uniquement les migrations compatibles nécessaires, puis démarrer. Conserver l’ancienne base jusqu’à la recette. Un dump contient déjà le schéma et le marqueur Alembic : ne pas initialiser son schéma avant `pg_restore`. Pour revenir à une ancienne version, restaurer ensemble image et dump compatibles. Le downgrade Alembic initial supprime le schéma ; ce n’est pas un outil de récupération des données.

Dans Coolify, exécuter ces outils côté service PostgreSQL ou depuis un conteneur client 17 sur son réseau privé, en fournissant les secrets sans les imprimer. Programmer sauvegardes, rétention et essais périodiques de restauration. Le dump logique ne remplace pas une stratégie WAL/PITR si une restauration à un instant précis est exigée. Références : [pg_dump 17](https://www.postgresql.org/docs/17/app-pgdump.html), [pg_restore 17](https://www.postgresql.org/docs/17/app-pgrestore.html).

## Utilisation

- Créez une batterie avec numéro, chimie, cellules et capacité nominale. `001` reste `001` et est unique par compte. Une saisie `001, 002, 003` crée un lot de batteries avec les mêmes caractéristiques.
- Une charge ne demande que sa date préremplie et les mAh ajoutés. Les autres données restent facultatives dans les détails.
- La fiche d'une batterie neuve propose « Première utilisation / rodage selon le fabricant ». Ce tutoriel général reste facultatif et consultable ; les cases déjà cochées restent liées à la batterie. Il traite la chimie, les cellules, l'équilibrage lorsqu'il s'applique, la tension finale, le courant, la surveillance, le refroidissement d'un pack chaud et le stockage. Les noms des programmes varient selon les chargeurs : aucun menu n'est présumé. Les cases cochées ne donnent aucun statut de sécurité.
- Si une procédure de premiers cycles existe, saisissez l'URL du fabricant et la référence ou révision à laquelle elle s'applique. Sinon, le guide reste général et ne prescrit aucun nombre de cycles. Il ne pilote aucun chargeur.
- Courant (A) = capacité (mAh) ÷ 1 000 × taux de **charge** (C). Exemple arithmétique : 1 300 mAh à 1C = 1,3 A. Le taux maximal documenté pour le pack, sa source et le courant choisi pour une charge sont distincts. Le maximum calculé à partir des valeurs de la fiche n'est pas un conseil de première charge ni une autorisation vérifiée par l'application. Le courant choisi dans le guide est une aide ponctuelle à la comparaison, non enregistrée. Le C de décharge ne remplace pas le C de charge. Si le maximum ou la tension finale manquent, consultez l'étiquette ou la notice du pack ; aucun réglage LiPo n'est repris automatiquement pour LiHV, Li-ion ou LiFe.
- Une session de référence permet de noter date et heure du relevé, état de charge et origine de cette estimation, chargeur, température, conditions et mesures par cellule. Les sources Gens Ace / Tattu et Spektrum indiquées dans le guide concernent leurs produits ; leurs procédures ne sont pas universelles.
- Les tensions (V) et résistances internes (mΩ) exigent autant de positions que de cellules. Une position vide entre deux points-virgules est une mesure absente. Les graphiques gardent une couleur par cellule et interrompent la courbe aux absences.
- Une session expirée ouvre une reconnexion devant le formulaire. Les brouillons sont dans `sessionStorage` sous l'identifiant de l'utilisateur, de la batterie et de la session. Ils ne sont pas sur le serveur avant validation. En se connectant à un autre compte, l'ancien formulaire est fermé et son brouillon n'est pas affiché. Fermer l'onglet peut effacer les brouillons.
- Les QR utilisent `lipowatcher:v1:<UUID>`, indépendant du domaine. Le scanner intégré accepte aussi les anciennes URL `/b/<UUID>` en extrayant uniquement l'identifiant ; il ouvre la fiche locale après authentification. Un appareil photo générique peut ne pas savoir ouvrir le nouveau format : utilisez le scanner intégré ou la recherche par numéro. Étiquette individuelle et planche A4 sont imprimables.
- Hors connexion, aucune saisie n'est annoncée comme enregistrée. Les exports batteries et sessions sont séparés ; les mesures par cellule y figurent comme tableaux JSON. Les champs textuels susceptibles d'être des formules de tableur sont neutralisés.

La capacité mesurée d'un test est comparée au nominal déclaré. Une estimation depuis une charge partielle n'apparaît que pour un écart d'au moins 20 points de pourcentage et des mAh positifs. Un dépassement du nominal reste visible sans plafonnement, avec un message invitant à revoir les conditions et les données de départ. Aucun pourcentage global de santé ni verdict de sécurité n'est déduit.

## Modèles, lots et catalogue

Les écrans **Modèles**, **Lots**, **Mes chargeurs** et **Catalogue** sont accessibles dans la navigation de l’atelier et le menu du téléphone.

- Un modèle privé réunit marque, gamme, référence, chimie, cellules, capacité, C de décharge annoncé, connecteur, poids, limites de charge documentées (C, A, tension finale du pack en V), sources, champ d’application, notes et provenance. Les limites ne sont pas des courants conseillés pour une session.
- « Créer des batteries » propose une quantité et une suite de numéros disponibles, avec zéros initiaux. L’aperçu est modifiable. L’enregistrement est atomique : toute collision annule batteries et lot. Une révision modifiée depuis l’aperçu exige un nouvel aperçu.
- Chaque batterie conserve les caractéristiques copiées et `model_id` / `model_revision`. Une modification, un refus ou un archivage du modèle ne réécrit pas les batteries. Les batteries historiques sans modèle restent utilisables et modifiables.
- Un lot privé contient nom, date, vendeur, prix total, devise (code de 3 lettres), état neuf/occasion et notes. On peut y associer des batteries existantes, les retirer, les déplacer vers un autre lot ou supprimer le regroupement. Batteries et sessions sont conservées. Une batterie appartient à un seul lot ; changer les métadonnées d’un lot ne réécrit pas les fiches individuelles.
- « Dupliquer les caractéristiques » crée une batterie avec un nouveau numéro, sans sessions, cycles antérieurs, étapes cochées ni association à un lot. Le lien d’origine du modèle est conservé.

Le catalogue démarre vide. Un auteur peut proposer son modèle privé. L’administrateur examine la provenance et les sources, publie, refuse avec motif ou archive. Une modification d’un modèle publié reste en attente ; le catalogue et les créations depuis cette publication utilisent la dernière version acceptée jusqu’à la décision administrative. Les décisions demandent la révision examinée et refusent une proposition modifiée entre-temps. Les auteurs peuvent modifier leurs propres fiches ; les copies privées appartiennent à leur nouvel utilisateur.

Seules les caractéristiques du modèle et les métadonnées de provenance/révision sont exposées aux comptes authentifiés. Aucun numéro de batterie, lot, vendeur, prix ou session n’est ajouté à la publication. Les notes et la provenance d’un modèle seront publiées : n’y saisissez pas d’informations personnelles. Les brouillons et motifs de refus restent visibles seulement à l’auteur et aux administrateurs. L’historique public contient les publications et archivages ; l’auteur voit toutes ses révisions privées ; l’administrateur voit aussi les propositions soumises et leurs décisions, sans accès aux révisions privées non soumises. La validation est administrative, sans certification technique. Aucun catalogue automatique n’est alimenté.

## Chargeurs et mesures

Chaque appareil physique possède son propre identifiant, un nom personnel, marque, modèle, nombre de canaux, firmware, notes et statut actif/archivé. Deux appareils du même modèle restent distincts. Dans les détails facultatifs d’une session, choisissez l’appareil et éventuellement un canal. Le dernier choix pertinent pour cette batterie (appareil actif ou ancien nom libre) est prérempli ; un canal devenu incompatible n’est pas proposé. Le choix peut être modifié ou remplacé par un nom libre. Date et mAh suffisent toujours pour une charge.

Le texte historique du chargeur reste dans `entries.charger`. Les nouveaux liens `charger_id` et `charger_channel` sont facultatifs. Le nom de l’appareil est aussi copié dans la session : le renommer ou l’archiver ne change pas les anciens libellés. Une session historique garde son appareil archivé lors d’une correction, mais aucune nouvelle association à un appareil archivé n’est acceptée. Les filtres de la fiche limitent les graphiques et repères de suivi à l’appareil/canal choisi, y compris les anciens noms libres. Aucune correction numérique entre appareils n’est appliquée.

Une future aide propre au chargeur sélectionné sera séparée du guide général, liée au modèle et à la révision de sa notice officielle. La sélection actuelle ne détecte aucune connexion USB et ne collecte pas les données du chargeur.

## Limites

L’application ne pilote aucun chargeur et ne lit pas ses données USB. Le guide reste général et facultatif ; il ne certifie aucune batterie. Android physique, caméra/PWA sur appareil, HTTPS/Coolify et délivrabilité SMTP publique restent à vérifier sur votre installation. Les requêtes qui partagent une batterie, un compte modifié ou un bucket peuvent attendre son verrou ; aucun benchmark de charge n’a été réalisé. Le SMTP ne dispose pas d’une file durable ; après échec, renvoyer la demande depuis l’application.

## Inscription, courrier et comptes historiques

Le premier compte est créé par le bootstrap privé décrit plus haut. L’inscription publique crée ensuite uniquement des comptes non administrateurs par e-mail et mot de passe (12 à 256 caractères, Argon2). Les anciennes connexions par identifiant restent valides. Dans **Mon compte**, fournir une adresse et le mot de passe actuel, ouvrir le courrier puis confirmer avec une session du compte associé. Cette confirmation exige à la fois la boîte aux lettres et l’accès au compte ; elle ne connecte pas automatiquement. Une autre session ne peut pas consommer le lien. Une adresse de remplacement n’efface pas l’adresse vérifiée existante avant confirmation.

**Mot de passe oublié** envoie un lien à l’adresse déjà renseignée sur le compte, y compris avant sa première vérification. Le lien prouve l’accès à cette boîte ; la réinitialisation ne marque pas l’adresse comme vérifiée et n’autorise aucun transfert. Après une récupération avant vérification, se reconnecter puis renvoyer la vérification depuis Mon compte. Un ancien compte sans adresse nécessite l’assistance de son exploitant. Réinitialiser le mot de passe clôture toutes les sessions et invalide les autres jetons du compte. Les réponses aux demandes d’inscription, de vérification et de récupération restent génériques, sans confirmer l’existence d’une adresse. Aucun annuaire public n’est disponible.

Les jetons sont aléatoires, stockés sous forme de SHA-256, expirent et ne se réutilisent pas après consommation. Les liens portent leur jeton dans un fragment `#token=...`, absent des requêtes HTTP du navigateur et des journaux d’accès. Le frontend retire ce fragment de l’adresse immédiatement et garde le jeton en mémoire uniquement ; après un rechargement volontaire, rouvrir le courrier si nécessaire. Ne configurez aucun proxy/outil d’observation pour journaliser les corps de ces API. Les erreurs de validation ne réaffichent pas les valeurs saisies. `Referrer-Policy: no-referrer` et `Cache-Control: no-store` protègent les réponses privées.

Toutes les mutations, même login et bootstrap, exigent le cookie CSRF HttpOnly et son empreinte dans `X-CSRF-Token`, obtenue avec `GET /api/auth/csrf`. La validation d’Origin et de `Sec-Fetch-Site` reste active. Les clients API doivent effectuer cet échange. La connexion conserve les limites `LOGIN_*` par identifiant/adresse IP. Inscription et demandes de courrier utilisent `AUTH_REQUEST_MAX` requêtes par `AUTH_REQUEST_WINDOW` secondes, séparément par IP et adresse hachées. Le courrier de vérification/réinitialisation expire après `VERIFY_TOKEN_MINUTES` / `RESET_TOKEN_MINUTES`, 60 par défaut.

### SMTP en production / Coolify

Renseigner ces variables dans les secrets/environnements du service Coolify, jamais dans le dépôt :

| Variable | Configuration |
|---|---|
| `APP_ENV` | `production` (valeur par défaut) |
| `PUBLIC_URL` | origine HTTPS publique exacte, par exemple `https://batteries.example.org`, sans chemin |
| `COOKIE_SECURE` | `true` |
| `PUBLIC_REGISTRATION` | `true`, ou `false` pour fermer les nouvelles inscriptions |
| `SMTP_HOST`, `SMTP_FROM` | hôte SMTP et expéditeur autorisé par votre fournisseur |
| `SMTP_PORT`, `SMTP_TLS` | `587` / `starttls`, ou `465` / `ssl` selon le fournisseur |
| `SMTP_USER`, `SMTP_PASSWORD` | identifiants dans le gestionnaire de secrets |
| `FORWARDED_ALLOW_IPS` | IP/CIDR du seul proxy fiable, cf. section Coolify |

La configuration production refuse l’origine HTTP, les cookies non sécurisés et `SMTP_TLS=none`. Sans configuration de courrier valide, l’inscription et les actions nécessitant du courrier sont indisponibles ; aucun lien de secours n’est affiché dans l’application. Vérifier la livraison réelle, l’expéditeur, SPF/DKIM et les dossiers indésirables chez votre fournisseur. L’URL d’envoi est configurée explicitement et n’est jamais dérivée d’un en-tête Host fourni par un client. Les liens déjà émis nécessitent encore leur ancien domaine ; les QR batterie restent utilisables après changement de serveur.

Le SMTP de test ne sert qu’aux comptes fictifs : Mailpit `axllent/mailpit:v1.30.6`, port SMTP 1025 sur un réseau Docker isolé, interface web en boucle locale. `APP_ENV=test` ou `local` permet HTTP et `SMTP_TLS=none` dans ce contexte. Aucun compte ou courrier de test n’a été ajouté à la base existante. Le module ne conserve pas une file de livraison durable : après un échec SMTP, le journal contient seulement une alerte générique ; renvoyer la vérification/récupération, ou annuler puis reproposer un transfert. Il ne garantit pas la remise en boîte aux lettres.

## Transfert individuel entre comptes

1. Les deux comptes ajoutent et vérifient leur e-mail.
2. Depuis la fiche, l’expéditeur choisit **Transférer cette batterie**, saisit l’adresse du destinataire et prépare l’aperçu.
3. Les textes libres sont exclus par défaut. Sélectionner explicitement les notes, incidents textuels et annotations à transmettre, examiner les sessions et confirmer l’adresse ainsi que l’aperçu avant l’envoi.
4. Le destinataire ouvre le courrier, se connecte au compte ayant vérifié exactement l’adresse invitée, consulte les données, choisit son numéro et accepte ou refuse. Il peut aussi retrouver la proposition dans **Transferts**. Ouvrir le lien n’accepte jamais.
5. L’acceptation atomique conserve le UUID et les QR, transfère les caractéristiques et tout le journal technique, puis clôture la proposition. Un numéro déjà utilisé provoque un refus sans écraser les batteries. Les deux côtés gardent un reçu minimal daté. Une ancienne étiquette conserve son numéro imprimé ; si le numéro choisi change, le QR ouvre la bonne fiche mais il faut réimprimer l’étiquette pour actualiser son texte.

Une proposition par batterie, aucun transfert groupé, aucun auto-transfert. Durée par défaut : 72 heures (`TRANSFER_HOURS`). L’expéditeur peut annuler avant l’acceptation. Toute modification du contenu technique ou des textes associés invalide la proposition, même si elle est ensuite annulée par une modification inverse ; il faut un nouvel aperçu confirmé. Expiration et invalidation sont calculées aux lectures des propositions et contrôlées à l’acceptation ; les écritures techniques invalident dans leur transaction. Aucun processus de fond n’est nécessaire pour empêcher une acceptation expirée. L’index unique partiel `transfers_one_pending`, les verrous ciblés sur la batterie et la proposition (`FOR UPDATE`) et la transition conditionnelle depuis `pending` empêchent deux propositions en attente et deux acceptations concurrentes. Une seule acceptation peut réussir, tous workers confondus.

**Transmis** : chimie/cellules/capacité et autres caractéristiques déclarées, limites avec leurs sources, état déclaré, historique antérieur connu/inconnu, sessions et mesures avec valeurs absentes, dates/température/conditions et provenance pseudonyme, étapes cochées du guide, textes sélectionnés. **Exclus** : numéro personnel de l’expéditeur (le destinataire choisit le sien), date/lot/prix/vendeur d’achat, références privées de catalogue/appareils, nom personnel et fiche privée d’un chargeur, aéronef, comptes/contacts et brouillons. Un chargeur enregistré a un instantané technique de marque/modèle/canal ; un ancien texte libre de chargeur n’est pas transmis. Pour les sessions antérieures à cette fonctionnalité sans instantané, marque/modèle sont relevés depuis la fiche existante lors du premier aperçu ; ils ne constituent pas une preuve de l’identification à la date du relevé. Les champs libres non sélectionnés deviennent vides dans la copie accessible au destinataire, sans suppression des nombres ou dates.

Les relevés transmis sont verrouillés pour modification et suppression. Le nouveau propriétaire ajoute ses propres sessions ou une **annotation datée** qui laisse le relevé original intact. Les annotations sont append-only, portent une provenance pseudonyme, et leur texte reste privé par défaut lors d’un futur transfert. Les transferts successifs conservent les provenances et une chaîne datée sans e-mail ni identifiant de compte dans la fiche technique. Une batterie ayant été transférée ne peut pas être supprimée avec son historique : la retirer de l’usage via le statut. Les champs descriptifs de la batterie restent éditables par son propriétaire actuel ; les relevés antérieurs restent inchangés.

L’expéditeur perd les API de fiche, sessions et QR, l’impression et les exports liés à cette batterie, même avec les UUID connus. Un reçu n’ouvre pas la fiche après un transfert ultérieur. Le rôle administrateur du catalogue n’accorde aucun accès aux propositions ni à la propriété des batteries. Ces données restent déclaratives, sans garantie de sécurité, preuve d’authenticité physique ou fonctionnalité commerciale.

### Recette avec deux comptes, puis Android

Sur une instance de recette avec SMTP : créer A et B, vérifier leurs adresses, créer chez A un pack `001` avec une charge simple, des cellules partiellement mesurées et deux notes fictives. Proposer à B en ne partageant qu’une note ; contrôler son courrier et l’aperçu. Chez B créer déjà `001` pour vérifier le refus sans perte, puis accepter sous `002`. Vérifier QR inchangé, historique/graphes, note exclue absente, verrouillage des relevés, annotation et nouvelle charge. Chez A vérifier le reçu et le refus d’accès à l’ancienne URL, au QR et aux exports. Refaire avec annulation/refus/expiration et modifier une mesure après une proposition pour vérifier l’invalidation. Un troisième compte, même administrateur, doit être refusé.

Sur Android physique avec l’URL HTTPS finale : répéter ce parcours dans Chrome et dans la PWA installée, ouvrir les liens depuis le client mail, vérifier login par e-mail/ancien identifiant, session expirée pendant saisie, refus d’enregistrement hors connexion, scan des anciennes et nouvelles étiquettes et cookies Secure. Les essais de navigateur mobile simulé des lots précédents sont archivés ; ce lot PostgreSQL valide les API et la compilation. Android physique, HTTPS/Coolify et un fournisseur SMTP public restent non vérifiés. Le rapport actuel est `VALIDATION_POSTGRESQL.md`. `VALIDATION_TRANSFERS.md` et `VALIDATION.md` sont des archives antérieures à PostgreSQL ; leurs commandes SQLite ne s’appliquent plus.
