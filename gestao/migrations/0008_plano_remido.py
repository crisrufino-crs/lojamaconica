from django.db import migrations, models


def criar_plano_remido(apps, schema_editor):
    """Acrescenta a conta "1.6 Remido (isenção de mensalidade)" ao plano de contas das Lojas que ainda não têm."""
    import unicodedata
    Loja, PlanoConta = apps.get_model("gestao", "Loja"), apps.get_model("gestao", "PlanoConta")

    def normal(texto):
        return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()

    for loja in Loja.objects.all():
        contas = list(PlanoConta.objects.filter(loja=loja))
        if any(normal(c.nome).startswith("remido") or "isencao" in normal(c.nome) for c in contas):
            continue
        receitas = next((c for c in contas if c.codigo == "1"), None)
        codigo = "1.6"
        usados = {c.codigo for c in contas}
        n = 6
        while codigo in usados:
            n += 1
            codigo = f"1.{n}"
        PlanoConta.objects.create(loja=loja, codigo=codigo, nome="Remido (isenção de mensalidade)", tipo="R",
                                  pai=receitas, aceita_lancamento=True)


class Migration(migrations.Migration):
    dependencies = [("gestao", "0007_irmao_financeiro")]
    operations = [
        migrations.AlterField(
            model_name="lancamento", name="status",
            field=models.CharField(choices=[("aberto", "Em aberto"), ("pago", "Quitado"), ("isento", "Isento"),
                                            ("cancelado", "Cancelado")], default="aberto", max_length=10,
                                   verbose_name="situação")),
        migrations.RunPython(criar_plano_remido, migrations.RunPython.noop),
    ]
