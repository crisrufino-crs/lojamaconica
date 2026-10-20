"""Gráficos desenhados no próprio servidor, em SVG (sem bibliotecas externas: abrem rápido e imprimem bem).

Regras visuais: marcas finas, pontas arredondadas de 4px apoiadas na linha de base, 2px de espaço entre
barras, linhas de 2px, grade discreta, cor definida pela série (nunca pela posição) e dica (tooltip) ao
passar o mouse ou focar com o teclado em cada marca.
"""
import math
from decimal import Decimal

from django.utils.html import escape
from django.utils.safestring import mark_safe

from .templatetags.gestao_tags import dinheiro

LARG, ALT = 1000, 300
ESQ, DIR, TOPO, BASE = 72, 12, 22, 28


def _num(v):
    return float(v or 0)


def _escala(minimo, maximo, passos=4):
    """Divisões 'redondas' para o eixo (ex.: 0, 500, 1.000, 1.500)."""
    minimo, maximo = min(0.0, minimo), max(0.0, maximo)
    if maximo == minimo:
        maximo = minimo + 1
    bruto = (maximo - minimo) / passos
    potencia = 10 ** math.floor(math.log10(bruto))
    passo = next(m * potencia for m in (1, 2, 2.5, 5, 10) if m * potencia >= bruto)
    inicio = math.floor(minimo / passo) * passo
    fim = math.ceil(maximo / passo) * passo
    marcas, v = [], inicio
    while v <= fim + passo / 2:
        marcas.append(v)
        v += passo
    return inicio, fim, marcas


def _rotulo_eixo(v, moeda=True):
    if not moeda:
        return f"{v:.0f}%"
    if abs(v) >= 1000:
        texto = f"{v / 1000:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".").replace(",0", "")
        return f"R$ {texto} mil"
    return f"R$ {v:,.0f}".replace(",", ".")


def _barra(x, y0, y1, largura, cor, dica):
    """Barra com as pontas de dados arredondadas (4px) e a base reta sobre a linha zero."""
    r = min(4, largura / 2, abs(y1 - y0))
    if y1 <= y0:  # valor positivo: ponta em cima
        d = (f"M{x:.1f},{y0:.1f} V{y1 + r:.1f} Q{x:.1f},{y1:.1f} {x + r:.1f},{y1:.1f} H{x + largura - r:.1f} "
             f"Q{x + largura:.1f},{y1:.1f} {x + largura:.1f},{y1 + r:.1f} V{y0:.1f} Z")
    else:  # valor negativo: ponta embaixo
        d = (f"M{x:.1f},{y0:.1f} V{y1 - r:.1f} Q{x:.1f},{y1:.1f} {x + r:.1f},{y1:.1f} H{x + largura - r:.1f} "
             f"Q{x + largura:.1f},{y1:.1f} {x + largura:.1f},{y1 - r:.1f} V{y0:.1f} Z")
    return f'<path d="{d}" fill="{cor}" class="marca" tabindex="0" data-dica="{escape(dica)}"><title>{escape(dica)}</title></path>'


def _grade(marcas, y, moeda=True):
    partes = []
    for v in marcas:
        yy = y(v)
        cor = "var(--viz-eixo)" if v == 0 else "var(--viz-grade)"
        partes.append(f'<line x1="{ESQ}" x2="{LARG - DIR}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{cor}" stroke-width="1"/>')
        partes.append(f'<text x="{ESQ - 8}" y="{yy + 4:.1f}" text-anchor="end" class="viz-texto">{escape(_rotulo_eixo(v, moeda))}</text>')
    return "".join(partes)


def _svg(conteudo, titulo):
    return mark_safe(f'<svg viewBox="0 0 {LARG} {ALT}" role="img" aria-label="{escape(titulo)}" class="viz-svg">{conteudo}</svg>')


def barras_agrupadas(rotulos, series, titulo):
    """series: [(nome, valores, cor_css)] — ex.: Receitas × Despesas por mês."""
    todos = [_num(v) for _, valores, _ in series for v in valores]
    inicio, fim, marcas = _escala(min(todos or [0]), max(todos or [1]))
    alto = ALT - TOPO - BASE
    y = lambda v: TOPO + alto * (fim - v) / (fim - inicio)  # noqa: E731
    faixa = (LARG - ESQ - DIR) / len(rotulos)
    grupo = faixa * 0.72
    largura = (grupo - 2 * (len(series) - 1)) / len(series)
    partes = [_grade(marcas, y)]
    for i, rotulo in enumerate(rotulos):
        x0 = ESQ + i * faixa + (faixa - grupo) / 2
        for j, (nome, valores, cor) in enumerate(series):
            v = _num(valores[i])
            if v:
                partes.append(_barra(x0 + j * (largura + 2), y(0), y(v), largura, cor,
                                     f"{rotulo} · {nome}: {dinheiro(Decimal(str(v)))}"))
        partes.append(f'<text x="{ESQ + i * faixa + faixa / 2:.1f}" y="{ALT - 8}" text-anchor="middle" class="viz-texto">{escape(rotulo)}</text>')
    return _svg("".join(partes), titulo)


def linha(rotulos, valores, nome, titulo, cor="var(--viz-serie-1)"):
    """Uma série no tempo (ex.: saldo em caixa ao fim de cada mês). Valores None ficam de fora (meses futuros)."""
    presentes = [_num(v) for v in valores if v is not None]
    inicio, fim, marcas = _escala(min(presentes or [0]), max(presentes or [1]))
    alto = ALT - TOPO - BASE
    y = lambda v: TOPO + alto * (fim - v) / (fim - inicio)  # noqa: E731
    faixa = (LARG - ESQ - DIR) / len(rotulos)
    x = lambda i: ESQ + i * faixa + faixa / 2  # noqa: E731
    pontos = [(x(i), y(_num(v)), rotulos[i], v) for i, v in enumerate(valores) if v is not None]
    partes = [_grade(marcas, y)]
    if len(pontos) > 1:
        caminho = " ".join(f"{'M' if k == 0 else 'L'}{px:.1f},{py:.1f}" for k, (px, py, _, _) in enumerate(pontos))
        partes.append(f'<path d="{caminho}" fill="none" stroke="{cor}" stroke-width="2" stroke-linejoin="round"/>')
    for px, py, rotulo, v in pontos:
        dica = f"{rotulo} · {nome}: {dinheiro(v)}"
        partes.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="{cor}" stroke="var(--viz-superficie)" stroke-width="2"/>'
                      f'<circle cx="{px:.1f}" cy="{py:.1f}" r="14" fill="transparent" class="marca" tabindex="0" '
                      f'data-dica="{escape(dica)}"><title>{escape(dica)}</title></circle>')
    if pontos:  # rótulo direto só no último ponto
        px, py, _, v = pontos[-1]
        partes.append(f'<text x="{min(px, LARG - DIR - 4):.1f}" y="{py - 12:.1f}" text-anchor="end" class="viz-texto forte">{escape(dinheiro(v))}</text>')
    for i, rotulo in enumerate(rotulos):
        partes.append(f'<text x="{x(i):.1f}" y="{ALT - 8}" text-anchor="middle" class="viz-texto">{escape(rotulo)}</text>')
    return _svg("".join(partes), titulo)


def colunas_percentuais(rotulos, valores, nome, titulo, cor="var(--viz-serie-1)"):
    """Percentual por mês (0 a 100%), ex.: mensalidades pagas sobre as já vencidas."""
    alto = ALT - TOPO - BASE
    y = lambda v: TOPO + alto * (100 - v) / 100  # noqa: E731
    faixa = (LARG - ESQ - DIR) / len(rotulos)
    largura = faixa * 0.56
    partes = [_grade([0, 25, 50, 75, 100], y, moeda=False)]
    for i, (rotulo, v) in enumerate(zip(rotulos, valores)):
        cx = ESQ + i * faixa + faixa / 2
        if v is not None and v > 0:
            partes.append(_barra(cx - largura / 2, y(0), y(v), largura, cor, f"{rotulo} · {nome}: {v}%"))
        if v is not None:
            partes.append(f'<text x="{cx:.1f}" y="{y(v) - 6:.1f}" text-anchor="middle" class="viz-texto">{v}%</text>')
        partes.append(f'<text x="{cx:.1f}" y="{ALT - 8}" text-anchor="middle" class="viz-texto">{escape(rotulo)}</text>')
    return _svg("".join(partes), titulo)


def barras_horizontais(itens, nome, titulo, cor="var(--viz-serie-1)", maximo_itens=7):
    """Ranking (ex.: receitas por conta do plano). Além de 7 itens, o resto vira 'Outros'."""
    itens = [(n, _num(v)) for n, v in itens if _num(v) > 0]
    if len(itens) > maximo_itens:
        itens = itens[:maximo_itens - 1] + [("Outros", sum(v for _, v in itens[maximo_itens - 1:]))]
    if not itens:
        return mark_safe('<p class="vazio">Nenhum valor quitado neste ano.</p>')
    largura_total, linha_alt, esq_rot, dir_val = 520, 34, 170, 110
    altura = linha_alt * len(itens) + 8
    maior = max(v for _, v in itens)
    util = largura_total - esq_rot - dir_val
    total = sum(v for _, v in itens)
    partes = []
    for i, (n, v) in enumerate(itens):
        topo = 4 + i * linha_alt
        largura = max(util * v / maior, 2)
        rotulo = n if len(n) <= 24 else n[:23] + "…"
        dica = f"{n} · {dinheiro(Decimal(str(v)))} ({v / total * 100:.0f}% do total)"
        r = min(4, largura / 2)
        y0, y1 = topo + 6, topo + linha_alt - 8
        d = (f"M{esq_rot},{y0} H{esq_rot + largura - r:.1f} Q{esq_rot + largura:.1f},{y0} {esq_rot + largura:.1f},{y0 + r} "
             f"V{y1 - r} Q{esq_rot + largura:.1f},{y1} {esq_rot + largura - r:.1f},{y1} H{esq_rot} Z")
        partes.append(f'<text x="{esq_rot - 10}" y="{topo + linha_alt / 2 + 3:.1f}" text-anchor="end" class="viz-texto forte">{escape(rotulo)}</text>')
        partes.append(f'<path d="{d}" fill="{cor}" class="marca" tabindex="0" data-dica="{escape(dica)}"><title>{escape(dica)}</title></path>')
        partes.append(f'<text x="{esq_rot + largura + 8:.1f}" y="{topo + linha_alt / 2 + 3:.1f}" class="viz-texto">{escape(dinheiro(Decimal(str(v))))}</text>')
    return mark_safe(f'<svg viewBox="0 0 {largura_total} {altura}" role="img" aria-label="{escape(titulo)}" class="viz-svg">{"".join(partes)}</svg>')
