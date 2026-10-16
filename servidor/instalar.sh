#!/usr/bin/env bash
# =============================================================================
#  Instalador automático do Sistema de Gestão de Lojas em um servidor VPS
#  (Ubuntu 22.04 ou 24.04 — por exemplo, o VPS da Hostinger).
#
#  Como usar (no terminal do VPS, como root):
#     curl -fsSL https://raw.githubusercontent.com/crisrufino-crs/lojamaconica/main/servidor/instalar.sh | bash
#
#  Pode ser rodado de novo a qualquer momento: nesse caso ele ATUALIZA o sistema
#  (baixa a versão nova, ajusta o banco e reinicia), sem apagar nenhum dado.
# =============================================================================
set -euo pipefail

DOMINIO="${DOMINIO:-gestao.ordofraternus.com.br}"
REPO="${REPO:-https://github.com/crisrufino-crs/lojamaconica.git}"
RAMO="${RAMO:-main}"
BASE=/opt/gestao
APP=$BASE/app
VENV=$BASE/venv
ENVF=$BASE/.env
USUARIO=gestao
BANCO=gestao

passo() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m    ✔ %s\033[0m\n' "$*"; }
aviso() { printf '\033[1;33m    ! %s\033[0m\n' "$*"; }
pare()  { printf '\n\033[1;31mERRO: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || pare "rode como administrador (root). Se não estiver como root, use: sudo bash"
command -v apt-get >/dev/null || pare "este instalador é para servidores Ubuntu/Debian."

PRIMEIRA_VEZ=1
[ -f "$ENVF" ] && PRIMEIRA_VEZ=0

# Pergunta feita direto no teclado (funciona mesmo quando o script vem pelo "curl | bash").
perguntar() {
    local texto="$1" resposta=""
    if [ -r /dev/tty ]; then
        read -r -p "$texto" resposta </dev/tty || true
    fi
    printf '%s' "$resposta"
}

# ---------------------------------------------------------------------------
passo "1/9  Instalando os programas necessários (pode levar alguns minutos)"
export DEBIAN_FRONTEND=noninteractive
timedatectl set-timezone America/Sao_Paulo 2>/dev/null || true
apt-get update -qq
apt-get install -y -qq git curl ca-certificates gnupg openssl nginx python3 python3-venv python3-pip \
    certbot python3-certbot-nginx ufw postgresql-common >/dev/null
# Repositório oficial do PostgreSQL: versão atual, capaz de copiar o banco do Render.
if [ ! -f /etc/apt/sources.list.d/pgdg.list ] && [ ! -f /etc/apt/sources.list.d/pgdg.sources ]; then
    /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y >/dev/null 2>&1 \
        || aviso "não consegui usar o repositório oficial do PostgreSQL; usarei a versão do Ubuntu."
fi
apt-get install -y -qq postgresql postgresql-client >/dev/null
systemctl enable --now postgresql >/dev/null 2>&1 || true
ok "programas instalados"

# ---------------------------------------------------------------------------
passo "2/9  Baixando o sistema"
id "$USUARIO" >/dev/null 2>&1 || useradd --system --home-dir "$BASE" --shell /usr/sbin/nologin "$USUARIO"
mkdir -p "$BASE" "$BASE/arquivos" "$BASE/backups"
git config --global --add safe.directory "$APP" 2>/dev/null || true
if [ -d "$APP/.git" ]; then
    git -C "$APP" fetch -q origin "$RAMO"
    git -C "$APP" reset -q --hard "origin/$RAMO"
    ok "sistema atualizado para a versão mais nova"
else
    git clone -q --branch "$RAMO" "$REPO" "$APP" || pare "não consegui baixar o sistema de $REPO"
    ok "sistema baixado"
fi
chown -R "$USUARIO:$USUARIO" "$APP" "$BASE/arquivos" "$BASE/backups"

# ---------------------------------------------------------------------------
passo "3/9  Preparando o Python"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$APP/requirements.txt"
chown -R "$USUARIO:$USUARIO" "$VENV"
ok "Python pronto"

# ---------------------------------------------------------------------------
passo "4/9  Banco de dados"
IP="$(curl -fsS4 --max-time 10 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
if [ "$PRIMEIRA_VEZ" -eq 1 ]; then
    SENHA_BANCO="$(openssl rand -hex 24)"
    CHAVE="$(openssl rand -base64 60 | tr -dc 'A-Za-z0-9' | head -c 60)"
    if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$USUARIO'" | grep -q 1; then
        sudo -u postgres psql -qc "ALTER ROLE $USUARIO WITH LOGIN PASSWORD '$SENHA_BANCO'"
    else
        sudo -u postgres psql -qc "CREATE ROLE $USUARIO WITH LOGIN PASSWORD '$SENHA_BANCO'"
    fi
    sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$BANCO'" | grep -q 1 \
        || sudo -u postgres createdb -O "$USUARIO" "$BANCO"
    umask 027
    cat > "$ENVF" <<EOF
# Configuração do sistema neste servidor (gerada pelo instalador). Guarde em segredo.
DJANGO_SECRET_KEY=$CHAVE
DJANGO_ALLOWED_HOSTS=$DOMINIO,$IP,localhost,127.0.0.1
DJANGO_CSRF_ORIGINS=https://$DOMINIO
DOMINIOS_PROPRIOS=$DOMINIO
DJANGO_SSL_REDIRECT=0
DATABASE_URL=postgres://$USUARIO:$SENHA_BANCO@127.0.0.1:5432/$BANCO
MEDIA_ROOT=$BASE/arquivos
PUSH_CONTATO=mailto:suporte@ordofraternus.com.br

# E-mail (preencha para o sistema enviar e-mails de verdade e depois rode o instalador de novo)
#EMAIL_HOST=smtp.hostinger.com
#EMAIL_PORT=587
#EMAIL_HOST_USER=nao-responda@ordofraternus.com.br
#EMAIL_HOST_PASSWORD=
#EMAIL_REMETENTE=nao-responda@ordofraternus.com.br
EOF
    umask 022
    ok "banco criado e configuração gravada em $ENVF"
else
    ok "banco já existia (nada foi apagado)"
fi
chown root:"$USUARIO" "$ENVF"
chmod 640 "$ENVF"

# Atalho para os comandos do sistema: gestao-comando <comando>
cat > /usr/local/bin/gestao-comando <<EOF
#!/usr/bin/env bash
exec runuser -u $USUARIO -- bash -c 'set -a; . $ENVF; set +a; cd $APP; exec $VENV/bin/python manage.py "\$@"' _ "\$@"
EOF
chmod 755 /usr/local/bin/gestao-comando

# Chaves das notificações no celular (uma única vez)
if ! grep -q '^PUSH_CHAVE_PUBLICA=.\+' "$ENVF"; then
    CHAVES="$(gestao-comando gerar_chaves_push 2>/dev/null || true)"
    if [ -n "$CHAVES" ]; then
        printf '\n# Notificações no celular\n%s\n' "$CHAVES" >> "$ENVF"
        ok "chaves das notificações no celular geradas"
    fi
fi

# ---------------------------------------------------------------------------
passo "5/9  Dados"
TEM_TABELAS="$(sudo -u postgres psql -d "$BANCO" -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"
if [ "${TEM_TABELAS:-0}" -eq 0 ]; then
    echo
    echo "    Quer trazer os dados que já estão no Render (Irmãos, financeiro, presenças...)?"
    echo "    No Render: abra o banco 'lojas-banco' > Connect > copie o 'External Database URL'."
    URL_RENDER="$(perguntar '    Cole aqui o endereço (ou só aperte Enter para começar do zero): ')"
    if [ -n "$URL_RENDER" ]; then
        DUMP="$BASE/backups/render-$(date +%Y%m%d-%H%M).dump"
        if pg_dump --format=custom --no-owner --no-acl --dbname="$URL_RENDER" --file="$DUMP"; then
            chown "$USUARIO" "$DUMP"
            DATABASE_URL="$(grep '^DATABASE_URL=' "$ENVF" | cut -d= -f2-)"
            pg_restore --no-owner --no-acl --dbname="$DATABASE_URL" "$DUMP" \
                || aviso "a cópia terminou com avisos (normalmente sem importância)."
            ok "dados do Render copiados"
        else
            aviso "não consegui ler o banco do Render; o sistema começa com a carga inicial."
        fi
    fi
fi

# ---------------------------------------------------------------------------
passo "6/9  Montando o sistema"
gestao-comando migrate --noinput
gestao-comando collectstatic --noinput >/dev/null
gestao-comando loja_inicial
gestao-comando garantir_admin
gestao-comando carga_inicial || true
ok "sistema montado"

# ---------------------------------------------------------------------------
passo "7/9  Deixando o sistema sempre ligado"
cat > /etc/systemd/system/gestao.service <<EOF
[Unit]
Description=Sistema de Gestão de Lojas
After=network.target postgresql.service

[Service]
User=$USUARIO
Group=$USUARIO
WorkingDirectory=$APP
EnvironmentFile=$ENVF
ExecStart=$VENV/bin/gunicorn config.wsgi --workers 3 --bind 127.0.0.1:8000 --timeout 60
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# Avisos automáticos (aniversariantes, lembretes) toda manhã às 7h
cat > /etc/systemd/system/gestao-rotina.service <<EOF
[Unit]
Description=Avisos automáticos do dia - Gestão de Lojas
[Service]
Type=oneshot
ExecStart=/usr/local/bin/gestao-comando rotina_diaria
EOF
cat > /etc/systemd/system/gestao-rotina.timer <<EOF
[Unit]
Description=Roda os avisos automáticos toda manhã
[Timer]
OnCalendar=*-*-* 07:00:00
Persistent=true
[Install]
WantedBy=timers.target
EOF

# Cópia de segurança do banco toda madrugada (guarda 30 dias)
cat > /etc/systemd/system/gestao-backup.service <<EOF
[Unit]
Description=Cópia de segurança do banco - Gestão de Lojas
[Service]
Type=oneshot
ExecStart=/bin/bash -c 'sudo -u postgres pg_dump --format=custom $BANCO > $BASE/backups/banco-\$(date +%%Y%%m%%d).dump && find $BASE/backups -name "banco-*.dump" -mtime +30 -delete'
EOF
cat > /etc/systemd/system/gestao-backup.timer <<EOF
[Unit]
Description=Cópia de segurança diária do banco
[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true
[Install]
WantedBy=timers.target
EOF

# Atalho para atualizar no futuro: gestao-atualizar
cat > /usr/local/bin/gestao-atualizar <<EOF
#!/usr/bin/env bash
curl -fsSL https://raw.githubusercontent.com/crisrufino-crs/lojamaconica/$RAMO/servidor/instalar.sh | DOMINIO=$DOMINIO bash
EOF
chmod 755 /usr/local/bin/gestao-atualizar

systemctl daemon-reload
systemctl enable --now gestao-rotina.timer gestao-backup.timer >/dev/null 2>&1
systemctl enable gestao.service >/dev/null 2>&1
systemctl restart gestao.service
sleep 3
systemctl is-active --quiet gestao.service || { journalctl -u gestao.service -n 30 --no-pager; pare "o sistema não ligou (veja as mensagens acima)."; }
ok "sistema ligado (reinicia sozinho se o servidor reiniciar)"

# ---------------------------------------------------------------------------
passo "8/9  Endereço na internet"
cat > /etc/nginx/sites-available/gestao <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMINIO $IP;
    client_max_body_size 25m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 90s;
    }
}
EOF
ln -sf /etc/nginx/sites-available/gestao /etc/nginx/sites-enabled/gestao
rm -f /etc/nginx/sites-enabled/default
nginx -t >/dev/null 2>&1 || pare "configuração do nginx inválida"
systemctl reload nginx
ufw allow OpenSSH >/dev/null
ufw allow 'Nginx Full' >/dev/null
ufw --force enable >/dev/null
ok "servidor web pronto"

# ---------------------------------------------------------------------------
passo "9/9  Cadeado de segurança (https)"
APONTA="$(getent ahostsv4 "$DOMINIO" 2>/dev/null | awk 'NR==1{print $1}')"
HTTPS=0
if [ -d "/etc/letsencrypt/live/$DOMINIO" ]; then
    certbot --nginx -d "$DOMINIO" --non-interactive --redirect --reinstall >/dev/null 2>&1 || true
    HTTPS=1
    ok "cadeado já existia e foi mantido"
elif [ "$APONTA" = "$IP" ]; then
    if certbot --nginx -d "$DOMINIO" --non-interactive --agree-tos --register-unsafely-without-email --redirect; then
        HTTPS=1
        ok "cadeado instalado (renova sozinho)"
    else
        aviso "não consegui emitir o cadeado agora; rode 'gestao-atualizar' daqui a alguns minutos."
    fi
else
    aviso "o endereço $DOMINIO ainda não aponta para este servidor (aponta para: ${APONTA:-nada})."
    aviso "Crie um registro do tipo A:  nome 'gestao'  →  valor $IP"
    aviso "Depois de alguns minutos, rode:  gestao-atualizar"
fi

# ---------------------------------------------------------------------------
echo
printf '\033[1;32m=============================================================\033[0m\n'
printf '\033[1;32m  PRONTO! Sistema instalado neste servidor.\033[0m\n'
printf '\033[1;32m=============================================================\033[0m\n'
if [ "$HTTPS" -eq 1 ]; then
    echo "  Acesse:  https://$DOMINIO/entrar/"
else
    echo "  Assim que o endereço apontar para cá:  https://$DOMINIO/entrar/"
    echo "  IP deste servidor (para o registro A):  $IP"
fi
echo "  Login do Administrador:  Cristiano  (com a sua senha de sempre)"
echo
echo "  Atualizar o sistema no futuro:   gestao-atualizar"
echo "  Cópias de segurança diárias em:  $BASE/backups"
echo
