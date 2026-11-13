# Tutorial: LLM model lifecycle with MLflow 3 and Unity Catalog

[Version française](docs/README.fr.md)

This project shows, with two Databricks jobs, the full lifecycle of an LLM-based
question-answering assistant: one job builds a model version, a separate job
evaluates and promotes it. The "model" is a prompt wrapped around a Foundation Model
endpoint: no fine-tuning, no GPU. It is registered in Unity Catalog, evaluated with an
LLM judge and deterministic scorers, then promoted to `champion` only if it passes the
validation thresholds. A deliberately weak prompt variant lets you show the gate in
action. Data is synthetic (`samples.bakehouse`), and this document explains what would
change in production. Target audience: platform engineers who are new to MLflow.

## Use case
An assistant answers questions about fictitious suppliers taken from
`samples.bakehouse.sales_suppliers` (synthetic Databricks sample data, read-only).
The "model" has no weights: it is a prompt plus generation parameters around  an
existing Foundation Model endpoint.

## Project layout

```
mlflow_llm_lifecycle/
├── databricks.yml                      # bundle, "dev" target, all variables
├── README.md                           # this document
├── docs/README.fr.md                   # French version
├── resources/
│   ├── llm_lifecycle.experiment.yml    # MLflow experiment managed by the bundle
│   ├── build_model.job.yml             # job 1: build a version (dev side)
│   └── promote_model.job.yml           # job 2: evaluate, gate, promote (target side)
└── src/
    ├── build/01_setup_knowledge        # notebooks of job 1
    ├── build/02_train
    ├── promote/01_setup_eval_set       # notebooks of job 2
    ├── promote/02_evaluate
    ├── promote/03_validate
    ├── promote/04_promote
    ├── promote/05_skip_promotion
    ├── common/params.py                # reads job parameters
    ├── common/prompts.py               # "good" and "weak" prompt variants
    └── model/faq_assistant.py          # the MLflow "models from code" model
```

## The two jobs

In production, the two jobs run in two different workspaces. In this tutorial they run
in the same workspace, and two schemas play the roles of dev and prd.

```
build_model   (dev):    setup_knowledge -> train                          => <catalog>.<dev_schema>.faq_assistant vN @candidate
                                                                              |  model_version = N
promote_model (target): setup_eval_set -> evaluate -> validate -> check_gate -+-> promote         => <catalog>.<prd_schema>.faq_assistant vM @champion
                                                                             +-> skip_promotion
```

| Job | Task | Role | Writes |
| --- | --- | --- | --- |
| build_model | setup_knowledge | builds `faq_knowledge` (the FAQ) from samples | dev Delta table |
| build_model | train | freezes prompt and parameters as an MLflow model | train run, dev version N, `candidate` alias |
| promote_model | setup_eval_set | builds `faq_eval_set`, owned by the promotion side | prd Delta table |
| promote_model | evaluate | 15 questions, 1 LLM judge and 2 deterministic scorers on dev version N | evaluate run, metrics, traces, per-row table |
| promote_model | validate | compares metrics to thresholds, never fails the job | `validation_*` tags on dev version N, `decision` task value |
| promote_model | check_gate | condition task on `decision` | nothing |
| promote_model | promote | copies N to the prd model (version M) and moves `champion` | prd version M, `champion` alias, traceability tags |
| promote_model | skip_promotion | explains why promotion was blocked | `promotion_status=skipped` on dev version N |

The only link between the jobs is the `model_version` parameter of `promote_model`
(empty = the dev version that carries `candidate`). A rejected version never reaches the
prd model. Every run creates new versions; nothing is ever deleted.

## Prerequisites
* A recent Databricks CLI, authenticated against the dev workspace through a profile
  (`databricks auth login`). No host or token is stored in any file.
* Serverless compute for jobs. Otherwise, use the commented `job_clusters` block in
  `resources/build_model.job.yml` (and add it to `promote_model.job.yml`).
* The serverless environment ships `mlflow-skinny` 3.x and `databricks-agents`.
  No `%pip` is used, so the tutorial also works in workspaces without PyPI access.
* Unity Catalog privileges: `USE CATALOG` and `CREATE SCHEMA` on the catalog (or an
  existing schema with `CREATE TABLE` and `CREATE MODEL`), `SELECT` on `samples`.
* Permission to query the `serving_endpoint` and `judge_model` endpoints.

## Main variables (override with `--var="name=value"`)
`catalog` (main, adjust to your workspace), `dev_schema` (mlflow_tuto_dev),
`prd_schema` (mlflow_tuto_prd),
`model_name` (faq_assistant), `serving_endpoint`, `judge_model`, `source_table`,
`min_correctness` (0.8), `min_keyword_rate` (0.8), `min_length_pass_rate` (1.0),
`max_answer_chars` (600).

## Commands
From the folder that contains `databricks.yml`:

```bash
databricks bundle validate -t dev
databricks bundle deploy -t dev

# Scenario 1: good prompt -> promoted
databricks bundle run build_model -t dev
databricks bundle run promote_model -t dev            # promotes the @candidate version

# Scenario 2: deliberately weak prompt -> blocked by the gate
databricks bundle run build_model -t dev --params prompt_variant=weak
databricks bundle run promote_model -t dev

# Promote a specific dev version (what an external scheduler would pass)
databricks bundle run promote_model -t dev --params model_version=3
```

Other settings you can change per run:
`--params temperature=0.7,max_tokens=100` or `--params system_prompt="..."`
(the system prompt override only applies to the good variant).

## Demo in front of the team
1. Scenario 1. `build_model` creates dev version N. In the `promote_model` graph,
   `check_gate` is true and `promote` runs: prd version M gets `champion`.
2. Scenario 2. `build_model` creates dev version N+1. In `promote_model`, `validate`
   succeeds (green) but publishes `failed`, so `skip_promotion` runs instead. Nothing
   is copied to prd and `champion` stays on M.
3. Expected results (measured while building the tutorial): good variant,
   correctness and keyword at 1.0; weak variant, both at 0.0.

## What to look at in the UI after each task
* **setup_knowledge / setup_eval_set**: Catalog Explorer > `<catalog>.<dev_schema>.faq_knowledge`
  and `<catalog>.<prd_schema>.faq_eval_set`. The History tab shows one Delta version per run.
* **train**: Experiments > `[dev ...] mlflow_llm_lifecycle_dev` > run `train-good`.
  Parameters tab, `prompt/` artifacts (system prompt, template, FAQ) and the logged
  model. Then Catalog Explorer > `<dev_schema>` > Models > `faq_assistant`: a new
  version with the `candidate` alias and the `validation_status=pending` tag.
* **evaluate**: run `evaluate-vN`. Metrics tab (`correctness/mean`, etc.), Traces tab
  (one trace per question, with its assessments and the judge's rationale), and the
  `evaluation/per_row_results.json` artifact.
* **validate**: on the dev model version, the `validation_status` and
  `validation_reason` tags. In the job run, the task output and task values.
* **promote**: Catalog Explorer > `<prd_schema>` > Models > `faq_assistant`: version M
  with the `champion` alias and the `source_model`, `source_version`, `promoted_by`,
  `promoted_at` and `previous_champion_version` tags. The dev version gets `promoted_to`.
* **skip_promotion**: on the dev version, `promotion_status=skipped` and the reason.

## Cleanup
```bash
# Deletes both jobs, the MLflow experiment (and its runs) and the deployed files
databricks bundle destroy -t dev
# Deletes the UC models and all their versions
databricks registered-models delete <catalog>.<dev_schema>.faq_assistant
databricks registered-models delete <catalog>.<prd_schema>.faq_assistant
```
Then, in SQL: `DROP SCHEMA <catalog>.<dev_schema> CASCADE;` and
`DROP SCHEMA <catalog>.<prd_schema> CASCADE;` (the `faq_*` tables).

## What a real setup would change
* **Two workspaces instead of two schemas.** `build_model` is deployed to the dev
  workspace and `promote_model` to the prd workspace (two bundle targets). The prd
  catalog is bound to the prd workspace only. To evaluate and copy the dev version,
  the prd workspace needs read-only access to the dev catalog (catalog binding). The
  copy itself is the same `MlflowClient().copy_model_version(...)` call used here.
* **An external scheduler triggers the promotion.** A tool such as Control-M calls
  the Jobs API `run-now` on `promote_model` with `model_version=N`.
* **A service principal runs the promotion job.** `run_as: service_principal_name` in a prod
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
