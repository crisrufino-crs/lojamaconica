"""Guarda os arquivos enviados dentro do banco de dados (tabela ArquivoGuardado).

Na hospedagem gratuita o disco é apagado a cada reinício; no banco os arquivos ficam para sempre.
Fotos grandes são reduzidas automaticamente para não encher o banco.
"""
import io
import mimetypes
import os
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.urls import reverse
from django.utils.deconstruct import deconstructible

LADO_MAXIMO = 1600          # pixels: suficiente para foto de ficha e brasão
FORMATOS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}


def reduzir_imagem(dados, nome):
    """Diminui fotos grandes (mantendo o formato). Se não for uma imagem válida, devolve como veio."""
    formato = FORMATOS.get(os.path.splitext(nome)[1].lower())
    if not formato:
        return dados
    try:
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(dados))
        img = ImageOps.exif_transpose(img)
        if max(img.size) <= LADO_MAXIMO and len(dados) <= 400_000:
            return dados
        img.thumbnail((LADO_MAXIMO, LADO_MAXIMO))
        if formato == "JPEG" and img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        saida = io.BytesIO()
        opcoes = {"quality": 85, "optimize": True} if formato in ("JPEG", "WEBP") else {"optimize": True}
        img.save(saida, formato, **opcoes)
        nova = saida.getvalue()
        return nova if len(nova) < len(dados) else dados
    except Exception:
        return dados


@deconstructible
class BancoStorage(Storage):
    def _modelo(self):
        from .models import ArquivoGuardado
        return ArquivoGuardado

    def _disco(self, nome):
        """Arquivo antigo que ainda esteja no disco (instalações anteriores)."""
        raiz = Path(settings.MEDIA_ROOT).resolve()
        caminho = (raiz / nome).resolve()
        return caminho if raiz in caminho.parents and caminho.is_file() else None

    def _save(self, nome, conteudo):
        conteudo.seek(0) if hasattr(conteudo, "seek") else None
        dados = conteudo.read()
        if isinstance(dados, str):
            dados = dados.encode()
        dados = reduzir_imagem(dados, nome)
        tipo = mimetypes.guess_type(nome)[0] or "application/octet-stream"
        self._modelo().objects.update_or_create(
            nome=nome, defaults={"conteudo": dados, "tipo": tipo, "tamanho": len(dados)})
        return nome

    def _open(self, nome, mode="rb"):
        registro = self._modelo().objects.filter(nome=nome).only("conteudo").first()
        if registro is not None:
            return ContentFile(bytes(registro.conteudo), name=nome)
        antigo = self._disco(nome)
        if antigo:
            return ContentFile(antigo.read_bytes(), name=nome)
        raise FileNotFoundError(nome)

    def exists(self, nome):
        return self._modelo().objects.filter(nome=nome).exists() or self._disco(nome) is not None

    def delete(self, nome):
        if nome:
            self._modelo().objects.filter(nome=nome).delete()

    def size(self, nome):
        registro = self._modelo().objects.filter(nome=nome).only("tamanho").first()
        if registro is not None:
            return registro.tamanho
        antigo = self._disco(nome)
        if antigo:
            return antigo.stat().st_size
        raise FileNotFoundError(nome)

    def url(self, nome):
        return reverse("arquivo", args=[nome])

    def listdir(self, caminho):
        prefixo = f"{caminho.rstrip('/')}/" if caminho else ""
        nomes = self._modelo().objects.filter(nome__startswith=prefixo).values_list("nome", flat=True)
        pastas, arquivos = set(), []
        for n in nomes:
            resto = n[len(prefixo):]
            if "/" in resto:
                pastas.add(resto.split("/", 1)[0])
            else:
                arquivos.append(resto)
        return sorted(pastas), sorted(arquivos)
