"""Acesso do Administrador da plataforma.

A senha não fica escrita aqui: só o "embaralhado" dela (hash), que o sistema usa
para conferir a senha digitada mas que não permite ler a senha de volta.
Para trocar a senha, entre no sistema e use "Trocar senha".
"""
ADMIN_LOGIN = "Cristiano"
ADMIN_SENHA_HASH = "pbkdf2_sha256$1000000$06hg8HlxyRYGLShwYrOj9s$jJmIdXlQTgpMxc2d0UZ0g/pnvUIwHImsM9BWBLBIXZA="
LOGINS_ANTIGOS = ("plataforma", "cristi286913ano", "administrador")
