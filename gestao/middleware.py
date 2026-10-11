from django.contrib.auth import logout
from django.shortcuts import redirect

from .models import Loja


class LojaMiddleware:
    """Descobre em qual Loja a pessoa está trabalhando e guarda em request.loja.

    Usuário comum: sempre a Loja dele. Administrador da plataforma: a Loja que
    ele escolheu na tela "Lojas" (fica guardada na sessão).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.loja = None
        usuario = request.user
        if usuario.is_authenticated:
            if usuario.is_superuser:
                loja_id = request.session.get("loja_id")
                if loja_id:
                    request.loja = Loja.objects.filter(pk=loja_id).first()
            else:
                request.loja = usuario.loja
                if request.loja is None or not request.loja.ativa:
                    logout(request)
                    return redirect("entrar")
            # Quem recebeu a senha por e-mail só usa o sistema depois de trocá-la.
            if usuario.deve_trocar_senha and not request.path.startswith(("/senha/", "/sair/", "/static/")):
                return redirect("senha_trocar")
        return self.get_response(request)
