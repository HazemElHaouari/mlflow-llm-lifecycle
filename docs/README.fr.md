# Tutoriel : cycle de vie d'un modèle LLM avec MLflow 3 et Unity Catalog

[English version](../README.md)

Ce projet montre, avec deux jobs Databricks, le cycle de vie complet d'un assistant
question-réponse basé sur un LLM : un job fabrique une version du modèle, un autre
job l'évalue et la promeut. Le « modèle » est un prompt autour d'un endpoint
Foundation Model : pas de fine-tuning, pas de GPU. Il est enregistré dans Unity Catalog,
évalué avec un juge LLM et des scorers déterministes, puis promu `champion` seulement
s'il passe les seuils de validation. Une variante de prompt volontairement faible permet
de montrer le gate en action. Les données sont synthétiques (`samples.bakehouse`), et
ce document explique ce qui changerait en production. Le public visé : les ingénieurs
plateforme qui découvrent MLflow.

## Le cas d'usage
Un assistant répond à des questions sur des fournisseurs fictifs, issus de
`samples.bakehouse.sales_suppliers` (données synthétiques Databricks, en lecture
seule). Le « modèle » n'a aucun poids : c'est un prompt et des paramètres autour
d'un endpoint Foundation Model existant.

## Arborescence

```
mlflow_llm_lifecycle/
├── databricks.yml                      # bundle, cible "dev", toutes les variables
├── README.md                           # version anglaise
├── docs/README.fr.md                   # ce document
├── resources/
│   ├── llm_lifecycle.experiment.yml    # expérience MLflow gérée par le bundle
│   ├── build_model.job.yml             # job 1 : fabriquer une version (côté dev)
│   └── promote_model.job.yml           # job 2 : évaluer, gate, promouvoir (côté cible)
└── src/
    ├── build/01_setup_knowledge        # notebooks du job 1
    ├── build/02_train
    ├── promote/01_setup_eval_set       # notebooks du job 2
    ├── promote/02_evaluate
    ├── promote/03_validate
    ├── promote/04_promote
    ├── promote/05_skip_promotion
    ├── common/params.py                # lecture des paramètres du job
    ├── common/prompts.py               # variantes de prompt "good" et "weak"
    └── model/faq_assistant.py          # le modèle MLflow "from code"
```

## Les deux jobs

En production, les deux jobs tournent dans deux workspaces différents. Dans ce tutoriel,
ils tournent dans le même workspace, et deux schémas jouent les rôles de dev et de prd.

```
build_model   (dev) :    setup_knowledge -> train                          => <catalog>.<dev_schema>.faq_assistant vN @candidate
                                                                               |  model_version = N
promote_model (cible) :  setup_eval_set -> evaluate -> validate -> check_gate -+-> promote         => <catalog>.<prd_schema>.faq_assistant vM @champion
                                                                              +-> skip_promotion
```

| Job | Tâche | Rôle | Écrit |
| --- | --- | --- | --- |
| build_model | setup_knowledge | crée `faq_knowledge` (la FAQ) depuis samples | table Delta dev |
| build_model | train | fige prompt et paramètres en modèle MLflow | run train, version dev N, alias `candidate` |
| promote_model | setup_eval_set | crée `faq_eval_set`, détenu par le côté promotion | table Delta prd |
| promote_model | evaluate | 15 questions, 1 juge LLM et 2 scorers déterministes sur la version dev N | run evaluate, métriques, traces, tableau par ligne |
| promote_model | validate | compare aux seuils, ne fait jamais échouer le job | tags `validation_*` sur la version dev N, task value `decision` |
| promote_model | check_gate | condition task sur `decision` | aucune |
| promote_model | promote | copie N vers le modèle prd (version M) et déplace `champion` | version prd M, alias `champion`, tags de traçabilité |
| promote_model | skip_promotion | explique le refus | `promotion_status=skipped` sur la version dev N |

Le seul lien entre les deux jobs est le paramètre `model_version` de `promote_model`
(vide = la version dev qui porte `candidate`). Une version refusée n'arrive jamais dans
le modèle prd. Chaque run crée de nouvelles versions ; rien n'est jamais supprimé.

## Prérequis
* Databricks CLI récente, authentifiée sur le workspace dev via un profil
  (`databricks auth login`). Aucun host ni token n'est présent dans les fichiers.
* Compute serverless pour les jobs. Sinon, utiliser le bloc `job_clusters`
  commenté dans `resources/build_model.job.yml` (et l'ajouter à `promote_model.job.yml`).
* L'environnement serverless fournit `mlflow-skinny` 3.x et `databricks-agents`.
  Aucun `%pip` n'est utilisé : le tutoriel fonctionne aussi dans les workspaces
  sans accès à PyPI.
* Droits UC : `USE CATALOG` et `CREATE SCHEMA` sur le catalogue (ou un schéma
  existant avec `CREATE TABLE` et `CREATE MODEL`), `SELECT` sur `samples`.
* Droit d'interroger les endpoints `serving_endpoint` et `judge_model`.

## Variables principales (surcharge : `--var="nom=valeur"`)
`catalog` (main, à adapter), `dev_schema` (mlflow_tuto_dev), `prd_schema` (mlflow_tuto_prd), `model_name` (faq_assistant),
`serving_endpoint`, `judge_model`, `source_table`, `min_correctness` (0.8),
`min_keyword_rate` (0.8), `min_length_pass_rate` (1.0), `max_answer_chars` (600).

## Commandes
Depuis le dossier qui contient `databricks.yml` :

```bash
databricks bundle validate -t dev
databricks bundle deploy -t dev

# Scénario 1 : prompt correct -> promu
databricks bundle run build_model -t dev
databricks bundle run promote_model -t dev            # promeut la version @candidate

# Scénario 2 : prompt volontairement faible -> bloqué par le gate
databricks bundle run build_model -t dev --params prompt_variant=weak
databricks bundle run promote_model -t dev

# Promouvoir une version dev précise (ce que passerait un ordonnanceur externe)
databricks bundle run promote_model -t dev --params model_version=3
```

Autres réglages possibles :
`--params temperature=0.7,max_tokens=100` ou `--params system_prompt="..."`
(cette surcharge ne s'applique qu'à la variante good).

## Démo devant l'équipe
1. Scénario 1. `build_model` crée la version dev N. Dans le graphe de `promote_model`,
   `check_gate` est vrai et `promote` s'exécute : la version prd M reçoit `champion`.
2. Scénario 2. `build_model` crée la version dev N+1. Dans `promote_model`, `validate`
   réussit (vert) mais publie `failed`, et c'est `skip_promotion` qui s'exécute. Rien
   n'est copié vers prd et `champion` reste sur M.
3. Résultats attendus (mesurés lors de la mise au point) : variante good,
   correctness et keyword à 1.0 ; variante weak, les deux à 0.0.

## Que regarder dans l'UI après chaque tâche
* **setup_knowledge / setup_eval_set** : Catalog Explorer > `<catalog>.<dev_schema>.faq_knowledge`
  et `<catalog>.<prd_schema>.faq_eval_set`. L'onglet Historique montre une version Delta par run.
* **train** : Experiments > `[dev ...] mlflow_llm_lifecycle_dev` > run `train-good`.
  Onglet Paramètres, Artefacts `prompt/` (prompt système, template, FAQ) et le modèle
  logué. Puis Catalog Explorer > `<dev_schema>` > Modèles > `faq_assistant` : nouvelle
  version avec l'alias `candidate` et le tag `validation_status=pending`.
* **evaluate** : run `evaluate-vN`. Onglet Métriques (`correctness/mean`, etc.),
  onglet Traces (une trace par question, avec ses évaluations et la justification
  du juge), artefact `evaluation/per_row_results.json`.
* **validate** : sur la version dev, tags `validation_status` et `validation_reason`.
  Dans le run du job, sortie de la tâche et task values.
* **promote** : Catalog Explorer > `<prd_schema>` > Modèles > `faq_assistant` : version M
  avec l'alias `champion` et les tags `source_model`, `source_version`, `promoted_by`,
  `promoted_at` et `previous_champion_version`. La version dev reçoit `promoted_to`.
* **skip_promotion** : sur la version dev, `promotion_status=skipped` et la raison.

## Nettoyage
```bash
# Supprime les deux jobs, l'expérience MLflow (et ses runs) et les fichiers déployés
databricks bundle destroy -t dev
# Supprime les modèles UC et toutes leurs versions
databricks registered-models delete <catalog>.<dev_schema>.faq_assistant
databricks registered-models delete <catalog>.<prd_schema>.faq_assistant
```
Puis, en SQL : `DROP SCHEMA <catalog>.<dev_schema> CASCADE;` et
`DROP SCHEMA <catalog>.<prd_schema> CASCADE;` (tables `faq_*`).

## Ce qui changerait dans un environnement réel
* **Deux workspaces au lieu de deux schémas.** `build_model` est déployé dans le
  workspace dev et `promote_model` dans le workspace prd (deux cibles de bundle). Le
  catalogue prd est rattaché uniquement au workspace prd. Pour évaluer et copier la
  version dev, le workspace prd a besoin d'un accès en lecture seule au catalogue dev
  (catalog binding). La copie reste le même appel `MlflowClient().copy_model_version(...)`.
* **Un ordonnanceur externe déclenche la promotion.** Un outil comme Control-M appelle
  l'API Jobs `run-now` sur `promote_model` avec `model_version=N`.
* **Un service principal exécute le job de promotion.** `run_as: service_principal_name` dans une
  cible prod. Les personnes n'ont plus de droits d'écriture directs : le tag
  `promoted_by` affiche alors le service principal, et `promoted_by_job` relie la
  promotion au run et donc au commit déployé.
* **Une étape d'approbation humaine.** Entre `check_gate` et `promote`, une
  validation humaine est ajoutée : tâche d'approbation, ou « deployment job » MLflow 3
  associé au modèle UC, qui affiche un bouton d'approbation dans l'UI du modèle. Le
  gate automatique devient une condition nécessaire mais non suffisante.
* **Les grants Unity Catalog contrôlent l'alias.** Lire ou charger un modèle demande
  `EXECUTE` (plus `USE CATALOG` et `USE SCHEMA`). Poser ou déplacer un alias, ou
  écrire des tags de version, est réservé au propriétaire du modèle ou aux
  détenteurs de `MANAGE` (à vérifier dans la documentation Unity Catalog). En prod,
  seul le service principal du job de promotion détient ce droit sur le modèle prod.
  C'est là que se trouve le vrai contrôle « qui peut déplacer champion ». Les
  consommateurs, eux, chargent `models:/...@champion` et n'ont que `EXECUTE`.

## Limites assumées
* Pas de serving endpoint pour le modèle (hors périmètre).
* `max_concurrent_runs: 1` évite que deux runs déplacent `candidate` en même temps.
* Le prompt est logué comme artefact. Le Prompt Registry MLflow 3 (`mlflow.genai.register_prompt`)
  serait une extension naturelle, s'il est activé dans le workspace.
