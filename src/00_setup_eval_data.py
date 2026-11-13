# Databricks notebook source
# MAGIC %md
# MAGIC # Étape 0 : setup_eval_data
# MAGIC
# MAGIC **Ce que fait cette étape :** crée le schéma (s'il n'existe pas) et deux tables Delta dérivées de la table d'exemple `samples.bakehouse.sales_suppliers` (fournisseurs fictifs d'une boulangerie, données synthétiques fournies par Databricks).
# MAGIC * `faq_knowledge` : une phrase « fait » par fournisseur. C'est la FAQ que le modèle reçoit dans son prompt.
# MAGIC * `faq_eval_set` : environ 15 paires question / réponse attendue / mot-clé attendu, générées de façon déterministe.
# MAGIC
# MAGIC **Ce qu'elle lit :** la table source (lecture seule).
# MAGIC
# MAGIC **Ce qu'elle écrit dans MLflow :** rien. Elle écrit seulement dans Unity Catalog. Les tables sont remplacées à chaque run et l'historique Delta conserve les versions précédentes.

# COMMAND ----------

from common.params import get_param

catalog = get_param(dbutils, "catalog")
schema = get_param(dbutils, "schema")
source_table = get_param(dbutils, "source_table")
eval_set_size = int(get_param(dbutils, "eval_set_size"))

knowledge_table = f"{catalog}.{schema}.faq_knowledge"
eval_table = f"{catalog}.{schema}.faq_eval_set"

# COMMAND ----------

# IF NOT EXISTS : relançable sans effet de bord.
spark.sql("CREATE SCHEMA IF NOT EXISTS IDENTIFIER(:s)", args={"s": f"{catalog}.{schema}"})

# COMMAND ----------

spark.sql(
    """
    CREATE OR REPLACE TABLE IDENTIFIER(:tgt)
    COMMENT 'Tutoriel MLflow : FAQ synthetique derivee de samples.bakehouse'
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

# COMMAND ----------

# Trois types de question en rotation. Le mot-clé attendu sert au scorer déterministe.
spark.sql(
    """
    CREATE OR REPLACE TABLE IDENTIFIER(:tgt)
    COMMENT 'Tutoriel MLflow : jeu d evaluation question / reponse attendue'
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

# COMMAND ----------

display(spark.table(eval_table).orderBy("question_id"))
print(f"{knowledge_table} : {spark.table(knowledge_table).count()} faits")
print(f"{eval_table} : {spark.table(eval_table).count()} questions")
