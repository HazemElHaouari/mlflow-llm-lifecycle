# Databricks notebook source
# MAGIC %md
# MAGIC # Étape 2 : evaluate
# MAGIC
# MAGIC **Ce que fait cette étape :** charge la version désignée par l'alias `candidate`, lui pose chaque question de `faq_eval_set`, puis note les réponses avec :
# MAGIC * `Correctness` : juge LLM intégré à MLflow, qui compare la réponse à la réponse attendue ;
# MAGIC * `contains_expected_keyword` : scorer déterministe, le mot-clé attendu est-il présent dans la réponse ?
# MAGIC * `answer_under_length_limit` : scorer déterministe, la réponse fait-elle moins de `max_answer_chars` caractères ?
# MAGIC
# MAGIC **Ce qu'elle lit :** le modèle `@candidate` et la table `faq_eval_set`.
# MAGIC
# MAGIC **Ce qu'elle écrit dans MLflow :** un run « evaluate » qui contient les métriques agrégées (`*/mean`), une trace par question avec ses évaluations (onglet Traces) et le tableau `evaluation/per_row_results.json`.

# COMMAND ----------

import mlflow
import pandas as pd
from mlflow import MlflowClient
from mlflow.genai.scorers import Correctness, scorer
from common.params import get_param

catalog = get_param(dbutils, "catalog")
schema = get_param(dbutils, "schema")
model_name = get_param(dbutils, "model_name")
experiment_id = get_param(dbutils, "experiment_id")
judge_model = get_param(dbutils, "judge_model")
MAX_ANSWER_CHARS = int(get_param(dbutils, "max_answer_chars"))

full_model_name = f"{catalog}.{schema}.{model_name}"
eval_table = f"{catalog}.{schema}.faq_eval_set"
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(experiment_id=experiment_id)
client = MlflowClient()

# COMMAND ----------

candidate = client.get_model_version_by_alias(full_model_name, "candidate")
expected_version = dbutils.jobs.taskValues.get(taskKey="train", key="model_version", debugValue="")
if expected_version and candidate.version != expected_version:
    print(f"ATTENTION : 'candidate' pointe sur v{candidate.version}, train a produit v{expected_version}")

model = mlflow.pyfunc.load_model(f"models:/{full_model_name}@candidate")

# COMMAND ----------

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

with mlflow.start_run(run_name=f"evaluate-v{candidate.version}") as run:
    mlflow.set_tags({"lifecycle_stage": "evaluate", "model_name": full_model_name,
                     "model_version": candidate.version})
    mlflow.log_params({"model_uri": f"models:/{full_model_name}/{candidate.version}",
                       "eval_table": eval_table, "eval_table_delta_version": table_version,
                       "judge_model": judge_model, "max_answer_chars": MAX_ANSWER_CHARS})
    results = mlflow.genai.evaluate(
        data=eval_data,
        predict_fn=predict_fn,
        scorers=[Correctness(model=judge_model), contains_expected_keyword, answer_under_length_limit],
        model_id=getattr(candidate, "model_id", None),  # lie les traces au LoggedModel MLflow 3
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
dbutils.jobs.taskValues.set(key="eval_run_id", value=run.info.run_id)
