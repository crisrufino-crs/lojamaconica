from django.core.management.base import BaseCommand

from gestao.models import Loja
from gestao.servicos import rotina_diaria


class Command(BaseCommand):
    help = "Envia os avisos automáticos do dia (aniversariantes e lembretes). Agende para rodar toda manhã."

    def handle(self, *args, **opcoes):
        for loja in Loja.objects.filter(ativa=True):
            resumo = rotina_diaria(loja)
            self.stdout.write(f"{loja}: {resumo or 'já tinha rodado hoje'}")
