import re
import unicodedata


def normalizar_texto(valor: object) -> str:
    """ÚNICA función de normalización de texto del servicio.

    Quita acentos, pasa a minúsculas y convierte cualquier racha de caracteres que no sean
    letras o dígitos en un solo "_". Sirve para nombres de campo ("Tienda ID" -> "tienda_id"),
    familias ("LÁCTEOS " -> "lacteos") e identificadores ("T05" -> "t05").
    """
    s = unicodedata.normalize("NFKD", str(valor))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", s.casefold()).strip("_")
