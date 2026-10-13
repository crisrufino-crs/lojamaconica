"""Importa Irmãos de uma planilha .xlsx (mesmo formato da planilha modelo).

    python manage.py importar_irmaos cadastro.xlsx --loja 123            (só confere, não grava)
    python manage.py importar_irmaos cadastro.xlsx --loja 123 --gravar   (grava)

--loja aceita o número, o código interno ou o nome da Loja.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from gestao import importacao
from gestao.models import Loja


def achar_loja(texto):
    texto = str(texto).strip()
    filtro = Q(numero=texto) | Q(nome__iexact=texto)
    if texto.isdigit():
        filtro |= Q(pk=int(texto))
    lojas = list(Loja.objects.filter(filtro))
    if not lojas:
        lojas = list(Loja.objects.filter(nome__icontains=texto))
    if len(lojas) != 1:
        opcoes = "; ".join(f"{l.pk} = {l}" for l in Loja.objects.all())
        raise CommandError(f"Não encontrei uma única Loja para “{texto}”. Lojas cadastradas: {opcoes}")
    return lojas[0]


class Command(BaseCommand):
    help = "Importa o cadastro de Irmãos de uma planilha do Excel (.xlsx)."

    def add_arguments(self, parser):
        parser.add_argument("arquivo")
        parser.add_argument("--loja", required=True, help="Número, código ou nome da Loja.")
        parser.add_argument("--gravar", action="store_true", help="Sem isto, apenas confere a planilha.")

    def handle(self, *args, **o):
        loja = achar_loja(o["loja"])
        try:
            with open(o["arquivo"], "rb") as arquivo:
                analise = importacao.analisar(loja, arquivo)
        except OSError as erro:
            raise CommandError(f"Não consegui abrir a planilha: {erro}")
        except importacao.ErroDePlanilha as erro:
            raise CommandError(str(erro))
        self.stdout.write(f"{loja}: {analise['novos']} novo(s), {analise['atualizados']} a atualizar, "
                          f"{len(analise['erros'])} linha(s) recusada(s).")
        for e in analise["erros"]:
            self.stdout.write(f"  linha {e['linha']} ({e['nome'] or 'sem nome'}): {'; '.join(e['problemas'])}")
        if analise["ignoradas"]:
            self.stdout.write("  colunas ignoradas: " + "; ".join(analise["ignoradas"]))
        if not o["gravar"]:
            self.stdout.write("Nada foi gravado. Rode de novo com --gravar para importar.")
            return
        criados, atualizados = importacao.gravar(loja, analise)
        self.stdout.write(self.style.SUCCESS(f"Gravado: {criados} incluído(s), {atualizados} atualizado(s)."))
