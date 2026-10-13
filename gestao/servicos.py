"""Regras de negócio: os cálculos e rotinas que as telas usam."""
import calendar
import uuid
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from . import push
from .models import (CertificadoVisita, ContaBancaria, Evento, EmailEnviado, Grau, Irmao, Lancamento, Licenca,
                     Notificacao, PlanoConta, Presenca, RotinaDiaria, Sessao, Transferencia, Usuario)

ZERO = Decimal("0")

GRAUS_PADRAO = ["Aprendiz", "Companheiro", "Mestre", "Mestre Instalado"]
PLANO_PADRAO = [
    ("1", "RECEITAS", "R", False), ("1.1", "Mensalidades", "R", True), ("1.2", "Joias e taxas de iniciação", "R", True),
    ("1.3", "Tronco de beneficência", "R", True), ("1.4", "Eventos e jantares", "R", True),
    ("1.5", "Doações recebidas", "R", True), ("1.6", "Remido (isenção de mensalidade)", "R", True),
    ("1.9", "Outras receitas", "R", True),
    ("2", "DESPESAS", "D", False), ("2.1", "Taxas da Potência", "D", True), ("2.2", "Aluguel e condomínio", "D", True),
    ("2.3", "Água, luz e internet", "D", True), ("2.4", "Manutenção do Templo", "D", True),
    ("2.5", "Material ritualístico e de expediente", "D", True), ("2.6", "Beneficência e filantropia", "D", True),
    ("2.7", "Eventos e ágapes", "D", True), ("2.8", "Tarifas bancárias", "D", True), ("2.9", "Outras despesas", "D", True),
]


def preparar_loja(loja):
    """Deixa uma Loja nova pronta para uso: graus, plano de contas e um caixa."""
    for ordem, nome in enumerate(GRAUS_PADRAO, 1):
        Grau.objects.get_or_create(loja=loja, nome=nome, defaults={"ordem": ordem})
    por_codigo = {}
    for codigo, nome, tipo, aceita in PLANO_PADRAO:
        pai = por_codigo.get(codigo.rsplit(".", 1)[0]) if "." in codigo else None
        por_codigo[codigo], _ = PlanoConta.objects.get_or_create(
            loja=loja, codigo=codigo, defaults={"nome": nome, "tipo": tipo, "aceita_lancamento": aceita, "pai": pai})
    if not ContaBancaria.objects.filter(loja=loja).exists():
        ContaBancaria.objects.create(loja=loja, nome="Caixa")


# --- Datas --------------------------------------------------------------------
def somar_meses(d, meses, dia=None):
    total = d.month - 1 + meses
    ano, mes = d.year + total // 12, total % 12 + 1
    dia = min(dia or d.day, calendar.monthrange(ano, mes)[1])
    return date(ano, mes, dia)


def limites_do_mes(ano, mes):
    return date(ano, mes, 1), date(ano, mes, calendar.monthrange(ano, mes)[1])


def faz_aniversario(nascimento, dia):
    if not nascimento:
        return False
    if (nascimento.month, nascimento.day) == (dia.month, dia.day):
        return True
    # quem nasceu em 29/02 comemora em 28/02 nos anos não bissextos
    return ((nascimento.month, nascimento.day) == (2, 29) and (dia.month, dia.day) == (2, 28)
            and not calendar.isleap(dia.year))


# --- Secretaria ---------------------------------------------------------------
def atualizar_grau(irmao):
    """O grau atual do Irmão é sempre o mais alto registrado no histórico dele."""
    ultimo = irmao.graus.select_related("grau").order_by("-grau__ordem", "-data").first()
    if ultimo and irmao.grau_id != ultimo.grau_id:
        irmao.grau = ultimo.grau
        irmao.save(update_fields=["grau"])


def enviar_emails(loja, assunto, corpo, pessoas, publico, usuario=None, automatico=False):
    """Envia um e-mail individual para cada pessoa (ninguém vê o endereço dos outros).

    ``pessoas`` é uma lista de (nome, e-mail). O texto pode usar {nome}.
    Devolve quantos e-mails saíram.
    """
    vistos, mensagens = set(), []
    remetente = settings.DEFAULT_FROM_EMAIL
    nome_remetente = str(loja).replace('"', "")
    for nome, email in pessoas:
        email = (email or "").strip().lower()
        if not email or email in vistos:
            continue
        vistos.add(email)
        mensagens.append(EmailMessage(
            subject=assunto.replace("{nome}", nome), body=corpo.replace("{nome}", nome),
            from_email=f'"{nome_remetente}" <{remetente}>', to=[email],
            reply_to=[loja.email] if loja.email else None))
    enviados = get_connection().send_messages(mensagens) if mensagens else 0
    EmailEnviado.objects.create(loja=loja, assunto=assunto, corpo=corpo, publico=publico, total=enviados or 0,
                                destinatarios=", ".join(sorted(vistos)), enviado_por=usuario, automatico=automatico)
    return enviados or 0


# --- Chancelaria --------------------------------------------------------------
def sincronizar_presencas(sessao):
    """Monta (ou completa) a lista de chamada da sessão.

    Entram os Irmãos do quadro com grau suficiente para a sessão. Quem está de
    licença na data já entra marcado como Licenciado.
    """
    irmaos = Irmao.objects.filter(loja=sessao.loja, situacao__in=Irmao.NO_QUADRO)
    if sessao.grau_id:
        irmaos = irmaos.filter(Q(grau__isnull=True) | Q(grau__ordem__gte=sessao.grau.ordem))
    irmaos = irmaos.filter(Q(data_iniciacao__isnull=True) | Q(data_iniciacao__lte=sessao.data))
    existentes = set(sessao.presencas.values_list("irmao_id", flat=True))
    licenciados = set(Licenca.objects.filter(loja=sessao.loja, inicio__lte=sessao.data, fim__gte=sessao.data)
                      .values_list("irmao_id", flat=True))
    novas = [Presenca(loja=sessao.loja, sessao=sessao, irmao=i, status="L" if i.pk in licenciados else "F")
             for i in irmaos if i.pk not in existentes]
    Presenca.objects.bulk_create(novas)
    return len(novas)


def frequencia(loja, inicio, fim, irmao=None):
    """Frequência de cada Irmão no período.

    Sessões em que o Irmão estava licenciado não entram na conta. Falta abonada
    e certificado de visita contam ou não conforme a personalização da Loja.
    """
    presencas = Presenca.objects.filter(loja=loja, sessao__data__range=(inicio, fim))
    visitas = CertificadoVisita.objects.filter(loja=loja, data__range=(inicio, fim))
    irmaos = Irmao.objects.filter(loja=loja).select_related("grau")
    if irmao is not None:
        presencas, visitas, irmaos = presencas.filter(irmao=irmao), visitas.filter(irmao=irmao), irmaos.filter(pk=irmao.pk)
    contagem = defaultdict(Counter)
    for irmao_id, status, n in presencas.values_list("irmao_id", "status").annotate(n=Count("id")):
        contagem[irmao_id][status] = n
    n_visitas = dict(visitas.values_list("irmao_id").annotate(n=Count("id")))
    if irmao is None:
        irmaos = irmaos.filter(Q(situacao__in=Irmao.NO_QUADRO) | Q(pk__in=list(contagem)))
    linhas = []
    for i in irmaos:
        c = contagem[i.pk]
        convocado = c["P"] + c["F"] + c["A"]
        validas = c["P"] + (c["A"] if loja.abono_conta_presenca else 0)
        faltas_reais = convocado - validas
        v = n_visitas.get(i.pk, 0)
        if loja.visita_conta_presenca:
            validas += min(v, faltas_reais)
        percentual = round(validas * 100 / convocado, 1) if convocado else None
        linhas.append({"irmao": i, "sessoes": convocado, "presencas": c["P"], "faltas": c["F"], "abonos": c["A"],
                       "licencas": c["L"], "visitas": v, "validas": validas, "percentual": percentual,
                       "apto": percentual is not None and percentual >= loja.frequencia_minima})
    return linhas


# --- Tesouraria ---------------------------------------------------------------
def movimento_conta(conta, inicio=None, fim=None):
    """Entradas, saídas e transferências de uma conta num período (datas inclusas)."""
    pagos = Lancamento.objects.filter(conta=conta, status="pago")
    entradas_t, saidas_t = Transferencia.objects.filter(destino=conta), Transferencia.objects.filter(origem=conta)
    if inicio:
        pagos, entradas_t, saidas_t = (pagos.filter(data_pagamento__gte=inicio), entradas_t.filter(data__gte=inicio),
                                       saidas_t.filter(data__gte=inicio))
    if fim:
        pagos, entradas_t, saidas_t = (pagos.filter(data_pagamento__lte=fim), entradas_t.filter(data__lte=fim),
                                       saidas_t.filter(data__lte=fim))
    soma = lambda qs, campo: qs.aggregate(s=Sum(campo))["s"] or ZERO  # noqa: E731
    return {"receitas": soma(pagos.filter(tipo="R"), "valor_pago"), "despesas": soma(pagos.filter(tipo="P"), "valor_pago"),
            "transf_entrada": soma(entradas_t, "valor"), "transf_saida": soma(saidas_t, "valor")}


def saldo_conta(conta, ate=None):
    m = movimento_conta(conta, fim=ate)
    return conta.saldo_inicial + m["receitas"] - m["despesas"] + m["transf_entrada"] - m["transf_saida"]


def quitar(lancamento, data, valor, conta):
    lancamento.status, lancamento.data_pagamento, lancamento.valor_pago, lancamento.conta = "pago", data, valor, conta
    lancamento.save(update_fields=["status", "data_pagamento", "valor_pago", "conta"])
    boleto = getattr(lancamento, "boleto", None)
    if boleto and boleto.status != "pago":
        boleto.status = "pago"
        boleto.save(update_fields=["status"])


def estornar(lancamento):
    lancamento.status, lancamento.data_pagamento, lancamento.valor_pago = "aberto", None, None
    lancamento.save(update_fields=["status", "data_pagamento", "valor_pago"])
    boleto = getattr(lancamento, "boleto", None)
    if boleto and boleto.status == "pago":
        boleto.status = "registrado"
        boleto.save(update_fields=["status"])


@transaction.atomic
def gerar_mensalidades(loja, irmaos, plano, valor, primeiro_vencimento, meses, descricao="Mensalidade"):
    """Cria as mensalidades de vários Irmãos para vários meses de uma vez.

    Não duplica: se o Irmão já tem lançamento desse plano para o mês, pula.
    Devolve (criados, pulados).
    """
    serie, criados, pulados = uuid.uuid4(), [], 0
    ja_existem = set(Lancamento.objects.filter(loja=loja, plano=plano, tipo="R", competencia__isnull=False)
                     .exclude(status="cancelado").values_list("irmao_id", "competencia"))
    for n in range(meses):
        vencimento = somar_meses(primeiro_vencimento, n)
        competencia = vencimento.replace(day=1)
        for irmao in irmaos:
            if irmao.situacao == "remido" and not plano.eh_remido:  # remido é isento de mensalidade
                continue
            if (irmao.pk, competencia) in ja_existem:
                pulados += 1
                continue
            if plano.eh_remido:  # registro da isenção: sem valor a pagar, não entra como dívida
                criados.append(Lancamento(
                    loja=loja, tipo="R", irmao=irmao, plano=plano, valor=Decimal("0"), valor_pago=Decimal("0"),
                    status="isento", vencimento=vencimento, competencia=competencia, serie=serie,
                    descricao=f"{descricao} {competencia:%m/%Y}", observacao="Remido: isento de mensalidade"))
                continue
            criados.append(Lancamento(
                loja=loja, tipo="R", irmao=irmao, plano=plano, valor=irmao.mensalidade_individual or valor,
                vencimento=vencimento,
                competencia=competencia, serie=serie, descricao=f"{descricao} {competencia:%m/%Y}"))
    Lancamento.objects.bulk_create(criados)
    if plano.eh_remido and criados:
        # quem ficou isento nesses meses não deve mais as mensalidades em aberto desses mesmos meses
        for l in criados:
            Lancamento.objects.filter(loja=loja, irmao=l.irmao, tipo="R", status="aberto", competencia=l.competencia) \
                .exclude(plano=plano).update(status="cancelado", observacao="Cancelada: Irmão remido (isento)")
    return len(criados), pulados


def balancete(loja, ano, mes):
    inicio, fim = limites_do_mes(ano, mes)
    pagos = Lancamento.objects.filter(loja=loja, status="pago", data_pagamento__range=(inicio, fim))
    por_plano = {(p, t): s for p, t, s in pagos.values_list("plano_id", "tipo").annotate(s=Sum("valor_pago"))}
    planos = {p.pk: p for p in PlanoConta.objects.filter(loja=loja)}
    receitas = sorted(((planos[p], s) for (p, t), s in por_plano.items() if t == "R"), key=lambda x: x[0].codigo)
    despesas = sorted(((planos[p], s) for (p, t), s in por_plano.items() if t == "P"), key=lambda x: x[0].codigo)
    contas = []
    for conta in ContaBancaria.objects.filter(loja=loja):
        anterior = saldo_conta(conta, inicio - timedelta(days=1))
        m = movimento_conta(conta, inicio, fim)
        final = anterior + m["receitas"] - m["despesas"] + m["transf_entrada"] - m["transf_saida"]
        contas.append({"conta": conta, "anterior": anterior, "final": final, **m})
    total_r, total_d = sum((s for _, s in receitas), ZERO), sum((s for _, s in despesas), ZERO)
    return {"inicio": inicio, "fim": fim, "receitas": receitas, "despesas": despesas, "total_receitas": total_r,
            "total_despesas": total_d, "resultado": total_r - total_d, "contas": contas,
            "saldo_anterior": sum((c["anterior"] for c in contas), ZERO),
            "saldo_final": sum((c["final"] for c in contas), ZERO)}


# --- Notificações -------------------------------------------------------------
def notificar(loja, usuarios, titulo, texto):
    """Cria o aviso dentro do sistema e 'cutuca' os celulares inscritos."""
    usuarios = list(usuarios)
    Notificacao.objects.bulk_create([Notificacao(loja=loja, usuario=u, titulo=titulo, texto=texto) for u in usuarios])
    return push.cutucar(usuarios)


def rotina_diaria(loja, hoje=None):
    """Avisos automáticos do dia: aniversariantes e lembrete de sessão/evento.

    Pode ser chamada várias vezes no mesmo dia sem repetir avisos.
    """
    hoje = hoje or timezone.localdate()
    with transaction.atomic():
        registro, novo = RotinaDiaria.objects.get_or_create(loja=loja, data=hoje)
        if not novo:
            return None
    usuarios = list(Usuario.objects.filter(loja=loja, is_active=True))
    aniversariantes = [i for i in Irmao.objects.filter(loja=loja, situacao__in=Irmao.NO_QUADRO,
                                                       data_nascimento__isnull=False)
                       if faz_aniversario(i.data_nascimento, hoje)]
    for irmao in aniversariantes:
        notificar(loja, [u for u in usuarios if u.irmao_id != irmao.pk],
                  "Aniversariante do dia", f"Hoje é aniversário do Ir∴ {irmao.nome}. Envie seus cumprimentos!")
    com_email = [(i.nome, i.email) for i in aniversariantes if i.email and i.receber_emails]
    emails = 0
    if com_email:
        emails = enviar_emails(
            loja, "Feliz aniversário, Ir∴ {nome}!",
            "Querido Ir∴ {nome},\n\nNesta data especial, todos os Irmãos da " + str(loja) +
            " desejam a você saúde, paz e muitas realizações.\n\nReceba nosso tríplice e fraternal abraço.\n\n" + str(loja),
            com_email, "Aniversariantes do dia", automatico=True)
    inicio_dia = timezone.make_aware(timezone.datetime.combine(hoje, timezone.datetime.min.time()))
    compromissos = [f"{s.get_tipo_display()}" + (f" — {s.descricao}" if s.descricao else "")
                    for s in Sessao.objects.filter(loja=loja, data=hoje)]
    lembretes = [f"Sessão {c}" for c in compromissos]
    for e in Evento.objects.filter(loja=loja, inicio__gte=inicio_dia, inicio__lt=inicio_dia + timedelta(days=1)):
        hora = timezone.localtime(e.inicio).strftime("%H:%M")
        if e.tipo == "sessao" and lembretes:
            continue  # a sessão já foi lembrada acima
        lembretes.append(f"{e.titulo} às {hora}" + (f" — {e.local}" if e.local else ""))
    for texto in lembretes:
        notificar(loja, usuarios, "Lembrete: é hoje!", texto)
    registro.resumo = f"{len(aniversariantes)} aniversariante(s), {emails} e-mail(s), {len(lembretes)} lembrete(s)"
    registro.save(update_fields=["resumo"])
    return registro.resumo


def enviar_senha_por_email(usuario, senha, endereco_do_sistema):
    """Manda login e senha inicial para o e-mail do usuário. Devolve True se o envio foi aceito."""
    loja = usuario.loja
    mensagem = EmailMessage(
        subject=f"Seu acesso ao sistema — {loja}" if loja else "Seu acesso ao sistema",
        body=(f"Olá{', ' + usuario.first_name if usuario.first_name else ''}!\n\n"
              f"Seu acesso ao sistema{' da ' + str(loja) if loja else ''} foi criado.\n\n"
              f"Endereço: {endereco_do_sistema}\nNome de acesso: {usuario.username}\nSenha inicial: {senha}\n\n"
              "No primeiro acesso o sistema vai pedir que você troque esta senha por uma só sua.\n"
              "Não encaminhe este e-mail a ninguém."),
        from_email=settings.DEFAULT_FROM_EMAIL, to=[usuario.email],
        reply_to=[loja.email] if loja and loja.email else None)
    return mensagem.send() == 1
