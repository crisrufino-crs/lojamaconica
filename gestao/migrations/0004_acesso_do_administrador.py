from django.db import migrations

from gestao.acesso_admin import ADMIN_LOGIN, ADMIN_SENHA_HASH, LOGINS_ANTIGOS


def ajustar(apps, schema_editor):
    """Em bancos já criados, passa o administrador antigo para o login e a senha definitivos."""
    Usuario = apps.get_model("gestao", "Usuario")
    if Usuario.objects.filter(username=ADMIN_LOGIN).exists():
        return
    antigo = Usuario.objects.filter(username__in=LOGINS_ANTIGOS, is_superuser=True).order_by("pk").first()
    if antigo:
        antigo.username, antigo.password, antigo.first_name = ADMIN_LOGIN, ADMIN_SENHA_HASH, "Cristiano"
        antigo.save(update_fields=["username", "password", "first_name"])


class Migration(migrations.Migration):
    dependencies = [("gestao", "0003_senha_por_email")]
    operations = [migrations.RunPython(ajustar, migrations.RunPython.noop)]
