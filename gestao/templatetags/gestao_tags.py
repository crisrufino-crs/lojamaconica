from datetime import date, datetime, time
from decimal import Decimal

from django import template
from django.utils import timezone
from django.utils.html import format_html

register = template.Library()


def dinheiro(valor):
    if valor is None or valor == "":
        return ""
    texto = f"{abs(Decimal(valor)):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if Decimal(valor) < 0 else ''}R$ {texto}"


def formatar(valor):
    if valor is None or valor == "":
        return "—"
    if valor is True:
        return "Sim"
    if valor is False:
        return "Não"
    if isinstance(valor, Decimal):
        return dinheiro(valor)
    if isinstance(valor, datetime):
        return timezone.localtime(valor).strftime("%d/%m/%Y %H:%M") if timezone.is_aware(valor) else valor.strftime("%d/%m/%Y %H:%M")
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    if isinstance(valor, time):
        return valor.strftime("%H:%M")
    return valor


register.filter("brl", dinheiro)
register.filter("fmt", formatar)


@register.filter
def chave(dicionario, nome):
    return dicionario.get(nome) if dicionario else None


@register.simple_tag
def celula(objeto, coluna):
    """Valor de uma coluna da listagem: nome de campo, 'a.b' ou função."""
    if callable(coluna):
        return formatar(coluna(objeto))
    valor = objeto
    for parte in coluna.split("."):
        if valor is None:
            break
        mostrar = getattr(valor, f"get_{parte}_display", None)
        valor = mostrar() if mostrar else getattr(valor, parte, None)
        if callable(valor):
            valor = valor()
    return formatar(valor)


@register.simple_tag
def selo(texto, tipo=""):
    return format_html('<span class="selo {}">{}</span>', tipo, texto)
