> Rapport historique avant PostgreSQL. Les commandes de base de données ci-dessous sont obsolètes ; consulter README.md et VALIDATION_POSTGRESQL.md pour la version actuelle.

# Lot inscription et transferts — 4 octobre 2026

## Livré

- Inscription par e-mail sans privilège administrateur ; comptes à ancien identifiant conservés ; ajout/remplacement et vérification d’adresse depuis Mon compte.
- Récupération de mot de passe, y compris avant première vérification, sans valider automatiquement l’adresse ; révocation des sessions après reset.
- SMTP configuré par environnement, aucun lien de secours affiché ; jetons aléatoires, empreintes SHA-256, expiration et usage unique. Vérifier une adresse demande aussi une session du compte associé.
- Protection CSRF pour toutes les mutations, réponses génériques, limites persistantes par IP/adresse, erreurs sans réaffichage des valeurs sensibles.
- Proposition individuelle avec aperçu lié au contenu, au propriétaire et au destinataire. Notes/annotations privées par défaut ; confirmation explicite de l’envoi et de l’acceptation.
- Acceptation atomique, collision de numéro sans écrasement, une seule proposition en attente et une seule acceptation réussie. Annulation, refus, expiration et invalidation après modification, même suivie d’un retour aux anciennes valeurs.
- UUID/QR conservés. Reçus minimaux pour chaque côté, retrait des accès du propriétaire précédent, exclusion des données d’achat et références d’appareils privées.
- Relevés transmis verrouillés ; ajout d’annotations datées et de nouvelles sessions ; provenances pseudonymes et chaîne des transferts successifs. Les exports de sessions comprennent les annotations, sans identifiant du compte auteur.
- README et exemples de configuration SMTP/Coolify actualisés. Saisie de charge, graphiques par cellule et guide général facultatif conservés.

## Tests exécutés et résultats

| Vérification | Résultat réel |
|---|---|
| `.venv/Scripts/python.exe -m pytest -q` | **33 tests réussis**, un avertissement de dépréciation Starlette/AnyIO |
| `npm --prefix frontend test` | **4 tests réussis** : QR ancien/versionné, isolation des brouillons, calcul A, appareils/canaux |
| `npm --prefix frontend run build` et build frontend dans Docker | TypeScript + Vite + service worker compilés sans erreur |
| `docker build -t lipowatcher:transfers-validation .` | Image construite |
| `docker compose build`, `docker compose up -d` | Conteneur local reconstruit/recréé avec le volume existant |
| Suite backend dans l’image finale | **33 tests réussis**, avec un vrai serveur SMTP TCP `aiosmtpd` |
| Migration sur anciennes tables avec batterie, session et étapes | Anciennes colonnes/lignes inchangées, provenance de l’ancien auteur ajoutée, relevé non verrouillé avant transfert |
| Sauvegarde/restauration WAL, migration en échec et connexions fermées | Tests backend réussis |
| Base locale avant/après migration et recréation | Les anciennes lignes des **10 tables existantes** sont identiques sur leurs anciennes colonnes ; **1 compte, 1 batterie, 0 session** conservés |
| SQLite local après mise à jour | **4 migrations**, `integrity_check=ok`, aucune erreur de clé étrangère |
| `/api/health` et healthcheck Docker | Réponse `status=ok`, conteneur **healthy** |

Les tests de comptes/transferts couvrent : réponses génériques, élévation de privilèges refusée même avec `is_admin` fourni à l’inscription, vérification et reset expirés/rejoués, jetons stockés hachés, mauvaises protections CSRF/origines, limitation des inscriptions et du courrier, ancien compte enrichi sans perte, récupération avant vérification sans contournement, changement d’e-mail du destinataire, trois utilisateurs et un administrateur isolés, mauvais destinataire et lien détourné, auto-transfert, tous les statuts, QR identique avant/après, accès/export/QR de l’expéditeur refusés, notes et références privées exclues, collisions, deux acceptations simultanées (une **200**, une **409**), aperçu modifié ou destinataire remplacé, verrouillage des relevés, annotations et transferts successifs, instantané de chargeur ancien figé au premier aperçu avec une origine explicitement non vérifiée.

Commande utilisée pour la suite dans l’image finale, avec les tests montés en lecture seule, sans montage de la base locale :

```powershell
docker run --rm -e COOKIE_SECURE=false --mount 'type=bind,source=D:\code python\LipoWatcher\tests,target=/app/tests,readonly' lipowatcher:transfers-validation python -c "import subprocess; subprocess.run(['pip','install','--quiet','aiosmtpd==1.4.6'],check=True); subprocess.run(['python','-m','pytest','-q','-p','no:cacheprovider'],check=True)"
```

### Navigateur mobile simulé et vrai SMTP de test

Essais effectués à **390 × 844** dans le navigateur intégré, sur un conteneur isolé `127.0.0.1:8005` et Mailpit `axllent/mailpit:v1.30.6`. Son SMTP était sur le réseau Docker interne, sans port SMTP public ; l’interface Mailpit était liée à `127.0.0.1:8026`. Exclusivement des comptes et données fictifs en `example.com`, aucune adresse réelle contactée.

Parcours constatés dans l’interface : inscription et réponse générique ; e-mail reçu par SMTP ; connexion par ancien identifiant et par e-mail ; aperçu avec notes décochées par défaut et unités françaises ; sélection d’une note technique ; confirmation d’envoi ; proposition reçue ; numéro `001` et confirmation d’acceptation ; reçu accepté ; fiche reçue avec valeurs par cellule absentes représentées par `—`, original verrouillé sans commandes de modification/suppression ; annotation datée enregistrée sans changer les 780 mAh originaux.

Un lien de vérification réellement reçu par Mailpit a ensuite été ouvert et confirmé dans le navigateur. Le fragment avait été retiré de l’URL. Pour l’essai d’expiration, seules les sessions du destinataire fictif ont été révoquées dans la base du conteneur de test : la confirmation a proposé une connexion, puis retrouvé le même lien en mémoire et réussi après reconnexion. Aucun jeton n’a été imprimé dans le rapport ou les sorties de ces essais.

Pas de débordement horizontal constaté : largeur du document **375 px** pour un viewport **390 px**, sur l’aperçu et la fiche après transfert. Le service worker a nécessité une actualisation supplémentaire après reconstruction pour afficher le nouveau bundle.

Le volume de test a aussi survécu à plusieurs recréations : **2 comptes, 1 batterie, 1 charge, 1 annotation et 1 transfert accepté** conservés ; intégrité SQLite `ok`. Capture de recette : `docs/transfer-mobile.png`. Après les essais, les conteneurs de test et SMTP sont arrêtés ; le volume de test et les images sont conservés. Le conteneur local habituel reste disponible sur le port 8000.

## Données, fichiers et configuration

Sauvegarde cohérente prise avant la mise à jour : `data/pre-transfers-update.db`, copie également gardée dans le volume. Comparaison avec `data/post-transfers-update.db`. Aucun exemple n’a été ajouté à la base existante ; son fichier `.env` n’a pas été modifié ni affiché.

Fichiers principaux : `backend/accounts.py`, `backend/mail.py`, `backend/transfers.py`, `backend/app.py`, migration additive `backend/migrations/004_accounts_transfers.sql`, `frontend/src/Accounts.tsx`, `frontend/src/Transfers.tsx`, `frontend/src/api.ts`, `frontend/src/main.tsx`, `tests/test_accounts_transfers.py`, `tests/conftest.py`, `requirements-dev.txt`, exemples `.env` et `README.md`.

Les comptes existants sans e-mail restent utilisables avec leur ancien identifiant. L’instance locale actuelle n’a pas de SMTP configuré : `/api/auth/options` renvoie `registration=false, mail=false`. Les nouveaux écrans d’inscription et de récupération deviennent disponibles après configuration du courrier ; les anciens comptes continuent à fonctionner. Ce point est un état de configuration observé, pas un SMTP public validé.

## Limites et recette restante

- **Non vérifiés** : Android physique, installation et caméra sur un téléphone réel, HTTPS/Coolify, proxy de production et fournisseur SMTP public/délivrabilité. Aucun déploiement public effectué.
- **Limite de livraison** : pas de file SMTP durable ni de reprise automatique après échec ; renvoyer les messages ou annuler/reproposer une invitation. Une proposition reste retrouvable dans le compte du destinataire vérifié.
- **Expiration** : les délais sont contrôlés à la lecture et dans la transaction d’acceptation ; le stockage du statut est mis à jour à ces occasions, sans tâche de fond.
- **Provenance** : déclarative et pseudonyme, pas une certification des mesures ni une preuve de l’identité physique du pack. Les caractéristiques de fiche sont éditables par le propriétaire actuel ; les relevés transmis restent verrouillés.
- **Anciens chargeurs** : les sessions sans instantané utilisent les informations disponibles à leur premier aperçu ; une ancienne saisie libre n’est pas transmise comme identification vérifiée.
- **Brouillons** : locaux, isolés par compte/batterie/session ; fermer/recharger un onglet peut perdre certains formulaires non persistés. Les liens de courrier sont gardés en mémoire, jamais dans les brouillons ; rouvrir le courrier après un rechargement.

Recette A/B : vérifier leurs e-mails, créer chez A un pack `001` avec charge et notes fictives, proposer à B avec une note sélectionnée, contrôler l’aperçu et le courrier, essayer un numéro déjà utilisé puis accepter sous un autre, vérifier QR/historique/verrouillage/annotation. Chez A, contrôler reçu et refus d’accès à la fiche, aux sessions, au QR et à l’export. Refaire annulation/refus/expiration et une modification après proposition. Sur Android avec le domaine HTTPS final, répéter depuis le client mail, puis vérifier cookies Secure, reconnexion pendant saisie, hors connexion, installation PWA et anciennes/nouvelles étiquettes.
