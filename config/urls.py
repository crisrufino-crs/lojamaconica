from django.http import HttpResponse
from django.urls import include, path


def saude(request):
    """Resposta rápida (sem banco de dados) para o monitor que mantém o site acordado."""
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [path("saude/", saude, name="saude"), path("", include("gestao.urls"))]
