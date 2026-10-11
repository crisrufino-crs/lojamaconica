"""Testes automáticos. Rode com:  python manage.py test"""
import json
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import boleto, push, servicos
from .crud import RECURSOS
from .models import (Ata, Bem, Boleto, CampoExtra, CertificadoVisita, ContaBancaria, Emprestimo, Evento, Grau,
                     HistoricoGrau, InscricaoPush, Irmao, Lancamento, Licenca, Loja, Notificacao, PlanoConta, Presenca,
                     Sessao, Transferencia, Usuario)
from .relatorios import RELATORIOS

SENHA = "senha-de-teste-123"
D = Decimal


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.hoje = timezone.localdate()
        cls.loja = Loja.objects.create(nome="Loja A", numero="1", cnpj="00.000.000/0001-91", valor_mensalidade=D("100"))
        cls.outra = Loja.objects.create(nome="Loja B", numero="2")
        for l in (cls.loja, cls.outra):
            servicos.preparar_loja(l)
        cls.graus = {g.nome: g for g in Grau.objects.filter(loja=cls.loja)}
        cls.irmaos = [Irmao.objects.create(loja=cls.loja, nome=f"Irmão {n}", email=f"i{n}@exemplo.com.br",
                                           grau=cls.graus["Mestre"], data_nascimento=date(1970, 5, 20))
                      for n in range(4)]
        cls.alheio = Irmao.objects.create(loja=cls.outra, nome="Irmão de outra Loja", email="x@exemplo.com.br")
        novo = lambda login, **k: Usuario.objects.create_user(login, f"{login}@exemplo.com.br", SENHA, **k)  # noqa: E731
        cls.master = novo("master", loja=cls.loja, is_master=True, irmao=cls.irmaos[0])
        cls.secretario = novo("secretario", loja=cls.loja, acesso_secretaria=True)
        cls.tesoureiro = novo("tesoureiro", loja=cls.loja, acesso_tesouraria=True)
        cls.chanceler = novo("chanceler", loja=cls.loja, acesso_chancelaria=True)
        cls.hospitaleiro = novo("hospitaleiro", loja=cls.loja, acesso_hospitalaria=True)
        cls.comum = novo("comum", loja=cls.loja, irmao=cls.irmaos[1])
        cls.master_b = novo("masterb", loja=cls.outra, is_master=True)
        cls.plataforma = Usuario.objects.create_superuser("plataforma", "p@exemplo.com.br", SENHA)
        cls.caixa = ContaBancaria.objects.get(loja=cls.loja)
        cls.banco = ContaBancaria.objects.create(loja=cls.loja, nome="Banco", banco_codigo="001", banco_nome="Banco Exemplo",
                                                 agencia="1234", conta="56789", carteira="17", convenio="1234567")
        cls.plano = {p.codigo: p for p in PlanoConta.objects.filter(loja=cls.loja)}

    def entrar(self, usuario):
        self.client.force_login(usuario)

    def receber(self, irmao=None, valor="100", vencimento=None, **k):
        return Lancamento.objects.create(loja=self.loja, tipo="R", descricao="Mensalidade", irmao=irmao or self.irmaos[1],
                                         plano=self.plano["1.1"], valor=D(valor), vencimento=vencimento or self.hoje, **k)


class SeparacaoEntreLojas(Base):
    """Uma Loja nunca pode ver nem alterar dados de outra."""

    def test_listas_so_mostram_a_propria_loja(self):
        self.entrar(self.master)
        resposta = self.client.get(reverse("irmaos_lista"))
        self.assertContains(resposta, "Irmão 0")
        self.assertNotContains(resposta, "Irmão de outra Loja")

    def test_nao_abre_registro_de_outra_loja(self):
        self.entrar(self.master)
        for nome in ("irmaos_editar", "irmaos_excluir", "irmao_ficha"):
            self.assertEqual(self.client.get(reverse(nome, args=[self.alheio.pk])).status_code, 404, nome)
        self.client.post(reverse("irmaos_excluir", args=[self.alheio.pk]))
        self.assertTrue(Irmao.objects.filter(pk=self.alheio.pk).exists())

    def test_nao_aceita_ligar_registro_a_irmao_de_outra_loja(self):
        self.entrar(self.master)
        resposta = self.client.post(reverse("dependentes_novo"), {"irmao": self.alheio.pk, "nome": "Fulana", "parentesco": "conjuge"})
        self.assertEqual(resposta.status_code, 200)  # voltou para o formulário com erro
        self.assertFalse(self.alheio.dependentes.exists())
        self.assertNotContains(self.client.get(reverse("dependentes_novo")), "Irmão de outra Loja")

    def test_registro_novo_cai_na_loja_de_quem_cadastra(self):
        self.entrar(self.master_b)
        self.client.post(reverse("bens_novo"), {"descricao": "Andador", "estado": "bom"})
        self.assertEqual(Bem.objects.get(descricao="Andador").loja, self.outra)

    def test_master_nao_alcanca_usuario_de_outra_loja(self):
        self.entrar(self.master_b)
        self.assertEqual(self.client.get(reverse("usuarios_editar", args=[self.master.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("usuarios_lista")), "secretario")

    def test_relatorios_e_frequencia_nao_misturam_lojas(self):
        self.entrar(self.master_b)
        resposta = self.client.get(reverse("relatorio", args=["quadro"]))
        self.assertContains(resposta, "Irmão de outra Loja")
        self.assertNotContains(resposta, "Irmão 0")

    def test_boleto_e_arquivo_de_outra_loja(self):
        l = self.receber()
        b = boleto.emitir(l, self.banco)
        self.entrar(self.master_b)
        self.assertEqual(self.client.get(reverse("boleto_ver", args=[b.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("arquivo", args=[f"loja_{self.loja.pk}/atas/x.pdf"])).status_code, 404)

    def test_loja_bloqueada_derruba_o_acesso(self):
        self.entrar(self.master_b)
        Loja.objects.filter(pk=self.outra.pk).update(ativa=False)
        self.assertRedirects(self.client.get(reverse("inicio")), reverse("entrar"), fetch_redirect_response=False)

    def test_mesmo_nome_de_grau_em_lojas_diferentes(self):
        self.entrar(self.master)
        resposta = self.client.post(reverse("graus_novo"), {"nome": "Mestre", "ordem": 3})
        self.assertContains(resposta, "já existe")  # repetido dentro da mesma Loja: recusa com aviso, sem quebrar
        self.assertEqual(Grau.objects.filter(nome="Mestre").count(), 2)


class Acessos(Base):
    def test_cada_cargo_so_entra_no_seu_modulo(self):
        portas = {"secretaria": "irmaos_lista", "tesouraria": "receber_lista", "chancelaria": "sessoes_lista",
                  "hospitalaria": "bens_lista", "admin": "usuarios_lista"}
        casos = {self.secretario: {"secretaria"}, self.tesoureiro: {"tesouraria"}, self.chanceler: {"chancelaria"},
                 self.hospitaleiro: {"hospitalaria"}, self.comum: set(), self.master: set(portas)}
        for usuario, liberados in casos.items():
            self.entrar(usuario)
            for modulo, tela in portas.items():
                esperado = 200 if modulo in liberados else 403
                self.assertEqual(self.client.get(reverse(tela)).status_code, esperado, f"{usuario} em {modulo}")

    def test_sem_login_vai_para_a_entrada(self):
        resposta = self.client.get(reverse("irmaos_lista"))
        self.assertEqual(resposta.status_code, 302)
        self.assertIn(reverse("entrar"), resposta.url)

    def test_irmao_comum_ve_os_proprios_dados_e_so_eles(self):
        meu, de_outro = self.receber(self.irmaos[1]), self.receber(self.irmaos[2], valor="77.77")
        meu_boleto, boleto_de_outro = boleto.emitir(meu, self.banco), boleto.emitir(de_outro, self.banco)
        self.entrar(self.comum)
        pagina = self.client.get(reverse("minha_area"))
        self.assertContains(pagina, "Irmão 1")
        self.assertNotContains(pagina, "77,77")
        self.assertEqual(self.client.get(reverse("boleto_ver", args=[meu_boleto.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("boleto_ver", args=[boleto_de_outro.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("relatorio", args=["inadimplencia"])).status_code, 403)

    def test_master_nao_consegue_virar_administrador_da_plataforma(self):
        self.entrar(self.master)
        self.client.post(reverse("usuarios_editar", args=[self.comum.pk]), {
            "username": "comum", "is_active": "on", "is_superuser": "on", "is_staff": "on", "loja": self.outra.pk})
        self.comum.refresh_from_db()
        self.assertFalse(self.comum.is_superuser)
        self.assertEqual(self.comum.loja, self.loja)
        self.assertEqual(self.client.get(reverse("plataforma_lojas")).status_code, 403)

    def test_master_cria_usuario_com_acesso_de_cargo(self):
        self.entrar(self.master)
        self.client.post(reverse("usuarios_novo"), {"username": "novo.tes", "senha": "uma-senha-boa-99", "is_active": "on",
                                                    "acesso_tesouraria": "on", "irmao": self.irmaos[3].pk})
        novo = Usuario.objects.get(username="novo.tes")
        self.assertEqual((novo.loja, novo.irmao, novo.pode("tesouraria"), novo.pode("secretaria")),
                         (self.loja, self.irmaos[3], True, False))
        self.assertTrue(novo.check_password("uma-senha-boa-99"))

    def test_criar_acesso_para_todos(self):
        self.entrar(self.master)
        self.client.post(reverse("usuarios_criar_todos"))
        self.assertEqual(Usuario.objects.filter(loja=self.loja, irmao__isnull=False).count(), 4)
        self.assertFalse(Usuario.objects.filter(irmao=self.alheio).exists())

    def test_plataforma_cria_loja_e_entra_nela(self):
        self.entrar(self.plataforma)
        self.assertRedirects(self.client.get(reverse("inicio")), reverse("plataforma_lojas"))
        self.client.post(reverse("plataforma_nova_loja"), {"nome": "Loja C", "master_login": "masterc", "master_nome": "João Silva",
                                                           "master_email": "c@exemplo.com.br", "master_senha": "senha-forte-321"})
        nova = Loja.objects.get(nome="Loja C")
        self.assertEqual(Grau.objects.filter(loja=nova).count(), 4)
        self.assertTrue(Usuario.objects.get(username="masterc").is_master)
        self.client.post(reverse("plataforma_lojas"), {"loja": nova.pk, "acao": "entrar"})
        self.assertContains(self.client.get(reverse("inicio")), "Loja C")

    def test_personalizacao_muda_cores_e_campo_extra_aparece_no_cadastro(self):
        self.entrar(self.master)
        dados = {"nome": "Loja A Renomeada", "cor_primaria": "#7A1F2B", "cor_destaque": "#C9A227", "valor_mensalidade": "120",
                 "dia_vencimento": 5, "frequencia_minima": 60}
        self.assertRedirects(self.client.post(reverse("personalizar"), dados), reverse("personalizar"))
        self.assertContains(self.client.get(reverse("inicio")), "--primaria:#7A1F2B")
        self.client.post(reverse("campos-extras_novo"), {"rotulo": "Nome simbólico", "ordem": 1})
        extra = CampoExtra.objects.get(loja=self.loja)
        self.assertContains(self.client.get(reverse("irmaos_novo")), "Nome simbólico")
        self.client.post(reverse("irmaos_novo"), {"nome": "Novo Irmão", "situacao": "ativo", "receber_emails": "on",
                                                  f"extra_{extra.pk}": "Hiram"})
        self.assertEqual(Irmao.objects.get(nome="Novo Irmão").extras, {str(extra.pk): "Hiram"})
        self.entrar(self.master_b)
        self.assertNotContains(self.client.get(reverse("irmaos_novo")), "Nome simbólico")


class TodasAsTelasAbrem(Base):
    def test_cadastros(self):
        self.entrar(self.master)
        for slug in RECURSOS:
            for sufixo in ("_lista", "_novo"):
                self.assertEqual(self.client.get(reverse(slug + sufixo)).status_code, 200, slug + sufixo)

    def test_demais_telas(self):
        sessao = Sessao.objects.create(loja=self.loja, data=self.hoje)
        servicos.sincronizar_presencas(sessao)
        ata = Ata.objects.create(loja=self.loja, data=self.hoje, titulo="Ata 1", texto="Texto")
        l = self.receber()
        self.entrar(self.master)
        telas = ["inicio", "minha_area", "emails", "mensalidades_serie", "boletos", "relatorios", "notificacoes", "personalizar",
                 "usuarios_criar_todos", "senha_trocar", "sw", "manifesto",
                 ("irmao_ficha", self.irmaos[0].pk), ("ata_ver", ata.pk), ("sessao_presenca", sessao.pk),
                 ("lancamento_quitar", l.pk), ("lancamento_cancelar", l.pk), ("boleto_gerar", l.pk)]
        for tela in telas:
            url = reverse(tela) if isinstance(tela, str) else reverse(tela[0], args=[tela[1]])
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.client.logout()
        for tela in ("entrar", "senha_esqueci"):
            self.assertEqual(self.client.get(reverse(tela)).status_code, 200)

    def test_todos_os_relatorios_na_tela_e_em_planilha(self):
        sessao = Sessao.objects.create(loja=self.loja, data=self.hoje)
        servicos.sincronizar_presencas(sessao)
        servicos.quitar(self.receber(), self.hoje, D("100"), self.caixa)
        self.receber(vencimento=self.hoje - timedelta(days=40))
        boleto.emitir(self.receber(), self.banco)
        Emprestimo.objects.create(loja=self.loja, bem=Bem.objects.create(loja=self.loja, descricao="Cadeira"), beneficiario="Fulano")
        self.entrar(self.master)
        self.assertGreaterEqual(len(RELATORIOS), 25)
        for slug in RELATORIOS:
            url = reverse("relatorio", args=[slug])
            self.assertEqual(self.client.get(url).status_code, 200, slug)
            planilha = self.client.get(url, {"formato": "csv"})
            self.assertEqual(planilha.status_code, 200, slug)
            self.assertIn("text/csv", planilha["Content-Type"])


class Tesouraria(Base):
    def test_saldo_quitacao_estorno_e_transferencia(self):
        l = self.receber(valor="150")
        despesa = Lancamento.objects.create(loja=self.loja, tipo="P", descricao="Luz", favorecido="Cia", plano=self.plano["2.3"],
                                            valor=D("40"), vencimento=self.hoje)
        self.assertEqual(servicos.saldo_conta(self.caixa), D("0"))
        servicos.quitar(l, self.hoje, D("150"), self.caixa)
        servicos.quitar(despesa, self.hoje, D("40"), self.caixa)
        Transferencia.objects.create(loja=self.loja, origem=self.caixa, destino=self.banco, valor=D("60"), data=self.hoje)
        self.assertEqual(servicos.saldo_conta(self.caixa), D("50"))
        self.assertEqual(servicos.saldo_conta(self.banco), D("60"))
        servicos.estornar(l)
        self.assertEqual(servicos.saldo_conta(self.caixa), D("-100"))
        l.refresh_from_db()
        self.assertEqual((l.status, l.valor_pago), ("aberto", None))

    def test_telas_de_quitar_estornar_e_cancelar(self):
        l = self.receber(valor="100")
        self.entrar(self.tesoureiro)
        self.client.post(reverse("lancamento_quitar", args=[l.pk]), {"data": self.hoje.isoformat(), "valor": "95.50", "conta": self.caixa.pk})
        l.refresh_from_db()
        self.assertEqual((l.status, l.valor_pago, l.conta), ("pago", D("95.50"), self.caixa))
        self.client.post(reverse("lancamento_estornar", args=[l.pk]))
        self.client.post(reverse("lancamento_cancelar", args=[l.pk]))
        l.refresh_from_db()
        self.assertEqual(l.status, "cancelado")

    def test_lancamento_exige_plano_do_tipo_certo(self):
        self.entrar(self.tesoureiro)
        base = {"descricao": "Teste", "favorecido": "Alguém", "valor": "10", "vencimento": self.hoje.isoformat()}
        self.client.post(reverse("receber_novo"), {**base, "plano": self.plano["2.3"].pk})  # despesa em conta a receber
        self.client.post(reverse("receber_novo"), {**base, "plano": self.plano["1"].pk})  # conta que só agrupa
        self.assertFalse(Lancamento.objects.exists())
        self.client.post(reverse("pagar_novo"), {**base, "plano": self.plano["2.3"].pk})
        self.assertEqual(Lancamento.objects.get().tipo, "P")

    def test_mensalidades_em_serie_nao_duplicam(self):
        self.entrar(self.tesoureiro)
        dados = {"plano": self.plano["1.1"].pk, "descricao": "Mensalidade", "valor": "100", "meses": 12,
                 "primeiro_vencimento": "2026-01-31", "irmaos": [i.pk for i in self.irmaos[:3]]}
        self.client.post(reverse("mensalidades_serie"), dados)
        self.assertEqual(Lancamento.objects.count(), 36)
        vencimentos = sorted(set(Lancamento.objects.values_list("vencimento", flat=True)))
        self.assertEqual(vencimentos[1], date(2026, 2, 28))  # dia 31 vira o último dia de fevereiro
        self.assertEqual(vencimentos[2], date(2026, 3, 31))
        self.client.post(reverse("mensalidades_serie"), {**dados, "irmaos": [i.pk for i in self.irmaos]})
        self.assertEqual(Lancamento.objects.count(), 48)  # só o 4º Irmão ganhou as 12 dele

    def test_balancete_fecha_com_os_saldos(self):
        self.caixa.saldo_inicial = D("1000")
        self.caixa.save()
        mes_passado = self.hoje.replace(day=1) - timedelta(days=1)
        servicos.quitar(self.receber(valor="300"), mes_passado, D("300"), self.caixa)
        servicos.quitar(self.receber(valor="200"), self.hoje, D("200"), self.caixa)
        despesa = Lancamento.objects.create(loja=self.loja, tipo="P", descricao="Luz", favorecido="Cia", plano=self.plano["2.3"],
                                            valor=D("50"), vencimento=self.hoje)
        servicos.quitar(despesa, self.hoje, D("50"), self.caixa)
        Transferencia.objects.create(loja=self.loja, origem=self.caixa, destino=self.banco, valor=D("100"), data=self.hoje)
        b = servicos.balancete(self.loja, self.hoje.year, self.hoje.month)
        self.assertEqual((b["total_receitas"], b["total_despesas"], b["resultado"]), (D("200"), D("50"), D("150")))
        self.assertEqual((b["saldo_anterior"], b["saldo_final"]), (D("1300"), D("1450")))
        self.assertEqual(b["saldo_final"], b["saldo_anterior"] + b["resultado"])  # transferência não muda o total
        self.assertEqual(b["saldo_final"], servicos.saldo_conta(self.caixa) + servicos.saldo_conta(self.banco))


class Boletos(Base):
    def test_digitos_verificadores_com_exemplo_publicado(self):
        # Exemplo clássico da documentação de cobrança do Banco do Brasil.
        barras = "00193373700000001000500940144816060680935031"
        self.assertEqual(boleto.modulo11(barras[:4] + barras[5:]), barras[4])
        self.assertEqual(boleto.linha_digitavel(barras), "00190.50095 40144.816069 06809.350314 3 37370000000100")

    def test_fator_de_vencimento_inclusive_a_virada_de_2025(self):
        self.assertEqual(boleto.fator_vencimento(date(2000, 7, 3)), "1000")
        self.assertEqual(boleto.fator_vencimento(date(2025, 2, 21)), "9999")
        self.assertEqual(boleto.fator_vencimento(date(2025, 2, 22)), "1000")
        self.assertEqual(boleto.fator_vencimento(date(2026, 10, 11)), "1596")

    def test_boleto_tem_44_digitos_coerentes_e_nosso_numero_sequencial(self):
        b1, b2 = boleto.emitir(self.receber(valor="180.50"), self.banco), boleto.emitir(self.receber(), self.banco)
        self.assertEqual(len(b1.codigo_barras), 44)
        self.assertEqual(b1.codigo_barras[:4], "0019")
        self.assertEqual(b1.codigo_barras[9:19], "0000018050")
        self.assertEqual(boleto.modulo11(b1.codigo_barras[:4] + b1.codigo_barras[5:]), b1.codigo_barras[4])
        self.assertEqual(int(b2.nosso_numero), int(b1.nosso_numero) + 1)
        self.assertEqual(boleto.emitir(b1.lancamento, self.banco), b1)  # não gera dois boletos para a mesma conta
        self.assertEqual(sum(w for _, w in boleto.barras_para_desenho(b1.codigo_barras)), 4 + 22 * 18 + 5)

    def _retorno_do_banco(self, remessa, movimento="06", pago=None):
        """Simula o arquivo que o banco devolveria para os boletos de uma remessa."""
        linhas = []
        for linha in remessa.conteudo.splitlines():
            if linha[7] == "3" and linha[13] == "P":
                t = list(" " * 240)
                t[0:8], t[13], t[15:17] = linha[0:7] + "3", "T", movimento
                t[37:57], t[81:96], t[105:130] = linha[37:57], linha[85:100], linha[195:220]
                u = list(" " * 240)
                u[0:8], u[13], u[15:17] = linha[0:7] + "3", "U", movimento
                u[77:92] = pago or linha[85:100]
                u[137:145] = self.hoje.strftime("%d%m%Y")
                linhas += ["".join(t), "".join(u)]
        return "\r\n".join(linhas)

    def test_remessa_cnab_e_baixa_automatica_pelo_retorno(self):
        lancamentos = [self.receber(self.irmaos[n], valor="180") for n in range(3)]
        self.entrar(self.tesoureiro)
        self.client.post(reverse("boletos"), {"acao": "lote", "conta": self.banco.pk})
        self.assertEqual(Boleto.objects.count(), 3)
        self.client.post(reverse("boletos"), {"acao": "remessa", "conta": self.banco.pk})
        remessa = self.loja and Boleto.objects.first().remessa
        linhas = remessa.conteudo.splitlines()
        self.assertEqual(len(linhas), 2 + 3 * 2 + 2)
        self.assertTrue(all(len(l) == 240 for l in linhas))
        self.assertEqual([l[7] for l in linhas], ["0", "1", "3", "3", "3", "3", "3", "3", "5", "9"])
        self.assertEqual(linhas[2][85:100], "000000000018000")
        self.assertEqual(linhas[-1][23:29], "000010")  # total de registros do arquivo
        self.assertEqual(set(Boleto.objects.values_list("status", flat=True)), {"remetido"})
        baixado = self.client.get(reverse("remessa_baixar", args=[remessa.pk]))
        self.assertIn("attachment", baixado["Content-Disposition"])
        # segunda remessa não reenvia os mesmos boletos
        self.assertIsNone(boleto.gerar_remessa(self.loja, self.banco))

        arquivo = SimpleUploadedFile("retorno.ret", self._retorno_do_banco(remessa).encode("latin-1"))
        self.client.post(reverse("boletos"), {"acao": "retorno", "arquivo": arquivo})
        for l in lancamentos:
            l.refresh_from_db()
            self.assertEqual((l.status, l.valor_pago, l.data_pagamento, l.conta), ("pago", D("180"), self.hoje, self.banco))
        self.assertEqual(set(Boleto.objects.values_list("status", flat=True)), {"pago"})
        self.assertEqual(servicos.saldo_conta(self.banco), D("540"))
        # o mesmo retorno enviado duas vezes não quita em dobro
        boleto.processar_retorno(self.loja, "de novo.ret", self._retorno_do_banco(remessa))
        self.assertEqual(servicos.saldo_conta(self.banco), D("540"))

    def test_retorno_de_registro_e_de_outra_loja(self):
        b = boleto.emitir(self.receber(), self.banco)
        remessa = boleto.gerar_remessa(self.loja, self.banco)
        boleto.processar_retorno(self.loja, "r.ret", self._retorno_do_banco(remessa, movimento="02"))
        b.refresh_from_db()
        self.assertEqual((b.status, b.lancamento.status), ("registrado", "aberto"))
        r = boleto.processar_retorno(self.outra, "r.ret", self._retorno_do_banco(remessa))  # outra Loja sobe o arquivo
        b.lancamento.refresh_from_db()
        self.assertEqual((r.quitados, r.nao_encontrados, b.lancamento.status), (0, 1, "aberto"))


class Chancelaria(Base):
    def _sessoes(self, n, **k):
        sessoes = [Sessao.objects.create(loja=self.loja, data=self.hoje - timedelta(days=7 * i), **k) for i in range(n)]
        for s in sessoes:
            servicos.sincronizar_presencas(s)
        return sessoes

    def _marcar(self, irmao, situacoes):
        for p, status in zip(Presenca.objects.filter(irmao=irmao).order_by("sessao__data"), situacoes):
            p.status = status
            p.save()

    def test_presenca_com_um_clique(self):
        sessao = self._sessoes(1)[0]
        p = sessao.presencas.get(irmao=self.irmaos[0])
        self.entrar(self.chanceler)
        r = self.client.post(reverse("presenca_marcar", args=[p.pk]), {"status": "P"})
        self.assertEqual(r.json()["resumo"], {"P": 1, "F": 3, "A": 0, "L": 0})
        r = self.client.post(reverse("presenca_marcar", args=[p.pk]), {"status": "A", "justificativa": "Viagem"})
        p.refresh_from_db()
        self.assertEqual((p.status, p.justificativa), ("A", "Viagem"))
        self.assertEqual(self.client.post(reverse("presenca_marcar", args=[p.pk]), {"status": "X"}).status_code, 400)
        self.client.post(reverse("sessao_presenca", args=[sessao.pk]), {"acao": "todos"})
        self.assertEqual(sessao.presencas.filter(status="P").count(), 3)  # o abonado continua abonado
        self.entrar(self.secretario)
        self.assertEqual(self.client.post(reverse("presenca_marcar", args=[p.pk]), {"status": "P"}).status_code, 403)

    def test_criar_sessao_pela_tela_monta_a_chamada(self):
        Licenca.objects.create(loja=self.loja, irmao=self.irmaos[3], inicio=self.hoje - timedelta(days=1), fim=self.hoje + timedelta(days=1))
        self.irmaos[2].situacao = "desligado"
        self.irmaos[2].save()
        self.entrar(self.chanceler)
        self.client.post(reverse("sessoes_novo"), {"data": self.hoje.isoformat(), "tipo": "ordinaria"})
        situacoes = dict(Presenca.objects.values_list("irmao__nome", "status"))
        self.assertEqual(situacoes, {"Irmão 0": "F", "Irmão 1": "F", "Irmão 3": "L"})

    def test_sessao_de_grau_so_chama_quem_tem_o_grau(self):
        self.irmaos[0].grau = self.graus["Aprendiz"]
        self.irmaos[0].save()
        sessao = self._sessoes(1, grau=self.graus["Mestre"])[0]
        self.assertNotIn(self.irmaos[0].pk, sessao.presencas.values_list("irmao_id", flat=True))
        self.assertEqual(sessao.presencas.count(), 3)

    def test_calculo_de_frequencia(self):
        self._sessoes(10)
        a, b, c, d = self.irmaos
        self._marcar(a, "PPPPPPFFFF")  # 60%
        self._marcar(b, "PPPPAAFFFF")  # 4 presenças + 2 abonos = 60%
        self._marcar(c, "PPPPLLLLLF")  # 5 sessões fora por licença: 4 de 5 = 80%
        self._marcar(d, "PPPPFFFFFF")  # 40% + 2 visitas = 60%
        for n in (5, 20):
            CertificadoVisita.objects.create(loja=self.loja, irmao=d, data=self.hoje - timedelta(days=n), loja_visitada="Loja X")
        inicio = self.hoje - timedelta(days=100)

        def pct():
            return {f["irmao"].nome: (f["percentual"], f["apto"]) for f in servicos.frequencia(self.loja, inicio, self.hoje)}
        self.assertEqual(pct(), {"Irmão 0": (60.0, True), "Irmão 1": (60.0, True), "Irmão 2": (80.0, True), "Irmão 3": (60.0, True)})
        Loja.objects.filter(pk=self.loja.pk).update(abono_conta_presenca=False, visita_conta_presenca=False, frequencia_minima=60)
        self.loja.refresh_from_db()
        self.assertEqual(pct(), {"Irmão 0": (60.0, True), "Irmão 1": (40.0, False), "Irmão 2": (80.0, True), "Irmão 3": (40.0, False)})
        # fora do período não conta
        ultima_semana = servicos.frequencia(self.loja, self.hoje - timedelta(days=6), self.hoje, a)[0]
        self.assertEqual(ultima_semana["sessoes"], 1)

    def test_visitas_nao_passam_de_cem_por_cento(self):
        self._sessoes(2)
        self._marcar(self.irmaos[0], "PF")
        for n in range(5):
            CertificadoVisita.objects.create(loja=self.loja, irmao=self.irmaos[0], data=self.hoje - timedelta(days=n), loja_visitada="X")
        self.assertEqual(servicos.frequencia(self.loja, self.hoje - timedelta(days=30), self.hoje, self.irmaos[0])[0]["percentual"], 100.0)

    def test_relatorio_de_frequencia_mostra_aptos(self):
        self._sessoes(4)
        self._marcar(self.irmaos[0], "PPPP")
        self.entrar(self.chanceler)
        pagina = self.client.get(reverse("relatorio", args=["frequencia"])).content.decode()
        self.assertIn("1 apto(s) de 4", pagina)
        self.assertIn("100,0%", pagina)


class SecretariaEHospitalaria(Base):
    def test_controle_de_graus_atualiza_o_grau_atual(self):
        irmao = Irmao.objects.create(loja=self.loja, nome="Recém iniciado")
        self.entrar(self.secretario)
        for grau, quando in (("Aprendiz", "2024-03-01"), ("Companheiro", "2025-03-01")):
            self.client.post(reverse("graus-historico_novo"), {"irmao": irmao.pk, "grau": self.graus[grau].pk, "data": quando})
        irmao.refresh_from_db()
        self.assertEqual(irmao.grau, self.graus["Companheiro"])
        self.assertEqual(HistoricoGrau.objects.filter(irmao=irmao).count(), 2)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_disparo_de_email_individual_e_respeita_quem_nao_quer_receber(self):
        Irmao.objects.filter(pk=self.irmaos[3].pk).update(receber_emails=False)
        self.entrar(self.secretario)
        self.client.post(reverse("emails"), {"publico": "ativos", "assunto": "Convocação", "corpo": "Prezado Ir∴ {nome}, compareça."})
        self.assertEqual(len(mail.outbox), 3)
        self.assertTrue(all(len(m.to) == 1 for m in mail.outbox))
        self.assertEqual({m.to[0] for m in mail.outbox}, {"i0@exemplo.com.br", "i1@exemplo.com.br", "i2@exemplo.com.br"})
        self.assertIn("Prezado Ir∴ Irmão 0", next(m.body for m in mail.outbox if m.to == ["i0@exemplo.com.br"]))
        self.assertContains(self.client.get(reverse("emails")), "Convocação")

    def test_ata_com_arquivo_fica_protegida(self):
        with tempfile.TemporaryDirectory() as pasta, self.settings(MEDIA_ROOT=__import__("pathlib").Path(pasta)):
            self.entrar(self.secretario)
            pdf = SimpleUploadedFile("ata.pdf", b"%PDF-1.4 teste", content_type="application/pdf")
            self.client.post(reverse("atas_novo"), {"data": self.hoje.isoformat(), "titulo": "Ata 10", "arquivo": pdf})
            ata = Ata.objects.get()
            self.assertTrue(ata.arquivo.name.startswith(f"loja_{self.loja.pk}/atas/"))
            url = reverse("arquivo", args=[ata.arquivo.name])
            self.assertEqual(b"".join(self.client.get(url).streaming_content), b"%PDF-1.4 teste")
            self.entrar(self.comum)  # Irmão sem cargo na Secretaria não baixa ata
            self.assertEqual(self.client.get(url).status_code, 403)
            self.entrar(self.master_b)  # outra Loja nem fica sabendo que existe
            self.assertEqual(self.client.get(url).status_code, 404)
            self.entrar(self.secretario)  # arquivo perigoso é recusado
            self.client.post(reverse("atas_novo"), {"data": self.hoje.isoformat(), "titulo": "Ata ruim",
                                                    "arquivo": SimpleUploadedFile("x.html", b"<script>")})
            self.assertFalse(Ata.objects.filter(titulo="Ata ruim").exists())

    def test_emprestimo_e_devolucao(self):
        self.entrar(self.hospitaleiro)
        self.client.post(reverse("bens_novo"), {"descricao": "Cadeira de rodas", "estado": "bom", "codigo": "H-1"})
        bem = Bem.objects.get()
        dados = {"bem": bem.pk, "beneficiario": "Dona Maria", "data_emprestimo": self.hoje.isoformat()}
        self.client.post(reverse("emprestimos_novo"), dados)
        self.assertEqual(Emprestimo.objects.count(), 1)
        self.client.post(reverse("emprestimos_novo"), {**dados, "beneficiario": "Seu José"})  # já está emprestada
        self.assertEqual(Emprestimo.objects.count(), 1)
        self.assertContains(self.client.get(reverse("bens_lista")), "Emprestado a Dona Maria")
        self.client.post(reverse("emprestimo_devolver", args=[Emprestimo.objects.get().pk]))
        self.assertEqual(Emprestimo.objects.get().data_devolucao, self.hoje)
        self.client.post(reverse("emprestimos_novo"), {**dados, "beneficiario": "Seu José"})
        self.assertEqual(Emprestimo.objects.count(), 2)
        self.client.post(reverse("bens_excluir", args=[bem.pk]))  # bem com histórico não pode sumir
        self.assertTrue(Bem.objects.filter(pk=bem.pk).exists())


CHAVES = push.gerar_chaves()


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", PUSH_CHAVE_PUBLICA=CHAVES[0],
                   PUSH_CHAVE_PRIVADA=CHAVES[1])
class AvisosAutomaticos(Base):
    def test_rotina_diaria_aniversario_e_lembrete_sem_repetir(self):
        Irmao.objects.filter(pk=self.irmaos[1].pk).update(data_nascimento=date(1980, self.hoje.month, self.hoje.day))
        Sessao.objects.create(loja=self.loja, data=self.hoje, tipo="magna")
        agora = timezone.now()
        Evento.objects.create(loja=self.loja, titulo="Jantar", inicio=agora.replace(hour=23, minute=0), local="Salão")
        Evento.objects.create(loja=self.loja, titulo="Semana que vem", inicio=agora + timedelta(days=7))
        resumo = servicos.rotina_diaria(self.loja)
        self.assertEqual(resumo, "1 aniversariante(s), 1 e-mail(s), 2 lembrete(s)")
        self.assertEqual(mail.outbox[0].to, ["i1@exemplo.com.br"])
        self.assertIn("Feliz aniversário, Ir∴ Irmão 1", mail.outbox[0].subject)
        do_master = list(self.master.notificacoes.values_list("texto", flat=True))
        self.assertEqual(len(do_master), 3)
        self.assertTrue(any("aniversário do Ir∴ Irmão 1" in t for t in do_master))
        self.assertEqual(self.comum.notificacoes.count(), 2)  # o aniversariante não é avisado do próprio aniversário
        self.assertFalse(Notificacao.objects.filter(loja=self.outra).exists())
        self.assertIsNone(servicos.rotina_diaria(self.loja))  # rodar de novo no mesmo dia não repete nada
        self.assertEqual(len(mail.outbox), 1)

    def test_aniversario_de_29_de_fevereiro(self):
        self.assertTrue(servicos.faz_aniversario(date(1980, 2, 29), date(2027, 2, 28)))
        self.assertFalse(servicos.faz_aniversario(date(1980, 2, 29), date(2028, 2, 28)))
        self.assertTrue(servicos.faz_aniversario(date(1980, 2, 29), date(2028, 2, 29)))

    def test_assinatura_do_toque_e_valida(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
        cabecalho = push.cabecalho_vapid("https://fcm.googleapis.com/fcm/send/abc")
        token = cabecalho.split("t=")[1].split(",")[0]
        partes = token.split(".")
        corpo = json.loads(push._b64_ler(partes[1]))
        self.assertEqual(corpo["aud"], "https://fcm.googleapis.com")
        assinatura = push._b64_ler(partes[2])
        publica = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push._b64_ler(CHAVES[0]))
        publica.verify(encode_dss_signature(int.from_bytes(assinatura[:32], "big"), int.from_bytes(assinatura[32:], "big")),
                       f"{partes[0]}.{partes[1]}".encode(), ec.ECDSA(hashes.SHA256()))  # levanta erro se inválida

    def test_inscricao_toque_e_busca_dos_avisos(self):
        self.entrar(self.comum)
        enviar = lambda endpoint: self.client.post(reverse("push_inscrever"), json.dumps({"endpoint": endpoint}),  # noqa: E731
                                                   content_type="application/json").status_code
        self.assertEqual(enviar("http://169.254.169.254/latest/meta-data"), 400)
        self.assertEqual(enviar("https://servidor-interno.local/x"), 400)
        self.assertEqual(enviar("https://fcm.googleapis.com.atacante.com/x"), 400)
        self.assertEqual(enviar("https://fcm.googleapis.com/fcm/send/abc"), 200)
        self.assertEqual(enviar("https://updates.push.services.mozilla.com/wpush/v2/xyz"), 200)
        with mock.patch("gestao.push._enviar", side_effect=[201, 410]) as toque:
            aceitos = servicos.notificar(self.loja, [self.comum, self.master], "Lembrete: é hoje!", "Sessão Ordinária")
        self.assertEqual((aceitos, toque.call_count), (1, 2))
        self.assertEqual(InscricaoPush.objects.count(), 1)  # a inscrição cancelada (410) foi removida
        avisos = self.client.get(reverse("push_pendentes")).json()["avisos"]
        self.assertEqual(avisos, [{"titulo": "Lembrete: é hoje!", "texto": "Sessão Ordinária"}])
        self.assertEqual(self.client.get(reverse("push_pendentes")).json()["avisos"], [])  # não mostra duas vezes
        self.assertContains(self.client.get(reverse("notificacoes")), "Sessão Ordinária")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class TabelaDeAcessos(TestCase):
    def _rodar(self, linhas, *extras):
        from io import StringIO
        from django.core.management import call_command
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("perfil;login;nome;email;loja\n" + "\n".join(linhas))
        saida = StringIO()
        call_command("criar_acessos", f.name, *extras, stdout=saida)
        return saida.getvalue()

    def test_cria_administrador_com_senha_e_os_demais_pelo_email(self):
        from django.core.management.base import CommandError
        tabela = ["administrador;administrador;Administrador;;", "master;veneravel;João Silva;v@exemplo.com.br;Loja Nova",
                  "tesouraria+secretaria;tes;Pedro Lima;t@exemplo.com.br;Loja Nova", "irmao;ir;Zé;z@exemplo.com.br;Loja Nova"]
        saida = self._rodar(tabela, "--senha-admin", "Senha.Forte.9431", "--site", "sistema.exemplo.com.br", "--enviar-emails")
        self.assertNotIn("Senha.Forte.9431", saida)  # a senha nunca aparece na tela
        admin = Usuario.objects.get(username="administrador")
        self.assertTrue(admin.is_superuser and admin.check_password("Senha.Forte.9431") and admin.loja is None)
        loja = Loja.objects.get(nome="Loja Nova")
        self.assertEqual(Grau.objects.filter(loja=loja).count(), 4)
        v, t, i = (Usuario.objects.get(username=u) for u in ("veneravel", "tes", "ir"))
        self.assertEqual((v.loja, v.is_master, v.is_superuser), (loja, True, False))
        self.assertEqual((t.pode("tesouraria"), t.pode("secretaria"), t.pode("chancelaria")), (True, True, False))
        self.assertEqual(i.modulos, [])
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["t@exemplo.com.br", "v@exemplo.com.br", "z@exemplo.com.br"])
        link = next(p for p in mail.outbox[0].body.split() if p.startswith("https://sistema.exemplo.com.br/senha/nova/"))
        caminho = link.replace("https://sistema.exemplo.com.br", "")
        pagina = self.client.get(caminho, follow=True)
        self.client.post(pagina.redirect_chain[-1][0], {"new_password1": "minha-senha-nova-77", "new_password2": "minha-senha-nova-77"})
        self.assertTrue(self.client.login(username=mail.outbox[0].to[0][0] == "v" and "veneravel" or
                                          Usuario.objects.get(email=mail.outbox[0].to[0]).username, password="minha-senha-nova-77"))
        # rodar de novo não troca a senha de quem já criou a sua nem duplica ninguém
        self._rodar(tabela, "--senha-admin", "Senha.Forte.9431")
        self.assertEqual(Usuario.objects.count(), 4)
        self.assertTrue(Usuario.objects.get(email=mail.outbox[0].to[0]).check_password("minha-senha-nova-77"))
        with self.assertRaises(CommandError):
            self._rodar(["secretaria;sec;Sem Email;;Loja Nova"])
        with self.assertRaises(CommandError):
            self._rodar(["administrador;adm2;Outro;;"], "--senha-admin", "123")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SenhaPorEmail(Base):
    DADOS = {"username": "novo.irmao", "first_name": "Carlos", "email": "carlos@exemplo.com.br", "is_active": "on",
             "senha": "Inicial-2026-xy", "enviar_senha": "on"}

    def test_senha_cadastrada_vai_para_o_email_e_precisa_ser_trocada(self):
        self.entrar(self.master)
        self.client.post(reverse("usuarios_novo"), self.DADOS)
        novo = Usuario.objects.get(username="novo.irmao")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["carlos@exemplo.com.br"])
        self.assertIn("Nome de acesso: novo.irmao", mail.outbox[0].body)
        self.assertIn("Senha inicial: Inicial-2026-xy", mail.outbox[0].body)
        self.assertNotIn("Inicial-2026-xy", novo.password)  # no banco só fica o embaralhado, nunca a senha
        self.client.logout()
        self.assertTrue(self.client.login(username="novo.irmao", password="Inicial-2026-xy"))
        self.assertRedirects(self.client.get(reverse("minha_area")), reverse("senha_trocar"))
        self.client.post(reverse("senha_trocar"), {"old_password": "Inicial-2026-xy", "new_password1": "so-minha-senha-88",
                                                   "new_password2": "so-minha-senha-88"})
        self.assertEqual(self.client.get(reverse("minha_area")).status_code, 200)
        self.assertFalse(Usuario.objects.get(username="novo.irmao").deve_trocar_senha)

    def test_sem_marcar_o_envio_ou_sem_email(self):
        self.entrar(self.master)
        self.client.post(reverse("usuarios_novo"), {**self.DADOS, "enviar_senha": ""})
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(Usuario.objects.get(username="novo.irmao").deve_trocar_senha)
        resposta = self.client.post(reverse("usuarios_novo"), {**self.DADOS, "username": "outro", "email": ""})
        self.assertContains(resposta, "Preencha o e-mail para enviar a senha")
        self.assertFalse(Usuario.objects.filter(username="outro").exists())
        # editar sem digitar senha não envia nada nem mexe na senha
        u = Usuario.objects.get(username="novo.irmao")
        self.client.post(reverse("usuarios_editar", args=[u.pk]), {**self.DADOS, "senha": "", "first_name": "Carlos Alberto"})
        u.refresh_from_db()
        self.assertEqual((u.first_name, u.check_password("Inicial-2026-xy"), len(mail.outbox)), ("Carlos Alberto", True, 0))


class AcessoDoAdministrador(TestCase):
    def test_demo_cria_o_administrador_com_a_senha_propria(self):
        from io import StringIO
        from django.core.management import call_command
        from .acesso_admin import ADMIN_LOGIN, ADMIN_SENHA_HASH
        call_command("demo", stdout=StringIO())
        admin = Usuario.objects.get(is_superuser=True)
        self.assertEqual((admin.username, admin.password), (ADMIN_LOGIN, ADMIN_SENHA_HASH))
        self.assertFalse(admin.check_password("loja-demo-2026"))  # não usa a senha de exemplo
        self.assertFalse(Usuario.objects.filter(username__in=["plataforma", "cristi286913ano"]).exists())
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("plataforma_lojas")).status_code, 200)


class GarantirAdministrador(TestCase):
    def test_cria_quando_falta_e_nao_mexe_na_senha_de_quem_ja_trocou(self):
        from io import StringIO
        from django.core.management import call_command
        from .acesso_admin import ADMIN_LOGIN, ADMIN_SENHA_HASH
        call_command("garantir_admin", stdout=StringIO())
        admin = Usuario.objects.get()
        self.assertEqual((admin.username, admin.password, admin.is_superuser), (ADMIN_LOGIN, ADMIN_SENHA_HASH, True))
        admin.set_password("outra-senha-minha-55")
        admin.is_superuser = False
        admin.save()
        call_command("garantir_admin", stdout=StringIO())
        admin.refresh_from_db()
        self.assertTrue(admin.is_superuser and admin.check_password("outra-senha-minha-55"))
        self.assertEqual(Usuario.objects.count(), 1)
        call_command("garantir_admin", "--redefinir", stdout=StringIO())
        self.assertEqual(Usuario.objects.get().password, ADMIN_SENHA_HASH)
