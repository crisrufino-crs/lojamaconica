# Sistema de Gestão de Lojas Maçônicas

Sistema web para várias Lojas, cada uma com seus dados, usuários e personalização.
Módulos: Secretaria, Tesouraria, Chancelaria, Hospitalaria, Relatórios, área do Irmão e Administração.

## Testar no seu computador (10 minutos)

1. Instale o Python 3.11 ou mais novo (python.org). No Windows, marque "Add Python to PATH".
2. Abra o Terminal (Mac) ou Prompt de Comando (Windows) dentro desta pasta e rode, uma linha por vez:

       pip install -r requirements.txt
       python manage.py migrate
       python manage.py demo
       python manage.py runserver

3. Abra http://127.0.0.1:8000 no navegador.

O comando `demo` cria duas Lojas fictícias. Senha de todos: `loja-demo-2026`

| Login | O que enxerga |
|---|---|
| `Cristiano` | Administrador: todas as Lojas (cria, bloqueia e entra em qualquer uma). Senha própria, não é a de exemplo |
| `veneravel` | Usuário Master da Loja Fraternidade e Luz (tudo da Loja) |
| `secretario`, `tesoureiro`, `chanceler`, `hospitaleiro` | Só o módulo do cargo |
| `irmao` | Irmão comum: só os próprios dados, frequência e boletos |
| `master.sul` | Master da segunda Loja (para ver que os dados não se misturam) |

Para começar do zero, sem dados de exemplo: apague a pasta `dados`, rode `migrate` e depois
`python manage.py createsuperuser` (esse é o administrador da plataforma, que cria as Lojas).

## Criar os acessos de verdade (tabela de acessos)

1. Abra `acessos.csv` no Excel e preencha o e-mail de cada pessoa e o nome da Loja (pode acrescentar linhas).
   Perfis aceitos: `administrador`, `master`, `secretaria`, `tesouraria`, `chancelaria`, `hospitalaria`, `irmao`
   (dá para combinar, ex.: `secretaria+tesouraria`).
2. Rode:

       python manage.py criar_acessos acessos.csv --site ENDERECO-DO-SISTEMA --enviar-emails

3. O comando pede a senha do Administrador na hora (ela não fica gravada em nenhum arquivo).
   Os demais recebem um e-mail com o link para criar a própria senha; sem servidor de e-mail configurado,
   cada um usa "Esqueci minha senha" depois.

Rodar de novo é seguro: atualiza perfis, não duplica ninguém e não troca a senha de quem já criou a sua.

## Cadastrar a senha e enviar por e-mail

Em Administração › Usuários e acessos, ao incluir ou alterar um usuário, o Master digita a senha no campo
**Senha** e deixa marcado **Enviar esta senha para o e-mail cadastrado**. A pessoa recebe login e senha por
e-mail e o sistema a obriga a trocar a senha no primeiro acesso. Precisa do servidor de e-mail configurado.

## Importar Irmãos do Excel

Em Secretaria › Importar planilha: baixe a planilha modelo, preencha (uma linha por Irmão) e envie o arquivo .xlsx.
O sistema mostra uma conferência (quantos serão incluídos, atualizados e recusados, com o motivo) e só grava
depois da confirmação. Irmão que já existe (mesmo CIM, CPF ou nome) é atualizado, não duplicado.

## Carga inicial pela instalação (sem usar a tela)

Coloque a planilha (.xlsx, no formato da planilha modelo) na pasta `carga/` e, em `carga/loja.txt`, o número
ou o nome da Loja de destino. Na próxima instalação no Render, os Irmãos são importados automaticamente.
Cada planilha é importada uma única vez, então as instalações seguintes não desfazem alterações feitas pelo site.
Para importar de novo, troque o arquivo. Pelo terminal: `python manage.py importar_irmaos arquivo.xlsx --loja 123 --gravar`.

## O que já funciona e o que depende de contratar/configurar

| Recurso | Situação |
|---|---|
| Cadastros, graus, atas, agenda, informativos, presença, licenças, visitas, bens, empréstimos | Pronto |
| Contas a pagar/receber, plano de contas, mensalidades em série, transferências, balancete | Pronto |
| Relatórios (32), com impressão/PDF e exportação para Excel | Pronto |
| Acessos por cargo, usuário Master, várias Lojas isoladas, personalização e campos extras | Pronto |
| Disparo de e-mail e e-mail de aniversário | Pronto; precisa de um servidor de e-mail (SMTP) no `.env`. Sem ele, fica em modo de teste e nada é enviado |
| Avisos automáticos (aniversariantes, lembrete do dia) | Prontos dentro do sistema; precisa agendar `python manage.py rotina_diaria` para rodar toda manhã no servidor |
| Aviso no celular (push) | Código pronto, **ainda não testado em celular de verdade**: exige o sistema publicado com HTTPS e as chaves do `gerar_chaves_push`. No iPhone, o Irmão precisa "Adicionar à Tela de Início" |
| Boleto, remessa e baixa automática | Fluxo completo pronto no padrão FEBRABAN/CNAB 240, **mas ainda não homologado em nenhum banco** (veja abaixo) |

### Boletos: o que falta para cobrar de verdade

Dígitos verificadores, fator de vencimento e linha digitável seguem o padrão nacional e foram conferidos
contra um exemplo publicado. Duas coisas variam de banco para banco e precisam de um programador com o
manual do banco da Loja em mãos:

1. o "campo livre" do código de barras (função `campo_livre` em `gestao/boleto.py`, hoje em formato genérico);
2. detalhes do arquivo CNAB 240 — todo banco exige um teste (homologação) antes de liberar a cobrança.

Até lá, os boletos gerados são demonstração e **não devem ser entregues para pagamento**.
Alternativa mais simples: trocar o CNAB por um serviço de cobrança com API (Asaas, Efí, Inter, Sicoob),
que registra o boleto e avisa o pagamento sozinho. O ponto de troca é o mesmo arquivo `gestao/boleto.py`.

## Publicar um ambiente de teste no Render (com link)

O projeto já traz a receita pronta (`render.yaml`): um site e um banco PostgreSQL no plano gratuito.

1. Crie uma conta em github.com e um repositório **privado** (ex.: `gestao-lojas`).
2. No repositório, clique em "uploading an existing file" e arraste **o conteúdo desta pasta** (o `manage.py` precisa ficar na raiz, não dentro de uma subpasta).
3. Crie uma conta em render.com entrando com o GitHub. Clique em **New › Blueprint**, escolha o repositório e depois **Apply**.
4. Aguarde alguns minutos. O link aparece no serviço `gestao-lojas` (algo como `https://gestao-lojas.onrender.com`).

Os dados de exemplo são criados sozinhos na primeira publicação (senha `loja-demo-2026`; para outra senha,
defina a variável `DEMO_SENHA` no Render antes da primeira publicação).

Limites do plano gratuito: o site "dorme" após alguns minutos sem uso e demora cerca de um minuto para acordar;
arquivos enviados (atas, fotos, brasão) somem a cada nova publicação; o banco gratuito expira em cerca de 30 dias;
não há rotina diária agendada. Serve para demonstração, **não para dados reais**.

## Colocar no ar (definitivo)

Precisa de hospedagem que rode Python (Railway, Render, um VPS) e um banco PostgreSQL. Resumo para quem for publicar:

    pip install -r requirements.txt
    # preencher o .env a partir de .env.exemplo
    python manage.py migrate && python manage.py collectstatic --noinput
    gunicorn config.wsgi
    # agendar (cron) todo dia às 7h:  python manage.py rotina_diaria

- Os arquivos enviados (atas, fotos, brasões) ficam em `dados/arquivos`: use um disco persistente e inclua no backup, junto com o banco.
- O sistema guarda dados pessoais (CPF, endereço, família). Pela LGPD, cada Loja deve informar os Irmãos, e quem hospeda deve manter backup e acesso restrito ao servidor.

## Conferir se está tudo certo

    python manage.py test

São 47 testes automáticos: separação entre Lojas, acessos por cargo, contas da Tesouraria, cálculo de
frequência, boletos/remessa/retorno, avisos automáticos e abertura de todas as telas e relatórios.

## Onde fica cada coisa (para o programador)

- `gestao/models.py` — tabelas (todo registro tem o campo `loja`)
- `gestao/cadastros.py` — descrição de cada tela de cadastro e os menus
- `gestao/servicos.py` — regras: frequência, saldos, balancete, mensalidades, rotina diária
- `gestao/relatorios.py` — um bloco por relatório
- `gestao/boleto.py`, `gestao/push.py` — boletos/CNAB e notificações
- `gestao/views.py`, `gestao/templates/`, `gestao/static/` — telas
