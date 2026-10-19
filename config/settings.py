"""Configurações do sistema de gestão de Lojas.

Tudo que muda entre o computador de teste e o servidor de verdade vem de
variáveis de ambiente (veja o arquivo .env.exemplo).
"""
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent


def env(nome, padrao=""):
    return os.environ.get(nome, padrao)


# Sem chave secreta definida = modo de teste no próprio computador.
MODO_TESTE = not env("DJANGO_SECRET_KEY")
SECRET_KEY = env("DJANGO_SECRET_KEY", "chave-apenas-para-teste-local-nao-usar-em-producao")
DEBUG = env("DJANGO_DEBUG", "1" if MODO_TESTE else "0") == "1"
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("DJANGO_CSRF_ORIGINS").split(",") if o.strip()]
# Hospedagens como Render e Railway informam sozinhas o endereço do site.
# Endereço próprio do sistema (domínio ordofraternus.com.br) e outros extras.
DOMINIOS_PROPRIOS = [d.strip() for d in env("DOMINIOS_PROPRIOS", "gestao.ordofraternus.com.br").split(",") if d.strip()]
for _host in (env("RENDER_EXTERNAL_HOSTNAME"), env("RAILWAY_PUBLIC_DOMAIN"), *DOMINIOS_PROPRIOS):
    if _host:
        ALLOWED_HOSTS.append(_host)
        CSRF_TRUSTED_ORIGINS.append(f"https://{_host}")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "gestao",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "gestao.middleware.LojaMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

try:  # arquivos estáticos (CSS) servidos pelo próprio sistema em produção
    import whitenoise  # noqa: F401
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
except ImportError:
    pass

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "gestao.contexto.sistema",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


def _banco(url):
    if not url:
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "dados" / "banco.sqlite3"}
    if url.startswith("sqlite:///"):
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": url[len("sqlite:///"):]}
    # Tolera endereços colados com sobras: "psql '...'", aspas, espaços e quebras de linha.
    url = re.sub(r"\s+", "", url)
    url = re.sub(r"^psql", "", url).strip("'\"")
    # Se faltou o "?" antes dos parâmetros (ex.: "neondbsslmode=require"), recoloca.
    url = re.sub(r"(/[^/?]*?)(sslmode=|channel_binding=)", r"\1?\2", url, count=1) if "?" not in url else url
    p = urlparse(url)
    # Parâmetros do endereço (ex.: ?sslmode=require do Neon) viram opções da conexão.
    opcoes = {k: v[-1] for k, v in parse_qs(p.query).items()}
    host = p.hostname or ""
    if host not in ("", "localhost", "127.0.0.1") and "." in host and "render" not in host and "sslmode" not in opcoes:
        opcoes["sslmode"] = "require"  # bancos externos (Neon etc.) exigem conexão segura
    return {
        "ENGINE": "django.db.backends.postgresql",
        # O nome do banco é sempre o último pedaço do caminho (ignora repetições coladas por engano).
        "NAME": p.path.rstrip("/").rsplit("/", 1)[-1],
        "USER": unquote(p.username or ""),
        "PASSWORD": unquote(p.password or ""),
        "HOST": p.hostname or "",
        "PORT": str(p.port or ""),
        "CONN_MAX_AGE": 60,
        "OPTIONS": opcoes,
    }


# BANCO_URL (ex.: banco gratuito e permanente no Neon) tem prioridade sobre o DATABASE_URL do Render.
# Enquanto os dois existirem, o banco antigo fica disponível como "antigo" para a cópia automática.
_URL_NOVO = env("BANCO_URL")
_URL_ANTIGO = env("DATABASE_URL")
DATABASES = {"default": _banco(_URL_NOVO or _URL_ANTIGO)}
if _URL_NOVO and _URL_ANTIGO and _URL_NOVO != _URL_ANTIGO:
    DATABASES["antigo"] = _banco(_URL_ANTIGO)
(BASE_DIR / "dados").mkdir(exist_ok=True)

AUTH_USER_MODEL = "gestao.Usuario"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "entrar"
LOGIN_REDIRECT_URL = "inicio"
LOGOUT_REDIRECT_URL = "entrar"

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Arquivos enviados (atas, brasões, fotos). Nunca ficam públicos: só saem pela
# tela protegida "arquivo", que confere a Loja e o acesso de quem pede.
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(BASE_DIR / "dados" / "arquivos")))
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- E-mail -----------------------------------------------------------------
# Sem servidor de e-mail configurado, as mensagens aparecem na tela do
# terminal em vez de serem enviadas (bom para testar sem incomodar ninguém).
if env("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env("EMAIL_HOST")
    EMAIL_PORT = int(env("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = env("EMAIL_HOST_USER")
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
    EMAIL_USE_TLS = env("EMAIL_USE_TLS", "1") == "1"
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = env("EMAIL_REMETENTE", "nao-responda@localhost")
EMAIL_ENVIADO_DE_VERDADE = bool(env("EMAIL_HOST"))

# --- Notificações no celular (Web Push) ---------------------------------------
# Gere as chaves com:  python manage.py gerar_chaves_push
PUSH_CHAVE_PUBLICA = env("PUSH_CHAVE_PUBLICA")
PUSH_CHAVE_PRIVADA = env("PUSH_CHAVE_PRIVADA")
PUSH_CONTATO = env("PUSH_CONTATO", "mailto:suporte@localhost")

# --- Segurança em produção -----------------------------------------------------
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env("DJANGO_SSL_REDIRECT", "1") == "1"
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SESSION_COOKIE_AGE = 60 * 60 * 12
