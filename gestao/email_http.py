"""Envio de e-mail pela internet (HTTPS), sem usar as portas de SMTP.

A hospedagem gratuita do Render bloqueia as portas 25, 465 e 587 (SMTP). Por isso o sistema
envia os e-mails pela API de um serviço de e-mail:

- Resend  (resend.com)  → variável RESEND_API_KEY
- Brevo   (brevo.com)   → variável BREVO_API_KEY

O remetente vem de EMAIL_REMETENTE (ex.: "Loja L'Aquila Romana <nao-responda@ordofraternus.com.br>").
"""
import json
import logging
import urllib.error
import urllib.request
from email.utils import parseaddr

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

log = logging.getLogger(__name__)


def _html(mensagem):
    for conteudo, tipo in getattr(mensagem, "alternatives", []) or []:
        if tipo == "text/html":
            return conteudo
    return None


class _BaseHTTP(BaseEmailBackend):
    url = ""

    def _cabecalhos(self):
        raise NotImplementedError

    def _corpo(self, mensagem):
        raise NotImplementedError

    def send_messages(self, mensagens):
        enviados = 0
        for mensagem in mensagens:
            if not mensagem.recipients():
                continue
            pedido = urllib.request.Request(
                self.url, data=json.dumps(self._corpo(mensagem)).encode(), method="POST",
                headers={"Content-Type": "application/json", "Accept": "application/json", **self._cabecalhos()})
            try:
                with urllib.request.urlopen(pedido, timeout=20) as resposta:
                    if 200 <= resposta.status < 300:
                        enviados += 1
            except urllib.error.HTTPError as erro:
                detalhe = erro.read().decode(errors="replace")[:300]
                log.error("Falha ao enviar e-mail (%s): %s", erro.code, detalhe)
                if not self.fail_silently:
                    raise RuntimeError(f"O serviço de e-mail recusou o envio ({erro.code}): {detalhe}") from erro
            except OSError as erro:
                log.error("Falha ao enviar e-mail: %s", erro)
                if not self.fail_silently:
                    raise
        return enviados


class ResendBackend(_BaseHTTP):
    url = "https://api.resend.com/emails"

    def _cabecalhos(self):
        return {"Authorization": f"Bearer {settings.RESEND_API_KEY}"}

    def _corpo(self, m):
        corpo = {"from": m.from_email, "to": m.to, "subject": m.subject, "text": m.body}
        if m.cc:
            corpo["cc"] = m.cc
        if m.bcc:
            corpo["bcc"] = m.bcc
        if m.reply_to:
            corpo["reply_to"] = m.reply_to
        html = _html(m)
        if html:
            corpo["html"] = html
        return corpo


class BrevoBackend(_BaseHTTP):
    url = "https://api.brevo.com/v3/smtp/email"

    def _cabecalhos(self):
        return {"api-key": settings.BREVO_API_KEY}

    @staticmethod
    def _pessoa(endereco):
        nome, email = parseaddr(endereco)
        return {"email": email, **({"name": nome} if nome else {})}

    def _corpo(self, m):
        corpo = {"sender": self._pessoa(m.from_email), "to": [self._pessoa(e) for e in m.to],
                 "subject": m.subject, "textContent": m.body}
        if m.cc:
            corpo["cc"] = [self._pessoa(e) for e in m.cc]
        if m.bcc:
            corpo["bcc"] = [self._pessoa(e) for e in m.bcc]
        if m.reply_to:
            corpo["replyTo"] = self._pessoa(m.reply_to[0])
        html = _html(m)
        if html:
            corpo["htmlContent"] = html
        return corpo
