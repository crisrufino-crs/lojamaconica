"""Descrição de cada cadastro do sistema (quais colunas, filtros e ações cada tela tem)."""
from . import servicos
from .crud import Recurso
from .forms import (EmprestimoForm, IrmaoForm, LicencaForm, PagarForm, PlanoContaForm, ReceberForm,
                    TransferenciaForm, UsuarioForm)
from .models import (Ata, Bem, CampoExtra, CertificadoVisita, ContaBancaria, Dependente, Emprestimo, Evento, Grau,
                     HistoricoGrau, Informativo, Irmao, Lancamento, Licenca, LojaCoirma, PlanoConta, Sessao,
                     Transferencia, Usuario)

# ---------------------------------------------------------------- Secretaria
Recurso("irmaos", Irmao, "secretaria", form=IrmaoForm, relacionados=("grau",),
        colunas=[("Nome", "nome"), ("CIM", "cim"), ("Grau", "grau"), ("Cargo", "cargo"), ("Situação", "situacao"),
                 ("Celular", "celular"), ("E-mail", "email")],
        busca=("nome", "cim", "cpf", "email"), filtros=("situacao", "grau"),
        acoes=[("Ficha", "irmao_ficha")], classe_linha=lambda i: "" if i.no_quadro else "apagado",
        acoes_topo=[("Importar do Excel", "importar_irmaos")],
        ajuda="O grau é atualizado pelo Controle de Graus (abra a ficha do Irmão).")

Recurso("dependentes", Dependente, "secretaria", relacionados=("irmao",),
        campos=["irmao", "nome", "parentesco", "data_nascimento", "email", "celular", "observacoes"],
        colunas=[("Nome", "nome"), ("Parentesco", "parentesco"), ("Irmão", "irmao"), ("Nascimento", "data_nascimento"),
                 ("Celular", "celular")],
        busca=("nome", "irmao__nome"), filtros=("parentesco",))

Recurso("graus-historico", HistoricoGrau, "secretaria", titulo="Controle de graus", relacionados=("irmao", "grau"),
        campos=["irmao", "grau", "data", "observacao"],
        colunas=[("Irmão", "irmao"), ("Grau", "grau"), ("Data", "data"), ("Observação", "observacao")],
        busca=("irmao__nome",), filtros=("grau",),
        apos_salvar=lambda obj, novo, request: servicos.atualizar_grau(obj.irmao),
        ajuda="Registre aqui iniciações, elevações, exaltações e instalações. O grau atual do Irmão é atualizado sozinho.")

Recurso("coirmas", LojaCoirma, "secretaria",
        campos=["nome", "numero", "potencia", "rito", "oriente", "uf", "veneravel", "email", "telefone", "dia_sessao",
                "endereco", "observacoes"],
        colunas=[("Loja", "nome"), ("Nº", "numero"), ("Potência", "potencia"), ("Oriente", "oriente"), ("UF", "uf"),
                 ("Venerável", "veneravel"), ("Sessões", "dia_sessao")],
        busca=("nome", "numero", "oriente", "veneravel"), filtros=("uf",))

Recurso("atas", Ata, "secretaria", relacionados=("grau",),
        campos=["numero", "data", "titulo", "grau", "texto", "arquivo", "aprovada"],
        colunas=[("Nº", "numero"), ("Data", "data"), ("Título", "titulo"), ("Grau", "grau"), ("Aprovada", "aprovada"),
                 ("Arquivo", lambda a: "Anexado" if a.arquivo else "")],
        busca=("titulo", "numero", "texto"), filtros=("grau",), acoes=[("Abrir", "ata_ver")])

Recurso("informativos", Informativo, "secretaria",
        campos=["titulo", "texto", "data", "publicado"],
        colunas=[("Data", "data"), ("Título", "titulo"), ("Visível para os Irmãos", "publicado")],
        busca=("titulo", "texto"),
        ajuda="Os informativos marcados como visíveis aparecem na área de cada Irmão.")

Recurso("eventos", Evento, "secretaria", titulo="Agenda de eventos", ordem=("-inicio",),
        campos=["titulo", "tipo", "entidade", "inicio", "fim", "local", "descricao"],
        colunas=[("Início", "inicio"), ("Título", "titulo"), ("Tipo", "tipo"), ("Entidade", "entidade"), ("Local", "local")],
        busca=("titulo", "entidade", "local"), filtros=("tipo",),
        ajuda="No dia do evento, todos os Irmãos com acesso recebem um lembrete automático.")

# ---------------------------------------------------------------- Tesouraria
ACOES_LANCAMENTO = [("Quitar", "lancamento_quitar", lambda l: l.status == "aberto"),
                    ("Boleto", "boleto_gerar", lambda l: l.tipo == "R" and l.status == "aberto"),
                    ("Estornar", "lancamento_estornar", lambda l: l.status == "pago"),
                    ("Cancelar", "lancamento_cancelar", lambda l: l.status == "aberto")]
COLUNAS_LANCAMENTO = [("Vencimento", "vencimento"), ("Descrição", "descricao"), ("De / para", "quem"),
                      ("Plano", "plano"), ("Valor", "valor"), ("Situação", "status"), ("Quitado em", "data_pagamento")]


def _classe_lancamento(l):
    return "atrasado" if l.vencido else ("apagado" if l.status == "cancelado" else "")


Recurso("receber", Lancamento, "tesouraria", titulo="Contas a receber", form=ReceberForm, fixos={"tipo": "R"},
        relacionados=("irmao", "plano"), colunas=COLUNAS_LANCAMENTO, busca=("descricao", "irmao__nome", "favorecido"),
        filtros=("status", "irmao", "plano"), acoes=ACOES_LANCAMENTO, classe_linha=_classe_lancamento,
        acoes_topo=[("Mensalidades em série", "mensalidades_serie")])

Recurso("pagar", Lancamento, "tesouraria", titulo="Contas a pagar", form=PagarForm, fixos={"tipo": "P"},
        relacionados=("irmao", "plano"), colunas=COLUNAS_LANCAMENTO, busca=("descricao", "irmao__nome", "favorecido"),
        filtros=("status", "plano"), acoes=[a for a in ACOES_LANCAMENTO if a[0] != "Boleto"],
        classe_linha=_classe_lancamento)

Recurso("plano-contas", PlanoConta, "tesouraria", form=PlanoContaForm, relacionados=("pai",),
        colunas=[("Código", "codigo"), ("Conta", lambda p: " " * p.nivel + p.nome), ("Tipo", "tipo"),
                 ("Aceita lançamentos", "aceita_lancamento")],
        busca=("codigo", "nome"), filtros=("tipo",),
        ajuda="As contas ficam em árvore pelo código: 1 Receitas › 1.1 Mensalidades. Contas que só agrupam outras não aceitam lançamentos.")

Recurso("contas", ContaBancaria, "tesouraria", titulo="Contas e caixas",
        campos=["nome", "saldo_inicial", "data_saldo_inicial", "ativa", "banco_codigo", "banco_nome", "agencia",
                "agencia_dv", "conta", "conta_dv", "carteira", "convenio"],
        colunas=[("Conta", "nome"), ("Banco", "banco_nome"), ("Agência", "agencia"), ("Número", "conta"),
                 ("Saldo atual", servicos.saldo_conta), ("Emite boleto", "emite_boleto"), ("Ativa", "ativa")],
        ajuda="Os dados bancários só são necessários na conta que vai emitir boletos.")

Recurso("transferencias", Transferencia, "tesouraria", titulo="Transferências entre contas", form=TransferenciaForm,
        relacionados=("origem", "destino"),
        colunas=[("Data", "data"), ("De", "origem"), ("Para", "destino"), ("Valor", "valor"), ("Descrição", "descricao")])

# --------------------------------------------------------------- Chancelaria
Recurso("sessoes", Sessao, "chancelaria", relacionados=("grau",), campos=["data", "tipo", "grau", "descricao"],
        colunas=[("Data", "data"), ("Tipo", "tipo"), ("Grau", "grau"), ("Descrição", "descricao"),
                 ("Presentes", lambda s: f"{s.presencas.filter(status='P').count()} de {s.presencas.exclude(status='L').count()}")],
        filtros=("tipo",), acoes=[("Registrar presença", "sessao_presenca")],
        apos_salvar=lambda obj, novo, request: servicos.sincronizar_presencas(obj),
        ajuda="Ao criar a sessão, a lista de chamada é montada sozinha com os Irmãos do quadro.")

Recurso("licencas", Licenca, "chancelaria", form=LicencaForm, relacionados=("irmao",),
        colunas=[("Irmão", "irmao"), ("Início", "inicio"), ("Término", "fim"), ("Motivo", "motivo")],
        busca=("irmao__nome",),
        ajuda="Sessões dentro do período da licença não contam como falta nem entram no cálculo de frequência.")

Recurso("visitas", CertificadoVisita, "chancelaria", relacionados=("irmao", "coirma"),
        campos=["irmao", "data", "coirma", "loja_visitada", "observacao"],
        colunas=[("Irmão", "irmao"), ("Data", "data"), ("Loja visitada", "onde"), ("Observação", "observacao")],
        busca=("irmao__nome", "loja_visitada", "coirma__nome"))

# -------------------------------------------------------------- Hospitalaria
Recurso("bens", Bem, "hospitalaria", campos=["codigo", "descricao", "categoria", "estado", "observacoes"],
        colunas=[("Código", "codigo"), ("Descrição", "descricao"), ("Categoria", "categoria"), ("Estado", "estado"),
                 ("Situação", lambda b: (f"Emprestado a {b.emprestimo_atual.beneficiario}" if b.emprestimo_atual else "Disponível"))],
        busca=("descricao", "codigo", "categoria"), filtros=("estado",))

Recurso("emprestimos", Emprestimo, "hospitalaria", form=EmprestimoForm, relacionados=("bem", "irmao"),
        colunas=[("Bem", "bem"), ("Quem usa", "beneficiario"), ("Irmão responsável", "irmao"),
                 ("Emprestado em", "data_emprestimo"), ("Previsão", "previsao_devolucao"), ("Devolvido em", "data_devolucao")],
        busca=("beneficiario", "bem__descricao", "irmao__nome"),
        acoes=[("Registrar devolução", "emprestimo_devolver", lambda e: e.data_devolucao is None)],
        classe_linha=lambda e: "atrasado" if e.atrasado else ("apagado" if e.data_devolucao else ""))

# ------------------------------------------------------------- Administração
def _enviar_senha(usuario, novo, request):
    from django.conf import settings
    from django.contrib import messages
    senha = getattr(usuario, "senha_para_enviar", None)
    if not senha:
        return
    try:
        servicos.enviar_senha_por_email(usuario, senha, request.build_absolute_uri("/"))
    except Exception as erro:  # servidor de e-mail fora do ar, senha do SMTP errada etc.
        messages.error(request, f"A senha foi gravada, mas o e-mail não saiu: {erro}")
        return
    if settings.EMAIL_ENVIADO_DE_VERDADE:
        messages.success(request, f"Senha enviada para {usuario.email}. Ela terá de ser trocada no primeiro acesso.")
    else:
        messages.info(request, "Modo de teste: o servidor de e-mail ainda não está configurado, então a senha foi gravada "
                               f"mas o e-mail para {usuario.email} não saiu de verdade.")


Recurso("usuarios", Usuario, "admin", titulo="Usuários e acessos", form=UsuarioForm, relacionados=("irmao",),
        colunas=[("Login", "username"), ("Nome", "get_full_name"), ("Irmão", "irmao"), ("Master", "is_master"),
                 ("Secretaria", "acesso_secretaria"), ("Tesouraria", "acesso_tesouraria"),
                 ("Chancelaria", "acesso_chancelaria"), ("Hospitalaria", "acesso_hospitalaria"), ("Liberado", "is_active")],
        busca=("username", "first_name", "last_name", "email"), ordem=("username",),
        acoes_topo=[("Criar acesso para todos os Irmãos", "usuarios_criar_todos")], apos_salvar=_enviar_senha,
        ajuda="Todo Irmão com usuário vê os próprios dados. Marque os módulos conforme o cargo de cada um.")

Recurso("graus", Grau, "admin", titulo="Graus da Loja", campos=["nome", "ordem"],
        colunas=[("Ordem", "ordem"), ("Grau", "nome")],
        ajuda="Ajuste a lista conforme o rito da Loja (inclusive graus filosóficos, se quiser controlá-los).")

Recurso("campos-extras", CampoExtra, "admin", titulo="Campos personalizados do cadastro de Irmãos",
        campos=["rotulo", "ordem"], colunas=[("Ordem", "ordem"), ("Campo", "rotulo")],
        ajuda="Crie campos que só a sua Loja usa (ex.: Padrinho, Nome simbólico, Tamanho da camisa). "
              "Eles aparecem no fim do cadastro de cada Irmão.")

MENUS = {
    "secretaria": [("Irmãos", "irmaos_lista"), ("Dependentes", "dependentes_lista"),
                   ("Controle de graus", "graus-historico_lista"), ("Lojas co-Irmãs", "coirmas_lista"),
                   ("Atas", "atas_lista"), ("Informativos", "informativos_lista"), ("Agenda", "eventos_lista"),
                   ("E-mails", "emails"), ("Importar planilha", "importar_irmaos")],
    "tesouraria": [("A receber", "receber_lista"), ("A pagar", "pagar_lista"),
                   ("Mensalidades em série", "mensalidades_serie"), ("Boletos", "boletos"),
                   ("Transferências", "transferencias_lista"), ("Contas e caixas", "contas_lista"),
                   ("Plano de contas", "plano-contas_lista")],
    "chancelaria": [("Sessões e presença", "sessoes_lista"), ("Licenças", "licencas_lista"),
                    ("Certificados de visita", "visitas_lista")],
    "hospitalaria": [("Bens e materiais", "bens_lista"), ("Empréstimos", "emprestimos_lista")],
    "admin": [("Usuários e acessos", "usuarios_lista"), ("Personalizar a Loja", "personalizar"),
              ("Graus", "graus_lista"), ("Campos personalizados", "campos-extras_lista")],
}
INICIO_DO_MODULO = {m: itens[0][1] for m, itens in MENUS.items()}
