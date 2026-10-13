from django.db import migrations

NOVA = {"nome": "L'AQUILA ROMANA", "numero": "3365", "potencia": "Grande Oriente do Brasil", "oriente": "São Paulo", "uf": "SP"}


def renomear(apps, schema_editor):
    """A Loja nº 123 (Fraternidade e Luz, criada como exemplo) passa a ser a L'AQUILA ROMANA nº 3365."""
    Loja = apps.get_model("gestao", "Loja")
    if Loja.objects.filter(numero=NOVA["numero"]).exists():
        return
    Loja.objects.filter(numero="123", nome__icontains="Fraternidade e Luz").update(**NOVA)


class Migration(migrations.Migration):
    dependencies = [("gestao", "0005_carga_importada")]
    operations = [migrations.RunPython(renomear, migrations.RunPython.noop)]
