from django.db import migrations

ANTIGO, NOVO = "plataforma", "cristi286913ano"


def renomear(apps, schema_editor):
    """Troca o nome de acesso do administrador da plataforma em bancos já criados. A senha não muda."""
    Usuario = apps.get_model("gestao", "Usuario")
    if not Usuario.objects.filter(username=NOVO).exists():
        Usuario.objects.filter(username=ANTIGO, is_superuser=True).update(username=NOVO)


def desfazer(apps, schema_editor):
    Usuario = apps.get_model("gestao", "Usuario")
    if not Usuario.objects.filter(username=ANTIGO).exists():
        Usuario.objects.filter(username=NOVO, is_superuser=True).update(username=ANTIGO)


class Migration(migrations.Migration):
    dependencies = [("gestao", "0001_initial")]
    operations = [migrations.RunPython(renomear, desfazer)]
