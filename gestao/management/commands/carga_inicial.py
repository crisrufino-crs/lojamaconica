"""Carga inicial na instalação: importa as planilhas colocadas na pasta "carga" do projeto.

Coloque o arquivo .xlsx em carga/ e, em carga/loja.txt, o número ou o nome da Loja de destino.
Cada planilha é importada UMA única vez (o sistema guarda uma "assinatura" do arquivo). Assim,
as instalações seguintes não desfazem alterações feitas depois pelo site.
"""
import hashlib
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from gestao import importacao
from gestao.management.commands.importar_irmaos import achar_loja
from gestao.models import CargaImportada


class Command(BaseCommand):
    help = "Importa, uma única vez, as planilhas de Irmãos da pasta carga/."

    def add_arguments(self, parser):
        parser.add_argument("--pasta", default=str(Path(settings.BASE_DIR) / "carga"))

    def handle(self, *args, **o):
        pasta = Path(o["pasta"])
        planilhas = sorted(pasta.glob("*.xlsx")) if pasta.is_dir() else []
        if not planilhas:
            self.stdout.write("Carga inicial: nenhuma planilha na pasta carga/.")
            return
        destino = (pasta / "loja.txt")
        if not destino.is_file() or not destino.read_text(encoding="utf-8").strip():
            self.stderr.write("Carga inicial: falta o arquivo carga/loja.txt com o número ou o nome da Loja. Nada importado.")
            return
        try:
            loja = achar_loja(destino.read_text(encoding="utf-8").strip().splitlines()[0])
        except Exception as erro:
            self.stderr.write(f"Carga inicial: {erro}")
            return
        for planilha in planilhas:
            conteudo = planilha.read_bytes()
            assinatura = hashlib.sha256(conteudo).hexdigest()
            if CargaImportada.objects.filter(loja=loja, assinatura=assinatura).exists():
                self.stdout.write(f"Carga inicial: {planilha.name} já tinha sido importada em {loja}.")
                continue
            try:
                with open(planilha, "rb") as arquivo:
                    analise = importacao.analisar(loja, arquivo)
                criados, atualizados = importacao.gravar(loja, analise)
            except Exception as erro:  # uma planilha com problema não pode derrubar a instalação do site
                self.stderr.write(f"Carga inicial: {planilha.name} não foi importada: {erro}")
                continue
            resumo = f"{criados} incluído(s), {atualizados} atualizado(s), {len(analise['erros'])} recusado(s)"
            CargaImportada.objects.create(loja=loja, arquivo=planilha.name[:200], assinatura=assinatura, resumo=resumo)
            self.stdout.write(f"Carga inicial: {planilha.name} → {loja}: {resumo}.")
            for e in analise["erros"]:
                self.stdout.write(f"  linha {e['linha']} recusada: {'; '.join(e['problemas'])}")
