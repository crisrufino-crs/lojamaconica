from django.core.management.base import BaseCommand

from gestao.push import gerar_chaves


class Command(BaseCommand):
    help = "Gera o par de chaves das notificações no celular. Rode uma única vez e guarde no arquivo .env."

    def handle(self, *args, **opcoes):
        publica, privada = gerar_chaves()
        self.stdout.write(f"PUSH_CHAVE_PUBLICA={publica}\nPUSH_CHAVE_PRIVADA={privada}")
