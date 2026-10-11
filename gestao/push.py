"""Notificações no celular (Web Push).

Funciona assim: o sistema manda um "toque" vazio para o celular; o celular
acorda, pergunta ao sistema quais avisos ainda não viu e mostra. Como o texto
não viaja junto com o toque, nenhum dado da Loja passa pelos servidores de
notificação do Google/Apple.
"""
import base64
import json
import logging
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from django.conf import settings

log = logging.getLogger(__name__)


def _b64(dados):
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode()


def _b64_ler(texto):
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def gerar_chaves():
    """Devolve (pública, privada) no formato que o navegador espera."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    chave = ec.generate_private_key(ec.SECP256R1())
    privada = chave.private_numbers().private_value.to_bytes(32, "big")
    publica = chave.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return _b64(publica), _b64(privada)


def cabecalho_vapid(endpoint, agora=None):
    """Monta a 'assinatura' que prova ao serviço de push que o toque veio deste sistema."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    destino = urlparse(endpoint)
    chave = ec.derive_private_key(int.from_bytes(_b64_ler(settings.PUSH_CHAVE_PRIVADA), "big"), ec.SECP256R1())
    cabecalho = _b64(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    corpo = _b64(json.dumps({"aud": f"{destino.scheme}://{destino.netloc}", "exp": int(agora or time.time()) + 12 * 3600,
                             "sub": settings.PUSH_CONTATO}, separators=(",", ":")).encode())
    r, s = decode_dss_signature(chave.sign(f"{cabecalho}.{corpo}".encode(), ec.ECDSA(hashes.SHA256())))
    assinatura = _b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={cabecalho}.{corpo}.{assinatura}, k={settings.PUSH_CHAVE_PUBLICA}"


def _enviar(endpoint):
    """Manda o toque. Devolve o código de resposta do serviço de push."""
    pedido = urllib.request.Request(endpoint, data=b"", method="POST", headers={
        "Authorization": cabecalho_vapid(endpoint), "TTL": "86400", "Content-Length": "0", "Urgency": "normal"})
    try:
        with urllib.request.urlopen(pedido, timeout=10) as resposta:
            return resposta.status
    except urllib.error.HTTPError as erro:
        return erro.code
    except Exception as erro:  # rede fora do ar etc. — tenta de novo na próxima
        log.warning("Falha ao enviar push: %s", erro)
        return 0


def cutucar(usuarios):
    """Toca os celulares inscritos desses usuários. Devolve quantos toques foram aceitos."""
    from .models import InscricaoPush
    if not (settings.PUSH_CHAVE_PUBLICA and settings.PUSH_CHAVE_PRIVADA):
        return 0
    aceitos = 0
    for inscricao in InscricaoPush.objects.filter(usuario__in=usuarios):
        codigo = _enviar(inscricao.endpoint)
        if codigo in (404, 410):  # o celular cancelou a inscrição
            inscricao.delete()
        elif 200 <= codigo < 300:
            aceitos += 1
    return aceitos
