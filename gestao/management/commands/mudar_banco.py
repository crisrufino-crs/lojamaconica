"""Copia, uma única vez, todos os dados do banco antigo (Render) para o banco novo (BANCO_URL, ex.: Neon).

Roda sozinho na publicação (build.sh), logo depois do migrate. Só copia quando o banco novo ainda não tem
nenhuma Loja; depois disso não faz mais nada. Se a cópia falhar, interrompe a publicação: assim o site
continua no ar com o banco antigo e nenhum dado fictício é criado no banco novo.
"""
import os
import tempfile

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from gestao.models import Irmao, Lancamento, Loja, Usuario

EXCLUIR = ["contenttypes", "auth.permission", "sessions"]
CONFERIR = [Loja, Usuario, Irmao, Lancamento]


class Command(BaseCommand):
    help = "Copia os dados do banco antigo (DATABASE_URL) para o banco novo (BANCO_URL), uma única vez."

    def handle(self, *args, **opcoes):
        if "antigo" not in settings.DATABASES:
            self.stdout.write("Mudança de banco: nada a fazer (não há banco antigo configurado).")
            return
        if Loja.objects.using("default").exists():
            self.stdout.write("Mudança de banco: o banco novo já tem dados; nada foi copiado.")
            return
        try:
            call_command("migrate", database="antigo", check_unapplied=True, verbosity=0)
        except SystemExit:
            # O banco antigo está numa versão anterior: atualiza antes de copiar.
            call_command("migrate", database="antigo", interactive=False, verbosity=0)
        antes = {m.__name__: m.objects.using("antigo").count() for m in CONFERIR}
        self.stdout.write(f"Mudança de banco: copiando {antes} ...")

        arquivo = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        arquivo.close()
        try:
            call_command("dumpdata", database="antigo", natural_foreign=True, natural_primary=True,
                         exclude=EXCLUIR, output=arquivo.name, verbosity=0)
            call_command("flush", database="default", interactive=False, verbosity=0)
            call_command("loaddata", arquivo.name, database="default", verbosity=0)
        finally:
            os.unlink(arquivo.name)

        depois = {m.__name__: m.objects.using("default").count() for m in CONFERIR}
        if depois != antes:
            raise CommandError(f"A cópia não conferiu: antes {antes}, depois {depois}.")
        self.stdout.write(self.style.SUCCESS(f"Mudança de banco: concluída e conferida {depois}."))
