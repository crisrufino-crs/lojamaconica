"""Cria os usuários a partir da tabela de acessos (acessos.csv).

    python manage.py criar_acessos acessos.csv --site sistema.suaempresa.com.br --enviar-emails

A senha do Administrador é pedida na hora (não fica gravada em nenhum arquivo).
Os demais usuários nascem sem senha conhecida por ninguém: cada um cria a sua
pelo e-mail de recuperação ("Esqueci minha senha").
"""
import csv
import getpass
import os
import secrets

from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction

from gestao import servicos
from gestao.models import Irmao, Loja, Usuario

MODULOS = ("secretaria", "tesouraria", "chancelaria", "hospitalaria")
PERFIS = ("administrador", "master", "irmao") + MODULOS


class Command(BaseCommand):
    help = "Cria/atualiza os usuários da tabela de acessos. Perfis: " + ", ".join(PERFIS) + " (pode combinar com +)."

    def add_arguments(self, parser):
        parser.add_argument("tabela", help="Arquivo CSV com as colunas: perfil;login;nome;email;loja")
        parser.add_argument("--senha-admin", help="Senha do Administrador (se omitida, é pedida na tela ou lida de ADMIN_SENHA).")
        parser.add_argument("--site", help="Endereço do sistema, usado no link do e-mail. Ex.: sistema.suaempresa.com.br")
        parser.add_argument("--enviar-emails", action="store_true", help="Envia já o e-mail para cada um criar a senha.")

    def handle(self, *args, **o):
        if o["enviar_emails"] and not o["site"]:
            raise CommandError("Para enviar os e-mails, informe o endereço do sistema com --site.")
        try:
            with open(o["tabela"], encoding="utf-8-sig", newline="") as arquivo:
                texto = arquivo.read()
        except OSError as erro:
            raise CommandError(f"Não consegui abrir a tabela: {erro}")
        separador = ";" if texto.splitlines()[0].count(";") >= texto.splitlines()[0].count(",") else ","
        linhas = [{(k or "").strip().lower(): (v or "").strip() for k, v in l.items()}
                  for l in csv.DictReader(texto.splitlines(), delimiter=separador)]
        faltando = {"perfil", "login", "nome", "email", "loja"} - set(linhas[0] if linhas else {})
        if faltando:
            raise CommandError("A tabela precisa das colunas perfil;login;nome;email;loja. Faltando: " + ", ".join(sorted(faltando)))

        erros, vistos = [], set()
        for n, l in enumerate(linhas, 2):
            perfis = [p.strip().lower() for p in l["perfil"].split("+") if p.strip()]
            l["perfis"] = perfis
            if not l["login"]:
                erros.append(f"linha {n}: falta o login")
            elif l["login"].lower() in vistos:
                erros.append(f"linha {n}: login repetido ({l['login']})")
            vistos.add(l["login"].lower())
            if not perfis or any(p not in PERFIS for p in perfis):
                erros.append(f"linha {n}: perfil “{l['perfil']}” não existe (use: {', '.join(PERFIS)})")
            admin = "administrador" in perfis
            if admin and len(perfis) > 1:
                erros.append(f"linha {n}: administrador não se combina com outros perfis")
            if not admin and not l["loja"]:
                erros.append(f"linha {n}: falta o nome da Loja")
            if l["email"]:
                try:
                    validate_email(l["email"])
                except ValidationError:
                    erros.append(f"linha {n}: e-mail inválido ({l['email']})")
            elif not admin:
                erros.append(f"linha {n}: falta o e-mail (sem ele a pessoa não consegue criar a senha)")
        if erros:
            raise CommandError("Corrija a tabela e rode de novo:\n  " + "\n  ".join(erros))

        senha_admin = None
        if any("administrador" in l["perfis"] for l in linhas):
            senha_admin = o["senha_admin"] or os.environ.get("ADMIN_SENHA") or getpass.getpass("Senha do Administrador: ")
            try:
                validate_password(senha_admin)
            except ValidationError as erro:
                raise CommandError("Senha do Administrador recusada: " + " ".join(erro.messages))

        resultado, novos = [], []
        with transaction.atomic():
            for l in linhas:
                admin = "administrador" in l["perfis"]
                loja = None
                if not admin:
                    loja, criada = Loja.objects.get_or_create(nome=l["loja"])
                    if criada:
                        servicos.preparar_loja(loja)
                usuario = Usuario.objects.filter(username__iexact=l["login"]).first()
                novo = usuario is None
                if novo:
                    usuario = Usuario(username=l["login"])
                elif not admin and usuario.loja_id not in (None, loja.pk):
                    raise CommandError(f"O login “{l['login']}” já existe em outra Loja. Escolha outro login.")
                partes = l["nome"].split(" ", 1)
                usuario.first_name, usuario.last_name = partes[0][:150], (partes[1] if len(partes) > 1 else "")[:150]
                usuario.email, usuario.loja, usuario.is_active = l["email"].lower(), loja, True
                usuario.is_superuser = usuario.is_staff = admin
                usuario.is_master = "master" in l["perfis"]
                for m in MODULOS:
                    setattr(usuario, f"acesso_{m}", m in l["perfis"])
                if admin:
                    usuario.set_password(senha_admin)
                    senha = "definida agora"
                elif novo:
                    usuario.set_password(secrets.token_urlsafe(24))  # ninguém conhece; a pessoa cria a dela por e-mail
                    senha = "a criar pelo e-mail de recuperação"
                    novos.append(usuario)
                else:
                    senha = "mantida (já existia)"
                if loja and usuario.irmao_id is None:
                    usuario.irmao = Irmao.objects.filter(loja=loja, email__iexact=usuario.email, usuario__isnull=True).first()
                usuario.save()
                resultado.append((l["login"], "+".join(l["perfis"]), str(loja or "todas as Lojas"), usuario.email or "—", senha))

        self.stdout.write(f"{'LOGIN':<22}{'PERFIL':<26}{'LOJA':<30}{'E-MAIL':<34}SENHA")
        for r in resultado:
            self.stdout.write(f"{r[0]:<22}{r[1]:<26}{r[2][:28]:<30}{r[3][:32]:<34}{r[4]}")
        if o["enviar_emails"]:
            for usuario in novos:
                form = PasswordResetForm({"email": usuario.email})
                if form.is_valid():
                    form.save(domain_override=o["site"], use_https=True, email_template_name="registration/email_senha.txt",
                              subject_template_name="registration/email_senha_assunto.txt")
            self.stdout.write(self.style.SUCCESS(f"E-mail para criar a senha enviado a {len(novos)} pessoa(s)."))
        elif novos:
            self.stdout.write(f"{len(novos)} pessoa(s) ainda sem senha: cada uma clica em “Esqueci minha senha” na tela de entrada "
                              "e informa o e-mail da tabela (ou rode de novo com --enviar-emails em usuários novos).")
