# Databricks notebook source
# MAGIC %md
# MAGIC # Étape 3 : validate (le gate)
# MAGIC
# MAGIC **Ce que fait cette étape :** compare les métriques du run d'évaluation aux seuils définis dans les variables du bundle. Elle **ne fait jamais échouer le job** : la décision (`passed` ou `failed`) est publiée et c'est la condition task `check_gate` qui choisit la branche suivante.
# MAGIC
# MAGIC **Ce qu'elle lit :** les métriques du run « evaluate » (via la task value `eval_run_id`) et les seuils.
# MAGIC
# MAGIC **Ce qu'elle écrit dans MLflow :**
# MAGIC * les tags `validation_status`, `validation_reason`, `validation_eval_run_id` et `validated_at` sur la version du modèle ;
# MAGIC * le tag `validation_status` sur le run d'évaluation ;
# MAGIC * les task values `decision` et `reason`.

# COMMAND ----------

from datetime import datetime, timezone
import mlflow
from mlflow import MlflowClient
from common.params import get_param

catalog = get_param(dbutils, "catalog")
schema = get_param(dbutils, "schema")
model_name = get_param(dbutils, "model_name")
thresholds = {
    "correctness/mean": float(get_param(dbutils, "min_correctness")),
    "contains_expected_keyword/mean": float(get_param(dbutils, "min_keyword_rate")),
    "answer_under_length_limit/mean": float(get_param(dbutils, "min_length_pass_rate")),
}

full_model_name = f"{catalog}.{schema}.{model_name}"
mlflow.set_registry_uri("databricks-uc")
client = MlflowClient()

version = dbutils.jobs.taskValues.get(taskKey="train", key="model_version")
eval_run_id = dbutils.jobs.taskValues.get(taskKey="evaluate", key="eval_run_id")
metrics = client.get_run(eval_run_id).data.metrics
print("Métriques disponibles :", metrics)

# COMMAND ----------

# CONTRÔLE DE GOUVERNANCE : la règle d'acceptation est explicite, versionnée dans le bundle.
failures = []
for metric, minimum in thresholds.items():
    value = metrics.get(metric)
    if value is None:
        failures.append(f"{metric} absent (erreur du scorer ?)")
    elif value < minimum:
        failures.append(f"{metric}={value:.2f} < {minimum}")

decision = "failed" if failures else "passed"
reason = "; ".join(failures) if failures else "tous les seuils sont atteints"

# COMMAND ----------

for key, value in {
    "validation_status": decision,
    "validation_reason": reason[:250],
    "validation_eval_run_id": eval_run_id,
    "validated_at": datetime.now(timezone.utc).isoformat(),
}.items():
    client.set_model_version_tag(full_model_name, version, key, value)
client.set_tag(eval_run_id, "validation_status", decision)

dbutils.jobs.taskValues.set(key="decision", value=decision)
dbutils.jobs.taskValues.set(key="reason", value=reason)
print(f"Version {version} : {decision} ({reason})")
