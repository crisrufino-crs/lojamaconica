"""Telas de cadastro genéricas: listar, incluir, alterar e excluir.

Cada cadastro do sistema é descrito uma vez (veja cadastros.py) e ganha as
quatro telas prontas, sempre presas à Loja de quem está usando.
"""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import CharField, ProtectedError, Q
from django.db.models.functions import Lower
from django.forms import modelform_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse

from .forms import FormDaLoja
from .permissoes import exige

RECURSOS = {}


class Recurso:
    def __init__(self, slug, modelo, modulo, colunas, campos=None, form=None, busca=(), filtros=(), fixos=None,
                 acoes=(), acoes_topo=(), apos_salvar=None, titulo=None, ajuda="", classe_linha=None,
                 pode_incluir=True, pode_excluir=True, ordem=None, relacionados=(), selecao=None, aviso_exclusao=None, alfabetica=None):
        self.slug, self.modelo, self.modulo, self.colunas = slug, modelo, modulo, colunas
        self.busca, self.filtros, self.fixos = busca, filtros, fixos or {}
        self.acoes, self.acoes_topo, self.apos_salvar = acoes, acoes_topo, apos_salvar
        self.titulo = titulo or str(modelo._meta.verbose_name_plural).capitalize()
        self.singular = str(modelo._meta.verbose_name)
        self.ajuda, self.classe_linha = ajuda, classe_linha
        self.pode_incluir, self.pode_excluir, self.ordem, self.relacionados = pode_incluir, pode_excluir, ordem, relacionados
        # Nome da tela que recebe os registros marcados na lista (exclusão em lote). Todas as listas dos módulos
        # têm; a de usuários (Administração) não, para ninguém apagar os acessos por engano.
        self.selecao = selecao or (f"{slug}_excluir_varios" if pode_excluir and modulo != "admin" else None)
        self.aviso_exclusao = aviso_exclusao  # função(consulta) -> texto de alerta mostrado na confirmação
        self.alfabetica = alfabetica or self._campo_alfabetico()  # campo (ou expressão) usado na ordem alfabética
        self.form = form or modelform_factory(modelo, form=FormDaLoja, fields=campos or "__all__")
        if fixos and not getattr(self.form, "fixos", None):
            self.form = type(f"{modelo.__name__}Form", (self.form,), {"fixos": fixos})
        for _, coluna in colunas:
            if callable(coluna):  # sem isto o Django tentaria executar a função sozinho ao montar a tela
                coluna.do_not_call_in_templates = True
        RECURSOS[slug] = self

    ORDENS = [("", "Padrão"), ("az", "Alfabética (A → Z)"), ("za", "Alfabética (Z → A)")]

    def _campo_alfabetico(self):
        """O campo de texto principal da lista: o primeiro da busca ou a primeira coluna de texto."""
        candidatos = list(self.busca) + [c.replace(".", "__") for _, c in self.colunas if isinstance(c, str)]
        for nome in candidatos:
            modelo, partes = self.modelo, nome.split("__")
            try:
                for parte in partes[:-1]:
                    modelo = modelo._meta.get_field(parte).related_model
                if isinstance(modelo._meta.get_field(partes[-1]), CharField):
                    return nome
            except Exception:
                continue
        return None

    def ordenar(self, qs, ordem):
        """Ordem alfabética de verdade em português: ignora acentos e maiúsculas (Álvaro vem antes de Bruno)."""
        if ordem not in ("az", "za") or not self.alfabetica:
            return qs
        import unicodedata
        from django.db.models import Case, IntegerField, When
        expressao = Lower(self.alfabetica)
        if qs.count() > 3000:  # listas muito grandes: ordem do próprio banco de dados (mais rápida)
            return qs.order_by(expressao.desc(nulls_last=True) if ordem == "za" else expressao.asc(nulls_last=True), "pk")
        valores = list(qs.annotate(_alfa=expressao).values_list("pk", "_alfa"))
        chave = lambda par: (par[1] is None or par[1] == "",  # noqa: E731
                             unicodedata.normalize("NFKD", par[1] or "").encode("ascii", "ignore").decode(), par[0])
        valores.sort(key=chave)
        if ordem == "za":
            vazios = [v for v in valores if not v[1]]
            valores = [v for v in valores if v[1]][::-1] + vazios
        if not valores:
            return qs
        posicao = Case(*[When(pk=pk, then=n) for n, (pk, _) in enumerate(valores)], output_field=IntegerField())
        return qs.order_by(posicao)

    # ---- consultas ---------------------------------------------------------
    def consulta(self, request):
        qs = self.modelo.objects.filter(loja=request.loja, **self.fixos)
        if self.relacionados:
            qs = qs.select_related(*self.relacionados)
        return qs.order_by(*self.ordem) if self.ordem else qs

    def opcoes_filtro(self, request):
        opcoes = []
        for nome in self.filtros:
            campo = self.modelo._meta.get_field(nome)
            if campo.choices:
                escolhas = [(str(v), r) for v, r in campo.choices]
            else:
                escolhas = [(str(o.pk), str(o)) for o in campo.related_model.objects.filter(loja=request.loja)]
            opcoes.append({"nome": nome, "rotulo": str(campo.verbose_name).capitalize(), "escolhas": escolhas,
                           "atual": request.GET.get(nome, "")})
        return opcoes

    # ---- telas ---------------------------------------------------------------
    def filtrada(self, request, parametros):
        """A lista já com a busca e os filtros escolhidos na tela. Devolve (consulta, termo, filtros)."""
        qs = self.consulta(request)
        termo = parametros.get("q", "").strip()
        if termo and self.busca:
            condicao = Q()
            for campo in self.busca:
                condicao |= Q(**{f"{campo}__icontains": termo})
            qs = qs.filter(condicao)
        filtros = self.opcoes_filtro(request)
        for f in filtros:
            f["atual"] = parametros.get(f["nome"], "")
            if f["atual"] and f["atual"] in dict(f["escolhas"]):
                qs = qs.filter(**{f["nome"]: f["atual"]})
        return self.ordenar(qs, parametros.get("ordem", "")), termo, filtros

    def lista(self, request):
        qs, termo, filtros = self.filtrada(request, request.GET)
        pagina = Paginator(qs, 50).get_page(request.GET.get("pagina"))
        parametros = request.GET.copy()
        parametros.pop("pagina", None)
        linhas = [{"obj": o, "classe": self.classe_linha(o) if self.classe_linha else "",
                   "acoes": [(a[0], reverse(a[1], args=[o.pk])) for a in self.acoes if len(a) < 3 or a[2](o)]}
                  for o in pagina]
        return render(request, "gestao/lista.html", {
            "recurso": self, "modulo": self.modulo, "pagina": pagina, "linhas": linhas, "termo": termo, "filtros": filtros,
            "total": pagina.paginator.count, "parametros": parametros.urlencode(),
            "ordens": self.ORDENS if self.alfabetica else [], "ordem_atual": request.GET.get("ordem", "")})

    def editar(self, request, pk=None):
        objeto = get_object_or_404(self.modelo, pk=pk, loja=request.loja, **self.fixos) if pk else None
        if objeto is None and not self.pode_incluir:
            return redirect(f"{self.slug}_lista")
        iniciais = {k: v for k, v in request.GET.items()} if objeto is None else None
        form = self.form(request.POST or None, request.FILES or None, instance=objeto, loja=request.loja, initial=iniciais)
        if request.method == "POST" and form.is_valid():
            salvo = form.save()
            if self.apos_salvar:
                self.apos_salvar(salvo, objeto is None, request)
            messages.success(request, f"{self.singular.capitalize()} {'incluído(a)' if objeto is None else 'atualizado(a)'} com sucesso.")
            destino = request.POST.get("voltar") or request.GET.get("voltar")
            if destino and destino.startswith("/") and not destino.startswith("//"):
                return redirect(destino)
            return redirect(f"{self.slug}_lista")
        return render(request, "gestao/form.html", {"recurso": self, "modulo": self.modulo, "form": form, "objeto": objeto,
                                                    "titulo": f"{'Alterar' if objeto else 'Incluir'} {self.singular}",
                                                    "voltar": reverse(f"{self.slug}_lista")})

    def excluir(self, request, pk):
        objeto = get_object_or_404(self.modelo, pk=pk, loja=request.loja, **self.fixos)
        if objeto == request.user:
            messages.error(request, "Você não pode excluir o seu próprio usuário.")
            return redirect(f"{self.slug}_lista")
        if request.method == "POST" and self.pode_excluir:
            try:
                objeto.delete()
                messages.success(request, f"{self.singular.capitalize()} excluído(a).")
            except ProtectedError:
                messages.error(request, "Não é possível excluir: existem outros registros que dependem deste. "
                                        "Exclua ou altere esses registros primeiro.")
            return redirect(f"{self.slug}_lista")
        return render(request, "gestao/excluir.html", {"recurso": self, "modulo": self.modulo, "objeto": objeto,
                                                       "voltar": reverse(f"{self.slug}_lista")})

    def excluir_varios(self, request):
        """Exclusão em lote dos registros marcados na lista: confere na tela e só apaga com a palavra EXCLUIR."""
        if request.method != "POST":
            return redirect(f"{self.slug}_lista")
        if request.POST.get("todos") == "1" and "confirmar" not in request.POST:
            escolhidos = self.filtrada(request, request.POST)[0]
        else:
            ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
            escolhidos = self.consulta(request).filter(pk__in=ids)
        total = escolhidos.count()
        if not total:
            messages.error(request, "Nenhum registro foi selecionado.")
            return redirect(f"{self.slug}_lista")
        erro = ""
        if "confirmar" in request.POST:
            if request.POST.get("palavra", "").strip().upper() != "EXCLUIR":
                erro = "Para confirmar, digite a palavra EXCLUIR no campo."
            else:
                pendentes, excluidos = list(escolhidos), 0
                while pendentes:  # repete enquanto houver avanço: um registro pode depender de outro da própria seleção
                    restantes = []
                    for objeto in pendentes:
                        try:
                            with transaction.atomic():
                                objeto.delete()
                            excluidos += 1
                        except ProtectedError:
                            restantes.append(objeto)
                    if len(restantes) == len(pendentes):
                        break
                    pendentes = restantes
                if excluidos:
                    messages.success(request, f"{excluidos} registro(s) excluído(s) em {self.titulo}.")
                if pendentes:
                    messages.error(request, f"{len(pendentes)} registro(s) foram mantidos porque outros dados dependem deles "
                                            "(por exemplo, lançamentos ligados a uma conta). Exclua esses dados primeiro.")
                return redirect(f"{self.slug}_lista")
        return render(request, "gestao/excluir_varios.html", {
            "modulo": self.modulo, "total": total, "ids": list(escolhidos.values_list("pk", flat=True)), "erro": erro,
            "titulo": f"Excluir {total} registro(s) de {self.titulo}?", "acao": reverse(self.selecao),
            "voltar": reverse(f"{self.slug}_lista"), "amostra": [str(o) for o in escolhidos[:12]],
            "descricao": "Os registros selecionados serão apagados, junto com o que pertence só a eles.",
            "alerta": self.aviso_exclusao(escolhidos) if self.aviso_exclusao else ""})

    def urls(self):
        protege = exige(self.modulo)
        base = f"{self.modulo}/{self.slug}/" if self.modulo != "admin" else f"administracao/{self.slug}/"
        return [
            path(base, protege(self.lista), name=f"{self.slug}_lista"),
            path(base + "novo/", protege(self.editar), name=f"{self.slug}_novo"),
            path(base + "<int:pk>/", protege(self.editar), name=f"{self.slug}_editar"),
            path(base + "<int:pk>/excluir/", protege(self.excluir), name=f"{self.slug}_excluir"),
        ] + ([path(base + "excluir-selecionados/", protege(self.excluir_varios), name=f"{self.slug}_excluir_varios")]
             if self.selecao == f"{self.slug}_excluir_varios" else [])
