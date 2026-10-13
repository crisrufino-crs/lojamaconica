"""Relatórios. Cada função devolve colunas + linhas; a mesma tela mostra, imprime e exporta para planilha (CSV)."""
from datetime import date, timedelta
from decimal import Decimal

from django import forms
from django.db.models import Count, Q, Sum
from django.utils import timezone

from . import servicos
from .models import (Ata, Bem, Boleto, CertificadoVisita, ContaBancaria, Dependente, EmailEnviado, Emprestimo, Evento,
                     Grau, HistoricoGrau, Irmao, Lancamento, Licenca, LojaCoirma, PlanoConta, Presenca, Sessao,
                     Transferencia)

ZERO = Decimal("0")
MESES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro",
         "Novembro", "Dezembro"]
RELATORIOS = {}


class Linha(list):
    """Linha do relatório. ``classe`` pode ser 'grupo', 'total' ou ''."""
    def __init__(self, celulas, classe=""):
        super().__init__(celulas)
        self.classe = classe


def relatorio(slug, titulo, modulo, filtros=(), descricao=""):
    def registrar(funcao):
        RELATORIOS[slug] = {"slug": slug, "titulo": titulo, "modulo": modulo, "filtros": filtros, "funcao": funcao,
                            "descricao": descricao}
        return funcao
    return registrar


class FiltroForm(forms.Form):
    """Monta só os filtros que o relatório pediu."""

    def __init__(self, dados, loja, filtros):
        hoje = timezone.localdate()
        super().__init__(dados or None)
        data = lambda rotulo, inicial: forms.DateField(  # noqa: E731
            label=rotulo, initial=inicial, required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
        if "periodo" in filtros:
            self.fields["inicio"] = data("De", hoje.replace(month=1, day=1))
            self.fields["fim"] = data("Até", hoje)
        if "mes" in filtros:
            self.fields["mes"] = forms.TypedChoiceField(label="Mês", coerce=int, initial=hoje.month, required=False,
                                                        choices=[(i + 1, m) for i, m in enumerate(MESES)])
        if "ano" in filtros:
            self.fields["ano"] = forms.IntegerField(label="Ano", initial=hoje.year, required=False, min_value=1900, max_value=2200)
        if "situacao" in filtros:
            self.fields["situacao"] = forms.ChoiceField(
                label="Situação", required=False, initial="quadro",
                choices=[("quadro", "Todo o quadro"), ("", "Todos (inclusive desligados)")] + Irmao.SITUACOES)
        if "grau" in filtros:
            self.fields["grau"] = forms.ModelChoiceField(label="Grau", required=False, queryset=Grau.objects.filter(loja=loja))
        if "conta" in filtros:
            self.fields["conta"] = forms.ModelChoiceField(label="Conta", required=False,
                                                          queryset=ContaBancaria.objects.filter(loja=loja))
        if "irmao" in filtros:
            self.fields["irmao"] = forms.ModelChoiceField(label="Irmão", required=False,
                                                          queryset=Irmao.objects.filter(loja=loja))

    def valores(self):
        """Valores escolhidos; o que ficou em branco usa o valor inicial."""
        validos = self.cleaned_data if self.is_bound and self.is_valid() else {}
        saida = {}
        for nome, campo in self.fields.items():
            valor = validos.get(nome) if self.is_bound else None
            if valor in (None, "") and not (self.is_bound and nome in ("situacao", "grau", "conta", "irmao")):
                valor = campo.initial() if callable(campo.initial) else campo.initial
            saida[nome] = valor
        return saida


def _irmaos(loja, f):
    qs = Irmao.objects.filter(loja=loja).select_related("grau")
    situacao = f.get("situacao")
    if situacao == "quadro":
        qs = qs.filter(situacao__in=Irmao.NO_QUADRO)
    elif situacao:
        qs = qs.filter(situacao=situacao)
    if f.get("grau"):
        qs = qs.filter(grau=f["grau"])
    return qs


def _idade(nascimento, em):
    return em.year - nascimento.year - ((em.month, em.day) < (nascimento.month, nascimento.day))


# ================================================================ Secretaria
@relatorio("quadro", "Quadro de Irmãos", "secretaria", ("situacao", "grau"), "Lista completa com grau, cargo e datas.")
def quadro(loja, f):
    linhas = [Linha([i.nome, i.cim, i.grau, i.cargo, i.get_situacao_display(), i.data_nascimento, i.data_iniciacao])
              for i in _irmaos(loja, f)]
    return {"colunas": ["Nome", "CIM", "Grau", "Cargo", "Situação", "Nascimento", "Iniciação"], "linhas": linhas,
            "resumo": f"{len(linhas)} Irmão(s)"}


@relatorio("contatos", "Lista de contatos", "secretaria", ("situacao", "grau"), "Telefones, e-mails e endereços.")
def contatos(loja, f):
    linhas = [Linha([i.nome, i.celular, i.telefone, i.email, i.endereco, i.cidade, i.uf]) for i in _irmaos(loja, f)]
    return {"colunas": ["Nome", "Celular", "Telefone", "E-mail", "Endereço", "Cidade", "UF"], "linhas": linhas}


@relatorio("aniversariantes", "Aniversariantes do mês", "secretaria", ("mes",), "Irmãos e dependentes que fazem aniversário no mês.")
def aniversariantes(loja, f):
    mes, ano = f["mes"], timezone.localdate().year
    pessoas = [(i.data_nascimento, i.nome, "Irmão", "", i.celular) for i in
               Irmao.objects.filter(loja=loja, situacao__in=Irmao.NO_QUADRO, data_nascimento__month=mes)]
    pessoas += [(d.data_nascimento, d.nome, d.get_parentesco_display(), d.irmao.nome, d.celular) for d in
                Dependente.objects.filter(loja=loja, irmao__situacao__in=Irmao.NO_QUADRO, data_nascimento__month=mes)
                .select_related("irmao")]
    pessoas.sort(key=lambda p: (p[0].day, p[1]))
    linhas = [Linha([f"{n.day:02d}/{n.month:02d}", nome, tipo, de, ano - n.year, cel]) for n, nome, tipo, de, cel in pessoas]
    return {"colunas": ["Dia", "Nome", "Quem é", "Dependente de", f"Idade em {ano}", "Celular"], "linhas": linhas,
            "resumo": f"{MESES[mes - 1]} — {len(linhas)} aniversariante(s)"}


@relatorio("dependentes", "Dependentes por Irmão", "secretaria", ("situacao",), "Cônjuges, filhos e demais dependentes.")
def dependentes(loja, f):
    hoje, linhas = timezone.localdate(), []
    for i in _irmaos(loja, f).prefetch_related("dependentes"):
        deps = list(i.dependentes.all())
        if not deps:
            continue
        linhas.append(Linha([i.nome, "", "", "", ""], "grupo"))
        linhas += [Linha(["", d.nome, d.get_parentesco_display(), d.data_nascimento,
                          _idade(d.data_nascimento, hoje) if d.data_nascimento else None]) for d in deps]
    return {"colunas": ["Irmão", "Dependente", "Parentesco", "Nascimento", "Idade"], "linhas": linhas}


@relatorio("por-grau", "Irmãos por grau", "secretaria", ("situacao",), "Quantos Irmãos há em cada grau.")
def por_grau(loja, f):
    contagem = dict(_irmaos(loja, f).values_list("grau__nome").annotate(n=Count("id")).order_by("grau__ordem"))
    linhas = [Linha([nome or "Sem grau registrado", n]) for nome, n in contagem.items()]
    linhas.append(Linha(["Total", sum(contagem.values())], "total"))
    return {"colunas": ["Grau", "Irmãos"], "linhas": linhas}


@relatorio("graus-periodo", "Iniciações, elevações e exaltações", "secretaria", ("periodo", "grau"),
           "Registros de grau lançados no período.")
def graus_periodo(loja, f):
    qs = HistoricoGrau.objects.filter(loja=loja, data__range=(f["inicio"], f["fim"])).select_related("irmao", "grau")
    if f.get("grau"):
        qs = qs.filter(grau=f["grau"])
    return {"colunas": ["Data", "Irmão", "Grau", "Observação"],
            "linhas": [Linha([h.data, h.irmao.nome, h.grau, h.observacao]) for h in qs.order_by("data")]}


@relatorio("tempo-de-ordem", "Tempo de Ordem", "secretaria", ("situacao",), "Anos de Maçonaria de cada Irmão (útil para homenagens).")
def tempo_de_ordem(loja, f):
    hoje = timezone.localdate()
    irmaos = _irmaos(loja, f).filter(data_iniciacao__isnull=False).order_by("data_iniciacao")
    return {"colunas": ["Nome", "Grau", "Iniciação", "Anos de Ordem"],
            "linhas": [Linha([i.nome, i.grau, i.data_iniciacao, _idade(i.data_iniciacao, hoje)]) for i in irmaos]}


@relatorio("cargos", "Administração da Loja (cargos)", "secretaria", (), "Irmãos que ocupam cargo.")
def cargos(loja, f):
    irmaos = Irmao.objects.filter(loja=loja, situacao__in=Irmao.NO_QUADRO).exclude(cargo="").select_related("grau")
    return {"colunas": ["Cargo", "Irmão", "Grau", "Celular", "E-mail"],
            "linhas": [Linha([i.cargo, i.nome, i.grau, i.celular, i.email]) for i in irmaos.order_by("cargo")]}


@relatorio("coirmas", "Lojas co-Irmãs", "secretaria", (), "Relação das Lojas cadastradas.")
def coirmas(loja, f):
    return {"colunas": ["Loja", "Nº", "Potência", "Oriente", "UF", "Venerável", "Sessões", "E-mail", "Telefone"],
            "linhas": [Linha([c.nome, c.numero, c.potencia, c.oriente, c.uf, c.veneravel, c.dia_sessao, c.email, c.telefone])
                       for c in LojaCoirma.objects.filter(loja=loja)]}


@relatorio("atas", "Atas no período", "secretaria", ("periodo",), "Atas registradas e se já foram aprovadas.")
def atas(loja, f):
    qs = Ata.objects.filter(loja=loja, data__range=(f["inicio"], f["fim"])).select_related("grau").order_by("data")
    return {"colunas": ["Data", "Nº", "Título", "Grau", "Aprovada", "Arquivo anexado"],
            "linhas": [Linha([a.data, a.numero, a.titulo, a.grau, a.aprovada, bool(a.arquivo)]) for a in qs]}


@relatorio("eventos", "Agenda no período", "secretaria", ("periodo",), "Eventos da Loja e de entidades.")
def eventos(loja, f):
    qs = Evento.objects.filter(loja=loja, inicio__date__range=(f["inicio"], f["fim"]))
    return {"colunas": ["Início", "Título", "Tipo", "Entidade", "Local"],
            "linhas": [Linha([e.inicio, e.titulo, e.get_tipo_display(), e.entidade, e.local]) for e in qs]}


@relatorio("emails", "E-mails enviados", "secretaria", ("periodo",), "Histórico de disparos, inclusive os automáticos.")
def emails(loja, f):
    qs = EmailEnviado.objects.filter(loja=loja, enviado_em__date__range=(f["inicio"], f["fim"]))
    return {"colunas": ["Enviado em", "Assunto", "Para", "Destinatários", "Automático"],
            "linhas": [Linha([e.enviado_em, e.assunto, e.publico, e.total, e.automatico]) for e in qs]}


# ================================================================ Tesouraria
@relatorio("balancete", "Balancete mensal", "tesouraria", ("mes", "ano"),
           "Receitas e despesas quitadas no mês, por plano de contas, e o saldo de cada conta.")
def balancete(loja, f):
    b = servicos.balancete(loja, f["ano"], f["mes"])
    linhas = [Linha(["RECEITAS", ""], "grupo")]
    linhas += [Linha([f"{p.codigo}  {p.nome}", s]) for p, s in b["receitas"]]
    linhas += [Linha(["Total de receitas", b["total_receitas"]], "total"), Linha(["DESPESAS", ""], "grupo")]
    linhas += [Linha([f"{p.codigo}  {p.nome}", s]) for p, s in b["despesas"]]
    linhas += [Linha(["Total de despesas", b["total_despesas"]], "total"),
               Linha(["RESULTADO DO MÊS (receitas − despesas)", b["resultado"]], "total"),
               Linha(["SALDOS DAS CONTAS", ""], "grupo")]
    for c in b["contas"]:
        linhas.append(Linha([f"{c['conta'].nome} — saldo anterior", c["anterior"]]))
        linhas.append(Linha([" + recebimentos", c["receitas"]]))
        linhas.append(Linha([" − pagamentos", c["despesas"]]))
        if c["transf_entrada"] or c["transf_saida"]:
            linhas.append(Linha([" ± transferências", c["transf_entrada"] - c["transf_saida"]]))
        linhas.append(Linha([f"{c['conta'].nome} — saldo final", c["final"]], "total"))
    linhas += [Linha(["Saldo anterior total", b["saldo_anterior"]], "total"),
               Linha(["Saldo final total", b["saldo_final"]], "total")]
    return {"colunas": ["Descrição", "Valor"], "linhas": linhas, "resumo": f"{MESES[f['mes'] - 1]} de {f['ano']}"}


def _lancamentos(loja, tipo, status):
    return Lancamento.objects.filter(loja=loja, tipo=tipo, status=status).select_related("irmao", "plano", "conta")


def _lista_abertos(qs):
    hoje, total, linhas = timezone.localdate(), ZERO, []
    for l in qs.order_by("vencimento"):
        atraso = (hoje - l.vencimento).days
        linhas.append(Linha([l.vencimento, l.quem, l.descricao, l.plano.nome, l.valor, atraso if atraso > 0 else None],
                            "atrasado" if atraso > 0 else ""))
        total += l.valor
    linhas.append(Linha(["Total", "", "", "", total, ""], "total"))
    return {"colunas": ["Vencimento", "De / para", "Descrição", "Plano", "Valor", "Dias em atraso"], "linhas": linhas}


@relatorio("receber-aberto", "Contas a receber em aberto", "tesouraria", ("irmao",), "Tudo o que a Loja ainda tem a receber.")
def receber_aberto(loja, f):
    qs = _lancamentos(loja, "R", "aberto")
    return _lista_abertos(qs.filter(irmao=f["irmao"]) if f.get("irmao") else qs)


@relatorio("pagar-aberto", "Contas a pagar em aberto", "tesouraria", (), "Compromissos que ainda não foram pagos.")
def pagar_aberto(loja, f):
    return _lista_abertos(_lancamentos(loja, "P", "aberto"))


@relatorio("inadimplencia", "Inadimplência por Irmão", "tesouraria", (), "Irmãos com valores vencidos e há quanto tempo.")
def inadimplencia(loja, f):
    hoje, por_irmao = timezone.localdate(), {}
    for l in _lancamentos(loja, "R", "aberto").filter(vencimento__lt=hoje, irmao__isnull=False):
        d = por_irmao.setdefault(l.irmao, {"n": 0, "total": ZERO, "mais_antigo": l.vencimento})
        d["n"] += 1
        d["total"] += l.valor
        d["mais_antigo"] = min(d["mais_antigo"], l.vencimento)
    linhas = [Linha([i.nome, i.celular, d["n"], d["mais_antigo"], (hoje - d["mais_antigo"]).days, d["total"]])
              for i, d in sorted(por_irmao.items(), key=lambda x: -x[1]["total"])]
    linhas.append(Linha(["Total", "", sum(d["n"] for d in por_irmao.values()), "", "",
                         sum((d["total"] for d in por_irmao.values()), ZERO)], "total"))
    return {"colunas": ["Irmão", "Celular", "Parcelas vencidas", "Mais antiga", "Dias em atraso", "Total devido"],
            "linhas": linhas, "resumo": f"{len(por_irmao)} Irmão(s) com valores vencidos"}


def _quitados(loja, tipo, f):
    qs = _lancamentos(loja, tipo, "pago").filter(data_pagamento__range=(f["inicio"], f["fim"]))
    if f.get("conta"):
        qs = qs.filter(conta=f["conta"])
    linhas, total = [], ZERO
    for l in qs.order_by("data_pagamento"):
        linhas.append(Linha([l.data_pagamento, l.quem, l.descricao, l.plano.nome, l.conta, l.valor_pago]))
        total += l.valor_pago
    linhas.append(Linha(["Total", "", "", "", "", total], "total"))
    return {"colunas": ["Data", "De / para", "Descrição", "Plano", "Conta", "Valor"], "linhas": linhas}


@relatorio("recebimentos", "Recebimentos no período", "tesouraria", ("periodo", "conta"), "Tudo o que entrou.")
def recebimentos(loja, f):
    return _quitados(loja, "R", f)


@relatorio("pagamentos", "Pagamentos no período", "tesouraria", ("periodo", "conta"), "Tudo o que saiu.")
def pagamentos(loja, f):
    return _quitados(loja, "P", f)


@relatorio("extrato", "Extrato de conta", "tesouraria", ("periodo", "conta"),
           "Movimento dia a dia de uma conta, com saldo.")
def extrato(loja, f):
    conta = f.get("conta") or ContaBancaria.objects.filter(loja=loja).first()
    if conta is None:
        return {"colunas": ["Aviso"], "linhas": [Linha(["Cadastre uma conta primeiro."])]}
    saldo = servicos.saldo_conta(conta, f["inicio"] - timedelta(days=1))
    movimentos = [(l.data_pagamento, l.descricao + (f" — {l.quem}" if l.quem else ""),
                   l.valor_pago if l.tipo == "R" else -l.valor_pago)
                  for l in Lancamento.objects.filter(loja=loja, conta=conta, status="pago",
                                                     data_pagamento__range=(f["inicio"], f["fim"])).select_related("irmao")]
    for t in Transferencia.objects.filter(loja=loja, data__range=(f["inicio"], f["fim"])).filter(
            Q(origem=conta) | Q(destino=conta)).select_related("origem", "destino"):
        if t.origem_id != t.destino_id:
            entrada = t.destino_id == conta.pk
            movimentos.append((t.data, f"Transferência {'de ' + t.origem.nome if entrada else 'para ' + t.destino.nome}",
                               t.valor if entrada else -t.valor))
    linhas = [Linha([f["inicio"], "Saldo anterior", None, None, saldo], "total")]
    for dia, descricao, valor in sorted(movimentos, key=lambda m: m[0]):
        saldo += valor
        linhas.append(Linha([dia, descricao, valor if valor > 0 else None, -valor if valor < 0 else None, saldo]))
    linhas.append(Linha([f["fim"], "Saldo final", None, None, saldo], "total"))
    return {"colunas": ["Data", "Histórico", "Entrada", "Saída", "Saldo"], "linhas": linhas, "resumo": conta.nome}


@relatorio("por-plano", "Receitas e despesas por plano de contas", "tesouraria", ("periodo",),
           "Totais quitados no período em cada conta do plano.")
def por_plano(loja, f):
    somas = dict(Lancamento.objects.filter(loja=loja, status="pago", data_pagamento__range=(f["inicio"], f["fim"]))
                 .values_list("plano_id").annotate(s=Sum("valor_pago")))
    linhas, totais = [], {"R": ZERO, "D": ZERO}
    for tipo, nome in (("R", "RECEITAS"), ("D", "DESPESAS")):
        linhas.append(Linha([nome, ""], "grupo"))
        for p in PlanoConta.objects.filter(loja=loja, tipo=tipo, aceita_lancamento=True):
            if somas.get(p.pk):
                linhas.append(Linha([f"{p.codigo}  {p.nome}", somas[p.pk]]))
                totais[tipo] += somas[p.pk]
        linhas.append(Linha([f"Total de {nome.lower()}", totais[tipo]], "total"))
    linhas.append(Linha(["RESULTADO DO PERÍODO", totais["R"] - totais["D"]], "total"))
    return {"colunas": ["Plano de contas", "Valor"], "linhas": linhas}


@relatorio("posicao-irmaos", "Posição financeira por Irmão", "tesouraria", ("ano", "situacao"),
           "Quanto cada Irmão pagou no ano e quanto ainda deve.")
def posicao_irmaos(loja, f):
    hoje = timezone.localdate()
    base = Lancamento.objects.filter(loja=loja, tipo="R", irmao__isnull=False)
    pago = dict(base.filter(status="pago", data_pagamento__year=f["ano"]).values_list("irmao_id").annotate(s=Sum("valor_pago")))
    aberto = dict(base.filter(status="aberto").values_list("irmao_id").annotate(s=Sum("valor")))
    vencido = dict(base.filter(status="aberto", vencimento__lt=hoje).values_list("irmao_id").annotate(s=Sum("valor")))
    linhas, tot = [], [ZERO, ZERO, ZERO]
    for i in _irmaos(loja, f):
        valores = [pago.get(i.pk, ZERO), aberto.get(i.pk, ZERO), vencido.get(i.pk, ZERO)]
        tot = [a + b for a, b in zip(tot, valores)]
        linhas.append(Linha([i.nome, *valores, "Em dia" if not valores[2] else "Em atraso"],
                            "atrasado" if valores[2] else ""))
    linhas.append(Linha(["Total", *tot, ""], "total"))
    return {"colunas": ["Irmão", f"Pago em {f['ano']}", "Em aberto", "Vencido", "Situação"], "linhas": linhas}


@relatorio("saldos", "Saldos das contas", "tesouraria", (), "Quanto há hoje em cada conta e no total.")
def saldos(loja, f):
    linhas, total = [], ZERO
    for c in ContaBancaria.objects.filter(loja=loja):
        s = servicos.saldo_conta(c)
        total += s
        linhas.append(Linha([c.nome, c.banco_nome, s]))
    linhas.append(Linha(["Total", "", total], "total"))
    return {"colunas": ["Conta", "Banco", "Saldo atual"], "linhas": linhas}


@relatorio("boletos", "Boletos emitidos", "tesouraria", ("periodo",), "Boletos gerados no período e a situação de cada um.")
def boletos(loja, f):
    qs = (Boleto.objects.filter(loja=loja, gerado_em__date__range=(f["inicio"], f["fim"]))
          .select_related("lancamento__irmao", "remessa"))
    return {"colunas": ["Gerado em", "Nosso número", "Pagador", "Descrição", "Vencimento", "Valor", "Situação", "Remessa"],
            "linhas": [Linha([b.gerado_em, b.nosso_numero, b.lancamento.quem, b.lancamento.descricao, b.lancamento.vencimento,
                              b.lancamento.valor, b.get_status_display(), b.remessa.numero if b.remessa else None]) for b in qs]}


@relatorio("previsao", "Previsão de caixa (próximos 90 dias)", "tesouraria", (),
           "O que está previsto para entrar e sair, mês a mês.")
def previsao(loja, f):
    hoje = timezone.localdate()
    abertos = Lancamento.objects.filter(loja=loja, status="aberto", vencimento__lte=hoje + timedelta(days=90))
    meses = {}
    for l in abertos:
        chave = date(l.vencimento.year, l.vencimento.month, 1) if l.vencimento >= hoje else None
        m = meses.setdefault(chave, {"R": ZERO, "P": ZERO})
        m[l.tipo] += l.valor
    saldo = sum((servicos.saldo_conta(c) for c in ContaBancaria.objects.filter(loja=loja)), ZERO)
    linhas = [Linha(["Saldo atual das contas", None, None, saldo], "total")]
    for chave in sorted(meses, key=lambda c: c or date.min):
        m = meses[chave]
        saldo += m["R"] - m["P"]
        linhas.append(Linha(["Já vencidos" if chave is None else f"{MESES[chave.month - 1]}/{chave.year}", m["R"], m["P"], saldo]))
    return {"colunas": ["Mês", "A receber", "A pagar", "Saldo previsto"], "linhas": linhas,
            "resumo": "Considera que tudo o que está em aberto será quitado no vencimento."}


# =============================================================== Chancelaria
@relatorio("frequencia", "Frequência por período (eleições)", "chancelaria", ("periodo", "situacao"),
           "Percentual de frequência de cada Irmão e quem está apto a votar e ser votado.")
def frequencia(loja, f):
    dados = servicos.frequencia(loja, f["inicio"], f["fim"])
    permitidos = set(_irmaos(loja, f).values_list("pk", flat=True))
    linhas, aptos = [], 0
    for d in dados:
        if d["irmao"].pk not in permitidos:
            continue
        aptos += d["apto"]
        pct = f"{d['percentual']:.1f}%".replace(".", ",") if d["percentual"] is not None else None
        linhas.append(Linha([d["irmao"].nome, d["irmao"].grau, d["sessoes"], d["presencas"], d["faltas"], d["abonos"],
                             d["licencas"], d["visitas"], pct,
                             "Apto" if d["apto"] else ("Sem sessões" if d["percentual"] is None else "Inapto")],
                            "" if d["apto"] or d["percentual"] is None else "atrasado"))
    regras = [f"mínimo de {loja.frequencia_minima}%",
              "falta abonada conta como presença" if loja.abono_conta_presenca else "falta abonada não conta como presença",
              "cada certificado de visita compensa uma falta" if loja.visita_conta_presenca else "visitas não compensam faltas",
              "sessões durante licença ficam fora da conta"]
    return {"colunas": ["Irmão", "Grau", "Sessões", "Presenças", "Faltas", "Abonadas", "Licenças", "Visitas", "Frequência", "Situação"],
            "linhas": linhas, "resumo": f"{aptos} apto(s) de {len(linhas)}. Regras desta Loja: " + "; ".join(regras) + "."}


@relatorio("sessoes", "Presença por sessão", "chancelaria", ("periodo",), "Quantos Irmãos compareceram a cada sessão.")
def sessoes(loja, f):
    qs = (Sessao.objects.filter(loja=loja, data__range=(f["inicio"], f["fim"])).select_related("grau").order_by("data")
          .annotate(p=Count("presencas", filter=Q(presencas__status="P")), fa=Count("presencas", filter=Q(presencas__status="F")),
                    a=Count("presencas", filter=Q(presencas__status="A")), li=Count("presencas", filter=Q(presencas__status="L"))))
    linhas = []
    for s in qs:
        convocados = s.p + s.fa + s.a
        linhas.append(Linha([s.data, s.get_tipo_display(), s.grau, s.p, s.fa, s.a, s.li,
                             f"{s.p * 100 / convocados:.0f}%" if convocados else None]))
    return {"colunas": ["Data", "Tipo", "Grau", "Presentes", "Faltas", "Abonadas", "Licenciados", "Comparecimento"],
            "linhas": linhas, "resumo": f"{len(linhas)} sessão(ões)"}


@relatorio("faltas", "Faltas e abonos", "chancelaria", ("periodo", "irmao"), "Cada falta do período e a justificativa do abono.")
def faltas(loja, f):
    qs = (Presenca.objects.filter(loja=loja, status__in=["F", "A"], sessao__data__range=(f["inicio"], f["fim"]))
          .select_related("irmao", "sessao").order_by("irmao__nome", "sessao__data"))
    if f.get("irmao"):
        qs = qs.filter(irmao=f["irmao"])
    return {"colunas": ["Irmão", "Data da sessão", "Tipo", "Situação", "Justificativa"],
            "linhas": [Linha([p.irmao.nome, p.sessao.data, p.sessao.get_tipo_display(), p.get_status_display(), p.justificativa])
                       for p in qs]}


@relatorio("licencas", "Licenças", "chancelaria", ("periodo",), "Licenças que caem dentro do período.")
def licencas(loja, f):
    qs = Licenca.objects.filter(loja=loja, inicio__lte=f["fim"], fim__gte=f["inicio"]).select_related("irmao")
    return {"colunas": ["Irmão", "Início", "Término", "Dias", "Motivo"],
            "linhas": [Linha([l.irmao.nome, l.inicio, l.fim, (l.fim - l.inicio).days + 1, l.motivo]) for l in qs]}


@relatorio("visitas", "Certificados de visita", "chancelaria", ("periodo", "irmao"), "Visitas a outras Lojas no período.")
def visitas(loja, f):
    qs = CertificadoVisita.objects.filter(loja=loja, data__range=(f["inicio"], f["fim"])).select_related("irmao", "coirma")
    if f.get("irmao"):
        qs = qs.filter(irmao=f["irmao"])
    return {"colunas": ["Data", "Irmão", "Loja visitada", "Observação"],
            "linhas": [Linha([v.data, v.irmao.nome, v.onde, v.observacao]) for v in qs.order_by("data")]}


# ============================================================== Hospitalaria
@relatorio("inventario", "Inventário de bens e materiais", "hospitalaria", (), "Todos os bens e onde cada um está.")
def inventario(loja, f):
    linhas = []
    for b in Bem.objects.filter(loja=loja):
        e = b.emprestimo_atual
        linhas.append(Linha([b.codigo, b.descricao, b.categoria, b.get_estado_display(),
                             f"Emprestado a {e.beneficiario}" if e else "Disponível", e.data_emprestimo if e else None]))
    return {"colunas": ["Código", "Descrição", "Categoria", "Estado", "Situação", "Desde"], "linhas": linhas}


@relatorio("emprestados", "Bens emprestados no momento", "hospitalaria", (), "O que está fora e com quem, com destaque para atrasos.")
def emprestados(loja, f):
    hoje = timezone.localdate()
    qs = Emprestimo.objects.filter(loja=loja, data_devolucao__isnull=True).select_related("bem", "irmao").order_by("data_emprestimo")
    return {"colunas": ["Bem", "Quem usa", "Contato", "Irmão responsável", "Emprestado em", "Previsão", "Dias fora"],
            "linhas": [Linha([str(e.bem), e.beneficiario, e.contato, e.irmao, e.data_emprestimo, e.previsao_devolucao,
                              (hoje - e.data_emprestimo).days], "atrasado" if e.atrasado else "") for e in qs]}


@relatorio("emprestimos", "Histórico de empréstimos", "hospitalaria", ("periodo",), "Empréstimos feitos no período.")
def emprestimos(loja, f):
    qs = (Emprestimo.objects.filter(loja=loja, data_emprestimo__range=(f["inicio"], f["fim"]))
          .select_related("bem", "irmao").order_by("data_emprestimo"))
    return {"colunas": ["Emprestado em", "Bem", "Quem usa", "Irmão responsável", "Previsão", "Devolvido em"],
            "linhas": [Linha([e.data_emprestimo, str(e.bem), e.beneficiario, e.irmao, e.previsao_devolucao, e.data_devolucao])
                       for e in qs]}


@relatorio("mensalidades-ano", "Controle de mensalidades do ano", "tesouraria", ("ano",),
           "Mês a mês, quem pagou, quem está em aberto e quem é remido — no formato da planilha da Tesouraria.")
def mensalidades_ano(loja, f):
    ano = f["ano"]
    plano = PlanoConta.objects.filter(loja=loja, codigo="1.1").first()
    lancs = Lancamento.objects.filter(loja=loja, tipo="R", irmao__isnull=False, competencia__year=ano).exclude(status="cancelado")
    if plano:
        lancs = lancs.filter(plano=plano)
    mapa = {}
    for l in lancs:
        mapa[(l.irmao_id, l.competencia.month)] = "Pago" if l.status == "pago" else (
            "Acordo" if "acordo" in l.observacao.lower() else ("Vencido" if l.vencido else "Aberto"))
    irmaos = sorted(Irmao.objects.filter(loja=loja, situacao__in=Irmao.NO_QUADRO),
                    key=lambda i: (i.centavos is None, i.centavos or 0, i.nome))
    linhas, pagos_mes = [], [0] * 12
    for i in irmaos:
        remido = i.situacao == "remido"
        meses = []
        for m in range(1, 13):
            valor = "Remido" if remido else mapa.get((i.pk, m), "")
            pagos_mes[m - 1] += valor == "Pago"
            meses.append(valor or None)
        linhas.append(Linha([f"{i.centavos:.2f}".replace(".", ",") if i.centavos is not None else None, i.nome,
                             "Sim" if i.cotizante else "Não", "Remido" if remido else (i.mensalidade_individual or loja.valor_mensalidade),
                             *meses], "atrasado" if "Vencido" in meses else ""))
    linhas.append(Linha(["", "Pagamentos no mês", "", "", *pagos_mes], "total"))
    return {"colunas": ["Centavos", "Obreiro", "Cotizante", "R$"] + [m[:3] for m in MESES], "linhas": linhas,
            "resumo": f"{ano}: {sum(pagos_mes)} mensalidade(s) paga(s); {sum(1 for v in mapa.values() if v == 'Vencido')} vencida(s)"}


@relatorio("centavos", "Tabela de centavos", "tesouraria", (), "Os centavos que identificam cada Irmão no pagamento, e os que estão livres.")
def centavos(loja, f):
    usados = {i.centavos: i for i in Irmao.objects.filter(loja=loja, centavos__isnull=False)}
    linhas = []
    for n in range(1, 100):
        c = Decimal(n) / 100
        i = usados.get(c)
        linhas.append(Linha([f"{c:.2f}".replace(".", ","), i.nome if i else "LIVRE", i.cim if i else "",
                             (i.mensalidade_individual or loja.valor_mensalidade) + c if i else None], "" if i else "apagado"))
    return {"colunas": ["Centavos", "Irmão", "CIM", "Valor a pagar"], "linhas": linhas,
            "resumo": f"{len(usados)} centavo(s) em uso, {99 - len(usados)} livre(s)"}
