from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

MODULOS = {
    "secretaria": "Secretaria",
    "tesouraria": "Tesouraria",
    "chancelaria": "Chancelaria",
    "hospitalaria": "Hospitalaria",
    "admin": "Administração",
}


def com_loja(view):
    """Exige login e uma Loja selecionada."""
    @wraps(view)
    @login_required
    def interno(request, *args, **kwargs):
        if request.loja is None:
            return redirect("plataforma_lojas")
        return view(request, *args, **kwargs)
    return interno


def exige(modulo):
    """Exige login, Loja selecionada e acesso ao módulo informado."""
    def decorador(view):
        @wraps(view)
        @com_loja
        def interno(request, *args, **kwargs):
            if not request.user.pode(modulo):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return interno
    return decorador


def so_plataforma(view):
    @wraps(view)
    @login_required
    def interno(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return interno
