"""Importação da planilha financeira da Loja (controle de mensalidades, centavos e tronco).

Segue o formato que a Tesouraria já usa:
  - aba de mensalidades: Centavos | Nome do Obreiro | (CIM) | Cotizante | R$ | JANEIRO ... DEZEMBRO,
    com PAGO, REMIDO, Acordo ou XXX-XXX em cada mês;
  - aba de centavos: CIM | NOME | CENTAVOS (o valor que identifica cada Irmão no pagamento);
  - aba do tronco de beneficência: um mês por linha com o valor arrecadado.
As abas são reconhecidas pelos títulos, não pelo nome. Abas sem esse formato são ignoradas e avisadas.

Como na importação de Irmãos: primeiro só lê e confere; grava apenas quando a pessoa confirma.
"""
import calendar
import difflib
import io
import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import servicos
from .importacao import ErroDePlanilha, chave
from .models import ContaBancaria, Irmao, Lancamento, PlanoConta

MAX_BYTES = 5 * 1024 * 1024
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]
PREFIXOS_MES = {chave(m)[:3]: i for i, m in enumerate(MESES, 1)}
PARTICULAS = {"de", "da", "do", "dos", "das", "e", "d"}
SITUACOES_MES = {"pago": "Pago", "aberto": "Em aberto", "acordo": "Acordo", "isento": "Remido / isento",
                 "nao_se_aplica": "Não se aplica (XXX)"}


# ----------------------------------------------------------------- utilidades
def _texto(valor):
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return re.sub(r"\s+", " ", str(valor)).strip()


def _centavos(valor):
    """Só aceita centavos de 0,01 a 0,99 (0,00 ou vazio = Irmão sem centavos de identificação)."""
    numero = _decimal(valor)
    return numero if numero is not None and Decimal("0.01") <= numero <= Decimal("0.99") else None


def _decimal(valor):
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float, Decimal)):
        return Decimal(str(round(float(valor), 2)))
    texto = _texto(valor).replace("R$", "").replace(" ", "")
    if not texto:
        return None
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return Decimal(texto).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def mes_do_titulo(valor):
    """'JANEIRO', 'jan', 'Jan.', e até 'agosot' (erro de digitação) -> número do mês; qualquer outra coisa -> None."""
    k = chave(valor)
    if len(k) < 3 or len(k) > 10:
        return None
    for numero, mes in enumerate(MESES, 1):
        completo = chave(mes)
        if k == completo or k == completo[:3] or (
                k[:3] == completo[:3] and abs(len(k) - len(completo)) <= 1
                and difflib.SequenceMatcher(None, k, completo).ratio() >= 0.8):
            return numero
    return None


def limpar_nome(valor):
    """'  FRANCISCO JULIANI   (3629)' -> 'FRANCISCO JULIANI'; tira números de outras Lojas do fim do nome."""
    texto = re.sub(r"\(.*?\)?$", "", _texto(valor)).strip()
    texto = re.sub(r"\(.*?\)", "", texto)
    palavras = texto.split()
    while palavras and re.fullmatch(r"[\d()./-]+", palavras[-1]):
        palavras.pop()
    return " ".join(palavras)


def nome_bonito(nome):
    """'CARLOS ROBERTO DE CAMPOS' -> 'Carlos Roberto de Campos' (só quando veio todo em maiúsculas)."""
    if nome != nome.upper():
        return nome
    return " ".join(p.lower() if p.lower() in PARTICULAS else p.capitalize() for p in nome.split())


def _tokens(nome):
    trocas = {"jr": "junior", "jnr": "junior", "fo": "filho"}
    saida = []
    for parte in re.split(r"[\s.]+", nome):
        k = chave(parte)
        if k and k not in PARTICULAS:
            saida.append(trocas.get(k, k))
    return saida


def semelhanca(a, b):
    """0 a 3: 3 = mesmo nome; 2 = mesmo primeiro e último nome e o resto compatível (aceita abreviação); 1 = muito parecido."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0
    if ta == tb:
        return 3
    curto, longo = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if curto[0] == longo[0] and curto[-1] == longo[-1]:
        restantes = list(longo[1:-1])
        for t in curto[1:-1]:
            achado = next((x for x in restantes if x == t or (len(t) == 1 and x.startswith(t))), None)
            if achado is None:
                break
            restantes.remove(achado)
        else:
            return 2
    if difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio() >= 0.9:
        return 1
    return 0


def achar_irmao(nome, irmaos, cim=""):
    """Devolve (irmão, aviso). Procura pelo CIM e depois pelo nome; recusa quando há empate."""
    if cim:
        por_cim = [i for i in irmaos if chave(i.cim) == chave(cim)]
        if len(por_cim) == 1:
            return por_cim[0], ""
    notas = sorted(((semelhanca(nome, i.nome), i) for i in irmaos), key=lambda x: -x[0])
    if not notas or notas[0][0] == 0:
        return None, ""
    melhores = [i for n, i in notas if n == notas[0][0]]
    if len(melhores) > 1:
        return None, "nome parecido com mais de um Irmão: " + ", ".join(i.nome for i in melhores[:3])
    return melhores[0], ("" if notas[0][0] >= 2 else f"nome parecido, conferir: “{melhores[0].nome}”")


def situacao_do_mes(valor):
    """Interpreta a célula de um mês. Devolve (situação, valor pago ou None, data ou None) ou levanta ValueError."""
    if isinstance(valor, datetime):
        return "pago", None, valor.date()
    if isinstance(valor, date):
        return "pago", None, valor
    if isinstance(valor, (int, float, Decimal)) and not isinstance(valor, bool):
        return ("pago", _decimal(valor), None) if valor else ("vazio", None, None)
    k = chave(valor)
    if not k:
        return "vazio", None, None
    if k in ("pago", "pg", "paga", "ok", "quitado", "quite", "pix", "recebido"):
        return "pago", None, None
    if k.startswith(("remido", "isento", "isenta", "remida")):
        return "isento", None, None
    if k.startswith("acordo"):
        return "acordo", None, None
    if set(k) <= {"x"} or k in ("na", "naoseaplica", "naose"):
        return "nao_se_aplica", None, None
    if k in ("aberto", "devendo", "pendente", "naopago", "emaberto"):
        return "aberto", None, None
    numero = _decimal(valor)
    if numero:
        return "pago", numero, None
    raise ValueError(f"“{_texto(valor)}” não reconhecido (use PAGO, REMIDO, Acordo ou XXX-XXX)")


# --------------------------------------------------------- leitura das abas
def _cabecalho(linhas, exige):
    """Acha, entre as 10 primeiras linhas, a que tem os títulos pedidos. Devolve (índice, {campo: coluna})."""
    for indice, valores in enumerate(linhas[:10]):
        colunas, meses = {}, {}
        for coluna, valor in enumerate(valores):
            k = chave(valor)
            if not k:
                continue
            mes = mes_do_titulo(valor)
            if mes and mes not in meses:
                meses[mes] = coluna
            elif "nome" in k and "nome" not in colunas:
                colunas["nome"] = coluna
            elif k.startswith("cotizante") and "cotizante" not in colunas:
                colunas["cotizante"] = coluna
            elif k in ("r", "rs", "valor", "mensalidade", "valormensal", "valordamensalidade") and "valor" not in colunas:
                colunas["valor"] = coluna
            elif k.startswith("centavo") and "centavos" not in colunas:
                colunas["centavos"] = coluna
            elif k in ("cim", "cadastro", "ncim", "nocim") and "cim" not in colunas:
                colunas["cim"] = coluna
        if exige == "mensalidade" and "nome" in colunas and len(meses) >= 6:
            return indice, colunas, meses
        if exige == "centavos" and {"nome", "cim", "centavos"} <= set(colunas) and len(meses) < 6:
            return indice, colunas, meses
    return None, None, None


def _valor(valores, coluna):
    return valores[coluna] if coluna is not None and coluna < len(valores) else None


def ler_planilha(arquivo):
    try:
        from openpyxl import load_workbook
        pasta = load_workbook(io.BytesIO(arquivo.read()), read_only=True, data_only=True)
    except Exception:
        raise ErroDePlanilha("Não consegui abrir o arquivo. Salve a planilha como “Pasta de Trabalho do Excel (.xlsx)” e envie de novo.")
    obreiros, centavos, tronco, usadas, ignoradas = [], [], [], [], []
    for aba in pasta.worksheets:
        linhas = [list(v) for v in aba.iter_rows(values_only=True)]
        indice, colunas, meses = _cabecalho(linhas, "mensalidade")
        if indice is not None:
            usadas.append(f"{aba.title} (mensalidades)")
            for numero, valores in enumerate(linhas[indice + 1:], indice + 2):
                nome = limpar_nome(_valor(valores, colunas["nome"]))
                if not nome or chave(nome) in ("livre", "total", "totais"):
                    continue
                obreiros.append({"aba": aba.title, "linha": numero, "nome": nome,
                                 "cim": _texto(_valor(valores, colunas.get("cim"))),
                                 "cotizante": _valor(valores, colunas.get("cotizante")),
                                 "valor": _valor(valores, colunas.get("valor")),
                                 "centavos": _valor(valores, colunas.get("centavos")),
                                 "meses": {m: _valor(valores, c) for m, c in meses.items()}})
            continue
        indice, colunas, _ = _cabecalho(linhas, "centavos")
        if indice is not None:
            usadas.append(f"{aba.title} (centavos)")
            for numero, valores in enumerate(linhas[indice + 1:], indice + 2):
                nome = limpar_nome(_valor(valores, colunas["nome"]))
                cents = _centavos(_valor(valores, colunas["centavos"]))
                if nome and chave(nome) != "livre" and cents is not None:
                    centavos.append({"linha": numero, "nome": nome, "cim": _texto(_valor(valores, colunas["cim"])),
                                     "centavos": cents})
            continue
        meses_achados = {}
        for valores in linhas:
            primeiro = next((v for v in valores if _texto(v)), None)
            mes = mes_do_titulo(primeiro) if primeiro is not None else None
            numeros = [v for v in valores if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)]
            if mes and numeros and mes not in meses_achados:
                meses_achados[mes] = _decimal(numeros[-1])
        if len(meses_achados) >= 6:
            usadas.append(f"{aba.title} (tronco)")
            tronco = [{"mes": m, "valor": v} for m, v in sorted(meses_achados.items())]
            continue
        ignoradas.append(aba.title)
    pasta.close()
    if not obreiros and not centavos and not tronco:
        raise ErroDePlanilha("Não encontrei nenhuma aba no formato esperado. A aba de mensalidades precisa ter a coluna "
                             "“Nome do Obreiro” e os meses (JANEIRO a DEZEMBRO) na linha de títulos. "
                             "Baixe a planilha no formato da Loja para ver o modelo.")
    return obreiros, centavos, tronco, usadas, ignoradas


# --------------------------------------------------------------- análise
def _sim_nao(valor):
    k = chave(valor)
    if k in ("sim", "s", "yes", "x", "cotizante", "acordo"):
        return True
    if k in ("nao", "n", "no"):
        return False
    return None


def meses_a_cobrar(ano, em_branco, hoje=None):
    """Até que mês uma célula vazia vira mensalidade em aberto."""
    hoje = hoje or timezone.localdate()
    if em_branco != "aberto" or ano > hoje.year:
        return 0
    return 12 if ano < hoje.year else hoje.month


def analisar(loja, arquivo, ano, em_branco="ignorar", incluir_novos=True):
    obreiros, tabela_centavos, tronco, usadas, ignoradas = ler_planilha(arquivo)
    irmaos = list(Irmao.objects.filter(loja=loja))
    ate_mes = meses_a_cobrar(ano, em_branco)
    por_cim_centavos = {chave(c["cim"]): c for c in tabela_centavos if c["cim"]}
    contagem, registros, vistos = Counter(), [], {}
    for o in obreiros:
        avisos = []
        cim = o["cim"]
        if not cim:  # a tabela de centavos costuma trazer o CIM: usa para achar o Irmão com segurança
            candidato = [c for c in tabela_centavos if semelhanca(c["nome"], o["nome"]) >= 2]
            cim = candidato[0]["cim"] if len(candidato) == 1 else ""
        irmao, aviso = achar_irmao(o["nome"], irmaos, cim)
        if aviso:
            avisos.append(aviso)
        if irmao and irmao.pk in vistos:
            avisos.append(f"o mesmo Irmão já aparece na linha {vistos[irmao.pk]}; esta linha foi ignorada")
            contagem["ignorados"] += 1
            registros.append({**_base(o), "acao": "ignorar", "avisos": avisos, "meses": {}})
            continue
        if irmao:
            vistos[irmao.pk] = o["linha"]
        valor_texto = chave(o["valor"])
        remido = valor_texto.startswith("remido") or valor_texto.startswith("isento")
        valor = None if remido else _decimal(o["valor"])
        cotizante = _sim_nao(o["cotizante"])
        if o["cotizante"] not in (None, "") and cotizante is None:
            avisos.append(f"cotizante “{_texto(o['cotizante'])}” não reconhecido; mantido como está")
        cents = _centavos(o["centavos"])
        if cim and chave(cim) in por_cim_centavos:
            cents = por_cim_centavos[chave(cim)]["centavos"]
        meses = {}
        for mes, celula in o["meses"].items():
            try:
                situacao, pago, quando = situacao_do_mes(celula)
            except ValueError as erro:
                avisos.append(f"{MESES[mes - 1]}: {erro}; mês ignorado")
                continue
            if situacao == "vazio":
                if remido or cotizante is False and not valor or mes > ate_mes:
                    continue
                situacao = "aberto"
            if remido and situacao in ("aberto", "acordo"):
                continue
            meses[str(mes)] = {"situacao": situacao, "valor": str(pago) if pago else None,
                               "data": quando.isoformat() if quando else None}
            contagem[situacao] += 1
        acao = "vincular" if irmao else ("novo" if incluir_novos else "ignorar")
        if acao == "novo":
            contagem["novos"] += 1
        elif acao == "ignorar":
            contagem["ignorados"] += 1
            avisos.append("não encontrado no cadastro de Irmãos; linha ignorada")
        registros.append({**_base(o), "acao": acao, "irmao_id": irmao.pk if irmao else None,
                          "irmao_nome": irmao.nome if irmao else nome_bonito(o["nome"]), "cim": cim,
                          "cotizante": cotizante, "valor": str(valor) if valor else None, "remido": remido,
                          "centavos": str(cents) if cents is not None else None, "meses": meses, "avisos": avisos})
    # centavos de quem está só na tabela de centavos
    centavos_soltos = []
    ja = {r.get("irmao_id") for r in registros}
    for c in tabela_centavos:
        irmao, _ = achar_irmao(c["nome"], irmaos, c["cim"])
        if irmao and irmao.pk not in ja:
            centavos_soltos.append({"irmao_id": irmao.pk, "irmao_nome": irmao.nome, "cim": c["cim"], "centavos": str(c["centavos"])})
    repetidos = [v for v, n in Counter(r["centavos"] for r in registros if r["centavos"] and r["acao"] != "ignorar").items() if n > 1]
    for r in registros:
        if r["centavos"] in repetidos:
            r["avisos"].append(f"centavos {r['centavos']} repetidos em mais de um Irmão; não serão gravados")
            r["centavos"] = None
    valores = Counter(r["valor"] for r in registros if r["valor"])
    return {"ano": ano, "em_branco": em_branco, "incluir_novos": incluir_novos, "ate_mes": ate_mes,
            "obreiros": registros, "centavos_soltos": centavos_soltos,
            "tronco": [{"mes": t["mes"], "valor": str(t["valor"] or 0)} for t in tronco],
            "tronco_total": str(sum((t["valor"] or Decimal(0) for t in tronco), Decimal(0))),
            "abas_usadas": usadas, "abas_ignoradas": ignoradas, "contagem": dict(contagem),
            "valor_mais_comum": valores.most_common(1)[0][0] if valores else None,
            "com_avisos": sum(1 for r in registros if r["avisos"])}


def _base(o):
    return {"aba": o["aba"], "linha": o["linha"], "nome_planilha": o["nome"]}


# --------------------------------------------------------------- gravação
def _vencimento(loja, ano, mes):
    return date(ano, mes, min(loja.dia_vencimento or 10, calendar.monthrange(ano, mes)[1]))


@transaction.atomic
def gravar(loja, analise, conta, plano_mensalidade, plano_tronco=None):
    ano, hoje = analise["ano"], timezone.localdate()
    padrao = loja.valor_mensalidade or (Decimal(analise["valor_mais_comum"]) if analise.get("valor_mais_comum") else Decimal(0))
    r = Counter()
    if not loja.valor_mensalidade and padrao:
        loja.valor_mensalidade = padrao
        loja.save(update_fields=["valor_mensalidade"])
        r["valor_padrao_definido"] = 1
    centavos_usados = {c: pk for pk, c in Irmao.objects.filter(loja=loja, centavos__isnull=False).values_list("pk", "centavos")}
    for o in analise["obreiros"]:
        if o["acao"] == "ignorar":
            continue
        if o["acao"] == "novo":
            irmao = Irmao.objects.create(loja=loja, nome=o["irmao_nome"][:150], cim=(o.get("cim") or "")[:30])
            r["irmaos_criados"] += 1
        else:
            irmao = Irmao.objects.filter(loja=loja, pk=o["irmao_id"]).first()
            if irmao is None:
                continue
        mudou = []
        if o["cotizante"] is not None and irmao.cotizante != o["cotizante"]:
            irmao.cotizante = o["cotizante"]
            mudou.append("cotizante")
        if o["valor"]:
            individual = None if Decimal(o["valor"]) == padrao else Decimal(o["valor"])
            if irmao.mensalidade_individual != individual:
                irmao.mensalidade_individual = individual
                mudou.append("mensalidade_individual")
        if o["remido"] and irmao.situacao == "ativo":
            irmao.situacao = "remido"
            mudou.append("situacao")
        if o["centavos"]:
            cents = Decimal(o["centavos"])
            dono = centavos_usados.get(cents)
            if dono not in (None, irmao.pk):
                r["centavos_em_conflito"] += 1
            elif irmao.centavos != cents:
                centavos_usados.pop(irmao.centavos, None)
                irmao.centavos, centavos_usados[cents] = cents, irmao.pk
                mudou.append("centavos")
        if o.get("cim") and not irmao.cim:
            irmao.cim = o["cim"][:30]
            mudou.append("cim")
        if mudou:
            irmao.save(update_fields=mudou)
            r["irmaos_atualizados"] += 1
        valor_irmao = Decimal(o["valor"]) if o["valor"] else (irmao.mensalidade_individual or padrao)
        existentes = {l.competencia: l for l in Lancamento.objects.filter(
            loja=loja, irmao=irmao, tipo="R", plano=plano_mensalidade, competencia__year=ano).exclude(status="cancelado")}
        for mes_txt, m in o["meses"].items():
            mes = int(mes_txt)
            competencia, vencimento = date(ano, mes, 1), _vencimento(loja, ano, mes)
            valor = Decimal(m["valor"]) if m["valor"] else valor_irmao
            atual = existentes.get(competencia)
            if m["situacao"] == "pago":
                quando = date.fromisoformat(m["data"]) if m["data"] else min(vencimento, hoje)
                if atual is None:
                    atual = Lancamento.objects.create(
                        loja=loja, tipo="R", irmao=irmao, plano=plano_mensalidade, valor=valor, vencimento=vencimento,
                        competencia=competencia, descricao=f"Mensalidade {competencia:%m/%Y}",
                        observacao="Importado da planilha financeira")
                    servicos.quitar(atual, quando, valor, conta)
                    r["pagos_criados"] += 1
                elif atual.status == "aberto":
                    servicos.quitar(atual, quando, valor, conta)
                    r["pagos_quitados"] += 1
                else:
                    r["ja_existiam"] += 1
            elif m["situacao"] in ("aberto", "acordo"):
                if atual is None:
                    Lancamento.objects.create(
                        loja=loja, tipo="R", irmao=irmao, plano=plano_mensalidade, valor=valor, vencimento=vencimento,
                        competencia=competencia, descricao=f"Mensalidade {competencia:%m/%Y}",
                        observacao="Acordo (planilha financeira)" if m["situacao"] == "acordo" else "Importado da planilha financeira")
                    r["abertos_criados" if m["situacao"] == "aberto" else "acordos_criados"] += 1
                else:
                    r["ja_existiam"] += 1
    for c in analise.get("centavos_soltos", []):
        irmao = Irmao.objects.filter(loja=loja, pk=c["irmao_id"]).first()
        cents = Decimal(c["centavos"])
        if irmao and irmao.centavos is None and centavos_usados.get(cents) in (None, irmao.pk):
            irmao.centavos, centavos_usados[cents] = cents, irmao.pk
            irmao.save(update_fields=["centavos"])
            r["centavos_gravados"] += 1
    if plano_tronco:
        for t in analise.get("tronco", []):
            valor = Decimal(t["valor"])
            if valor <= 0:
                continue
            competencia = date(ano, t["mes"], 1)
            descricao = f"Tronco de beneficência {competencia:%m/%Y}"
            if Lancamento.objects.filter(loja=loja, plano=plano_tronco, competencia=competencia, descricao=descricao).exists():
                r["ja_existiam"] += 1
                continue
            dia = min(date(ano, t["mes"], calendar.monthrange(ano, t["mes"])[1]), hoje)
            l = Lancamento.objects.create(loja=loja, tipo="R", favorecido="Sessões", plano=plano_tronco, valor=valor,
                                          vencimento=dia, competencia=competencia, descricao=descricao,
                                          observacao="Importado da planilha financeira")
            servicos.quitar(l, dia, valor, conta)
            r["tronco_lancado"] += 1
    return dict(r)


# ------------------------------------------------- planilha no formato da Loja
def planilha_da_loja(loja, ano):
    """A planilha de controle no mesmo formato que a Tesouraria usa, já preenchida com o que está no sistema.

    Serve de modelo: baixe, marque PAGO nos meses e envie de volta pela importação.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    escuro, cabecalho_fonte = PatternFill("solid", fgColor="1F3A5F"), Font(bold=True, color="FFFFFF")
    plano = PlanoConta.objects.filter(loja=loja, codigo="1.1").first()
    lancs = Lancamento.objects.filter(loja=loja, tipo="R", irmao__isnull=False, competencia__year=ano).exclude(status="cancelado")
    if plano:
        lancs = lancs.filter(Q(plano=plano) | Q(status="isento"))
    situacao = {}
    for l in lancs:
        texto = "PAGO" if l.status == "pago" else ("REMIDO" if l.status == "isento" else (
            "Acordo" if "acordo" in l.observacao.lower() else ""))
        situacao[(l.irmao_id, l.competencia.month)] = texto
    irmaos = sorted(Irmao.objects.filter(loja=loja, situacao__in=Irmao.NO_QUADRO),
                    key=lambda i: (i.centavos is None, i.centavos or 0, i.nome))
    pasta = Workbook()
    aba = pasta.active
    aba.title = "Mensalidade"
    titulos = ["Centavos", "Nome do Obreiro", "CIM", "Cotizante", "R$"] + [m.upper() for m in MESES]
    aba.append(titulos)
    for i in irmaos:
        remido = i.situacao == "remido"
        linha = [float(i.centavos) if i.centavos is not None else None, i.nome, i.cim or None,
                 "SIM" if i.cotizante else "NÃO", 0.0 if remido else float(i.mensalidade_individual or loja.valor_mensalidade or 0)]
        linha += ["REMIDO" if remido else situacao.get((i.pk, m), "") or None for m in range(1, 13)]
        aba.append(linha)
    for coluna, titulo in enumerate(titulos, 1):
        c = aba.cell(row=1, column=coluna)
        c.font, c.fill, c.alignment = cabecalho_fonte, escuro, Alignment(horizontal="center")
        aba.column_dimensions[get_column_letter(coluna)].width = 40 if coluna == 2 else (11 if coluna > 5 else 10)
    for linha in aba.iter_rows(min_row=2, min_col=1, max_col=1):
        linha[0].number_format = "0.00"
    aba.freeze_panes = "C2"
    cents = pasta.create_sheet("Centavos")
    cents.append(["NUMERO", "CIM", "NOME", "CENTAVOS", None, "CENTAVOS", "NOME"])
    usados = {i.centavos: i.nome for i in irmaos if i.centavos is not None}
    com_cents = [i for i in sorted(irmaos, key=lambda i: i.nome) if i.centavos is not None]
    livres = [(Decimal(n) / 100, usados.get(Decimal(n) / 100, "LIVRE")) for n in range(1, 100)]
    for n in range(max(len(com_cents), len(livres))):
        esquerda = [n + 1, com_cents[n].cim, com_cents[n].nome, float(com_cents[n].centavos)] if n < len(com_cents) else [None] * 4
        direita = [float(livres[n][0]), livres[n][1]] if n < len(livres) else [None, None]
        cents.append(esquerda + [None] + direita)
    for coluna in range(1, 8):
        c = cents.cell(row=1, column=coluna)
        if c.value:
            c.font, c.fill = cabecalho_fonte, escuro
    cents.column_dimensions["C"].width = cents.column_dimensions["G"].width = 40
    tronco = pasta.create_sheet("Tronco")
    tronco.append(["Tronco de beneficência", ano])
    plano_tronco = PlanoConta.objects.filter(loja=loja, codigo="1.3").first()
    for m, nome in enumerate(MESES, 1):
        total = sum((l.valor_pago or l.valor for l in Lancamento.objects.filter(
            loja=loja, plano=plano_tronco, competencia=date(ano, m, 1), status="pago")), Decimal(0)) if plano_tronco else 0
        tronco.append([nome, float(total)])
    tronco.column_dimensions["A"].width = 26
    ajuda = pasta.create_sheet("Como preencher")
    for linha in [["Como preencher a planilha financeira"], [""],
                  ["1.", "Na aba Mensalidade, uma linha por Obreiro. Nos meses escreva: PAGO, REMIDO, Acordo ou XXX-XXX (não se aplica)."],
                  ["2.", "Mês em branco = ainda não pago (vira mensalidade em aberto até o mês atual, se você escolher assim ao importar)."],
                  ["3.", "Cotizante: SIM ou NÃO. R$: o valor da mensalidade do Obreiro (0,00 ou REMIDO para os remidos)."],
                  ["4.", "Centavos: o número que identifica o Obreiro no pagamento (ex.: 0,07 → paga R$ 180,07)."],
                  ["5.", "Na aba Tronco, o valor arrecadado em cada mês."],
                  ["6.", "Enviar a mesma planilha de novo não duplica nada: o que já está no sistema é mantido."]]:
        ajuda.append(linha)
    ajuda["A1"].font = Font(bold=True, size=14)
    ajuda.column_dimensions["B"].width = 120
    saida = io.BytesIO()
    pasta.save(saida)
    return saida.getvalue()
