from django.conf import settings

from .permissoes import MODULOS


def _texto_sobre(cor):
    """Branco ou quase-preto, o que der mais leitura em cima da cor escolhida pela Loja."""
    try:
        r, g, b = (int(cor[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, TypeError):
        return "#FFFFFF"
    return "#111827" if (0.299 * r + 0.587 * g + 0.114 * b) > 160 else "#FFFFFF"


def sistema(request):
    usuario = getattr(request, "user", None)
    loja = getattr(request, "loja", None)
    dados = {"modulo": "", "loja": loja, "MODULOS": MODULOS, "modulos_do_usuario": [], "nao_lidas": 0,
             "push_chave_publica": settings.PUSH_CHAVE_PUBLICA,
             "cor_primaria": loja.cor_primaria if loja else "#1F3A5F",
             "cor_destaque": loja.cor_destaque if loja else "#B8860B"}
    dados["texto_primaria"] = _texto_sobre(dados["cor_primaria"])
    from .cadastros import MENUS
    dados["MENUS"] = MENUS
    if usuario is not None and usuario.is_authenticated and loja is not None:
        dados["modulos_do_usuario"] = [(m, MODULOS[m]) for m in usuario.modulos]
        dados["eh_master"] = usuario.pode("admin")
        dados["nao_lidas"] = usuario.notificacoes.filter(lida=False, loja=loja).count()
    return dados
