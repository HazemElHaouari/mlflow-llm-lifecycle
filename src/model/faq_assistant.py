# Modèle MLflow "from code" : ce fichier EST le modèle. Il ne contient aucun poids,
# seulement un prompt et des paramètres autour d'un endpoint Foundation Model.
# La configuration (prompt, FAQ, température...) est injectée via model_config au log.
import mlflow
import pandas as pd
from mlflow.deployments import get_deploy_client
from mlflow.pyfunc import PythonModel


class FaqAssistant(PythonModel):
    def load_context(self, context):
        self.config = context.model_config
        self.client = get_deploy_client("databricks")

    def _answer(self, question: str) -> str:
        cfg = self.config
        user_message = cfg["prompt_template"].format(faq=cfg["faq_text"], question=question)
        response = self.client.predict(
            endpoint=cfg["serving_endpoint"],
            inputs={
                "messages": [
                    {"role": "system", "content": cfg["system_prompt"]},
                    {"role": "user", "content": user_message},
                ],
                "temperature": float(cfg["temperature"]),
                "max_tokens": int(cfg["max_tokens"]),
            },
        )
        return response["choices"][0]["message"]["content"]

    def predict(self, context, model_input, params=None):
        questions = model_input["question"].tolist() if isinstance(model_input, pd.DataFrame) else list(model_input)
        return [self._answer(q) for q in questions]


mlflow.models.set_model(FaqAssistant())
