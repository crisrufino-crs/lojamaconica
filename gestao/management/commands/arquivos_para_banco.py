"""Leva para o banco de dados os arquivos que ainda estejam só no disco (instalações antigas). Pode rodar sempre."""
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from gestao.armazenamento import BancoStorage
from gestao.models import ArquivoGuardado


class Command(BaseCommand):
    help = "Copia para o banco os arquivos enviados que ainda estão só no disco."

    def handle(self, *args, **opcoes):
        raiz = Path(settings.MEDIA_ROOT)
        if not raiz.is_dir():
            self.stdout.write("Arquivos: nada no disco para levar ao banco.")
            return
        guardar, levados = BancoStorage(), 0
        for caminho in raiz.rglob("*"):
            if not caminho.is_file():
                continue
            nome = caminho.relative_to(raiz).as_posix()
            if not ArquivoGuardado.objects.filter(nome=nome).exists():
                guardar._save(nome, ContentFile(caminho.read_bytes()))
                levados += 1
        self.stdout.write(f"Arquivos: {levados} arquivo(s) levado(s) do disco para o banco.")
