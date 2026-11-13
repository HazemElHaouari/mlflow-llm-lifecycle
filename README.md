# Tutorial: LLM model lifecycle with MLflow 3 and Unity Catalog

[Version française](docs/README.fr.md)

This project shows, in a single Databricks job, the full lifecycle of an LLM-based
question-answering assistant. The "model" is a prompt wrapped around a Foundation Model
endpoint: no fine-tuning, no GPU. It is registered in Unity Catalog, evaluated with an
LLM judge and deterministic scorers, then promoted to `champion` only if it passes the
validation thresholds. A deliberately weak prompt variant lets you show the gate in
action. Data is synthetic (`samples.bakehouse`), and this document explains what would
change in production. Target audience: platform engineers who are new to MLflow.

## Use case
An assistant answers questions about fictitious suppliers taken from
`samples.bakehouse.sales_suppliers` (synthetic Databricks sample data, read-only).
The "model" has no weights: it is a prompt plus generation parameters around an
existing Foundation Model endpoint.

## Project layout

```
mlflow_llm_lifecycle/
├── databricks.yml                      # bundle, "dev" target, all variables
├── README.md                           # this document
├── docs/README.fr.md                   # French version
├── resources/
│   ├── llm_lifecycle.experiment.yml    # MLflow experiment managed by the bundle
│   └── llm_lifecycle.job.yml           # the job: 6 tasks + 1 condition task
└── src/
    ├── 00_setup_eval_data.py           # notebooks in Python source format
    ├── 01_train.py
    ├── 02_evaluate.py
    ├── 03_validate.py
    ├── 04_promote.py
    ├── 05_skip_promotion.py
    ├── common/params.py                # reads job parameters
    ├── common/prompts.py               # "good" and "weak" prompt variants
    └── model/faq_assistant.py          # the MLflow "models from code" model
```

## The job

```
setup_eval_data -> train -> evaluate -> validate -> check_gate -+-> promote         (if passed)
                                                                +-> skip_promotion  (if failed)
```

| Task | Role | Writes |
| --- | --- | --- |
| setup_eval_data | builds `faq_knowledge` and `faq_eval_set` from samples | UC Delta tables |
| train | freezes prompt and parameters as an MLflow model, new UC version | train run, model version, `candidate` alias |
| evaluate | 15 questions, 1 LLM judge and 2 deterministic scorers | evaluate run, metrics, traces, per-row table |
| validate | compares metrics to thresholds, never fails the job | `validation_*` tags, `decision` task value |
| check_gate | condition task on `decision` | nothing |
| promote | moves `champion` | `champion` alias, `promoted_*` tags |
| skip_promotion | explains why promotion was blocked | `promotion_status=skipped` tags |

Every run creates a new model version. Nothing is ever deleted.

## Prerequisites
* A recent Databricks CLI, authenticated against the dev workspace through a profile
  (`databricks auth login`). No host or token is stored in any file.
* Serverless compute for jobs. Otherwise, use the commented `job_clusters` block in
  `resources/llm_lifecycle.job.yml`.
* The serverless environment ships `mlflow-skinny` 3.x and `databricks-agents`.
  No `%pip` is used, so the tutorial also works in workspaces without PyPI access.
* Unity Catalog privileges: `USE CATALOG` and `CREATE SCHEMA` on the catalog (or an
  existing schema with `CREATE TABLE` and `CREATE MODEL`), `SELECT` on `samples`.
* Permission to query the `serving_endpoint` and `judge_model` endpoints.

## Main variables (override with `--var="name=value"`)
`catalog` (main, adjust to your workspace), `schema` (mlflow_llm_tutorial),
`model_name` (faq_assistant), `serving_endpoint`, `judge_model`, `source_table`,
`min_correctness` (0.8), `min_keyword_rate` (0.8), `min_length_pass_rate` (1.0),
`max_answer_chars` (600).

## Commands
From the folder that contains `databricks.yml`:

```bash
databricks bundle validate -t dev
databricks bundle deploy -t dev
# Run 1: good prompt -> the model is promoted to "champion"
databricks bundle run llm_lifecycle -t dev
# Run 2: deliberately weak prompt -> the gate blocks promotion
databricks bundle run llm_lifecycle -t dev --params prompt_variant=weak
```

Other settings you can change per run:
`--params temperature=0.7,max_tokens=100` or `--params system_prompt="..."`
(the system prompt override only applies to the good variant).

## Demo in front of the team
1. Start run 1. In the job graph, `check_gate` is true and `promote` runs.
2. Start run 2. `validate` succeeds (green) but publishes `failed`, so
   `skip_promotion` runs instead. `champion` stays on the version from run 1.
3. Expected results (measured while building the tutorial): good variant,
   correctness and keyword at 1.0; weak variant, both at 0.0.

## What to look at in the UI after each task
* **setup_eval_data**: Catalog Explorer > `<catalog>.<schema>`, tables `faq_eval_set`
  (sample data) and `faq_knowledge`. The History tab shows one Delta version per run.
* **train**: Experiments > `[dev ...] mlflow_llm_lifecycle_dev` > run `train-good`.
  Parameters tab, `prompt/` artifacts (system prompt, template, FAQ) and the logged
  model. Then Catalog Explorer > Models > `faq_assistant`: a new version with the
  `candidate` alias and the `validation_status=pending` tag.
* **evaluate**: run `evaluate-vN`. Metrics tab (`correctness/mean`, etc.), Traces tab
  (one trace per question, with its assessments and the judge's rationale), and the
  `evaluation/per_row_results.json` artifact.
* **validate**: on the model version, the `validation_status` and `validation_reason`
  tags. In the job run, the task output and task values.
* **promote / skip_promotion**: on the model, the `champion` alias and the
  `promoted_by`, `promoted_at` and `previous_champion_version` tags, or
  `promotion_status=skipped`.

## Cleanup
```bash
# Deletes the job, the MLflow experiment (and its runs) and the deployed files
databricks bundle destroy -t dev
# Deletes the UC model and all its versions
databricks registered-models delete <catalog>.<schema>.faq_assistant
```
Then, in SQL: `DROP SCHEMA <catalog>.<schema> CASCADE;` (the `faq_*` tables).

## What a real setup would change
* **Promotion to another catalog or workspace.** The model would be trained and
  validated in a dev or staging catalog, then copied to a prod catalog (for example
  with `MlflowClient().copy_model_version(...)`). The prod catalog is bound to the
  prod workspace only. A common alternative: deploy the code (this bundle) to prod and
  re-run train and evaluate there, instead of copying the artifact.
* **A service principal runs the job.** `run_as: service_principal_name` in a prod
  target. People no longer have direct write access: the `promoted_by` tag then shows
  the service principal, and `promoted_by_job` links the promotion to the job run, and
  therefore to the deployed commit.
* **A human approval step.** A human approval is added between `check_gate` and
  `promote`: an approval task, or an MLflow 3 "deployment job" attached to the UC
  model, which shows an approval button in the model UI. The automatic gate becomes a
  necessary but not sufficient condition.
* **Unity Catalog grants control the alias.** Reading or loading a model requires
  `EXECUTE` (plus `USE CATALOG` and `USE SCHEMA`). Setting or moving an alias, or
  writing version tags, is reserved to the model owner or to holders of `MANAGE`
  (check the Unity Catalog documentation). In prod, only the service principal of the
  promotion job holds that privilege on the prod model. This is where the real
  "who can move champion" control lives. Consumers load `models:/...@champion` and
  only have `EXECUTE`.

## Known limitations
* No serving endpoint is created for the model (out of scope).
* `max_concurrent_runs: 1` prevents two runs from moving `candidate` at the same time.
* The prompt is logged as an artifact. The MLflow 3 Prompt Registry
  (`mlflow.genai.register_prompt`) would be a natural extension, if it is enabled in
  your workspace.
