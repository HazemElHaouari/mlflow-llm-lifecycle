# Databricks notebook source
# MAGIC %md
# MAGIC # Étape 4 : promote
# MAGIC
# MAGIC **Ce que fait cette étape :** exécutée seulement si `check_gate` est vrai. Elle déplace l'alias `champion` vers la version validée et trace qui ou quoi l'a promue, et quand. L'ancienne version champion n'est pas supprimée : elle perd seulement l'alias, ce qui permet un rollback immédiat.
# MAGIC
# MAGIC **Ce qu'elle lit :** la version produite par `train` et son tag `validation_status`.
# MAGIC
# MAGIC **Ce qu'elle écrit dans MLflow :** l'alias `champion` et les tags `promoted_by`, `promoted_at`, `promoted_by_job`, `promotion_trigger` et `previous_champion_version`.

# COMMAND ----------

from datetime import datetime, timezone
import mlflow
from mlflow import MlflowClient
from common.params import get_param

catalog = get_param(dbutils, "catalog")
schema = get_param(dbutils, "schema")
model_name = get_param(dbutils, "model_name")
job_id = get_param(dbutils, "job_id")
job_run_id = get_param(dbutils, "job_run_id")
trigger_type = get_param(dbutils, "trigger_type", required=False)

full_model_name = f"{catalog}.{schema}.{model_name}"
mlflow.set_registry_uri("databricks-uc")
client = MlflowClient()
version = dbutils.jobs.taskValues.get(taskKey="train", key="model_version")

# COMMAND ----------

# Défense en profondeur : on revérifie le tag au lieu de faire confiance au seul graphe.
status = client.get_model_version(full_model_name, version).tags.get("validation_status")
if status != "passed":
    raise RuntimeError(f"Version {version} non validée (validation_status={status}) : promotion refusée")

try:
    previous = client.get_model_version_by_alias(full_model_name, "champion").version
except Exception:
    previous = "none"

# COMMAND ----------

# CONTRÔLE DE GOUVERNANCE : ce sont les droits UC sur le modèle qui décident qui peut faire cet appel.
client.set_registered_model_alias(full_model_name, "champion", version)
promoted_by = spark.sql("SELECT current_user()").first()[0]  # identité qui exécute le job
for key, value in {
    "promoted_by": promoted_by,
    "promoted_at": datetime.now(timezone.utc).isoformat(),
    "promoted_by_job": f"job {job_id} / run {job_run_id}",
    "promotion_trigger": trigger_type or "unknown",
    "previous_champion_version": previous,
}.items():
    client.set_model_version_tag(full_model_name, version, key, value)

print(f"'champion' : v{previous} -> v{version}, par {promoted_by}")
