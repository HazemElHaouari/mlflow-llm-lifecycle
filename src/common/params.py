def get_param(dbutils, name: str, required: bool = True) -> str:
    """Lit un paramètre fourni par le job (widget). Aucune valeur par défaut codée en dur."""
    dbutils.widgets.text(name, "")
    value = dbutils.widgets.get(name).strip()
    if required and not value:
        raise ValueError(f"Paramètre '{name}' manquant : il doit être fourni par le job du bundle.")
    return value
