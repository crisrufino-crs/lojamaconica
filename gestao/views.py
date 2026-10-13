import csv
import json
import mimetypes
import secrets
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import boleto as boletos_servico
from . import importacao
from . import importacao_financeira
from . import servicos
from .cadastros import INICIO_DO_MODULO
from .forms import ImportarFinanceiroForm, EmailForm, MensalidadesForm, NovaLojaForm, PersonalizarForm, QuitarForm
from .models import (PlanoConta, Ata, Boleto, CampoExtra, ContaBancaria, EmailEnviado, Emprestimo, Evento, Informativo,
                     InscricaoPush, Irmao, Lancamento, Loja, LojaCoirma, Notificacao, Presenca, Remessa, Retorno, Sessao,
                     Usuario)
from .permissoes import MODULOS, com_loja, exige, so_plataforma
from .relatorios import RELATORIOS, FiltroForm
from .templatetags.gestao_tags import formatar

ZERO = Decimal("0")


# =============================================================================
# Início e área do Irmão
# =============================================================================
@com_loja
def inicio(request):
    loja, usuario, hoje = request.loja, request.user, timezone.localdate()
    agora = timezone.now()
    contexto = {
        "proximos_eventos": Evento.objects.filter(loja=loja, inicio__gte=agora - timedelta(hours=3))[:5],
        "informativos": Informativo.objects.filter(loja=loja, publicado=True)[:4],
        "aniversariantes": sorted(
            Irmao.objects.filter(loja=loja, situacao__in=Irmao.NO_QUADRO, data_nascimento__month=hoje.month),
            key=lambda i: i.data_nascimento.day),
        "hoje": hoje, "cartoes": [],
    }
    cartoes = contexto["cartoes"]
    if usuario.pode("secretaria"):
        quadro = Irmao.objects.filter(loja=loja, situacao__in=Irmao.NO_QUADRO)
        cartoes.append(("Irmãos no quadro", quadro.count(), f"{quadro.filter(situacao='ativo').count()} ativos", "irmaos_lista"))
    if usuario.pode("tesouraria"):
        abertos = Lancamento.objects.filter(loja=loja, status="aberto")
        saldo = sum((servicos.saldo_conta(c) for c in ContaBancaria.objects.filter(loja=loja)), ZERO)
        vencido = abertos.filter(tipo="R", vencimento__lt=hoje).aggregate(s=Sum("valor"))["s"] or ZERO
        cartoes.append(("Saldo das contas", formatar(saldo), "todas as contas e caixas", "contas_lista"))
        cartoes.append(("A receber em aberto", formatar(abertos.filter(tipo="R").aggregate(s=Sum("valor"))["s"] or ZERO),
                        f"{formatar(vencido)} já vencidos", "receber_lista"))
        cartoes.append(("A pagar em aberto", formatar(abertos.filter(tipo="P").aggregate(s=Sum("valor"))["s"] or ZERO),
                        "compromissos pendentes", "pagar_lista"))
    if usuario.pode("chancelaria"):
        ultima = Sessao.objects.filter(loja=loja, data__lte=hoje).first()
        if ultima:
            presentes, convocados = ultima.presencas.filter(status="P").count(), ultima.presencas.exclude(status="L").count()
            cartoes.append(("Última sessão", f"{presentes} de {convocados}", f"presentes em {ultima.data:%d/%m/%Y}", "sessoes_lista"))
    if usuario.pode("hospitalaria"):
        fora = Emprestimo.objects.filter(loja=loja, data_devolucao__isnull=True)
        atrasados = sum(1 for e in fora if e.atrasado)
        cartoes.append(("Bens emprestados", fora.count(), f"{atrasados} com devolução atrasada", "emprestimos_lista"))
    return render(request, "gestao/inicio.html", contexto)


@com_loja
def minha_area(request):
    irmao, hoje = request.user.irmao, timezone.localdate()
    if irmao is None or irmao.loja_id != request.loja.pk:
        return render(request, "gestao/minha_area.html", {"irmao": None, "modulo": "minha"})
    inicio_ano = hoje.replace(month=1, day=1)
    lancamentos = Lancamento.objects.filter(loja=request.loja, irmao=irmao, tipo="R").exclude(status="cancelado") \
        .select_related("boleto").order_by("-vencimento")
    return render(request, "gestao/minha_area.html", {
        "modulo": "minha", "irmao": irmao, "dependentes": irmao.dependentes.all(), "graus": irmao.graus.select_related("grau"),
        "freq": servicos.frequencia(request.loja, inicio_ano, hoje, irmao)[0],
        "presencas": Presenca.objects.filter(irmao=irmao, sessao__data__gte=inicio_ano).select_related("sessao")
        .order_by("-sessao__data"),
        "abertos": [l for l in lancamentos if l.status == "aberto"],
        "quitados": [l for l in lancamentos if l.status == "pago"][:24],
        "total_aberto": sum((l.valor for l in lancamentos if l.status == "aberto"), ZERO),
        "extras": _extras_do_irmao(irmao), "ano": hoje.year,
    })


def _extras_do_irmao(irmao):
    return [(c.rotulo, irmao.extras.get(str(c.pk))) for c in CampoExtra.objects.filter(loja=irmao.loja)
            if irmao.extras.get(str(c.pk))]


@login_required
def modulo(request, nome):
    if nome not in INICIO_DO_MODULO:
        raise Http404
    return redirect(INICIO_DO_MODULO[nome])


# =============================================================================
# Secretaria
# =============================================================================
@exige("secretaria")
def irmao_ficha(request, pk):
    irmao = get_object_or_404(Irmao.objects.select_related("grau"), pk=pk, loja=request.loja)
    hoje = timezone.localdate()
    contexto = {"irmao": irmao, "modulo": "secretaria", "dependentes": irmao.dependentes.all(),
                "graus": irmao.graus.select_related("grau"), "extras": _extras_do_irmao(irmao),
                "visitas": irmao.visitas.select_related("coirma")[:10], "licencas": irmao.licencas.all()[:10],
                "usuario_do_irmao": getattr(irmao, "usuario", None),
                "freq": servicos.frequencia(request.loja, hoje.replace(month=1, day=1), hoje, irmao)[0], "ano": hoje.year,
                "voltar_ficha": reverse("irmao_ficha", args=[irmao.pk])}
    if request.user.pode("tesouraria"):
        contexto["abertos"] = irmao.lancamentos.filter(status="aberto", tipo="R")
    return render(request, "gestao/irmao_ficha.html", contexto)


@exige("secretaria")
def ata_ver(request, pk):
    ata = get_object_or_404(Ata, pk=pk, loja=request.loja)
    return render(request, "gestao/ata.html", {"ata": ata, "modulo": "secretaria"})


@exige("secretaria")
def emails(request):
    loja = request.loja
    form = EmailForm(request.POST or None, loja=loja)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        quadro = Irmao.objects.filter(loja=loja, situacao__in=Irmao.NO_QUADRO, receber_emails=True).exclude(email="")
        if d["publico"] == "ativos":
            pessoas, rotulo = [(i.nome, i.email) for i in quadro.filter(situacao="ativo")], "Irmãos ativos"
        elif d["publico"] == "quadro":
            pessoas, rotulo = [(i.nome, i.email) for i in quadro], "Todo o quadro"
        elif d["publico"] == "grau":
            pessoas = [(i.nome, i.email) for i in quadro.filter(grau__ordem__gte=d["grau"].ordem)]
            rotulo = f"Grau {d['grau']} e acima"
        elif d["publico"] == "coirmas":
            pessoas = [(c.veneravel or c.nome, c.email) for c in LojaCoirma.objects.filter(loja=loja).exclude(email="")]
            rotulo = "Lojas co-Irmãs"
        else:
            pessoas, rotulo = [(i.nome, i.email) for i in d["irmaos"] if i.receber_emails], "Irmãos escolhidos"
        if not pessoas:
            messages.error(request, "Nenhum destinatário com e-mail cadastrado nesse grupo.")
        else:
            try:
                total = servicos.enviar_emails(loja, d["assunto"], d["corpo"], pessoas, rotulo, request.user)
            except Exception as erro:  # servidor de e-mail fora do ar, senha errada etc.
                messages.error(request, f"O servidor de e-mail recusou o envio: {erro}")
            else:
                aviso = "" if settings.EMAIL_ENVIADO_DE_VERDADE else \
                    " (modo de teste: o servidor de e-mail ainda não está configurado, então nada saiu de verdade)"
                messages.success(request, f"E-mail enviado para {total} destinatário(s){aviso}.")
                return redirect("emails")
    return render(request, "gestao/emails.html", {
        "form": form, "modulo": "secretaria", "enviados": EmailEnviado.objects.filter(loja=loja)[:30],
        "modo_teste": not settings.EMAIL_ENVIADO_DE_VERDADE})


@exige("secretaria")
@require_POST
def irmaos_excluir_varios(request):
    """Exclusão em lote: recebe os Irmãos marcados na lista, mostra a conferência e só exclui com a palavra EXCLUIR."""
    from .crud import RECURSOS
    loja, recurso = request.loja, RECURSOS["irmaos"]
    if request.POST.get("todos") == "1" and "confirmar" not in request.POST:
        escolhidos = recurso.filtrada(request, request.POST)[0]
    else:
        ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
        escolhidos = Irmao.objects.filter(loja=loja, pk__in=ids)
    escolhidos = escolhidos.order_by("nome")
    total = escolhidos.count()
    if not total:
        messages.error(request, "Nenhum Irmão foi selecionado.")
        return redirect("irmaos_lista")
    com_financeiro = escolhidos.filter(lancamentos__isnull=False).distinct().count()
    pode_financeiro = request.user.pode("tesouraria")
    erro = ""
    if "confirmar" in request.POST:
        if request.POST.get("palavra", "").strip().upper() != "EXCLUIR":
            erro = "Para confirmar, digite a palavra EXCLUIR no campo."
        else:
            apagar_financeiro = pode_financeiro and request.POST.get("financeiro") == "1"
            excluidos = mantidos = 0
            with transaction.atomic():
                for irmao in escolhidos:
                    if irmao.lancamentos.exists():
                        if not apagar_financeiro:
                            mantidos += 1
                            continue
                        irmao.lancamentos.all().delete()  # leva junto os boletos desses lançamentos
                    irmao.delete()
                    excluidos += 1
            if excluidos:
                messages.success(request, f"{excluidos} Irmão(s) excluído(s), com seus dependentes, graus e presenças.")
            if mantidos:
                messages.error(request, f"{mantidos} Irmão(s) não foram excluídos porque têm lançamentos na Tesouraria.")
            return redirect("irmaos_lista")
    return render(request, "gestao/excluir_varios.html", {
        "titulo": f"Excluir {total} Irmão(s)?", "acao": reverse("irmaos_excluir_varios"), "voltar": reverse("irmaos_lista"),
        "descricao": "Junto com cada Irmão são apagados os dependentes, o histórico de graus, as presenças, as licenças e os "
                     "certificados de visita dele. O usuário de acesso continua existindo, mas deixa de estar ligado ao cadastro.",
        "modulo": "secretaria", "total": total, "amostra": [i.nome for i in escolhidos[:12]],
        "ids": list(escolhidos.values_list("pk", flat=True)),
        "com_financeiro": com_financeiro, "pode_financeiro": pode_financeiro, "erro": erro,
        "financeiro_marcado": request.POST.get("financeiro") == "1"})


@exige("secretaria")
def importar_irmaos(request):
    """Importa o cadastro de Irmãos de uma planilha do Excel: envia, confere na tela e só então grava."""
    loja, analise = request.loja, None
    guardada = request.session.get("importacao")
    if guardada and guardada.get("loja") != loja.pk:
        guardada = None
    if request.method == "POST":
        acao = request.POST.get("acao")
        if acao == "cancelar":
            request.session.pop("importacao", None)
            return redirect("importar_irmaos")
        if acao == "gravar":
            if not guardada:
                messages.error(request, "A conferência expirou. Envie a planilha de novo.")
                return redirect("importar_irmaos")
            criados, atualizados = importacao.gravar(loja, guardada["analise"])
            request.session.pop("importacao", None)
            request.session["importacao_salva"] = {"loja": loja.pk, "arquivo": guardada.get("arquivo"), "criados": criados,
                                                   "atualizados": atualizados, "recusados": len(guardada["analise"]["erros"])}
            messages.success(request, f"Importação concluída: {criados} Irmão(s) incluído(s) e {atualizados} atualizado(s).")
            return redirect("importar_irmaos")
        arquivo = request.FILES.get("arquivo")
        if not arquivo:
            messages.error(request, "Escolha a planilha antes de enviar.")
        elif not arquivo.name.lower().endswith(".xlsx"):
            messages.error(request, "Envie um arquivo do Excel com final .xlsx. Se a sua planilha é .xls ou .csv, "
                                    "abra no Excel e use Arquivo › Salvar como › Pasta de Trabalho do Excel (.xlsx).")
        elif arquivo.size > importacao.MAX_BYTES:
            messages.error(request, "A planilha passa de 5 MB. Remova imagens ou divida em arquivos menores.")
        else:
            try:
                analise = importacao.analisar(loja, arquivo)
            except importacao.ErroDePlanilha as erro:
                messages.error(request, str(erro))
            else:
                request.session["importacao"] = {"loja": loja.pk, "arquivo": arquivo.name[:120], "analise": analise}
                return redirect("importar_irmaos")
    if guardada:
        analise = guardada["analise"]
    salva = request.session.pop("importacao_salva", None) if not guardada else None
    if salva and salva.get("loja") != loja.pk:
        salva = None
    return render(request, "gestao/importar.html", {
        "salva": salva, "etapa": 3 if salva else (2 if analise else 1),
        "modulo": "secretaria", "analise": analise, "arquivo_nome": guardada and guardada.get("arquivo"),
        "amostra": analise and [{**r, "nascimento": "/".join(reversed(r["dados"]["data_nascimento"].split("-")))
                                 if r["dados"].get("data_nascimento") else ""} for r in analise["registros"][:15]]})


@exige("secretaria")
def importar_modelo(request):
    resposta = HttpResponse(importacao.planilha_modelo(request.loja),
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resposta["Content-Disposition"] = 'attachment; filename="modelo-cadastro-irmaos.xlsx"'
    return resposta


# =============================================================================
# Chancelaria
# =============================================================================
def _resumo_presenca(sessao):
    contagem = dict(sessao.presencas.values_list("status").annotate(n=Count("id")))
    return {s: contagem.get(s, 0) for s in "PFAL"}


@exige("chancelaria")
def sessao_presenca(request, pk):
    sessao = get_object_or_404(Sessao, pk=pk, loja=request.loja)
    if request.method == "POST":
        acao = request.POST.get("acao")
        if acao == "todos":
            sessao.presencas.filter(status="F").update(status="P")
            messages.success(request, "Todos os Irmãos sem licença foram marcados como presentes.")
        elif acao == "atualizar":
            messages.success(request, f"{servicos.sincronizar_presencas(sessao)} Irmão(s) acrescentado(s) à lista.")
        return redirect("sessao_presenca", pk=sessao.pk)
    return render(request, "gestao/presenca.html", {
        "sessao": sessao, "modulo": "chancelaria", "resumo": _resumo_presenca(sessao),
        "presencas": sessao.presencas.select_related("irmao__grau")})


@exige("chancelaria")
@require_POST
def presenca_marcar(request, pk):
    """Um clique: grava o novo status de um Irmão na sessão e devolve os totais."""
    presenca = get_object_or_404(Presenca, pk=pk, loja=request.loja)
    status = request.POST.get("status")
    if status not in dict(Presenca.STATUS):
        return JsonResponse({"erro": "Situação inválida."}, status=400)
    presenca.status = status
    presenca.justificativa = request.POST.get("justificativa", "")[:200] if status == "A" else ""
    presenca.save(update_fields=["status", "justificativa"])
    return JsonResponse({"status": status, "rotulo": presenca.get_status_display(),
                         "justificativa": presenca.justificativa, "resumo": _resumo_presenca(presenca.sessao)})


# =============================================================================
# Tesouraria
# =============================================================================
def _lancamento(request, pk):
    return get_object_or_404(Lancamento.objects.select_related("irmao", "plano"), pk=pk, loja=request.loja)


def _lista_de(lancamento):
    return "receber_lista" if lancamento.tipo == "R" else "pagar_lista"


@exige("tesouraria")
def lancamento_quitar(request, pk):
    l = _lancamento(request, pk)
    if l.status != "aberto":
        messages.error(request, "Este lançamento não está em aberto.")
        return redirect(_lista_de(l))
    form = QuitarForm(request.POST or None, loja=request.loja,
                      initial={"valor": l.valor, "conta": l.conta_id or ContaBancaria.objects.filter(loja=request.loja, ativa=True).first()})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        servicos.quitar(l, d["data"], d["valor"], d["conta"])
        messages.success(request, f"Quitação registrada: {l.descricao} — {formatar(d['valor'])}.")
        return redirect(_lista_de(l))
    return render(request, "gestao/form.html", {
        "form": form, "modulo": "tesouraria", "titulo": "Registrar quitação", "voltar": reverse(_lista_de(l)),
        "resumo": [("Lançamento", l.descricao), ("De / para", l.quem), ("Vencimento", l.vencimento), ("Valor", l.valor)],
        "botao": "Confirmar quitação"})


def _confirmar(request, titulo, texto, voltar, botao="Confirmar", modulo="tesouraria"):
    return render(request, "gestao/confirmar.html", {"titulo": titulo, "texto": texto, "voltar": voltar, "botao": botao,
                                                     "modulo": modulo})


@exige("tesouraria")
def lancamento_estornar(request, pk):
    l = _lancamento(request, pk)
    if request.method == "POST" and l.status == "pago":
        servicos.estornar(l)
        messages.success(request, "Quitação estornada. O lançamento voltou a ficar em aberto.")
        return redirect(_lista_de(l))
    return _confirmar(request, "Estornar quitação", f"Desfazer a quitação de “{l.descricao}” ({l.quem}, {formatar(l.valor_pago)})? "
                      "O valor sai do saldo da conta e o lançamento volta a ficar em aberto.", reverse(_lista_de(l)), "Estornar")


@exige("tesouraria")
def lancamento_cancelar(request, pk):
    l = _lancamento(request, pk)
    if request.method == "POST" and l.status == "aberto":
        l.status = "cancelado"
        l.save(update_fields=["status"])
        Boleto.objects.filter(lancamento=l).exclude(status="pago").update(status="baixado")
        messages.success(request, "Lançamento cancelado.")
        return redirect(_lista_de(l))
    return _confirmar(request, "Cancelar lançamento", f"Cancelar “{l.descricao}” ({l.quem}, {formatar(l.valor)})? "
                      "Ele deixa de aparecer como pendente, mas continua no histórico.", reverse(_lista_de(l)), "Cancelar lançamento")


@exige("tesouraria")
def mensalidades_serie(request):
    loja, hoje = request.loja, timezone.localdate()
    ativos = Irmao.objects.filter(loja=loja, situacao="ativo")
    ja_remidos = Irmao.objects.filter(loja=loja, situacao="remido")
    form = MensalidadesForm(request.POST or None, loja=loja, initial={
        "valor": loja.valor_mensalidade or None, "irmaos": list(ativos.values_list("pk", flat=True)),
        "remidos": list(ja_remidos.values_list("pk", flat=True)),
        "primeiro_vencimento": servicos.somar_meses(hoje, 1, loja.dia_vencimento)})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        marcados = {i.pk for i in d["remidos"]}
        novos_remidos = Irmao.objects.filter(loja=loja, pk__in=marcados).exclude(situacao="remido").update(situacao="remido")
        deixaram = ja_remidos.exclude(pk__in=marcados).update(situacao="ativo")
        pagantes = list(Irmao.objects.filter(loja=loja, pk__in=[i.pk for i in d["irmaos"]]))  # já com a situação atualizada
        criados, pulados = servicos.gerar_mensalidades(loja, pagantes, d["plano"], d["valor"],
                                                       d["primeiro_vencimento"], d["meses"], d["descricao"])
        aviso = f" {pulados} já existiam e foram mantidos como estavam." if pulados else ""
        if d["plano"].eh_remido:
            messages.success(request, f"{criados} registro(s) de isenção (“{d['descricao']}”, sem valor a pagar) lançado(s) "
                                      f"para {len(pagantes)} Remido(s).{aviso}")
            return redirect("receber_lista")
        isentos = f" {len(marcados)} remido(s) ficaram isentos." if marcados else ""
        mudancas = (f" {novos_remidos} Irmão(s) passaram a Remido" if novos_remidos else "") + (
            f"{';' if novos_remidos else ''} {deixaram} deixaram de ser Remido" if deixaram else "")
        messages.success(request, f"{criados} mensalidade(s) lançada(s) para {len(d['irmaos'])} Irmão(s).{isentos}{aviso}"
                                  + (f"{mudancas} no cadastro." if mudancas else ""))
        return redirect("receber_lista")
    return render(request, "gestao/form.html", {
        "form": form, "modulo": "tesouraria", "titulo": "Mensalidades em série", "voltar": reverse("receber_lista"),
        "ajuda": "Lança de uma vez as mensalidades de vários Irmãos para vários meses. Os Irmãos marcados como "
                 "Remidos ficam isentos. Se um Irmão já tiver mensalidade lançada para algum mês, ela não é duplicada.",
        "botao": "Lançar mensalidades", "script_remidos": True})


def _contas_boleto(loja):
    return [c for c in ContaBancaria.objects.filter(loja=loja, ativa=True) if c.emite_boleto]


@exige("tesouraria")
def boleto_gerar(request, pk):
    l = _lancamento(request, pk)
    existente = Boleto.objects.filter(lancamento=l).first()
    if existente:
        return redirect("boleto_ver", pk=existente.pk)
    contas = _contas_boleto(request.loja)
    if l.tipo != "R" or l.status != "aberto":
        messages.error(request, "Só é possível gerar boleto de contas a receber em aberto.")
        return redirect("receber_lista")
    if not contas:
        messages.error(request, "Nenhuma conta está pronta para emitir boletos. Em “Contas e caixas”, preencha "
                                "código do banco, agência e número da conta.")
        return redirect("contas_lista")
    if request.method == "POST":
        conta = next((c for c in contas if str(c.pk) == request.POST.get("conta")), contas[0])
        return redirect("boleto_ver", pk=boletos_servico.emitir(l, conta).pk)
    return render(request, "gestao/confirmar.html", {
        "titulo": "Gerar boleto", "modulo": "tesouraria", "voltar": reverse("receber_lista"), "botao": "Gerar boleto",
        "texto": f"Gerar boleto de {formatar(l.valor)} para {l.quem}, com vencimento em {formatar(l.vencimento)}?",
        "contas": contas})


@com_loja
def boleto_ver(request, pk):
    b = get_object_or_404(Boleto.objects.select_related("lancamento__irmao", "conta"), pk=pk, loja=request.loja)
    dono = request.user.irmao_id is not None and request.user.irmao_id == b.lancamento.irmao_id
    if not (request.user.pode("tesouraria") or dono):
        raise PermissionDenied
    x, barras = 0, []
    for preta, largura in boletos_servico.barras_para_desenho(b.codigo_barras):
        if preta:
            barras.append((x, largura))
        x += largura
    return render(request, "gestao/boleto.html", {"b": b, "l": b.lancamento, "barras": barras, "largura_total": x,
                                                  "modulo": "tesouraria" if request.user.pode("tesouraria") else ""})


@exige("tesouraria")
def importar_financeiro(request):
    """Importa a planilha financeira da Loja em três etapas: escolher o arquivo, verificar e salvar."""
    loja = request.loja
    guardada = request.session.get("importacao_fin")
    if guardada and guardada.get("loja") != loja.pk:
        guardada = None
    form = ImportarFinanceiroForm(loja=loja, initial={"conta": ContaBancaria.objects.filter(loja=loja, ativa=True).first()})
    if request.method == "POST":
        acao = request.POST.get("acao")
        if acao == "cancelar":
            request.session.pop("importacao_fin", None)
            return redirect("importar_financeiro")
        if acao == "gravar":
            if not guardada:
                messages.error(request, "A verificação expirou. Escolha o arquivo de novo.")
                return redirect("importar_financeiro")
            conta = ContaBancaria.objects.filter(loja=loja, pk=guardada["conta"]).first()
            plano = PlanoConta.objects.filter(loja=loja, codigo="1.1").first()
            if conta is None or plano is None:
                messages.error(request, "Falta a conta ou a conta 1.1 Mensalidades no plano de contas.")
                return redirect("importar_financeiro")
            plano_tronco = PlanoConta.objects.filter(loja=loja, codigo="1.3").first() if guardada["tronco"] else None
            resultado = importacao_financeira.gravar(loja, guardada["analise"], conta, plano, plano_tronco)
            request.session.pop("importacao_fin", None)
            request.session["importacao_fin_salva"] = {"loja": loja.pk, "arquivo": guardada["arquivo"],
                                                       "ano": guardada["analise"]["ano"], "resultado": resultado}
            messages.success(request, "Planilha financeira salva no sistema.")
            return redirect("importar_financeiro")
        form = ImportarFinanceiroForm(request.POST, request.FILES, loja=loja)
        if form.is_valid():
            d = form.cleaned_data
            try:
                analise = importacao_financeira.analisar(loja, d["arquivo"], d["ano"], d["em_branco"], d["incluir_novos"])
            except importacao.ErroDePlanilha as erro:
                messages.error(request, str(erro))
            else:
                request.session["importacao_fin"] = {"loja": loja.pk, "arquivo": d["arquivo"].name[:120], "analise": analise,
                                                     "conta": d["conta"].pk, "conta_nome": str(d["conta"]),
                                                     "tronco": d["importar_tronco"]}
                return redirect("importar_financeiro")
    salva = request.session.pop("importacao_fin_salva", None) if not guardada else None
    if salva and salva.get("loja") != loja.pk:
        salva = None
    analise = guardada["analise"] if guardada else None
    linhas = []
    if analise:
        siglas = {"pago": ("P", "ok"), "aberto": ("A", "ruim"), "acordo": ("Ac", "atencao"), "isento": ("R", ""),
                  "nao_se_aplica": ("—", "")}
        for o in analise["obreiros"]:
            curtos = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
            linhas.append({**o, "grade": [(*siglas.get((o["meses"].get(str(m)) or {}).get("situacao"), ("", "")), curtos[m - 1])
                                          for m in range(1, 13)]})
    return render(request, "gestao/importar_financeiro.html", {
        "modulo": "tesouraria", "form": form, "analise": analise, "guardada": guardada, "linhas": linhas, "salva": salva,
        "etapa": 3 if salva else (2 if analise else 1), "ano_atual": timezone.localdate().year,
        "meses_curtos": ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]})


@exige("tesouraria")
def planilha_financeira(request):
    try:
        ano = int(request.GET.get("ano") or timezone.localdate().year)
    except ValueError:
        ano = timezone.localdate().year
    resposta = HttpResponse(importacao_financeira.planilha_da_loja(request.loja, ano),
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resposta["Content-Disposition"] = f'attachment; filename="controle-financeiro-{ano}.xlsx"'
    return resposta


@exige("tesouraria")
def boletos(request):
    loja = request.loja
    contas = _contas_boleto(loja)
    if request.method == "POST":
        acao = request.POST.get("acao")
        conta = next((c for c in contas if str(c.pk) == request.POST.get("conta")), None)
        if acao in ("lote", "remessa") and conta is None:
            messages.error(request, "Escolha uma conta com os dados bancários preenchidos.")
        elif acao == "lote":
            pendentes = Lancamento.objects.filter(loja=loja, tipo="R", status="aberto", irmao__isnull=False, boleto__isnull=True)
            n = 0
            for l in pendentes:
                boletos_servico.emitir(l, conta)
                n += 1
            messages.success(request, f"{n} boleto(s) gerado(s)." if n else "Não havia contas a receber sem boleto.")
        elif acao == "remessa":
            remessa = boletos_servico.gerar_remessa(loja, conta)
            if remessa:
                messages.success(request, f"Remessa {remessa.numero} gerada com {remessa.quantidade} boleto(s). "
                                          "Baixe o arquivo abaixo e envie pelo internet banking.")
            else:
                messages.error(request, "Não há boletos aguardando remessa nessa conta.")
        elif acao == "retorno":
            arquivo = request.FILES.get("arquivo")
            if not arquivo or arquivo.size > 5 * 1024 * 1024:
                messages.error(request, "Escolha o arquivo de retorno que o banco forneceu (até 5 MB).")
            else:
                texto = arquivo.read().decode("latin-1", errors="replace")
                if not boletos_servico.ler_retorno(texto):
                    messages.error(request, "Não encontrei boletos nesse arquivo. Confira se é o retorno no padrão CNAB 240.")
                else:
                    r = boletos_servico.processar_retorno(loja, arquivo.name, texto)
                    messages.success(request, f"Retorno processado: {r.quitados} quitado(s) ({formatar(r.valor_total)}), "
                                              f"{r.registrados} registrado(s), {r.nao_encontrados} não encontrado(s).")
        return redirect("boletos")
    return render(request, "gestao/boletos.html", {
        "modulo": "tesouraria", "contas": contas,
        "lista": Boleto.objects.filter(loja=loja).select_related("lancamento__irmao", "remessa")[:200],
        "remessas": Remessa.objects.filter(loja=loja).select_related("conta")[:20],
        "retornos": Retorno.objects.filter(loja=loja)[:20],
        "sem_boleto": Lancamento.objects.filter(loja=loja, tipo="R", status="aberto", irmao__isnull=False, boleto__isnull=True).count(),
        "aguardando": Boleto.objects.filter(loja=loja, status="gerado", lancamento__status="aberto").count()})


@exige("tesouraria")
def remessa_baixar(request, pk):
    remessa = get_object_or_404(Remessa, pk=pk, loja=request.loja)
    resposta = HttpResponse(remessa.conteudo.encode("latin-1", errors="replace"), content_type="text/plain; charset=iso-8859-1")
    resposta["Content-Disposition"] = f'attachment; filename="remessa_{remessa.numero:06d}.rem"'
    return resposta


# =============================================================================
# Hospitalaria
# =============================================================================
@exige("hospitalaria")
def emprestimo_devolver(request, pk):
    e = get_object_or_404(Emprestimo.objects.select_related("bem"), pk=pk, loja=request.loja)
    if request.method == "POST" and e.data_devolucao is None:
        e.data_devolucao = timezone.localdate()
        e.save(update_fields=["data_devolucao"])
        messages.success(request, f"Devolução registrada: {e.bem} está disponível novamente.")
        return redirect("emprestimos_lista")
    return _confirmar(request, "Registrar devolução", f"Confirmar que “{e.bem}” foi devolvido por {e.beneficiario} hoje?",
                      reverse("emprestimos_lista"), "Registrar devolução", "hospitalaria")


# =============================================================================
# Relatórios
# =============================================================================
@com_loja
def relatorios(request):
    grupos = [(MODULOS[m], [r for r in RELATORIOS.values() if r["modulo"] == m]) for m in request.user.modulos]
    return render(request, "gestao/relatorios.html", {"grupos": grupos, "modulo": "relatorios"})


@com_loja
def relatorio(request, slug):
    definicao = RELATORIOS.get(slug)
    if definicao is None:
        raise Http404
    if not request.user.pode(definicao["modulo"]):
        raise PermissionDenied
    parametros = {k: v for k, v in request.GET.items() if k != "formato"}
    form = FiltroForm(parametros, request.loja, definicao["filtros"])
    resultado = definicao["funcao"](request.loja, form.valores())
    if request.GET.get("formato") == "csv":
        resposta = HttpResponse(content_type="text/csv; charset=utf-8")
        resposta["Content-Disposition"] = f'attachment; filename="{slug}.csv"'
        resposta.write("﻿")  # faz o Excel entender os acentos
        planilha = csv.writer(resposta, delimiter=";")
        planilha.writerow(resultado["colunas"])
        for linha in resultado["linhas"]:
            planilha.writerow([f"{c:.2f}".replace(".", ",") if isinstance(c, Decimal) else
                               ("" if c is None else str(formatar(c)).strip()) for c in linha])
        return resposta
    consulta = request.GET.copy()
    consulta["formato"] = "csv"
    return render(request, "gestao/relatorio.html", {"r": definicao, "form": form, "resultado": resultado,
                                                     "modulo": "relatorios", "link_csv": "?" + consulta.urlencode(),
                                                     "emitido_em": timezone.localtime()})


# =============================================================================
# Notificações
# =============================================================================
SERVICOS_PUSH = ("fcm.googleapis.com", ".push.services.mozilla.com", ".notify.windows.com", ".push.apple.com")


@com_loja
def notificacoes(request):
    lista = list(request.user.notificacoes.filter(loja=request.loja)[:60])
    request.user.notificacoes.filter(loja=request.loja, lida=False).update(lida=True, entregue_push=True)
    return render(request, "gestao/notificacoes.html", {"lista": lista, "modulo": "notificacoes",
                                                        "push_configurado": bool(settings.PUSH_CHAVE_PUBLICA)})


@login_required
@require_POST
def push_inscrever(request):
    try:
        endpoint = json.loads(request.body)["endpoint"]
        destino = urlparse(endpoint)
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"erro": "Pedido inválido."}, status=400)
    host = destino.hostname or ""
    # Só aceitamos endereços dos serviços oficiais de notificação (evita que alguém
    # use o sistema para "tocar" endereços internos).
    if destino.scheme != "https" or len(endpoint) > 600 or not any(
            host == s or (s.startswith(".") and host.endswith(s)) for s in SERVICOS_PUSH):
        return JsonResponse({"erro": "Serviço de notificação não reconhecido."}, status=400)
    InscricaoPush.objects.update_or_create(endpoint=endpoint, defaults={"usuario": request.user})
    return JsonResponse({"ok": True})


@login_required
def push_pendentes(request):
    """O celular chama aqui ao receber o toque, para saber o que mostrar."""
    with transaction.atomic():
        pendentes = list(request.user.notificacoes.select_for_update().filter(entregue_push=False, lida=False)[:5])
        Notificacao.objects.filter(pk__in=[n.pk for n in pendentes]).update(entregue_push=True)
    return JsonResponse({"avisos": [{"titulo": n.titulo, "texto": n.texto} for n in pendentes]})


def service_worker(request):
    resposta = render(request, "gestao/sw.js", content_type="application/javascript")
    resposta["Cache-Control"] = "no-cache"
    return resposta


def manifesto(request):
    loja = getattr(request, "loja", None)
    nome = loja.nome if loja else "Gestão da Loja"
    return JsonResponse({
        "name": nome, "short_name": nome[:12], "start_url": "/", "display": "standalone", "lang": "pt-BR",
        "background_color": "#F4F5F7", "theme_color": loja.cor_primaria if loja else "#1F3A5F",
        "icons": [{"src": static(f"gestao/icone-{t}.png"), "sizes": f"{t}x{t}", "type": "image/png"}
                  for t in (192, 512)]},
        content_type="application/manifest+json")


# =============================================================================
# Administração da Loja (usuário Master)
# =============================================================================
@exige("admin")
def personalizar(request):
    form = PersonalizarForm(request.POST or None, request.FILES or None, instance=request.loja)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Personalização salva. As cores e o brasão já valem para todos os usuários da Loja.")
        return redirect("personalizar")
    return render(request, "gestao/form.html", {
        "form": form, "modulo": "admin", "titulo": "Personalizar a Loja", "voltar": reverse("inicio"),
        "ajuda": "Nome, brasão, cores e regras desta Loja. Só os usuários desta Loja veem estas configurações.",
        "botao": "Salvar personalização"})


@exige("admin")
def usuarios_criar_todos(request):
    loja = request.loja
    sem_acesso = Irmao.objects.filter(loja=loja, situacao__in=Irmao.NO_QUADRO, usuario__isnull=True)
    if request.method == "POST":
        criados, sem_email, repetidos = 0, 0, 0
        for irmao in sem_acesso:
            email = irmao.email.strip().lower()
            if not email:
                sem_email += 1
            elif Usuario.objects.filter(username=email).exists():
                repetidos += 1
            else:
                partes = irmao.nome.split(" ", 1)
                usuario = Usuario(username=email, email=email, loja=loja, irmao=irmao, first_name=partes[0][:150],
                                  last_name=(partes[1] if len(partes) > 1 else "")[:150])
                usuario.set_password(secrets.token_urlsafe(24))  # ninguém sabe; o Irmão define a dele pelo "Esqueci minha senha"
                usuario.save()
                criados += 1
        messages.success(request, f"{criados} usuário(s) criado(s). O login de cada um é o próprio e-mail; "
                                  "peça que cliquem em “Esqueci minha senha” na tela de entrada para definir a senha.")
        if sem_email or repetidos:
            messages.error(request, f"Ficaram de fora: {sem_email} sem e-mail cadastrado e {repetidos} com e-mail já usado por outro usuário.")
        return redirect("usuarios_lista")
    return _confirmar(request, "Criar acesso para todos os Irmãos",
                      f"Criar um usuário para cada um dos {sem_acesso.count()} Irmão(s) do quadro que ainda não têm acesso? "
                      "O login será o e-mail do cadastro. Eles entram apenas com acesso aos próprios dados; "
                      "os acessos de cargo você marca depois, um a um.", reverse("usuarios_lista"), "Criar usuários", "admin")


# =============================================================================
# Plataforma (várias Lojas)
# =============================================================================
@so_plataforma
def plataforma_lojas(request):
    if request.method == "POST":
        loja = get_object_or_404(Loja, pk=request.POST.get("loja"))
        acao = request.POST.get("acao")
        if acao == "entrar":
            request.session["loja_id"] = loja.pk
            return redirect("inicio")
        if acao == "alternar":
            loja.ativa = not loja.ativa
            loja.save(update_fields=["ativa"])
            messages.success(request, f"{loja}: {'liberada' if loja.ativa else 'bloqueada — os usuários dela não conseguem mais entrar'}.")
        return redirect("plataforma_lojas")
    lojas = Loja.objects.annotate(n_usuarios=Count("usuarios", distinct=True))
    for loja in lojas:
        loja.n_irmaos = Irmao.objects.filter(loja=loja).count()
    return render(request, "gestao/plataforma.html", {"lojas": lojas, "modulo": "plataforma"})


@so_plataforma
def plataforma_nova_loja(request):
    form = NovaLojaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            loja = form.save()
            servicos.preparar_loja(loja)
            d = form.cleaned_data
            partes = d["master_nome"].split(" ", 1)
            Usuario.objects.create_user(username=d["master_login"], email=d["master_email"], password=d["master_senha"],
                                        loja=loja, is_master=True, first_name=partes[0],
                                        last_name=partes[1] if len(partes) > 1 else "")
        messages.success(request, f"{loja} criada, já com graus, plano de contas e o usuário Master “{d['master_login']}”.")
        return redirect("plataforma_lojas")
    return render(request, "gestao/form.html", {"form": form, "modulo": "plataforma", "titulo": "Nova Loja",
                                                "voltar": reverse("plataforma_lojas"), "botao": "Criar Loja",
                                                "ajuda": "Cada Loja tem seus próprios dados, usuários e personalização. "
                                                         "Uma Loja nunca enxerga os dados de outra."})


# =============================================================================
# Arquivos enviados (atas, fotos, brasão)
# =============================================================================
@com_loja
def arquivo(request, caminho):
    """Entrega um arquivo só para quem é da mesma Loja (e, no caso das atas, só para a Secretaria)."""
    partes = Path(caminho).parts
    if not partes or partes[0] != f"loja_{request.loja.pk}" or ".." in partes:
        raise Http404
    if len(partes) > 1 and partes[1] in ("atas", "retornos") and not request.user.pode("secretaria"):
        raise PermissionDenied
    completo = (settings.MEDIA_ROOT / caminho).resolve()
    if not completo.is_file() or settings.MEDIA_ROOT.resolve() not in completo.parents:
        raise Http404
    tipo = mimetypes.guess_type(completo.name)[0] or "application/octet-stream"
    mostrar_na_tela = tipo.startswith("image/") and tipo != "image/svg+xml" or tipo == "application/pdf"
    resposta = FileResponse(open(completo, "rb"), content_type=tipo, as_attachment=not mostrar_na_tela)
    resposta["X-Content-Type-Options"] = "nosniff"
    return resposta
