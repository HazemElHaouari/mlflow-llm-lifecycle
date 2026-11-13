# Databricks notebook source
# DBTITLE 1,Description
# MAGIC %md
# MAGIC # Job promote · étape 2 : evaluate
# MAGIC
# MAGIC **Côté :** workspace cible.
# MAGIC
# MAGIC **Ce que fait cette étape :** charge la version dev demandée (paramètre `model_version`, ou à défaut celle qui porte l'alias `candidate`), lui pose chaque question de `faq_eval_set`, puis note les réponses avec :
# MAGIC * `Correctness` : juge LLM intégré à MLflow, qui compare la réponse à la réponse attendue ;
# MAGIC * `contains_expected_keyword` : scorer déterministe, le mot-clé attendu est-il présent ?
# MAGIC * `answer_under_length_limit` : scorer déterministe, la réponse fait-elle moins de `max_answer_chars` caractères ?
# MAGIC
# MAGIC **Ce qu'elle lit :** une version du modèle dev (lecture seule) et la table `faq_eval_set` du schéma prd.
# MAGIC
# MAGIC **Ce qu'elle écrit dans MLflow :** un run « evaluate » : métriques agrégées (`*/mean`), une trace par question avec ses évaluations (onglet Traces) et le tableau `evaluation/per_row_results.json`.

# COMMAND ----------

# DBTITLE 1,Paramètres
import os, sys
sys.path.append(os.path.abspath(".."))  # rend src/common importable depuis src/promote
import mlflow
import pandas as pd
from mlflow import MlflowClient
from mlflow.genai.scorers import Correctness, scorer
from common.params import get_param

catalog = get_param(dbutils, "catalog")
dev_schema = get_param(dbutils, "dev_schema")
prd_schema = get_param(dbutils, "prd_schema")
model_name = get_param(dbutils, "model_name")
experiment_id = get_param(dbutils, "experiment_id")
judge_model = get_param(dbutils, "judge_model")
MAX_ANSWER_CHARS = int(get_param(dbutils, "max_answer_chars"))
requested_version = get_param(dbutils, "model_version", required=False)  # fourni par Control-M en prod

dev_model = f"{catalog}.{dev_schema}.{model_name}"
eval_table = f"{catalog}.{prd_schema}.faq_eval_set"
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(experiment_id=experiment_id)
client = MlflowClient()

# COMMAND ----------

# DBTITLE 1,Choisir et charger la version à évaluer
# Version explicite si fournie, sinon celle qui porte l'alias "candidate" en dev.
version = requested_version or client.get_model_version_by_alias(dev_model, "candidate").version
source_version = client.get_model_version(dev_model, version)
model = mlflow.pyfunc.load_model(f"models:/{dev_model}/{version}")
print(f"Évaluation de {dev_model} version {version}")

# COMMAND ----------

# DBTITLE 1,Scorers
# Scorers déterministes : du Python simple, lisible et reproductible.
@scorer
def contains_expected_keyword(outputs, expectations) -> bool:
    return expectations["expected_keyword"].lower() in str(outputs).lower()


@scorer
def answer_under_length_limit(outputs) -> bool:
    return len(str(outputs)) <= MAX_ANSWER_CHARS


def predict_fn(question: str) -> str:
    return model.predict(pd.DataFrame({"question": [question]}))[0]

# COMMAND ----------

# DBTITLE 1,Jeu d'évaluation
eval_pdf = spark.table(eval_table).orderBy("question_id").toPandas()
eval_data = [
    {
        "inputs": {"question": r.question},
        "expectations": {"expected_response": r.expected_response, "expected_keyword": r.expected_keyword},
    }
    for r in eval_pdf.itertuples()
]
table_version = spark.sql(f"DESCRIBE HISTORY {eval_table} LIMIT 1").first()["version"]

# COMMAND ----------

# DBTITLE 1,Évaluer et loguer
with mlflow.start_run(run_name=f"evaluate-v{version}") as run:
    mlflow.set_tags({"lifecycle_stage": "evaluate", "model_name": dev_model, "model_version": version})
    mlflow.log_params({"model_uri": f"models:/{dev_model}/{version}",
                       "eval_table": eval_table, "eval_table_delta_version": table_version,
                       "judge_model": judge_model, "max_answer_chars": MAX_ANSWER_CHARS})
    results = mlflow.genai.evaluate(
        data=eval_data,
        predict_fn=predict_fn,
        scorers=[Correctness(model=judge_model), contains_expected_keyword, answer_under_length_limit],
        model_id=getattr(source_version, "model_id", None),  # lie les traces au LoggedModel MLflow 3
    )
    # Tableau par ligne, plus simple à lire que les traces brutes.
    rdf = results.result_df
    per_row = pd.DataFrame({
        "question": rdf["request"].apply(lambda r: r.get("question") if isinstance(r, dict) else r),
        "response": rdf["response"],
        "expected_keyword": rdf.get("expected_keyword/value"),
        "correctness": rdf.get("correctness/value"),
        "contains_expected_keyword": rdf.get("contains_expected_keyword/value"),
        "answer_under_length_limit": rdf.get("answer_under_length_limit/value"),
    })
    mlflow.log_table(per_row, artifact_file="evaluation/per_row_results.json")

print(results.metrics)
display(per_row)
dbutils.jobs.taskValues.set(key="model_version", value=version)
dbutils.jobs.taskValues.set(key="eval_run_id", value=run.info.run_id)