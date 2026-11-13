# Deux variantes de prompt. "weak" est volontairement mauvaise : sans la FAQ et avec
# une consigne vague, elle doit être bloquée par la validation.

GOOD_SYSTEM_PROMPT = (
    "You are the internal assistant of the Bakehouse supplier desk. "
    "Answer ONLY from the FAQ below. Reply in one short sentence that names the "
    "supplier, ingredient or city explicitly. If the FAQ does not contain the answer, "
    "say you do not know."
)
GOOD_TEMPLATE = "FAQ:\n{faq}\n\nQuestion: {question}\nAnswer:"

WEAK_SYSTEM_PROMPT = (
    "You are a creative assistant. Never mention names, places or ingredients. "
    "Answer with a vague, general statement."
)
WEAK_TEMPLATE = "Question: {question}\nAnswer:"


def build_prompt_config(variant: str, system_prompt_override: str = "") -> dict:
    if variant == "good":
        return {
            "system_prompt": system_prompt_override or GOOD_SYSTEM_PROMPT,
            "prompt_template": GOOD_TEMPLATE,
            "include_faq": True,
        }
    if variant == "weak":
        # La surcharge system_prompt est ignorée pour garantir une démo reproductible.
        return {"system_prompt": WEAK_SYSTEM_PROMPT, "prompt_template": WEAK_TEMPLATE, "include_faq": False}
    raise ValueError(f"prompt_variant inconnu : '{variant}' (attendu : good ou weak)")
