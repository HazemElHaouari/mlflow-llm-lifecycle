# Databricks notebook source
# DBTITLE 1,Description
# MAGIC %md
# MAGIC # Job promote · étape 4 : promote
# MAGIC
# MAGIC **Côté :** workspace cible.
# MAGIC
# MAGIC **Ce que fait cette étape :** exécutée seulement si `check_gate` est vrai. Elle **copie** la version dev validée vers le modèle prd (nouvelle version M), puis déplace l'alias `champion` sur M. Une version refusée n'apparaît donc jamais dans le registre prd. L'ancienne version champion n'est pas supprimée : elle perd seulement l'alias, ce qui permet un rollback immédiat.
# MAGIC
# MAGIC **Ce qu'elle lit :** la version dev évaluée et son tag `validation_status`.
# MAGIC
# MAGIC **Ce qu'elle écrit dans MLflow :**
# MAGIC * une nouvelle version de `<catalog>.<prd_schema>.<model_name>` et l'alias `champion` ;
# MAGIC * sur cette version prd : `source_model`, `source_version`, `validation_eval_run_id`, `promoted_by`, `promoted_at`, `promoted_by_job`, `promotion_trigger`, `previous_champion_version` ;
# MAGIC * sur la version dev : `promoted_to`.

# COMMAND ----------

# DBTITLE 1,Paramètres
import os, sys
sys.path.append(os.path.abspath(".."))  # rend src/common importable depuis src/promote
from datetime import datetime, timezone
import mlflow
from mlflow import MlflowClient
from common.params import get_param

catalog = get_param(dbutils, "catalog")
dev_schema = get_param(dbutils, "dev_schema")
prd_schema = get_param(dbutils, "prd_schema")
model_name = get_param(dbutils, "model_name")
job_id = get_param(dbutils, "job_id")
job_run_id = get_param(dbutils, "job_run_id")
trigger_type = get_param(dbutils, "trigger_type", required=False)

dev_model = f"{catalog}.{dev_schema}.{model_name}"
prd_model = f"{catalog}.{prd_schema}.{model_name}"
mlflow.set_registry_uri("databricks-uc")
client = MlflowClient()
version = dbutils.jobs.taskValues.get(taskKey="evaluate", key="model_version")

# COMMAND ----------

# DBTITLE 1,Vérifier la validation
# Défense en profondeur : on revérifie le tag au lieu de faire confiance au seul graphe.
source = client.get_model_version(dev_model, version)
if source.tags.get("validation_status") != "passed":
    raise RuntimeError(
        f"{dev_model} v{version} non validée (validation_status={source.tags.get('validation_status')}) : promotion refusée"
    )

try:
    previous = client.get_model_version_by_alias(prd_model, "champion").version
except Exception:
    previous = "none"

# COMMAND ----------

# DBTITLE 1,Copier vers prd et poser champion
# CONTRÔLE DE GOUVERNANCE : seul ce job (en prod : son service principal) a le droit
# d'écrire dans le modèle prd et d'y déplacer l'alias "champion".
copied = client.copy_model_version(src_model_uri=f"models:/{dev_model}/{version}", dst_name=prd_model)
client.set_registered_model_alias(prd_model, "champion", copied.version)

promoted_by = spark.sql("SELECT current_user()").first()[0]  # identité qui exécute le job
for key, value in {
    "source_model": dev_model,
    "source_version": version,
    "validation_eval_run_id": source.tags.get("validation_eval_run_id", ""),
    "promoted_by": promoted_by,
    "promoted_at": datetime.now(timezone.utc).isoformat(),
    "promoted_by_job": f"job {job_id} / run {job_run_id}",
    "promotion_trigger": trigger_type or "unknown",
    "previous_champion_version": previous,
}.items():
    client.set_model_version_tag(prd_model, copied.version, key, value)
client.set_model_version_tag(dev_model, version, "promoted_to", f"{prd_model}/{copied.version}")

print(f"{dev_model} v{version} copiée en {prd_model} v{copied.version}")
print(f"'champion' : v{previous} -> v{copied.version}, par {promoted_by}")