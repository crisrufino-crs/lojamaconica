"""Banco de dados do sistema.

Regra de ouro: todo registro pertence a uma Loja (campo ``loja``). Nenhuma tela
busca dados sem filtrar pela Loja de quem está usando — é isso que mantém uma
Loja sem enxergar os dados da outra.
"""
import os
import uuid
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.validators import FileExtensionValidator, RegexValidator
from django.db import models
from django.utils import timezone

UFS = [(u, u) for u in "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()]
DIAS_SEMANA = [(0, "Segunda-feira"), (1, "Terça-feira"), (2, "Quarta-feira"), (3, "Quinta-feira"),
               (4, "Sexta-feira"), (5, "Sábado"), (6, "Domingo")]
cor_hex = RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Use uma cor no formato #1F3A5F.")
EXT_IMAGEM = ["jpg", "jpeg", "png", "webp"]
EXT_DOCUMENTO = ["pdf", "doc", "docx", "odt", "txt", "jpg", "jpeg", "png"]


def _caminho(pasta):
    def gerar(instancia, nome):
        loja_id = instancia.pk if isinstance(instancia, Loja) else instancia.loja_id
        ext = os.path.splitext(nome)[1].lower()
        return f"loja_{loja_id}/{pasta}/{uuid.uuid4().hex}{ext}"
    return gerar


def caminho_brasao(i, n): return _caminho("brasao")(i, n)
def caminho_foto(i, n): return _caminho("fotos")(i, n)
def caminho_ata(i, n): return _caminho("atas")(i, n)
def caminho_retorno(i, n): return _caminho("retornos")(i, n)


# =============================================================================
# Loja, usuários e personalização
# =============================================================================
class Loja(models.Model):
    nome = models.CharField("nome da Loja", max_length=150)
    numero = models.CharField("número", max_length=20, blank=True)
    potencia = models.CharField("potência / obediência", max_length=120, blank=True)
    rito = models.CharField("rito", max_length=80, blank=True)
    oriente = models.CharField("oriente (cidade)", max_length=80, blank=True)
    uf = models.CharField("UF", max_length=2, choices=UFS, blank=True)
    fundacao = models.DateField("data de fundação", null=True, blank=True)
    cnpj = models.CharField("CNPJ", max_length=18, blank=True)
    endereco = models.CharField("endereço", max_length=200, blank=True)
    cep = models.CharField("CEP", max_length=9, blank=True)
    email = models.EmailField("e-mail da Loja", blank=True,
                              help_text="As respostas dos e-mails enviados pelo sistema chegam aqui.")
    telefone = models.CharField(max_length=20, blank=True)
    brasao = models.ImageField("brasão", upload_to=caminho_brasao, blank=True,
                               validators=[FileExtensionValidator(EXT_IMAGEM)])
    cor_primaria = models.CharField("cor principal", max_length=7, default="#1F3A5F", validators=[cor_hex])
    cor_destaque = models.CharField("cor de destaque", max_length=7, default="#B8860B", validators=[cor_hex])
    dia_sessao = models.IntegerField("dia da sessão", choices=DIAS_SEMANA, null=True, blank=True)
    horario_sessao = models.TimeField("horário da sessão", null=True, blank=True)
    valor_mensalidade = models.DecimalField("valor da mensalidade", max_digits=10, decimal_places=2, default=Decimal("0"))
    dia_vencimento = models.PositiveSmallIntegerField("dia de vencimento", default=10)
    frequencia_minima = models.PositiveSmallIntegerField(
        "frequência mínima (%)", default=50,
        help_text="Percentual exigido para o Irmão ficar apto a votar e ser votado.")
    abono_conta_presenca = models.BooleanField("falta abonada conta como presença", default=True)
    visita_conta_presenca = models.BooleanField(
        "certificado de visita compensa falta", default=True,
        help_text="Cada certificado de visita no período cobre uma falta.")
    instrucoes_boleto = models.CharField("instruções no boleto", max_length=200, blank=True,
                                         default="Não receber após 30 dias do vencimento.")
    ativa = models.BooleanField(default=True)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nome"]

    def __str__(self):
        return f"{self.nome} nº {self.numero}" if self.numero else self.nome


class Usuario(AbstractUser):
    """Quem entra no sistema. Normalmente é um Irmão do quadro."""
    loja = models.ForeignKey(Loja, models.CASCADE, null=True, blank=True, related_name="usuarios")
    irmao = models.OneToOneField("Irmao", models.SET_NULL, null=True, blank=True, related_name="usuario",
                                 verbose_name="Irmão")
    is_master = models.BooleanField("usuário Master", default=False,
                                    help_text="Controla os demais usuários e a personalização da Loja.")
    acesso_secretaria = models.BooleanField("Secretaria", default=False)
    acesso_tesouraria = models.BooleanField("Tesouraria", default=False)
    acesso_chancelaria = models.BooleanField("Chancelaria", default=False)
    acesso_hospitalaria = models.BooleanField("Hospitalaria", default=False)
    deve_trocar_senha = models.BooleanField(default=False, editable=False)  # senha foi enviada por e-mail

    def __str__(self):
        return self.get_full_name() or self.username

    def pode(self, modulo):
        if not self.is_active:
            return False
        if self.is_superuser or self.is_master:
            return True
        if modulo == "admin":
            return False
        return bool(getattr(self, f"acesso_{modulo}", False))

    @property
    def modulos(self):
        return [m for m in ("secretaria", "tesouraria", "chancelaria", "hospitalaria") if self.pode(m)]


class DaLoja(models.Model):
    loja = models.ForeignKey(Loja, models.CASCADE, related_name="+", editable=False)

    class Meta:
        abstract = True


class CampoExtra(DaLoja):
    """Campo a mais no cadastro de Irmãos, criado pela própria Loja."""
    rotulo = models.CharField("nome do campo", max_length=60)
    ordem = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["ordem", "id"]
        verbose_name = "campo personalizado"
        verbose_name_plural = "campos personalizados"

    def __str__(self):
        return self.rotulo


# =============================================================================
# Secretaria
# =============================================================================
class Grau(DaLoja):
    nome = models.CharField(max_length=60)
    ordem = models.PositiveSmallIntegerField(default=1, help_text="1 para o primeiro grau, 2 para o seguinte...")

    class Meta:
        ordering = ["ordem", "nome"]
        constraints = [models.UniqueConstraint(fields=["loja", "nome"], name="grau_unico_por_loja")]

    def __str__(self):
        return self.nome


class Irmao(DaLoja):
    SITUACOES = [("ativo", "Ativo"), ("licenciado", "Licenciado"), ("irregular", "Irregular"),
                 ("remido", "Remido"), ("emerito", "Emérito"), ("quite_placet", "Quite Placet"),
                 ("desligado", "Desligado"), ("falecido", "Falecido")]
    ESTADOS_CIVIS = [("solteiro", "Solteiro"), ("casado", "Casado"), ("uniao", "União estável"),
                     ("divorciado", "Divorciado"), ("viuvo", "Viúvo")]
    NO_QUADRO = ("ativo", "licenciado", "irregular", "remido", "emerito")

    nome = models.CharField("nome completo", max_length=150)
    cim = models.CharField("CIM / cadastro", max_length=30, blank=True)
    situacao = models.CharField("situação", max_length=15, choices=SITUACOES, default="ativo")
    grau = models.ForeignKey(Grau, models.PROTECT, null=True, blank=True, verbose_name="grau atual")
    cargo = models.CharField("cargo na Loja", max_length=80, blank=True)
    data_nascimento = models.DateField("nascimento", null=True, blank=True)
    cpf = models.CharField("CPF", max_length=14, blank=True)
    rg = models.CharField("RG", max_length=20, blank=True)
    email = models.EmailField("e-mail", blank=True)
    celular = models.CharField(max_length=20, blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    endereco = models.CharField("endereço", max_length=200, blank=True)
    bairro = models.CharField(max_length=80, blank=True)
    cidade = models.CharField(max_length=80, blank=True)
    uf = models.CharField("UF", max_length=2, choices=UFS, blank=True)
    cep = models.CharField("CEP", max_length=9, blank=True)
    naturalidade = models.CharField(max_length=80, blank=True)
    profissao = models.CharField("profissão", max_length=80, blank=True)
    estado_civil = models.CharField("estado civil", max_length=12, choices=ESTADOS_CIVIS, blank=True)
    nome_pai = models.CharField("nome do pai", max_length=150, blank=True)
    nome_mae = models.CharField("nome da mãe", max_length=150, blank=True)
    data_iniciacao = models.DateField("iniciação", null=True, blank=True)
    data_filiacao = models.DateField("filiação à Loja", null=True, blank=True)
    foto = models.ImageField(upload_to=caminho_foto, blank=True, validators=[FileExtensionValidator(EXT_IMAGEM)])
    receber_emails = models.BooleanField("recebe e-mails da Loja", default=True)
    observacoes = models.TextField("observações", blank=True)
    extras = models.JSONField(default=dict, blank=True, editable=False)

    class Meta:
        ordering = ["nome"]
        verbose_name = "Irmão"
        verbose_name_plural = "Irmãos"

    def __str__(self):
        return self.nome

    @property
    def no_quadro(self):
        return self.situacao in self.NO_QUADRO


class Dependente(DaLoja):
    PARENTESCOS = [("conjuge", "Cônjuge"), ("filho", "Filho(a)"), ("enteado", "Enteado(a)"),
                   ("pai", "Pai"), ("mae", "Mãe"), ("neto", "Neto(a)"), ("outro", "Outro")]
    irmao = models.ForeignKey(Irmao, models.CASCADE, related_name="dependentes", verbose_name="Irmão")
    nome = models.CharField(max_length=150)
    parentesco = models.CharField(max_length=10, choices=PARENTESCOS, default="conjuge")
    data_nascimento = models.DateField("nascimento", null=True, blank=True)
    email = models.EmailField("e-mail", blank=True)
    celular = models.CharField(max_length=20, blank=True)
    observacoes = models.CharField("observações", max_length=200, blank=True)

    class Meta:
        ordering = ["irmao__nome", "nome"]

    def __str__(self):
        return self.nome


class HistoricoGrau(DaLoja):
    irmao = models.ForeignKey(Irmao, models.CASCADE, related_name="graus", verbose_name="Irmão")
    grau = models.ForeignKey(Grau, models.PROTECT)
    data = models.DateField()
    observacao = models.CharField("observação", max_length=200, blank=True)

    class Meta:
        ordering = ["-data", "-grau__ordem"]
        verbose_name = "registro de grau"
        verbose_name_plural = "registros de grau"

    def __str__(self):
        return f"{self.irmao} — {self.grau}"


class LojaCoirma(DaLoja):
    nome = models.CharField(max_length=150)
    numero = models.CharField("número", max_length=20, blank=True)
    potencia = models.CharField("potência", max_length=120, blank=True)
    rito = models.CharField(max_length=80, blank=True)
    oriente = models.CharField("oriente (cidade)", max_length=80, blank=True)
    uf = models.CharField("UF", max_length=2, choices=UFS, blank=True)
    veneravel = models.CharField("Venerável Mestre", max_length=150, blank=True)
    email = models.EmailField("e-mail", blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    dia_sessao = models.CharField("dia e horário das sessões", max_length=80, blank=True)
    endereco = models.CharField("endereço", max_length=200, blank=True)
    observacoes = models.TextField("observações", blank=True)

    class Meta:
        ordering = ["nome"]
        verbose_name = "Loja co-Irmã"
        verbose_name_plural = "Lojas co-Irmãs"

    def __str__(self):
        return f"{self.nome} nº {self.numero}" if self.numero else self.nome


class Ata(DaLoja):
    numero = models.CharField("número", max_length=20, blank=True)
    data = models.DateField("data da sessão")
    titulo = models.CharField("título", max_length=150)
    grau = models.ForeignKey(Grau, models.PROTECT, null=True, blank=True, verbose_name="grau da sessão")
    texto = models.TextField("texto da ata", blank=True)
    arquivo = models.FileField("arquivo (PDF, Word ou imagem)", upload_to=caminho_ata, blank=True,
                               validators=[FileExtensionValidator(EXT_DOCUMENTO)])
    aprovada = models.BooleanField(default=False)

    class Meta:
        ordering = ["-data"]

    def __str__(self):
        return self.titulo


class Informativo(DaLoja):
    titulo = models.CharField("título", max_length=150)
    texto = models.TextField()
    data = models.DateField(default=timezone.localdate)
    publicado = models.BooleanField("visível para os Irmãos", default=True)

    class Meta:
        ordering = ["-data", "-id"]

    def __str__(self):
        return self.titulo


class Evento(DaLoja):
    TIPOS = [("sessao", "Sessão"), ("loja", "Evento da Loja"), ("entidade", "Evento de entidade"),
             ("social", "Evento social / filantrópico")]
    titulo = models.CharField("título", max_length=150)
    tipo = models.CharField(max_length=10, choices=TIPOS, default="loja")
    entidade = models.CharField("entidade organizadora", max_length=120, blank=True,
                                help_text="Ex.: Potência, Fraternidade Feminina, DeMolay, outra Loja.")
    inicio = models.DateTimeField("início")
    fim = models.DateTimeField("término", null=True, blank=True)
    local = models.CharField(max_length=150, blank=True)
    descricao = models.TextField("descrição", blank=True)

    class Meta:
        ordering = ["inicio"]

    def __str__(self):
        return self.titulo


class EmailEnviado(DaLoja):
    assunto = models.CharField(max_length=150)
    corpo = models.TextField()
    publico = models.CharField("enviado para", max_length=120)
    total = models.PositiveIntegerField("destinatários", default=0)
    destinatarios = models.TextField(blank=True)
    enviado_em = models.DateTimeField(auto_now_add=True)
    enviado_por = models.ForeignKey(Usuario, models.SET_NULL, null=True, blank=True, related_name="+")
    automatico = models.BooleanField(default=False)

    class Meta:
        ordering = ["-enviado_em"]

    def __str__(self):
        return self.assunto


# =============================================================================
# Tesouraria
# =============================================================================
class ContaBancaria(DaLoja):
    nome = models.CharField("nome da conta", max_length=80, help_text="Ex.: Caixa, Banco do Brasil, Poupança.")
    saldo_inicial = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    data_saldo_inicial = models.DateField("data do saldo inicial", default=timezone.localdate)
    banco_codigo = models.CharField("código do banco", max_length=3, blank=True, help_text="Ex.: 001, 104, 237, 341, 756.")
    banco_nome = models.CharField("nome do banco", max_length=30, blank=True)
    agencia = models.CharField("agência", max_length=5, blank=True)
    agencia_dv = models.CharField("dígito da agência", max_length=1, blank=True)
    conta = models.CharField("número da conta", max_length=12, blank=True)
    conta_dv = models.CharField("dígito da conta", max_length=1, blank=True)
    carteira = models.CharField("carteira de cobrança", max_length=3, blank=True)
    convenio = models.CharField("convênio / código do beneficiário", max_length=20, blank=True)
    proximo_nosso_numero = models.PositiveIntegerField("próximo nosso número", default=1)
    proxima_remessa = models.PositiveIntegerField("próxima remessa", default=1)
    ativa = models.BooleanField(default=True)

    class Meta:
        ordering = ["nome"]
        verbose_name = "conta"
        verbose_name_plural = "contas"

    def __str__(self):
        return self.nome

    @property
    def emite_boleto(self):
        return bool(self.banco_codigo and self.agencia and self.conta)


class PlanoConta(DaLoja):
    TIPOS = [("R", "Receita"), ("D", "Despesa")]
    codigo = models.CharField("código", max_length=20, help_text="Ex.: 1, 1.1, 1.1.01")
    nome = models.CharField(max_length=100)
    tipo = models.CharField(max_length=1, choices=TIPOS)
    pai = models.ForeignKey("self", models.PROTECT, null=True, blank=True, related_name="filhas",
                            verbose_name="conta superior")
    aceita_lancamento = models.BooleanField(
        "aceita lançamentos", default=True,
        help_text="Desmarque nas contas que só servem para agrupar outras.")

    class Meta:
        ordering = ["codigo"]
        verbose_name = "conta do plano"
        verbose_name_plural = "plano de contas"
        constraints = [models.UniqueConstraint(fields=["loja", "codigo"], name="plano_codigo_unico_por_loja")]

    def __str__(self):
        return f"{self.codigo} {self.nome}"

    @property
    def nivel(self):
        return self.codigo.count(".")


class Lancamento(DaLoja):
    TIPOS = [("R", "A receber"), ("P", "A pagar")]
    STATUS = [("aberto", "Em aberto"), ("pago", "Quitado"), ("cancelado", "Cancelado")]
    tipo = models.CharField(max_length=1, choices=TIPOS)
    descricao = models.CharField("descrição", max_length=150)
    irmao = models.ForeignKey(Irmao, models.PROTECT, null=True, blank=True, related_name="lancamentos",
                              verbose_name="Irmão")
    favorecido = models.CharField("fornecedor / pagador", max_length=120, blank=True,
                                  help_text="Preencha quando não for um Irmão.")
    plano = models.ForeignKey(PlanoConta, models.PROTECT, verbose_name="plano de contas")
    conta = models.ForeignKey(ContaBancaria, models.PROTECT, null=True, blank=True, verbose_name="conta")
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    vencimento = models.DateField()
    competencia = models.DateField("mês de referência", null=True, blank=True)
    status = models.CharField("situação", max_length=10, choices=STATUS, default="aberto")
    data_pagamento = models.DateField("data da quitação", null=True, blank=True)
    valor_pago = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    serie = models.UUIDField(null=True, blank=True, editable=False)
    observacao = models.CharField("observação", max_length=200, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["vencimento", "id"]
        verbose_name = "lançamento"
        verbose_name_plural = "lançamentos"
        indexes = [models.Index(fields=["loja", "status", "vencimento"])]

    def __str__(self):
        return self.descricao

    @property
    def quem(self):
        return self.irmao.nome if self.irmao_id else self.favorecido

    @property
    def vencido(self):
        return self.status == "aberto" and self.vencimento < timezone.localdate()


class Transferencia(DaLoja):
    origem = models.ForeignKey(ContaBancaria, models.PROTECT, related_name="saidas", verbose_name="conta de origem")
    destino = models.ForeignKey(ContaBancaria, models.PROTECT, related_name="entradas", verbose_name="conta de destino")
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    data = models.DateField(default=timezone.localdate)
    descricao = models.CharField("descrição", max_length=150, blank=True)

    class Meta:
        ordering = ["-data", "-id"]
        verbose_name = "transferência"
        verbose_name_plural = "transferências"

    def __str__(self):
        return f"{self.origem} → {self.destino}"


class Remessa(DaLoja):
    conta = models.ForeignKey(ContaBancaria, models.PROTECT)
    numero = models.PositiveIntegerField("número")
    gerada_em = models.DateTimeField(auto_now_add=True)
    conteudo = models.TextField()
    quantidade = models.PositiveIntegerField(default=0)
    valor_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))

    class Meta:
        ordering = ["-gerada_em"]

    def __str__(self):
        return f"Remessa {self.numero}"


class Boleto(DaLoja):
    STATUS = [("gerado", "Aguardando remessa"), ("remetido", "Enviado ao banco"),
              ("registrado", "Registrado no banco"), ("pago", "Pago"), ("baixado", "Baixado / cancelado")]
    lancamento = models.OneToOneField(Lancamento, models.CASCADE, related_name="boleto")
    conta = models.ForeignKey(ContaBancaria, models.PROTECT)
    nosso_numero = models.CharField(max_length=20)
    codigo_barras = models.CharField(max_length=44)
    linha_digitavel = models.CharField(max_length=60)
    status = models.CharField(max_length=10, choices=STATUS, default="gerado")
    remessa = models.ForeignKey(Remessa, models.SET_NULL, null=True, blank=True, related_name="boletos")
    gerado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-gerado_em"]
        constraints = [models.UniqueConstraint(fields=["conta", "nosso_numero"], name="nosso_numero_unico_por_conta")]

    def __str__(self):
        return self.nosso_numero


class Retorno(DaLoja):
    arquivo_nome = models.CharField(max_length=150)
    processado_em = models.DateTimeField(auto_now_add=True)
    quitados = models.PositiveIntegerField(default=0)
    registrados = models.PositiveIntegerField(default=0)
    nao_encontrados = models.PositiveIntegerField(default=0)
    valor_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    resumo = models.TextField(blank=True)

    class Meta:
        ordering = ["-processado_em"]


# =============================================================================
# Chancelaria
# =============================================================================
class Sessao(DaLoja):
    TIPOS = [("ordinaria", "Ordinária"), ("magna", "Magna"), ("extraordinaria", "Extraordinária"),
             ("instrucao", "Instrução"), ("administrativa", "Administrativa"), ("eleitoral", "Eleitoral")]
    data = models.DateField()
    tipo = models.CharField(max_length=15, choices=TIPOS, default="ordinaria")
    grau = models.ForeignKey(Grau, models.PROTECT, null=True, blank=True, verbose_name="grau da sessão",
                             help_text="Só entram na lista os Irmãos deste grau ou acima. Em branco = todos.")
    descricao = models.CharField("descrição", max_length=150, blank=True)

    class Meta:
        ordering = ["-data", "-id"]
        verbose_name = "sessão"
        verbose_name_plural = "sessões"

    def __str__(self):
        return f"Sessão {self.get_tipo_display()} de {self.data:%d/%m/%Y}"


class Presenca(DaLoja):
    STATUS = [("P", "Presente"), ("F", "Falta"), ("A", "Falta abonada"), ("L", "Licenciado")]
    sessao = models.ForeignKey(Sessao, models.CASCADE, related_name="presencas")
    irmao = models.ForeignKey(Irmao, models.CASCADE, related_name="presencas")
    status = models.CharField(max_length=1, choices=STATUS, default="F")
    justificativa = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["irmao__nome"]
        constraints = [models.UniqueConstraint(fields=["sessao", "irmao"], name="presenca_unica")]


class Licenca(DaLoja):
    irmao = models.ForeignKey(Irmao, models.CASCADE, related_name="licencas", verbose_name="Irmão")
    inicio = models.DateField("início")
    fim = models.DateField("término")
    motivo = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-inicio"]
        verbose_name = "licença"
        verbose_name_plural = "licenças"

    def __str__(self):
        return f"Licença de {self.irmao}"


class CertificadoVisita(DaLoja):
    irmao = models.ForeignKey(Irmao, models.CASCADE, related_name="visitas", verbose_name="Irmão")
    data = models.DateField("data da visita")
    coirma = models.ForeignKey(LojaCoirma, models.SET_NULL, null=True, blank=True, verbose_name="Loja co-Irmã visitada")
    loja_visitada = models.CharField("outra Loja visitada", max_length=150, blank=True,
                                     help_text="Preencha se a Loja não estiver cadastrada como co-Irmã.")
    observacao = models.CharField("observação", max_length=200, blank=True)

    class Meta:
        ordering = ["-data"]
        verbose_name = "certificado de visita"
        verbose_name_plural = "certificados de visita"

    def __str__(self):
        return f"Visita de {self.irmao}"

    @property
    def onde(self):
        return str(self.coirma) if self.coirma_id else self.loja_visitada


# =============================================================================
# Hospitalaria
# =============================================================================
class Bem(DaLoja):
    ESTADOS = [("novo", "Novo"), ("bom", "Bom"), ("regular", "Regular"), ("manutencao", "Em manutenção"),
               ("baixado", "Baixado")]
    codigo = models.CharField("código / patrimônio", max_length=30, blank=True)
    descricao = models.CharField("descrição", max_length=150, help_text="Ex.: Cadeira de rodas dobrável.")
    categoria = models.CharField(max_length=60, blank=True, help_text="Ex.: Cadeira de rodas, Andador, Muleta.")
    estado = models.CharField(max_length=12, choices=ESTADOS, default="bom")
    observacoes = models.CharField("observações", max_length=200, blank=True)

    class Meta:
        ordering = ["descricao"]
        verbose_name = "bem"
        verbose_name_plural = "bens e materiais"

    def __str__(self):
        return f"{self.descricao} ({self.codigo})" if self.codigo else self.descricao

    @property
    def emprestimo_atual(self):
        return self.emprestimos.filter(data_devolucao__isnull=True).first()


class Emprestimo(DaLoja):
    bem = models.ForeignKey(Bem, models.PROTECT, related_name="emprestimos")
    irmao = models.ForeignKey(Irmao, models.SET_NULL, null=True, blank=True, verbose_name="Irmão responsável")
    beneficiario = models.CharField("quem vai usar", max_length=150)
    contato = models.CharField(max_length=80, blank=True)
    data_emprestimo = models.DateField("data do empréstimo", default=timezone.localdate)
    previsao_devolucao = models.DateField("previsão de devolução", null=True, blank=True)
    data_devolucao = models.DateField("devolvido em", null=True, blank=True)
    observacoes = models.CharField("observações", max_length=200, blank=True)

    class Meta:
        ordering = ["-data_emprestimo", "-id"]
        verbose_name = "empréstimo"
        verbose_name_plural = "empréstimos"

    def __str__(self):
        return f"{self.bem} → {self.beneficiario}"

    @property
    def atrasado(self):
        return (not self.data_devolucao and self.previsao_devolucao
                and self.previsao_devolucao < timezone.localdate())


# =============================================================================
# Notificações
# =============================================================================
class Notificacao(DaLoja):
    usuario = models.ForeignKey(Usuario, models.CASCADE, related_name="notificacoes")
    titulo = models.CharField(max_length=120)
    texto = models.CharField(max_length=250)
    criada_em = models.DateTimeField(auto_now_add=True)
    lida = models.BooleanField(default=False)
    entregue_push = models.BooleanField(default=False)

    class Meta:
        ordering = ["-criada_em"]


class InscricaoPush(models.Model):
    """Um celular (ou navegador) que aceitou receber notificações."""
    usuario = models.ForeignKey(Usuario, models.CASCADE, related_name="inscricoes_push")
    endpoint = models.URLField(max_length=600, unique=True)
    criada_em = models.DateTimeField(auto_now_add=True)


class RotinaDiaria(DaLoja):
    """Marca os dias em que os avisos automáticos já saíram (evita aviso repetido)."""
    data = models.DateField()
    resumo = models.CharField(max_length=250, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["loja", "data"], name="rotina_unica_por_dia")]


class CargaImportada(DaLoja):
    """Planilha de carga inicial já importada na instalação (para não importar a mesma planilha duas vezes)."""
    arquivo = models.CharField(max_length=200)
    assinatura = models.CharField(max_length=64)
    importada_em = models.DateTimeField(auto_now_add=True)
    resumo = models.CharField(max_length=250, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["loja", "assinatura"], name="carga_unica_por_loja")]
