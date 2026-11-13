# Databricks notebook source
# MAGIC %md
# MAGIC # Étape 1 : train (entraînement simulé)
# MAGIC
# MAGIC **Ce que fait cette étape :** aucun calcul GPU ni fine-tuning. Le « modèle » est un prompt (système + template) plus des paramètres de génération, autour d'un endpoint Foundation Model existant. On le fige comme modèle MLflow, on l'enregistre comme nouvelle version Unity Catalog et on pose l'alias `candidate`.
# MAGIC
# MAGIC **Ce qu'elle lit :** les paramètres du job (`prompt_variant`, `system_prompt`, `temperature`, `max_tokens`) et la table `faq_knowledge`.
# MAGIC
# MAGIC **Ce qu'elle écrit dans MLflow :**
# MAGIC * un run « train » qui contient les paramètres, le prompt système, le template et la FAQ en artefacts, ainsi que le modèle logué ;
# MAGIC * une nouvelle version du modèle UC (jamais d'écrasement) ;
# MAGIC * l'alias `candidate` et les tags de version (`prompt_variant`, `source_run_id`, `validation_status=pending`).

# COMMAND ----------

import os
import mlflow
import pandas as pd
from mlflow import MlflowClient
from mlflow.models.resources import DatabricksServingEndpoint
from common.params import get_param
from common.prompts import build_prompt_config

catalog = get_param(dbutils, "catalog")
schema = get_param(dbutils, "schema")
model_name = get_param(dbutils, "model_name")
serving_endpoint = get_param(dbutils, "serving_endpoint")
experiment_id = get_param(dbutils, "experiment_id")
prompt_variant = get_param(dbutils, "prompt_variant")
system_prompt_override = get_param(dbutils, "system_prompt", required=False)
temperature = float(get_param(dbutils, "temperature"))
max_tokens = int(get_param(dbutils, "max_tokens"))

full_model_name = f"{catalog}.{schema}.{model_name}"
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(experiment_id=experiment_id)
client = MlflowClient()

# COMMAND ----------

prompt = build_prompt_config(prompt_variant, system_prompt_override)
facts = spark.table(f"{catalog}.{schema}.faq_knowledge").orderBy("supplier_id").select("fact").toPandas()["fact"]
faq_text = "\n".join(f"- {f}" for f in facts) if prompt["include_faq"] else ""

model_config = {
    "system_prompt": prompt["system_prompt"],
    "prompt_template": prompt["prompt_template"],
    "faq_text": faq_text,                  # la « connaissance » est figée dans la version
    "serving_endpoint": serving_endpoint,
    "temperature": temperature,
    "max_tokens": max_tokens,
}

# COMMAND ----------

with mlflow.start_run(run_name=f"train-{prompt_variant}") as run:
    mlflow.set_tags({"lifecycle_stage": "train", "prompt_variant": prompt_variant})
    mlflow.log_params({
        "prompt_variant": prompt_variant,
        "serving_endpoint": serving_endpoint,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "faq_included": prompt["include_faq"],
    })
    mlflow.log_text(prompt["system_prompt"], "prompt/system_prompt.txt")
    mlflow.log_text(prompt["prompt_template"], "prompt/prompt_template.txt")
    mlflow.log_text(faq_text, "prompt/faq_text.txt")

    model_info = mlflow.pyfunc.log_model(
        name="faq_assistant",
        python_model=os.path.abspath("model/faq_assistant.py"),  # modèle "from code"
        model_config=model_config,
        input_example=pd.DataFrame({"question": ["Which supplier provides oats?"]}),
        # Déclare l'endpoint utilisé : permet le passage d'identité si le modèle est servi plus tard.
        resources=[DatabricksServingEndpoint(endpoint_name=serving_endpoint)],
        pip_requirements=[f"mlflow-skinny[databricks]=={mlflow.__version__}"],
        registered_model_name=full_model_name,   # crée une NOUVELLE version à chaque run
    )

version = str(model_info.registered_model_version)

# COMMAND ----------

# CONTRÔLE DE GOUVERNANCE : "candidate" signifie « à évaluer », jamais « utilisable ».
client.set_registered_model_alias(full_model_name, "candidate", version)
for key, value in {
    "prompt_variant": prompt_variant,
    "source_run_id": run.info.run_id,
    "validation_status": "pending",
}.items():
    client.set_model_version_tag(full_model_name, version, key, value)

dbutils.jobs.taskValues.set(key="model_version", value=version)
dbutils.jobs.taskValues.set(key="train_run_id", value=run.info.run_id)
print(f"{full_model_name} version {version} enregistrée avec l'alias 'candidate'")
