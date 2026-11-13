# Databricks notebook source
# DBTITLE 1,Description
# MAGIC %md
# MAGIC # Job build · étape 1 : setup_knowledge
# MAGIC
# MAGIC **Côté :** dev (fabriquer une version).
# MAGIC
# MAGIC **Ce que fait cette étape :** crée le schéma dev (s'il n'existe pas) et la table `faq_knowledge` à partir de `samples.bakehouse.sales_suppliers` (fournisseurs fictifs, données synthétiques Databricks). Une phrase « fait » par fournisseur : c'est la FAQ que le modèle reçoit dans son prompt.
# MAGIC
# MAGIC **Ce qu'elle lit :** la table source (lecture seule).
# MAGIC
# MAGIC **Ce qu'elle écrit dans MLflow :** rien. Elle écrit seulement dans Unity Catalog. La table est remplacée à chaque run, l'historique Delta conserve les versions précédentes.

# COMMAND ----------

# DBTITLE 1,Paramètres
import os, sys
sys.path.append(os.path.abspath(".."))  # rend src/common importable depuis src/build
from common.params import get_param

catalog = get_param(dbutils, "catalog")
dev_schema = get_param(dbutils, "dev_schema")
source_table = get_param(dbutils, "source_table")

knowledge_table = f"{catalog}.{dev_schema}.faq_knowledge"

# COMMAND ----------

# DBTITLE 1,Schéma dev
# IF NOT EXISTS : relançable sans effet de bord.
spark.sql("CREATE SCHEMA IF NOT EXISTS IDENTIFIER(:s)", args={"s": f"{catalog}.{dev_schema}"})

# COMMAND ----------

# DBTITLE 1,Table faq_knowledge
spark.sql(
    """
    CREATE OR REPLACE TABLE IDENTIFIER(:tgt)
    COMMENT 'MLflow tutorial: synthetic FAQ derived from samples.bakehouse'
    AS SELECT
      supplierID AS supplier_id,
      name       AS supplier_name,
      ingredient,
      city,
      continent,
      concat(name, ' provides ', ingredient, '. It is based in ', city, ' (', continent, ').') AS fact
    FROM IDENTIFIER(:src)
    """,
    args={"tgt": knowledge_table, "src": source_table},
)
display(spark.table(knowledge_table).orderBy("supplier_id"))