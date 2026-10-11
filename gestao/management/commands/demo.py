"""Cria dados de exemplo (fictícios) para conhecer o sistema: python manage.py demo"""
import os
import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from gestao import servicos
from gestao.acesso_admin import ADMIN_LOGIN, ADMIN_SENHA_HASH
from gestao.models import (Ata, Bem, CampoExtra, CertificadoVisita, ContaBancaria, Dependente, Emprestimo, Evento, Grau,
                           HistoricoGrau, Informativo, Irmao, Lancamento, Licenca, Loja, LojaCoirma, PlanoConta,
                           Presenca, Sessao, Transferencia, Usuario)

SENHA = os.environ.get("DEMO_SENHA") or "loja-demo-2026"
NOMES = ["Antônio Bragança Leal", "Bernardo Coutinho Reis", "Caio Figueira Nunes", "Davi Sampaio Rocha",
         "Eduardo Vilar Monteiro", "Fábio Queiroz Dantas", "Gustavo Arantes Lima", "Heitor Paiva Coelho",
         "Ivo Teles Barreto", "Jorge Macedo Pires", "Leandro Siqueira Faro", "Marcelo Tavares Brum",
         "Nelson Prado Guerra", "Otávio Rangel Cunha", "Paulo Lacerda Viana", "Rafael Bastos Meireles",
         "Sérgio Amaral Fontes", "Tiago Novaes Peixoto"]
CARGOS = {0: "Venerável Mestre", 1: "1º Vigilante", 2: "2º Vigilante", 3: "Orador", 4: "Secretário",
          5: "Tesoureiro", 6: "Chanceler", 7: "Hospitaleiro", 8: "Mestre de Cerimônias"}


class Command(BaseCommand):
    help = "Cria duas Lojas fictícias com dados de exemplo."

    @transaction.atomic
    def handle(self, *args, **opcoes):
        if Loja.objects.exists():
            self.stderr.write("Já existem Lojas cadastradas; os dados de exemplo só são criados em um banco vazio.")
            return
        random.seed(33)
        hoje = timezone.localdate()
        Usuario.objects.create(username=ADMIN_LOGIN, password=ADMIN_SENHA_HASH, email="", first_name="Cristiano",
                               is_superuser=True, is_staff=True)
        loja = Loja.objects.create(
            nome="A∴R∴L∴S∴ Fraternidade e Luz", numero="123", potencia="Grande Oriente Exemplo", rito="R∴E∴A∴A∴",
            oriente="São Paulo", uf="SP", cnpj="00.000.000/0001-91", email="secretaria@fraternidadeeluz.exemplo.com.br",
            dia_sessao=1, horario_sessao=time(20, 0), valor_mensalidade=Decimal("180.00"), dia_vencimento=10)
        servicos.preparar_loja(loja)
        graus = {g.nome: g for g in Grau.objects.filter(loja=loja)}
        padrinho = CampoExtra.objects.create(loja=loja, rotulo="Padrinho", ordem=1)

        irmaos = []
        for n, nome in enumerate(NOMES):
            anos_ordem = random.randint(1, 30)
            iniciacao = hoje - timedelta(days=anos_ordem * 365 + random.randint(0, 300))
            grau = "Aprendiz" if n >= 16 else ("Companheiro" if n >= 14 else ("Mestre Instalado" if n in (0, 3) else "Mestre"))
            if grau == "Aprendiz":
                iniciacao = hoje - timedelta(days=random.randint(60, 200))
            primeiro = nome.split()[0].lower().translate(str.maketrans("áãâéêíóôõúç", "aaaeeiooouc"))
            aniversario = hoje + timedelta(days=(n - 2) * 9)  # espalha aniversários; o 3º da lista faz hoje
            i = Irmao.objects.create(
                loja=loja, nome=nome, cim=f"{200100 + n}", situacao="ativo", cargo=CARGOS.get(n, ""),
                data_nascimento=date(hoje.year - random.randint(32, 70), aniversario.month, min(aniversario.day, 28)),
                email=f"{primeiro}@exemplo.com.br", celular=f"(11) 9{random.randint(7000, 9999)}-{random.randint(1000, 9999)}",
                endereco=f"Rua das Acácias, {100 + n * 7}", bairro="Centro", cidade="São Paulo", uf="SP", cep="01000-000",
                profissao=random.choice(["Engenheiro", "Advogado", "Professor", "Comerciante", "Médico", "Contador"]),
                estado_civil="casado" if n % 3 else "solteiro", data_iniciacao=iniciacao, data_filiacao=iniciacao,
                extras={str(padrinho.pk): NOMES[(n + 5) % len(NOMES)]} if n > 5 else {})
            passos = ["Aprendiz", "Companheiro", "Mestre", "Mestre Instalado"]
            for k, g in enumerate(passos[:passos.index(grau) + 1]):
                HistoricoGrau.objects.create(loja=loja, irmao=i, grau=graus[g], data=min(iniciacao + timedelta(days=400 * k), hoje),
                                             observacao=["Iniciação", "Elevação", "Exaltação", "Instalação"][k])
            servicos.atualizar_grau(i)
            if n % 3:
                Dependente.objects.create(loja=loja, irmao=i, nome=f"Helena {nome.split()[-1]}", parentesco="conjuge",
                                          data_nascimento=date(hoje.year - random.randint(30, 65), random.randint(1, 12), random.randint(1, 28)))
            if n % 4 == 1:
                Dependente.objects.create(loja=loja, irmao=i, nome=f"Lucas {nome.split()[-1]}", parentesco="filho",
                                          data_nascimento=date(hoje.year - random.randint(4, 22), hoje.month, random.randint(1, 28)))
            irmaos.append(i)
        irmaos[13].situacao = "licenciado"
        irmaos[13].save()
        Licenca.objects.create(loja=loja, irmao=irmaos[13], inicio=hoje - timedelta(days=40), fim=hoje + timedelta(days=50), motivo="Viagem a trabalho")

        acessos = [("veneravel", 0, {"is_master": True}), ("secretario", 4, {"acesso_secretaria": True}),
                   ("tesoureiro", 5, {"acesso_tesouraria": True}), ("chanceler", 6, {"acesso_chancelaria": True}),
                   ("hospitaleiro", 7, {"acesso_hospitalaria": True}), ("irmao", 10, {})]
        for login, indice, extras in acessos:
            i = irmaos[indice]
            Usuario.objects.create_user(login, i.email, SENHA, loja=loja, irmao=i, first_name=i.nome.split()[0],
                                        last_name=" ".join(i.nome.split()[1:]), **extras)

        coirmas = [LojaCoirma.objects.create(loja=loja, nome=n, numero=num, potencia="Grande Oriente Exemplo", oriente=o, uf="SP",
                                             veneravel=v, email=f"contato{num}@exemplo.com.br", dia_sessao=d)
                   for n, num, o, v, d in [("Acácia do Vale", "45", "Campinas", "Mário Esteves", "Quartas, 20h"),
                                           ("Estrela do Oriente", "78", "Santos", "Rui Camargo", "Segundas, 20h"),
                                           ("União e Trabalho", "210", "São Paulo", "Celso Antunes", "Quintas, 19h30")]]

        # Chancelaria: sessões semanais dos últimos meses, com presença
        dia = hoje - timedelta(days=(hoje.weekday() - 1) % 7)  # última terça (ou hoje)
        sessoes = []
        for semana in range(16):
            data = dia - timedelta(weeks=semana)
            s = Sessao.objects.create(loja=loja, data=data, tipo="magna" if semana == 6 else "ordinaria",
                                      grau=graus["Mestre"] if semana % 5 == 4 else None,
                                      descricao="Sessão Magna de Iniciação" if semana == 6 else "")
            servicos.sincronizar_presencas(s)
            sessoes.append(s)
        for p in Presenca.objects.filter(loja=loja, status="F").select_related("irmao"):
            assiduidade = 0.95 if p.irmao.cargo else (0.35 if p.irmao.nome.startswith(("Otávio", "Rafael")) else 0.75)
            if random.random() < assiduidade:
                p.status = "P"
            elif random.random() < 0.3:
                p.status, p.justificativa = "A", random.choice(["Viagem a trabalho", "Problema de saúde na família", "Plantão"])
            p.save()
        for k in (9, 11, 15):
            CertificadoVisita.objects.create(loja=loja, irmao=irmaos[k], data=hoje - timedelta(days=20 + k), coirma=random.choice(coirmas))

        # Secretaria: atas, informativos, agenda
        for s in sessoes[:4]:
            Ata.objects.create(loja=loja, numero=f"{1200 - sessoes.index(s)}", data=s.data, titulo=f"Ata da {s}", grau=graus["Aprendiz"],
                               aprovada=s != sessoes[0], texto="Aos trabalhos abertos ritualisticamente, foi lida e aprovada a ata anterior. "
                               "Expediente, ordem do dia e tronco de beneficência conforme registro.\n\n(Texto de exemplo.)")
        Informativo.objects.create(loja=loja, titulo="Campanha do agasalho", data=hoje - timedelta(days=3),
                                   texto="A Hospitalaria recebe doações de agasalhos e cobertores até o fim do mês. Entregas na Secretaria.")
        Informativo.objects.create(loja=loja, titulo="Regularização de mensalidades", data=hoje - timedelta(days=12),
                                   texto="Os boletos do semestre já estão disponíveis na área de cada Irmão.")
        agora = timezone.make_aware(datetime.combine(hoje, time(20, 0)))
        proxima = agora + timedelta(days=(1 - hoje.weekday()) % 7)
        Evento.objects.create(loja=loja, titulo="Sessão Ordinária", tipo="sessao", inicio=proxima, local="Templo da Loja")
        Evento.objects.create(loja=loja, titulo="Jantar de confraternização das famílias", tipo="social", inicio=agora + timedelta(days=18), local="Salão social")
        Evento.objects.create(loja=loja, titulo="Assembleia geral", tipo="entidade", entidade="Grande Oriente Exemplo", inicio=agora + timedelta(days=33), local="Sede da Potência")

        # Tesouraria
        caixa = ContaBancaria.objects.get(loja=loja, nome="Caixa")
        caixa.saldo_inicial = Decimal("850.00")
        caixa.save()
        banco = ContaBancaria.objects.create(loja=loja, nome="Banco Exemplo — conta corrente", saldo_inicial=Decimal("12400.00"),
                                             banco_codigo="001", banco_nome="Banco Exemplo", agencia="1234", agencia_dv="5",
                                             conta="56789", conta_dv="0", carteira="17", convenio="1234567")
        plano = {p.codigo: p for p in PlanoConta.objects.filter(loja=loja)}
        servicos.gerar_mensalidades(loja, [i for i in irmaos if i.situacao == "ativo"], plano["1.1"], loja.valor_mensalidade,
                                    date(hoje.year, 1, 10), 12)
        for l in Lancamento.objects.filter(loja=loja, vencimento__lt=hoje).select_related("irmao"):
            devedor = l.irmao.nome.startswith(("Otávio", "Marcelo")) and l.vencimento.month >= max(1, hoje.month - 3)
            if not devedor:
                servicos.quitar(l, l.vencimento - timedelta(days=random.randint(0, 5)), l.valor, banco)
        for mes in range(1, hoje.month + 1):
            for codigo, descricao, valor, quem in [("2.2", "Aluguel do Templo", "2200.00", "Imobiliária Exemplo"),
                                                   ("2.3", "Energia elétrica", "310.00", "Companhia de Energia"),
                                                   ("2.1", "Taxa de capitação", "540.00", "Grande Oriente Exemplo")]:
                venc = date(hoje.year, mes, 15)
                l = Lancamento.objects.create(loja=loja, tipo="P", descricao=f"{descricao} {mes:02d}/{hoje.year}", favorecido=quem,
                                              plano=plano[codigo], valor=Decimal(valor), vencimento=venc, competencia=venc.replace(day=1))
                if venc < hoje:
                    servicos.quitar(l, venc, l.valor, banco)
        tronco = Lancamento.objects.create(loja=loja, tipo="R", descricao="Tronco de beneficência", favorecido="Sessão", plano=plano["1.3"],
                                           valor=Decimal("265.00"), vencimento=hoje - timedelta(days=7))
        servicos.quitar(tronco, hoje - timedelta(days=7), tronco.valor, caixa)
        Transferencia.objects.create(loja=loja, origem=banco, destino=caixa, valor=Decimal("300.00"), data=hoje - timedelta(days=10),
                                     descricao="Suprimento de caixa")

        # Hospitalaria
        bens = [Bem.objects.create(loja=loja, codigo=c, descricao=d, categoria=cat) for c, d, cat in [
            ("H-001", "Cadeira de rodas dobrável", "Cadeira de rodas"), ("H-002", "Cadeira de rodas reforçada", "Cadeira de rodas"),
            ("H-003", "Andador articulado", "Andador"), ("H-004", "Par de muletas canadenses", "Muleta"),
            ("H-005", "Cadeira de banho", "Cadeira de banho"), ("H-006", "Cama hospitalar manual", "Cama")]]
        Emprestimo.objects.create(loja=loja, bem=bens[0], irmao=irmaos[8], beneficiario="Sra. Marta Barreto (mãe do Ir∴ Ivo)",
                                  contato="(11) 98888-0000", data_emprestimo=hoje - timedelta(days=50), previsao_devolucao=hoje - timedelta(days=5))
        Emprestimo.objects.create(loja=loja, bem=bens[2], irmao=irmaos[11], beneficiario="Ir∴ Marcelo Tavares Brum",
                                  data_emprestimo=hoje - timedelta(days=12), previsao_devolucao=hoje + timedelta(days=20))
        Emprestimo.objects.create(loja=loja, bem=bens[3], beneficiario="Vizinho da Loja — Sr. Olavo", data_emprestimo=hoje - timedelta(days=90),
                                  previsao_devolucao=hoje - timedelta(days=60), data_devolucao=hoje - timedelta(days=58))

        # Segunda Loja, para mostrar que os dados não se misturam
        outra = Loja.objects.create(nome="A∴R∴L∴S∴ Cavaleiros do Sul", numero="456", potencia="Grande Loja Exemplo", rito="Rito de York",
                                    oriente="Curitiba", uf="PR", cor_primaria="#7A1F2B", cor_destaque="#C9A227", valor_mensalidade=Decimal("150.00"))
        servicos.preparar_loja(outra)
        aprendiz = Grau.objects.get(loja=outra, nome="Mestre")
        for nome in ["Álvaro Pestana Luz", "Bruno Carvalhal Sá", "Cícero Mendonça Rios"]:
            Irmao.objects.create(loja=outra, nome=nome, grau=aprendiz, email=f"{nome.split()[0].lower()}@sul.exemplo.com.br")
        Usuario.objects.create_user("master.sul", "master@sul.exemplo.com.br", SENHA, loja=outra, is_master=True, first_name="Álvaro")

        self.stdout.write(self.style.SUCCESS(
            f"Dados de exemplo criados. Senha dos usuários de exemplo: {SENHA}\n"
            "  Cristiano    → Administrador de todas as Lojas (senha própria, definida à parte)\n"
            "  veneravel    → usuário Master da Loja Fraternidade e Luz\n"
            "  secretario, tesoureiro, chanceler, hospitaleiro → cada um só no seu módulo\n"
            "  irmao        → Irmão comum (só os próprios dados)\n"
            "  master.sul   → Master da segunda Loja (Cavaleiros do Sul)"))
