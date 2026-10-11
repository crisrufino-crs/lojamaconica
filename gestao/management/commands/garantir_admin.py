from django.core.management.base import BaseCommand

from gestao.acesso_admin import ADMIN_LOGIN, ADMIN_SENHA_HASH
from gestao.models import Usuario


class Command(BaseCommand):
    help = "Garante que o Administrador da plataforma existe e consegue entrar. Roda a cada instalação."

    def add_arguments(self, parser):
        parser.add_argument("--redefinir", action="store_true", help="Volta a senha do Administrador para a original.")

    def handle(self, *args, **opcoes):
        admin = Usuario.objects.filter(username__iexact=ADMIN_LOGIN).first()
        if admin is None:
            Usuario.objects.create(username=ADMIN_LOGIN, password=ADMIN_SENHA_HASH, first_name=ADMIN_LOGIN,
                                   is_superuser=True, is_staff=True, is_active=True)
            self.stdout.write(f"Administrador “{ADMIN_LOGIN}” criado.")
            return
        mudou = []
        if not (admin.is_superuser and admin.is_active and admin.loja_id is None and admin.username == ADMIN_LOGIN):
            admin.username, admin.is_superuser, admin.is_staff, admin.is_active, admin.loja = ADMIN_LOGIN, True, True, True, None
            mudou.append("acesso liberado")
        # Enquanto o Administrador nunca tiver conseguido entrar, a senha original é restaurada a cada instalação.
        # Depois do primeiro acesso, a senha passa a ser só dele e a instalação não mexe mais nela.
        if opcoes["redefinir"] or not admin.has_usable_password() or admin.last_login is None:
            admin.password, admin.deve_trocar_senha = ADMIN_SENHA_HASH, False
            mudou.append("senha original restaurada")
        admin.save()
        self.stdout.write(f"Administrador “{ADMIN_LOGIN}”: " + (", ".join(mudou) or "já estava certo"))
