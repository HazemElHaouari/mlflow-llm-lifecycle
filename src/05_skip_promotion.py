# Databricks notebook source
# MAGIC %md
# MAGIC # Étape 4 bis : skip_promotion
# MAGIC
# MAGIC **Ce que fait cette étape :** exécutée seulement si `check_gate` est faux. Elle explique pourquoi la promotion n'a pas eu lieu. L'alias `champion` ne bouge pas.
# MAGIC
# MAGIC **Ce qu'elle lit :** les task values `decision` et `reason` de `validate`.
# MAGIC
# MAGIC **Ce qu'elle écrit dans MLflow :** les tags `promotion_status=skipped` et `promotion_skip_reason` sur la version candidate.

# COMMAND ----------

import mlflow
from mlflow import MlflowClient
from common.params import get_param

catalog = get_param(dbutils, "catalog")
schema = get_param(dbutils, "schema")
model_name = get_param(dbutils, "model_name")
full_model_name = f"{catalog}.{schema}.{model_name}"
mlflow.set_registry_uri("databricks-uc")
client = MlflowClient()

version = dbutils.jobs.taskValues.get(taskKey="train", key="model_version")
decision = dbutils.jobs.taskValues.get(taskKey="validate", key="decision")
reason = dbutils.jobs.taskValues.get(taskKey="validate", key="reason")

client.set_model_version_tag(full_model_name, version, "promotion_status", "skipped")
client.set_model_version_tag(full_model_name, version, "promotion_skip_reason", reason[:250])

try:
    champion = client.get_model_version_by_alias(full_model_name, "champion").version
except Exception:
    champion = "aucune"
print(f"Promotion ignorée pour v{version} (décision : {decision}). Raison : {reason}")
print(f"La version champion reste : {champion}")
