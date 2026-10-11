"""Boletos: código de barras, linha digitável, arquivo de remessa e leitura do retorno.

O que é padrão nacional (FEBRABAN) está implementado aqui: dígitos
verificadores, fator de vencimento, linha digitável e os arquivos CNAB 240.

O que varia de banco para banco é o "campo livre" (25 dígitos do código de
barras) e alguns detalhes do CNAB. A função ``campo_livre`` abaixo usa um
formato genérico: ANTES DE EMITIR BOLETOS DE VERDADE ela precisa ser ajustada
ao manual do banco da Loja e os arquivos precisam passar pela homologação do
banco (todo banco exige esse teste antes de liberar a cobrança).
"""
from datetime import date, datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import Boleto, Remessa, Retorno
from .servicos import quitar

BASE_FATOR = date(1997, 10, 7)


def so_digitos(texto):
    return "".join(c for c in str(texto or "") if c.isdigit())


def modulo10(numero):
    soma, peso = 0, 2
    for d in reversed(numero):
        p = int(d) * peso
        soma += p // 10 + p % 10
        peso = 1 if peso == 2 else 2
    return str((10 - soma % 10) % 10)


def modulo11(numero):
    """Dígito geral do código de barras (pesos 2 a 9; resultado 0, 10 ou 11 vira 1)."""
    soma, peso = 0, 2
    for d in reversed(numero):
        soma += int(d) * peso
        peso = 2 if peso == 9 else peso + 1
    dv = 11 - soma % 11
    return "1" if dv in (0, 10, 11) else str(dv)


def fator_vencimento(vencimento):
    """Dias desde 07/10/1997. Em 22/02/2025 o contador chegou a 9999 e voltou para 1000."""
    dias = (vencimento - BASE_FATOR).days
    while dias > 9999:
        dias -= 9000
    return f"{dias:04d}"


def campo_livre(conta, nosso_numero):
    """FORMATO GENÉRICO — ajustar ao manual do banco antes de usar em produção."""
    return (so_digitos(conta.agencia).zfill(4)[-4:] + so_digitos(conta.carteira).zfill(2)[-2:] +
            so_digitos(nosso_numero).zfill(11)[-11:] + so_digitos(conta.conta).zfill(7)[-7:] + "0")


def codigo_de_barras(conta, nosso_numero, vencimento, valor):
    banco = so_digitos(conta.banco_codigo).zfill(3)
    centavos = f"{int((Decimal(valor) * 100).to_integral_value()):010d}"
    sem_dv = banco + "9" + fator_vencimento(vencimento) + centavos + campo_livre(conta, nosso_numero)
    return sem_dv[:4] + modulo11(sem_dv) + sem_dv[4:]


def linha_digitavel(barras):
    livre = barras[19:]
    c1, c2, c3 = barras[:4] + livre[:5], livre[5:15], livre[15:]
    c1, c2, c3 = c1 + modulo10(c1), c2 + modulo10(c2), c3 + modulo10(c3)
    return f"{c1[:5]}.{c1[5:]} {c2[:5]}.{c2[5:]} {c3[:5]}.{c3[5:]} {barras[4]} {barras[5:19]}"


def barras_para_desenho(barras):
    """Converte os 44 dígitos nas larguras das barras (padrão Intercalado 2 de 5).

    Devolve uma lista de (é_barra_preta, largura) para a tela desenhar.
    """
    padrao = {"0": "00110", "1": "10001", "2": "01001", "3": "11000", "4": "00101",
              "5": "10100", "6": "01100", "7": "00011", "8": "10010", "9": "01010"}
    desenho = [(True, 1), (False, 1), (True, 1), (False, 1)]
    for i in range(0, len(barras), 2):
        for preta, branca in zip(padrao[barras[i]], padrao[barras[i + 1]]):
            desenho += [(True, 3 if preta == "1" else 1), (False, 3 if branca == "1" else 1)]
    return desenho + [(True, 3), (False, 1), (True, 1)]


@transaction.atomic
def emitir(lancamento, conta):
    """Gera o boleto de um lançamento a receber (ou devolve o que já existe)."""
    existente = Boleto.objects.filter(lancamento=lancamento).first()
    if existente:
        return existente
    conta = type(conta).objects.select_for_update().get(pk=conta.pk)
    nosso_numero = f"{conta.proximo_nosso_numero:011d}"
    conta.proximo_nosso_numero += 1
    conta.save(update_fields=["proximo_nosso_numero"])
    barras = codigo_de_barras(conta, nosso_numero, lancamento.vencimento, lancamento.valor)
    return Boleto.objects.create(loja=lancamento.loja, lancamento=lancamento, conta=conta, nosso_numero=nosso_numero,
                                 codigo_barras=barras, linha_digitavel=linha_digitavel(barras))


# --- CNAB 240 -------------------------------------------------------------------
def _n(valor, tamanho):
    return so_digitos(valor).zfill(tamanho)[-tamanho:]


def _a(valor, tamanho):
    import unicodedata
    texto = unicodedata.normalize("NFKD", str(valor or "")).encode("ascii", "ignore").decode().upper()
    return texto[:tamanho].ljust(tamanho)


def _dinheiro(valor, tamanho=15):
    return f"{int((Decimal(valor) * 100).to_integral_value()):0{tamanho}d}"


def _linha(*partes):
    linha = "".join(partes)
    assert len(linha) == 240, f"registro CNAB com {len(linha)} posições"
    return linha


@transaction.atomic
def gerar_remessa(loja, conta):
    """Junta os boletos que ainda não foram ao banco num arquivo CNAB 240. Devolve a Remessa (ou None)."""
    boletos = list(Boleto.objects.select_for_update(of=("self",)).filter(loja=loja, conta=conta, status="gerado",
                                                             lancamento__status="aberto")
                   .select_related("lancamento__irmao"))
    if not boletos:
        return None
    conta = type(conta).objects.select_for_update().get(pk=conta.pk)
    numero, agora = conta.proxima_remessa, timezone.localtime()
    conta.proxima_remessa += 1
    conta.save(update_fields=["proxima_remessa"])
    banco, cnpj = _n(conta.banco_codigo, 3), so_digitos(loja.cnpj)
    ag_conta = (_n(conta.agencia, 5) + _a(conta.agencia_dv, 1) + _n(conta.conta, 12) + _a(conta.conta_dv, 1) + " ")
    linhas = [_linha(
        banco, "0000", "0", " " * 9, "2", _n(cnpj, 14), _a(conta.convenio, 20), ag_conta, _a(loja.nome, 30),
        _a(conta.banco_nome, 30), " " * 10, "1", agora.strftime("%d%m%Y"), agora.strftime("%H%M%S"),
        f"{numero:06d}", "103", "01600", " " * 20, " " * 20, " " * 29)]
    linhas.append(_linha(
        banco, "0001", "1", "R", "01", "  ", "060", " ", "2", _n(cnpj, 15), _a(conta.convenio, 20), ag_conta,
        _a(loja.nome, 30), _a(loja.instrucoes_boleto, 40), " " * 40, f"{numero:08d}", agora.strftime("%d%m%Y"),
        "0" * 8, " " * 33))
    sequencia, total = 0, Decimal("0")
    for b in boletos:
        l, irmao = b.lancamento, b.lancamento.irmao
        venc, zeros15 = l.vencimento.strftime("%d%m%Y"), "0" * 15
        sequencia += 1
        linhas.append(_linha(
            banco, "0001", "3", f"{sequencia:05d}", "P", " ", "01", ag_conta, _a(b.nosso_numero, 20),
            _n(conta.carteira or "1", 1), "1", "1", "2", "2", _a(l.pk, 15), venc, _dinheiro(l.valor), "00000", " ",
            "99", "N", agora.strftime("%d%m%Y"), "3", "0" * 8, zeros15, "0", "0" * 8, zeros15, zeros15, zeros15,
            _a(b.pk, 25), "3", "00", "1", "060", "09", "0" * 10, " "))
        sequencia += 1
        cpf = so_digitos(irmao.cpf if irmao else "")
        cep = _n(irmao.cep if irmao else "", 8)
        linhas.append(_linha(
            banco, "0001", "3", f"{sequencia:05d}", "Q", " ", "01", "1" if cpf else "0", _n(cpf, 15),
            _a(l.quem, 40), _a(irmao.endereco if irmao else "", 40), _a(irmao.bairro if irmao else "", 15),
            cep[:5], cep[5:], _a(irmao.cidade if irmao else "", 15), _a(irmao.uf if irmao else "", 2),
            "0", "0" * 15, " " * 40, "000", " " * 20, " " * 8))
        total += l.valor
    linhas.append(_linha(banco, "0001", "5", " " * 9, f"{sequencia + 2:06d}", "0" * 92, " " * 125))
    linhas.append(_linha(banco, "9999", "9", " " * 9, "000001", f"{sequencia + 4:06d}", "0" * 6, " " * 205))
    remessa = Remessa.objects.create(loja=loja, conta=conta, numero=numero, conteudo="\r\n".join(linhas) + "\r\n",
                                     quantidade=len(boletos), valor_total=total)
    Boleto.objects.filter(pk__in=[b.pk for b in boletos]).update(status="remetido", remessa=remessa)
    return remessa


MOV_REGISTRADO, MOV_PAGO, MOV_BAIXADO = {"02"}, {"06", "17"}, {"09"}


def ler_retorno(texto):
    """Lê um arquivo de retorno CNAB 240 e devolve as ocorrências (uma por boleto)."""
    ocorrencias, atual = [], None
    for linha in texto.splitlines():
        if len(linha) < 240 or linha[7] != "3":
            continue
        if linha[13] == "T":
            atual = {"movimento": linha[15:17], "nosso_numero": linha[37:57].strip(),
                     "identificacao": linha[105:130].strip(), "valor": Decimal(int(linha[81:96] or 0)) / 100,
                     "valor_pago": None, "data": None}
            ocorrencias.append(atual)
        elif linha[13] == "U" and atual is not None:
            atual["valor_pago"] = Decimal(int(linha[77:92] or 0)) / 100
            try:
                atual["data"] = datetime.strptime(linha[137:145], "%d%m%Y").date()
            except ValueError:
                pass
            atual = None
    return ocorrencias


@transaction.atomic
def processar_retorno(loja, nome_arquivo, texto, conta_padrao=None):
    """Dá baixa automática nos boletos pagos que vieram no arquivo do banco."""
    retorno = Retorno(loja=loja, arquivo_nome=nome_arquivo[:150])
    detalhes = []
    for o in ler_retorno(texto):
        boleto = None
        if o["identificacao"].isdigit():
            boleto = Boleto.objects.filter(loja=loja, pk=int(o["identificacao"])).first()
        if boleto is None and o["nosso_numero"]:
            boleto = Boleto.objects.filter(loja=loja, nosso_numero=o["nosso_numero"].zfill(11)[-11:]).first()
        if boleto is None:
            retorno.nao_encontrados += 1
            detalhes.append(f"Não encontrado: nosso número {o['nosso_numero']}")
            continue
        l = boleto.lancamento
        if o["movimento"] in MOV_PAGO:
            if l.status == "aberto":
                pago = o["valor_pago"] or o["valor"] or l.valor
                quitar(l, o["data"] or timezone.localdate(), pago, boleto.conta or conta_padrao)
                retorno.quitados += 1
                retorno.valor_total += pago
                detalhes.append(f"Quitado: {l.quem} — {l.descricao} — R$ {pago:.2f}")
            else:
                detalhes.append(f"Já estava quitado/cancelado: {l.quem} — {l.descricao}")
        elif o["movimento"] in MOV_REGISTRADO:
            if boleto.status in ("gerado", "remetido"):
                boleto.status = "registrado"
                boleto.save(update_fields=["status"])
            retorno.registrados += 1
        elif o["movimento"] in MOV_BAIXADO and boleto.status != "pago":
            boleto.status = "baixado"
            boleto.save(update_fields=["status"])
            detalhes.append(f"Baixado pelo banco: {l.quem} — {l.descricao}")
    retorno.resumo = "\n".join(detalhes)
    retorno.save()
    return retorno
