# Verrous d’écriture ciblés — LipoWatcher

Mis à jour le 5 octobre 2026. Analyse du code courant et tests sur PostgreSQL réel.

## Le verrou global du bilan initial

L’ancienne implémentation plaçait `serialize_writes()` dans `backend/db.py`. La méthode prenait le verrou advisory transactionnel PostgreSQL `pg_advisory_xact_lock(73492001)`. Ce verrou s’appliquait à la base PostgreSQL entière : PostgreSQL le partage entre toutes les connexions et tous les workers. Il restait acquis jusqu’au commit ou rollback. Tous les parcours qui appelaient cette méthode attendaient donc les uns derrière les autres, même s’ils modifiaient deux batteries sans lien.

Les opérations concernées incluaient notamment la création/mise à jour/suppression de batteries, les relevés, étapes du guide et annotations ; les inscriptions, jetons et compteurs d’authentification ; les modèles/catalogue, lots et chargeurs ; et l’aperçu, la proposition, la clôture ou l’acceptation d’un transfert. Une écriture de quota ou une opération de catalogue pouvait donc retarder une charge sans lien, et inversement.

Ce verrou avait servi de garde globale prudente pendant le passage de SQLite à PostgreSQL : il reproduisait une sérialisation de toutes les écritures et évitait les courses entre validation, modification d’historique et acceptation de transfert. Son prix était de bloquer aussi les écritures indépendantes. Il n’est plus utilisé ni présent dans le code actuel : `Transaction.serialize_writes()` et la clé `73492001` ont disparu. Le README, aux paragraphes « Verrous ciblés », décrit les règles désormais appliquées.

## Ce qui reste global, et ce qui ne l’est plus

Deux verrous advisory subsistent, tous deux courts et limités à leur transaction :

- `backend/migrations/env.py` utilise `73492000` pour éviter que plusieurs processus de migration changent simultanément le schéma. Il n’est pris que par la commande Alembic, jamais par les requêtes applicatives.
- `backend/app.py`, dans `/api/auth/setup`, utilise `73492004` pour empêcher deux premiers comptes administrateurs lors de l’initialisation concurrente d’une base vide. Il ne protège aucune session, batterie, limite de débit ni transfert.

Les autres verrous sont des verrous de ligne PostgreSQL. Leur portée est la ligne concernée, sur l’instance entière et donc partagée entre les workers :

- **Batteries et sessions** : `battery_for(..., write=True)` dans `backend/app.py` verrouille la ligne batterie avec `FOR UPDATE`. Les écritures de session, du guide et des annotations prennent ce verrou et réconcilient les propositions de cette batterie dans la même transaction. Une autre batterie a une autre ligne et peut avancer en parallèle.
- **Transferts** : `lock_transfer()` dans `backend/transfers.py` découvre et autorise la proposition, puis acquiert toujours la ligne batterie `FOR UPDATE`, ensuite la proposition `FOR UPDATE`, et relit le statut après l’attente. `reconcile()` verrouille les propositions en attente de cette batterie. L’acceptation met à jour la batterie, les sessions verrouillées et la proposition dans la même transaction. L’annulation et le refus suivent le même ordre. La transition conditionnelle `WHERE status='pending'` et l’index unique partiel `transfers_one_pending` sont des protections complémentaires.
- **États de proposition à rafraîchir** : la liste ne verrouille pas en masse toutes les batteries. `refresh_states()` acquiert un verrou batterie par batterie avec `SKIP LOCKED` ; une batterie occupée peut garder temporairement son affichage précédent. Une action réelle relit et contrôle toujours le statut, la propriété, l’expiration et l’empreinte sous verrou.
- **Limites d’envoi** : `backend/accounts.py:rate_limit()` incrémente chaque bucket avec `INSERT … ON CONFLICT DO UPDATE … RETURNING`. PostgreSQL sérialise les demandes qui utilisent le même bucket ; les buckets distincts ne sont pas bloqués entre eux. Les clés sont traitées dans un ordre stable.
- **Échecs de connexion** : `backend/app.py:lock_login_buckets()` crée puis verrouille seulement les buckets IP et identifiant concernés avec `FOR UPDATE`, dans un ordre stable. Contrôle, vérification du mot de passe, incrément/réinitialisation et création de session ont une transaction commune. Une limite IP est partagée par les utilisateurs qui passent par la même IP, comme prévu par la politique ; les autres buckets sont indépendants.
- Comptes/jetons, modèles et lots utilisent leurs lignes concernées ; les écritures groupées de membres verrouillent les batteries dans l’ordre de leurs identifiants. Les créations concurrentes reposent aussi sur les contraintes d’unicité PostgreSQL.

PostgreSQL est configuré en `READ COMMITTED`. Après avoir attendu un verrou de ligne, une requête suivante voit le statut validé par le transactionnaire précédent. L’ordre batterie puis proposition est commun aux chemins de transfert et aux modifications techniques, ce qui empêche qu’un relevé soit édité en même temps qu’il est accepté depuis un instantané ancien.

## Extraits du code courant

`backend/transfers.py` — ordre de verrouillage et relecture de la proposition :

```python
row = db.execute(f'SELECT * FROM transfers WHERE {column}=?', (value,)).fetchone()
authorized(row, user)
db.execute('SELECT id FROM batteries WHERE id=? FOR UPDATE', (row['battery_id'],)).fetchone()
row = db.execute('SELECT * FROM transfers WHERE id=? FOR UPDATE', (row['id'],)).fetchone()
authorized(row, user)
reconcile(db, now(), row['battery_id'])
row = db.execute('SELECT * FROM transfers WHERE id=?', (row['id'],)).fetchone()
```

L’acceptation n’enregistre la transition que depuis le statut `pending` et contrôle le nombre de lignes modifiées :

```python
changed = db.execute(
    "UPDATE transfers SET status='accepted', ... WHERE id=? AND status='pending'",
    (..., transfer_id),
)
if changed.rowcount != 1:
    fail(409, 'Proposition déjà clôturée')
```

`backend/app.py` — les modifications techniques verrouillent la ligne de leur batterie :

```python
def battery_for(db, user_id, battery_id, write=False):
    lock = "FOR UPDATE" if write else "FOR SHARE"
    row = db.execute(
        f"SELECT * FROM batteries WHERE id=? AND user_id=? {lock}",
        (battery_id, user_id),
    ).fetchone()
```

`backend/accounts.py` — limite d’envoi mise à jour atomiquement pour chaque ligne de quota :

```sql
INSERT INTO request_limits VALUES (?,1,?)
ON CONFLICT(bucket) DO UPDATE SET
  count=CASE WHEN request_limits.started<? THEN 1
             ELSE request_limits.count+1 END,
  started=CASE WHEN request_limits.started<?
               THEN excluded.started ELSE request_limits.started END
RETURNING count
```

## Vérifications sur deux workers

Exécuté dans un conteneur applicatif jetable, avec le PostgreSQL 17 réel du Compose et un schéma PostgreSQL distinct par test. La fixture démarre **deux processus Uvicorn** ; l’aide `independent_while_locked()` vérifie dans `pg_stat_activity` / `pg_blocking_pids()` que la première requête attend effectivement le verrou ciblé. Elle exige ensuite que la seconde finisse en moins de trois secondes avant de libérer ce verrou. Cela démontre que les deux requêtes ne partagent pas un verrou d’écriture global.

Résultat de la suite complète au moment de cet audit : **44 passed en 45,80 s**, un avertissement de dépréciation Starlette/AnyIO, aucun échec.

- `test_actual_two_workers_transfer_once` : huit acceptations simultanées d’une même proposition donnent exactement un HTTP 200 et sept HTTP 409 ; une seule batterie est transférée.
- `test_two_workers_accept_or_cancel_once` : acceptation et annulation concurrentes donnent un seul gagnant ; statut final accepté ou annulé et accès conforme à ce statut.
- `test_two_workers_edit_or_accept_preserves_consent` : édition et acceptation concurrentes aboutissent soit à l’acceptation de l’instantané original avant l’édition, soit à l’invalidation de la proposition avant que l’édition réussisse. Aucun relevé modifié n’est transféré sous l’ancien consentement.
- `test_two_workers_independent_transfers` : le worker A est explicitement bloqué sur la ligne de batterie A ; l’acceptation du transfert de B finit pendant ce blocage. Les deux demandes retournent 200.
- `test_two_workers_independent_entries` : l’écriture de session sur B finit pendant que A est verrouillée. Les deux demandes retournent 201.
- `test_login_limit_shared_between_workers` : douze connexions simultanées partagées entre workers produisent exactement cinq 401 puis sept 429 ; compteurs persistants conformes dans PostgreSQL.
- `test_login_independent_buckets_two_workers` : une tentative sur des buckets différents finit pendant qu’un bucket d’identifiant sans lien est verrouillé par le test.
- `test_actual_two_workers_email_limit` : douze demandes de courrier pour les mêmes buckets produisent trois 202 et neuf 429 ; les trois courriels passent par le serveur SMTP TCP de test et aucun incrément de bucket n’est perdu.
- `test_two_workers_independent_email_buckets` : deux demandes sur des buckets distincts finissent pendant qu’un autre bucket est verrouillé.

Les tests sont dans `tests/test_accounts_transfers.py` et `tests/test_postgres.py`. Commande exécutée :

```powershell
docker compose run --rm --no-deps --volume "${PWD}/tests:/app/tests:ro" --volume "${PWD}/tools:/app/tools:ro" --volume "${PWD}/requirements-dev.txt:/app/requirements-dev.txt:ro" app python tools/run_tests.py
```

## Portée et limites restantes

Les écritures sur **la même** batterie, proposition, bucket, compte, modèle ou lot sont intentionnellement coordonnées. Ce n’est pas la base entière : les tests confirment que des opérations sur deux batteries indépendantes avancent en parallèle. Les buckets de quota partagés limitent volontairement les clients qui ont une même IP ou un même identifiant. Le bootstrap initial et Alembic restent les deux seuls verrous advisory applicatifs/de schéma, avec des usages rares et bornés.

Ces tests vérifient la correction et l’indépendance des lignes, pas le débit maximal sous charge. Les transactions d’acceptation qui incluent un historique volumineux peuvent retenir leur ligne de batterie plus longtemps. Le SMTP s’exécute après la transaction d’envoi de proposition ; une erreur de livraison reste sujette à la limite documentée d’absence de file durable.
