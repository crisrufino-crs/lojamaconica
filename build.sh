#!/usr/bin/env bash
# Preparação automática na hospedagem: instala, cria as tabelas e, se o banco estiver vazio, os dados de exemplo.
set -o errexit
pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate --noinput
python manage.py demo || true
# Aconteça o que acontecer acima, o Administrador sempre existe e consegue entrar.
python manage.py garantir_admin
