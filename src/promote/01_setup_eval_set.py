# Databricks notebook source
# DBTITLE 1,Description
# MAGIC %md
# MAGIC # Job promote · étape 1 : setup_eval_set
# MAGIC
# MAGIC **Côté :** workspace cible (ici simulé par le schéma prd).
# MAGIC
# MAGIC **Ce que fait cette étape :** crée le schéma prd (s'il n'existe pas) et la table `faq_eval_set` : environ 15 paires question / réponse attendue / mot-clé attendu, générées de façon déterministe depuis `samples.bakehouse.sales_suppliers`.
# MAGIC
# MAGIC **Pourquoi ici et pas dans le job build :** le jeu d'évaluation appartient à celui qui contrôle la promotion. L'équipe qui fabrique le modèle ne peut pas modifier « l'examen ».
# MAGIC
# MAGIC **Ce qu'elle lit :** la table source (lecture seule).
# MAGIC
# MAGIC **Ce qu'elle écrit dans MLflow :** rien. Elle écrit seulement dans Unity Catalog.

# COMMAND ----------

# DBTITLE 1,Paramètres
import os, sys
sys.path.append(os.path.abspath(".."))  # rend src/common importable depuis src/promote
from common.params import get_param

catalog = get_param(dbutils, "catalog")
prd_schema = get_param(dbutils, "prd_schema")
source_table = get_param(dbutils, "source_table")
eval_set_size = int(get_param(dbutils, "eval_set_size"))

eval_table = f"{catalog}.{prd_schema}.faq_eval_set"

# COMMAND ----------

# DBTITLE 1,Schéma prd
# IF NOT EXISTS : relançable sans effet de bord.
spark.sql("CREATE SCHEMA IF NOT EXISTS IDENTIFIER(:s)", args={"s": f"{catalog}.{prd_schema}"})

# COMMAND ----------

# DBTITLE 1,Table faq_eval_set
# Trois types de question en rotation. Le mot-clé attendu sert au scorer déterministe.
spark.sql(
    """
    CREATE OR REPLACE TABLE IDENTIFIER(:tgt)
    COMMENT 'MLflow tutorial: evaluation set (question / expected answer)'
    AS WITH s AS (
      SELECT *, row_number() OVER (ORDER BY supplierID) AS rn FROM IDENTIFIER(:src)
    )
    SELECT
      rn AS question_id,
      CASE rn % 3 WHEN 1 THEN concat('Which ingredient does the supplier ', name, ' provide?')
                  WHEN 2 THEN concat('In which city is the supplier ', name, ' based?')
                  ELSE concat('Which supplier provides ', ingredient, '?') END AS question,
      CASE rn % 3 WHEN 1 THEN concat(name, ' provides ', ingredient, '.')
                  WHEN 2 THEN concat(name, ' is based in ', city, '.')
                  ELSE concat(ingredient, ' is provided by ', name, '.') END AS expected_response,
      CASE rn % 3 WHEN 1 THEN ingredient WHEN 2 THEN city ELSE name END AS expected_keyword
    FROM s
    WHERE rn <= :n
    """,
    args={"tgt": eval_table, "src": source_table, "n": eval_set_size},
)
display(spark.table(eval_table).orderBy("question_id"))