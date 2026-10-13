from decimal import Decimal

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone

from .models import (Bem, CampoExtra, ContaBancaria, Emprestimo, Irmao, Lancamento, Licenca, Loja, PlanoConta,
                     Transferencia, Usuario)


def _ajustar_campos(form, loja=None):
    """Calendário nos campos de data e, principalmente, só opções da própria Loja nas listas."""
    for campo in form.fields.values():
        if (loja is not None and isinstance(campo, forms.ModelChoiceField) and campo.queryset is not None
                and hasattr(campo.queryset.model, "loja")):
            campo.queryset = campo.queryset.filter(loja=loja)
        if isinstance(campo, forms.DateTimeField):
            campo.widget = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
        elif isinstance(campo, forms.DateField):
            campo.widget = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
        elif isinstance(campo, forms.TimeField):
            campo.widget = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")
        elif isinstance(campo.widget, forms.Textarea):
            campo.widget.attrs.setdefault("rows", 5)


class FormDaLoja(forms.ModelForm):
    """Base de todos os formulários: grava sempre na Loja de quem está usando."""
    fixos = {}

    def __init__(self, *args, loja, **kwargs):
        super().__init__(*args, **kwargs)
        self.loja = loja
        self.instance.loja = loja  # já na validação, para conferir "não pode repetir dentro da Loja"
        _ajustar_campos(self, loja)

    def _get_validation_exclusions(self):
        exclusoes = super()._get_validation_exclusions()
        exclusoes.discard("loja")
        return exclusoes

    def save(self, commit=True):
        self.instance.loja = self.loja
        for campo, valor in self.fixos.items():
            setattr(self.instance, campo, valor)
        return super().save(commit)


class FormSimples(forms.Form):
    def __init__(self, *args, loja=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.loja = loja
        _ajustar_campos(self, loja)


class IrmaoForm(FormDaLoja):
    class Meta:
        model = Irmao
        exclude = ["loja", "extras", "grau"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.campos_extras = list(CampoExtra.objects.filter(loja=self.loja))
        for extra in self.campos_extras:
            self.fields[f"extra_{extra.pk}"] = forms.CharField(
                label=extra.rotulo, required=False, max_length=200,
                initial=(self.instance.extras or {}).get(str(extra.pk), ""))

    def save(self, commit=True):
        self.instance.extras = {str(e.pk): self.cleaned_data.get(f"extra_{e.pk}", "") for e in self.campos_extras
                                if self.cleaned_data.get(f"extra_{e.pk}")}
        return super().save(commit)


class LancamentoForm(FormDaLoja):
    tipo_plano = None

    class Meta:
        model = Lancamento
        fields = ["descricao", "irmao", "favorecido", "plano", "valor", "vencimento", "competencia", "observacao"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plano"].queryset = self.fields["plano"].queryset.filter(tipo=self.tipo_plano, aceita_lancamento=True)
        self.fields["irmao"].queryset = self.fields["irmao"].queryset.order_by("nome")

    def clean(self):
        dados = super().clean()
        if not dados.get("irmao") and not dados.get("favorecido"):
            self.add_error("favorecido", "Informe o Irmão ou o nome do fornecedor/pagador.")
        if dados.get("valor") is not None and dados["valor"] <= 0:
            self.add_error("valor", "O valor precisa ser maior que zero.")
        if self.instance.pk and self.instance.status == "pago":
            raise forms.ValidationError("Este lançamento já está quitado. Estorne a quitação antes de alterar.")
        return dados


class ReceberForm(LancamentoForm):
    """Conta a receber. Com o plano “Remido”, o valor sugerido é R$ 0,00 (isenção), mas pode ser trocado
    pelo valor proposto (a mensalidade do Irmão) ou por qualquer outro valor."""
    fixos, tipo_plano = {"tipo": "R"}, "R"

    def __init__(self, *args, **kwargs):
        import json
        super().__init__(*args, **kwargs)
        remidos = [str(p.pk) for p in self.fields["plano"].queryset if p.eh_remido]
        padrao = self.loja.valor_mensalidade or Decimal("0")
        valores = {str(i.pk): str(i.mensalidade_individual or padrao)
                   for i in self.fields["irmao"].queryset.only("pk", "mensalidade_individual")}
        self.fields["plano"].widget.attrs["data-planos-remido"] = json.dumps(remidos)
        self.fields["valor"].widget.attrs.update({"data-proposto": str(padrao), "data-por-irmao": json.dumps(valores)})
        self.fields["valor"].help_text = ("Com o plano “Remido”, o valor vem como R$ 0,00 (isento). "
                                          "Você pode manter R$ 0,00 ou informar o valor proposto.")

    def clean(self):
        plano = self.cleaned_data.get("plano")
        if plano is not None and plano.eh_remido:
            dados = forms.ModelForm.clean(self)
            if not dados.get("irmao") and not dados.get("favorecido"):
                self.add_error("favorecido", "Informe o Irmão ou o nome do fornecedor/pagador.")
            if dados.get("valor") is not None and dados["valor"] < 0:
                self.add_error("valor", "O valor não pode ser negativo.")
            if self.instance.pk and self.instance.status == "pago":
                raise forms.ValidationError("Este lançamento já está quitado. Estorne a quitação antes de alterar.")
            return dados
        return super().clean()

    def save(self, commit=True):
        l = self.instance
        if l.status in ("aberto", "isento"):
            if l.plano_id and l.plano.eh_remido and not l.valor:
                # R$ 0,00 no plano Remido: isenção registrada, não é dívida
                l.status, l.valor, l.valor_pago = "isento", Decimal("0"), Decimal("0")
                if not l.observacao:
                    l.observacao = "Remido: isento de mensalidade"
            else:
                l.status, l.valor_pago = "aberto", None
        return super().save(commit)


class PagarForm(LancamentoForm):
    fixos, tipo_plano = {"tipo": "P"}, "D"


class QuitarForm(FormSimples):
    data = forms.DateField(label="Data da quitação", initial=timezone.localdate)
    valor = forms.DecimalField(label="Valor pago", max_digits=12, decimal_places=2, min_value=0.01)
    conta = forms.ModelChoiceField(label="Conta", queryset=ContaBancaria.objects.filter(ativa=True))


class TransferenciaForm(FormDaLoja):
    class Meta:
        model = Transferencia
        fields = ["origem", "destino", "valor", "data", "descricao"]

    def clean(self):
        dados = super().clean()
        if dados.get("origem") and dados.get("origem") == dados.get("destino"):
            self.add_error("destino", "Escolha uma conta de destino diferente da origem.")
        if dados.get("valor") is not None and dados["valor"] <= 0:
            self.add_error("valor", "O valor precisa ser maior que zero.")
        return dados


class PlanoContaForm(FormDaLoja):
    class Meta:
        model = PlanoConta
        fields = ["codigo", "nome", "tipo", "pai", "aceita_lancamento"]

    def clean(self):
        dados = super().clean()
        pai = dados.get("pai")
        if pai and self.instance.pk and pai.pk == self.instance.pk:
            self.add_error("pai", "Uma conta não pode ser superior a ela mesma.")
        if pai and dados.get("tipo") and pai.tipo != dados["tipo"]:
            self.add_error("pai", "A conta superior precisa ser do mesmo tipo (receita ou despesa).")
        codigo = dados.get("codigo")
        if codigo and PlanoConta.objects.filter(loja=self.loja, codigo=codigo).exclude(pk=self.instance.pk).exists():
            self.add_error("codigo", "Já existe uma conta com este código.")
        return dados


class MensalidadesForm(FormSimples):
    plano = forms.ModelChoiceField(label="Plano de contas", queryset=PlanoConta.objects.filter(tipo="R", aceita_lancamento=True))
    descricao = forms.CharField(label="Descrição", initial="Mensalidade", max_length=100,
                                help_text="O mês de referência é acrescentado automaticamente.")
    valor = forms.DecimalField(label="Valor de cada mensalidade", max_digits=10, decimal_places=2, min_value=0,
                               required=False, help_text="Não é preciso preencher quando o plano de contas é “Remido”.")
    primeiro_vencimento = forms.DateField(label="Primeiro vencimento")
    meses = forms.IntegerField(label="Quantidade de meses", min_value=1, max_value=24, initial=12)
    irmaos = forms.ModelMultipleChoiceField(
        label="Irmãos que pagam mensalidade", queryset=Irmao.objects.filter(situacao__in=["ativo", "licenciado", "irregular", "remido"]),
        widget=forms.CheckboxSelectMultiple, required=False)
    remidos = forms.ModelMultipleChoiceField(
        label="Remidos (isentos de mensalidade)", required=False,
        queryset=Irmao.objects.filter(situacao__in=["ativo", "licenciado", "irregular", "remido"]),
        widget=forms.CheckboxSelectMultiple,
        help_text="Marque os Irmãos remidos: eles não recebem mensalidade e ficam com a situação “Remido” no cadastro. "
                  "Desmarcar um remido o devolve à situação “Ativo”.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nome in ("irmaos", "remidos"):
            self.fields[nome].queryset = self.fields[nome].queryset.order_by("nome")

    def clean(self):
        dados = super().clean()
        remidos = set(dados.get("remidos") or [])
        plano = dados.get("plano")
        if plano is not None and plano.eh_remido:
            # Plano "Remido": registra a isenção (descrição "Isento", sem valor a pagar) para os Remidos marcados
            dados["valor"] = Decimal("0")
            if not dados.get("descricao") or dados["descricao"].strip().lower() == "mensalidade":
                dados["descricao"] = "Isento"
            dados["irmaos"] = sorted(remidos, key=lambda i: i.nome)
            if not dados["irmaos"]:
                self.add_error("remidos", "Com o plano “Remido”, marque na lista de Remidos quem fica isento.")
            return dados
        dados["irmaos"] = [i for i in (dados.get("irmaos") or []) if i not in remidos]  # remido nunca é cobrado
        if not dados["irmaos"] and "irmaos" not in self.errors:
            self.add_error("irmaos", "Marque pelo menos um Irmão que paga mensalidade.")
        if dados.get("valor") in (None, Decimal("0")) and "valor" not in self.errors:
            self.add_error("valor", "Informe o valor de cada mensalidade.")
        return dados


class LicencaForm(FormDaLoja):
    class Meta:
        model = Licenca
        fields = ["irmao", "inicio", "fim", "motivo"]

    def clean(self):
        dados = super().clean()
        if dados.get("inicio") and dados.get("fim") and dados["fim"] < dados["inicio"]:
            self.add_error("fim", "O término não pode ser antes do início.")
        return dados


class EmprestimoForm(FormDaLoja):
    class Meta:
        model = Emprestimo
        fields = ["bem", "irmao", "beneficiario", "contato", "data_emprestimo", "previsao_devolucao", "observacoes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        emprestados = Emprestimo.objects.filter(loja=self.loja, data_devolucao__isnull=True).exclude(pk=self.instance.pk)
        self.fields["bem"].queryset = (Bem.objects.filter(loja=self.loja)
                                       .exclude(estado__in=["baixado", "manutencao"])
                                       .exclude(pk__in=emprestados.values("bem_id")))
        self.fields["bem"].help_text = "Só aparecem os bens disponíveis no momento."


class EmailForm(FormSimples):
    PUBLICOS = [("ativos", "Irmãos ativos"), ("quadro", "Todo o quadro (ativos, licenciados, remidos...)"),
                ("grau", "Irmãos de um grau"), ("coirmas", "Lojas co-Irmãs"), ("escolher", "Escolher Irmãos")]
    publico = forms.ChoiceField(label="Enviar para", choices=PUBLICOS)
    grau = forms.ModelChoiceField(label="Grau (a partir de)", required=False, queryset=None)
    irmaos = forms.ModelMultipleChoiceField(label="Irmãos", required=False, queryset=None,
                                            widget=forms.CheckboxSelectMultiple)
    assunto = forms.CharField(max_length=150)
    corpo = forms.CharField(label="Mensagem", widget=forms.Textarea(attrs={"rows": 10}),
                            help_text="Escreva {nome} onde quiser que apareça o nome de quem recebe.")

    def __init__(self, *args, **kwargs):
        from .models import Grau
        super().__init__(*args, **kwargs)
        self.fields["grau"].queryset = Grau.objects.filter(loja=self.loja)
        self.fields["irmaos"].queryset = Irmao.objects.filter(loja=self.loja, situacao__in=Irmao.NO_QUADRO).exclude(email="")

    def clean(self):
        dados = super().clean()
        if dados.get("publico") == "grau" and not dados.get("grau"):
            self.add_error("grau", "Escolha o grau.")
        if dados.get("publico") == "escolher" and not dados.get("irmaos"):
            self.add_error("irmaos", "Marque pelo menos um Irmão.")
        return dados


class UsuarioForm(FormDaLoja):
    senha = forms.CharField(label="Senha", required=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
                            help_text="Mínimo de 8 caracteres. Na edição, deixe em branco para manter a senha atual.")
    enviar_senha = forms.BooleanField(
        label="Enviar esta senha para o e-mail cadastrado", required=False, initial=True,
        help_text="A pessoa recebe o login e a senha por e-mail e é obrigada a trocá-la no primeiro acesso.")

    class Meta:
        model = Usuario
        fields = ["username", "first_name", "last_name", "email", "irmao", "is_active", "is_master",
                  "acesso_secretaria", "acesso_tesouraria", "acesso_chancelaria", "acesso_hospitalaria"]
        labels = {"username": "Nome de acesso (login)", "first_name": "Nome", "last_name": "Sobrenome",
                  "is_active": "Acesso liberado"}
        help_texts = {"username": "É o que a pessoa digita para entrar. Ex.: o e-mail ou o CIM.",
                      "is_active": "Desmarque para bloquear a entrada sem apagar o usuário."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["irmao"].help_text = "Ligue o usuário ao cadastro do Irmão para ele ver os próprios dados."

    def clean_senha(self):
        senha = self.cleaned_data.get("senha")
        if not senha and not self.instance.pk:
            raise forms.ValidationError("Defina uma senha para o novo usuário.")
        if senha:
            validate_password(senha, self.instance)
        return senha

    def clean(self):
        dados = super().clean()
        if dados.get("senha") and dados.get("enviar_senha") and not dados.get("email"):
            self.add_error("email", "Preencha o e-mail para enviar a senha (ou desmarque o envio).")
        return dados

    def save(self, commit=True):
        senha = self.cleaned_data.get("senha")
        if senha:
            self.instance.set_password(senha)
            if self.cleaned_data.get("enviar_senha"):
                self.instance.deve_trocar_senha = True
                self.instance.senha_para_enviar = senha  # só na memória, para o envio logo após salvar
        return super().save(commit)


class PersonalizarForm(forms.ModelForm):
    class Meta:
        model = Loja
        exclude = ["ativa", "criada_em"]
        widgets = {"cor_primaria": forms.TextInput(attrs={"type": "color"}),
                   "cor_destaque": forms.TextInput(attrs={"type": "color"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _ajustar_campos(self)

    def clean_dia_vencimento(self):
        dia = self.cleaned_data["dia_vencimento"]
        if not 1 <= dia <= 31:
            raise forms.ValidationError("Use um dia entre 1 e 31.")
        return dia

    def clean_frequencia_minima(self):
        valor = self.cleaned_data["frequencia_minima"]
        if valor > 100:
            raise forms.ValidationError("Use um percentual de 0 a 100.")
        return valor


class NovaLojaForm(forms.ModelForm):
    master_login = forms.CharField(label="Login do usuário Master", max_length=150)
    master_nome = forms.CharField(label="Nome do usuário Master", max_length=150)
    master_email = forms.EmailField(label="E-mail do usuário Master")
    master_senha = forms.CharField(label="Senha inicial do Master", widget=forms.PasswordInput, min_length=8)

    class Meta:
        model = Loja
        fields = ["nome", "numero", "potencia", "rito", "oriente", "uf", "email"]

    def clean_master_login(self):
        login = self.cleaned_data["master_login"]
        if Usuario.objects.filter(username=login).exists():
            raise forms.ValidationError("Já existe um usuário com este login.")
        return login

    def clean_master_senha(self):
        validate_password(self.cleaned_data["master_senha"])
        return self.cleaned_data["master_senha"]


class ImportarFinanceiroForm(FormSimples):
    arquivo = forms.FileField(label="Planilha financeira (.xlsx)")
    ano = forms.IntegerField(label="Ano da planilha", min_value=2000, max_value=2100, initial=lambda: timezone.localdate().year)
    em_branco = forms.ChoiceField(
        label="Meses em branco", initial="ignorar",
        choices=[("ignorar", "Não lançar nada (só os meses marcados entram no sistema)"),
                 ("aberto", "Lançar como mensalidade em aberto, até o mês atual")])
    conta = forms.ModelChoiceField(label="Conta onde os pagamentos entram", queryset=ContaBancaria.objects.filter(ativa=True))
    incluir_novos = forms.BooleanField(label="Incluir no cadastro os Obreiros que não forem encontrados", required=False, initial=True)
    importar_tronco = forms.BooleanField(label="Importar também o tronco de beneficência (aba Tronco)", required=False, initial=True)

    def clean_arquivo(self):
        arquivo = self.cleaned_data["arquivo"]
        if not arquivo.name.lower().endswith(".xlsx"):
            raise forms.ValidationError("Envie um arquivo do Excel com final .xlsx (Arquivo › Salvar como › Pasta de Trabalho do Excel).")
        if arquivo.size > 5 * 1024 * 1024:
            raise forms.ValidationError("A planilha passa de 5 MB.")
        return arquivo
