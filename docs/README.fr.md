# Tutoriel : cycle de vie d'un modèle LLM avec MLflow 3 et Unity Catalog

[English version](../README.md)

Ce projet montre, en un seul job Databricks, le cycle de vie complet d'un assistant
question-réponse basé sur un LLM. Le « modèle » est un prompt autour d'un endpoint
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
│   └── llm_lifecycle.job.yml           # le job : 6 tâches + 1 condition task
└── src/
    ├── 00_setup_eval_data.py           # notebooks au format source Python
    ├── 01_train.py
    ├── 02_evaluate.py
    ├── 03_validate.py
    ├── 04_promote.py
    ├── 05_skip_promotion.py
    ├── common/params.py                # lecture des paramètres du job
    ├── common/prompts.py               # variantes de prompt "good" et "weak"
    └── model/faq_assistant.py          # le modèle MLflow "from code"
```

## Le job

```
setup_eval_data -> train -> evaluate -> validate -> check_gate -+-> promote         (si passed)
                                                                +-> skip_promotion  (si failed)
```

| Tâche | Rôle | Écrit |
| --- | --- | --- |
| setup_eval_data | crée `faq_knowledge` et `faq_eval_set` depuis samples | tables Delta UC |
| train | fige prompt et paramètres en modèle MLflow, nouvelle version UC | run train, version, alias `candidate` |
| evaluate | 15 questions, 1 juge LLM et 2 scorers déterministes | run evaluate, métriques, traces, tableau par ligne |
| validate | compare aux seuils, ne fait jamais échouer le job | tags `validation_*`, task value `decision` |
| check_gate | condition task sur `decision` | aucune |
| promote | déplace `champion` | alias `champion`, tags `promoted_*` |
| skip_promotion | explique le refus | tags `promotion_status=skipped` |

Chaque run crée une nouvelle version du modèle. Rien n'est jamais supprimé.

## Prérequis
* Databricks CLI récente, authentifiée sur le workspace dev via un profil
  (`databricks auth login`). Aucun host ni token n'est présent dans les fichiers.
* Compute serverless pour les jobs. Sinon, utiliser le bloc `job_clusters`
  commenté dans `resources/llm_lifecycle.job.yml`.
* L'environnement serverless fournit `mlflow-skinny` 3.x et `databricks-agents`.
  Aucun `%pip` n'est utilisé : le tutoriel fonctionne aussi dans les workspaces
  sans accès à PyPI.
* Droits UC : `USE CATALOG` et `CREATE SCHEMA` sur le catalogue (ou un schéma
  existant avec `CREATE TABLE` et `CREATE MODEL`), `SELECT` sur `samples`.
* Droit d'interroger les endpoints `serving_endpoint` et `judge_model`.

## Variables principales (surcharge : `--var="nom=valeur"`)
`catalog` (main, à adapter), `schema` (mlflow_llm_tutorial), `model_name` (faq_assistant),
`serving_endpoint`, `judge_model`, `source_table`, `min_correctness` (0.8),
`min_keyword_rate` (0.8), `min_length_pass_rate` (1.0), `max_answer_chars` (600).

## Commandes
Depuis le dossier qui contient `databricks.yml` :

```bash
databricks bundle validate -t dev
databricks bundle deploy -t dev
# Run 1 : prompt correct -> le modèle est promu "champion"
databricks bundle run llm_lifecycle -t dev
# Run 2 : prompt volontairement faible -> le gate bloque la promotion
databricks bundle run llm_lifecycle -t dev --params prompt_variant=weak
```

Autres réglages possibles :
`--params temperature=0.7,max_tokens=100` ou `--params system_prompt="..."`
(cette surcharge ne s'applique qu'à la variante good).

## Démo devant l'équipe
1. Lancer le run 1. Dans le graphe du job, `check_gate` est vrai et `promote` s'exécute.
2. Lancer le run 2. `validate` réussit (vert) mais publie `failed`, et c'est
   `skip_promotion` qui s'exécute. `champion` reste sur la version du run 1.
3. Résultats attendus (mesurés lors de la mise au point) : variante good,
   correctness et keyword à 1.0 ; variante weak, les deux à 0.0.

## Que regarder dans l'UI après chaque tâche
* **setup_eval_data** : Catalog Explorer > `<catalog>.<schema>`, tables `faq_eval_set`
  (aperçu) et `faq_knowledge`. L'onglet Historique montre une version Delta par run.
* **train** : Experiments > `[dev ...] mlflow_llm_lifecycle_dev` > run `train-good`.
  Onglet Paramètres, Artefacts `prompt/` (prompt système, template, FAQ) et le modèle
  logué. Puis Catalog Explorer > Modèles > `faq_assistant` : nouvelle version avec
  l'alias `candidate` et le tag `validation_status=pending`.
* **evaluate** : run `evaluate-vN`. Onglet Métriques (`correctness/mean`, etc.),
  onglet Traces (une trace par question, avec ses évaluations et la justification
  du juge), artefact `evaluation/per_row_results.json`.
* **validate** : sur la version du modèle, tags `validation_status` et `validation_reason`.
  Dans le run du job, sortie de la tâche et task values.
* **promote / skip_promotion** : sur le modèle, l'alias `champion` et les tags
  `promoted_by`, `promoted_at` et `previous_champion_version`, ou bien
  `promotion_status=skipped`.

## Nettoyage
```bash
# Supprime le job, l'expérience MLflow (et ses runs) et les fichiers déployés
databricks bundle destroy -t dev
# Supprime le modèle UC et toutes ses versions
databricks registered-models delete <catalog>.<schema>.faq_assistant
```
Puis, en SQL : `DROP SCHEMA <catalog>.<schema> CASCADE;` (tables `faq_*`).

## Ce qui changerait dans un environnement réel
* **Promotion vers un autre catalogue ou workspace.** Le modèle serait entraîné et
  validé dans un catalogue dev ou staging, puis copié vers un catalogue prod (par exemple
  `MlflowClient().copy_model_version(...)`). Le catalogue prod est rattaché
  uniquement au workspace prod. Variante courante : déployer le code (ce bundle) en
  prod et y rejouer train et evaluate, plutôt que de copier l'artefact.
* **Un service principal exécute le job.** `run_as: service_principal_name` dans une
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
