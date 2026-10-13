from django.contrib.auth import views as auth
from django.urls import path, reverse_lazy

from . import views
from .crud import RECURSOS



class TrocarSenha(auth.PasswordChangeView):
    def form_valid(self, form):
        form.user.deve_trocar_senha = False
        return super().form_valid(form)


class CriarNovaSenha(auth.PasswordResetConfirmView):
    def form_valid(self, form):
        form.user.deve_trocar_senha = False
        return super().form_valid(form)


urlpatterns = [
    # entrada e senha
    path("entrar/", auth.LoginView.as_view(template_name="registration/entrar.html", redirect_authenticated_user=True), name="entrar"),
    path("sair/", auth.LogoutView.as_view(), name="sair"),
    path("senha/trocar/", TrocarSenha.as_view(template_name="registration/senha.html", success_url=reverse_lazy("senha_trocada"),
                                                          extra_context={"titulo": "Trocar minha senha", "botao": "Trocar senha"}), name="senha_trocar"),
    path("senha/trocada/", auth.PasswordChangeDoneView.as_view(template_name="registration/aviso.html", extra_context={
        "titulo": "Senha trocada", "texto": "Sua nova senha já está valendo."}), name="senha_trocada"),
    path("senha/esqueci/", auth.PasswordResetView.as_view(
        template_name="registration/senha.html", email_template_name="registration/email_senha.txt",
        subject_template_name="registration/email_senha_assunto.txt", success_url=reverse_lazy("senha_enviada"),
        extra_context={"titulo": "Esqueci minha senha", "botao": "Enviar instruções",
                       "texto": "Informe o e-mail do seu cadastro. Você receberá um link para criar uma nova senha."}), name="senha_esqueci"),
    path("senha/enviada/", auth.PasswordResetDoneView.as_view(template_name="registration/aviso.html", extra_context={
        "titulo": "Confira seu e-mail", "texto": "Se o e-mail informado estiver cadastrado, as instruções chegam em alguns minutos."}), name="senha_enviada"),
    path("senha/nova/<uidb64>/<token>/", CriarNovaSenha.as_view(
        template_name="registration/senha.html", success_url=reverse_lazy("senha_definida"),
        extra_context={"titulo": "Criar nova senha", "botao": "Salvar senha"}), name="senha_nova"),
    path("senha/definida/", auth.PasswordResetCompleteView.as_view(template_name="registration/aviso.html", extra_context={
        "titulo": "Senha criada", "texto": "Pronto! Agora é só entrar com a nova senha."}), name="senha_definida"),

    path("", views.inicio, name="inicio"),
    path("minha-area/", views.minha_area, name="minha_area"),
    path("modulo/<slug:nome>/", views.modulo, name="modulo"),
    path("secretaria/irmaos/<int:pk>/ficha/", views.irmao_ficha, name="irmao_ficha"),
    path("secretaria/atas/<int:pk>/abrir/", views.ata_ver, name="ata_ver"),
    path("secretaria/emails/", views.emails, name="emails"),
    path("secretaria/irmaos/excluir-selecionados/", views.irmaos_excluir_varios, name="irmaos_excluir_varios"),
    path("secretaria/importar/", views.importar_irmaos, name="importar_irmaos"),
    path("secretaria/importar/modelo/", views.importar_modelo, name="importar_modelo"),
    path("chancelaria/sessoes/<int:pk>/presenca/", views.sessao_presenca, name="sessao_presenca"),
    path("chancelaria/presenca/<int:pk>/marcar/", views.presenca_marcar, name="presenca_marcar"),
    path("tesouraria/lancamentos/<int:pk>/quitar/", views.lancamento_quitar, name="lancamento_quitar"),
    path("tesouraria/lancamentos/<int:pk>/estornar/", views.lancamento_estornar, name="lancamento_estornar"),
    path("tesouraria/lancamentos/<int:pk>/cancelar/", views.lancamento_cancelar, name="lancamento_cancelar"),
    path("tesouraria/lancamentos/<int:pk>/boleto/", views.boleto_gerar, name="boleto_gerar"),
    path("tesouraria/mensalidades/", views.mensalidades_serie, name="mensalidades_serie"),
    path("tesouraria/boletos/", views.boletos, name="boletos"),
    path("tesouraria/importar/", views.importar_financeiro, name="importar_financeiro"),
    path("tesouraria/planilha/", views.planilha_financeira, name="planilha_financeira"),
    path("tesouraria/remessas/<int:pk>/baixar/", views.remessa_baixar, name="remessa_baixar"),
    path("boleto/<int:pk>/", views.boleto_ver, name="boleto_ver"),
    path("hospitalaria/emprestimos/<int:pk>/devolver/", views.emprestimo_devolver, name="emprestimo_devolver"),
    path("relatorios/", views.relatorios, name="relatorios"),
    path("relatorios/<slug:slug>/", views.relatorio, name="relatorio"),
    path("notificacoes/", views.notificacoes, name="notificacoes"),
    path("push/inscrever/", views.push_inscrever, name="push_inscrever"),
    path("push/pendentes/", views.push_pendentes, name="push_pendentes"),
    path("sw.js", views.service_worker, name="sw"),
    path("manifest.webmanifest", views.manifesto, name="manifesto"),
    path("administracao/personalizar/", views.personalizar, name="personalizar"),
    path("administracao/usuarios/criar-todos/", views.usuarios_criar_todos, name="usuarios_criar_todos"),
    path("plataforma/lojas/", views.plataforma_lojas, name="plataforma_lojas"),
    path("plataforma/lojas/nova/", views.plataforma_nova_loja, name="plataforma_nova_loja"),
    path("arquivo/<path:caminho>", views.arquivo, name="arquivo"),
]
for recurso in RECURSOS.values():
    urlpatterns += recurso.urls()
