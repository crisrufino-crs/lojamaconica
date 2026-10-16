"""Instalação em servidor próprio: cria a Loja de verdade (sem dados de exemplo) se o banco estiver vazio."""
from django.core.management.base import BaseCommand
from django.db import transaction

from gestao import servicos
from gestao.models import Loja


class Command(BaseCommand):
    help = "Cria a Loja L'AQUILA ROMANA nº 3365, pronta para uso e sem dados fictícios, quando ainda não há nenhuma Loja."

    @transaction.atomic
    def handle(self, *args, **opcoes):
        if Loja.objects.exists():
            self.stdout.write("Loja inicial: já existem Lojas cadastradas; nada a fazer.")
            return
        loja = Loja.objects.create(nome="L'AQUILA ROMANA", numero="3365", potencia="Grande Oriente do Brasil",
                                   rito="R∴E∴A∴A∴", oriente="São Paulo", uf="SP")
        servicos.preparar_loja(loja)
        self.stdout.write(f"Loja inicial: {loja} criada (graus, plano de contas e caixa prontos).")
