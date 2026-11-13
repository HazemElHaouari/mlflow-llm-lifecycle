# Databricks notebook source
# DBTITLE 1,Description
# MAGIC %md
# MAGIC # Job promote · étape 4 bis : skip_promotion
# MAGIC
# MAGIC **Côté :** workspace cible.
# MAGIC
# MAGIC **Ce que fait cette étape :** exécutée seulement si `check_gate` est faux. Elle explique pourquoi la promotion n'a pas eu lieu. Rien n'est copié vers prd et l'alias `champion` ne bouge pas.
# MAGIC
# MAGIC **Ce qu'elle lit :** les task values `decision` et `reason` de `validate`.
# MAGIC
# MAGIC **Ce qu'elle écrit dans MLflow :** les tags `promotion_status=skipped` et `promotion_skip_reason` sur la version dev évaluée.

# COMMAND ----------

# DBTITLE 1,Tracer le refus
import os, sys
sys.path.append(os.path.abspath(".."))  # rend src/common importable depuis src/promote
import mlflow
from mlflow import MlflowClient
from common.params import get_param

catalog = get_param(dbutils, "catalog")
dev_schema = get_param(dbutils, "dev_schema")
prd_schema = get_param(dbutils, "prd_schema")
model_name = get_param(dbutils, "model_name")
dev_model = f"{catalog}.{dev_schema}.{model_name}"
prd_model = f"{catalog}.{prd_schema}.{model_name}"
mlflow.set_registry_uri("databricks-uc")
client = MlflowClient()

version = dbutils.jobs.taskValues.get(taskKey="evaluate", key="model_version")
decision = dbutils.jobs.taskValues.get(taskKey="validate", key="decision")
reason = dbutils.jobs.taskValues.get(taskKey="validate", key="reason")

client.set_model_version_tag(dev_model, version, "promotion_status", "skipped")
client.set_model_version_tag(dev_model, version, "promotion_skip_reason", reason[:250])

try:
    champion = client.get_model_version_by_alias(prd_model, "champion").version
except Exception:
    champion = "aucune"
print(f"Promotion ignorée pour {dev_model} v{version} (décision : {decision}). Raison : {reason}")
print(f"La version champion de {prd_model} reste : {champion}")