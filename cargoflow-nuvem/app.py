import hmac
import math
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (Flask, abort, flash, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

# ---------------------------------------------------------------------------
# Configuração
#   CARGOFLOW_DB        caminho do arquivo do banco (na nuvem, use um disco persistente)
#   CARGOFLOW_SECRET    chave secreta das sessões (se ausente, é criada e guardada em arquivo)
#   CARGOFLOW_PRODUCAO  "1" na hospedagem: cookies só por https e sem modo debug
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("CARGOFLOW_DB", os.path.join(BASE_DIR, "cargoflow.db"))
PRODUCAO = os.environ.get("CARGOFLOW_PRODUCAO") == "1"

os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)

try:
    FUSO = ZoneInfo("America/Sao_Paulo")
except Exception:  # sem o pacote tzdata (Windows): o Brasil não tem horário de verão desde 2019
    FUSO = timezone(timedelta(hours=-3))


def carregar_chave_secreta():
    chave = os.environ.get("CARGOFLOW_SECRET")
    if chave:
        return chave

    caminho = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "chave_secreta.txt")
    nova = secrets.token_hex(32)
    try:
        with open(caminho, "x", encoding="utf-8") as arquivo:
            arquivo.write(nova)
    except FileExistsError:
        pass
    with open(caminho, encoding="utf-8") as arquivo:
        return arquivo.read().strip() or nova


app = Flask(__name__)
app.secret_key = carregar_chave_secreta()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=PRODUCAO,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=64 * 1024,  # os formulários são pequenos
)

STATUS_VALIDOS = [
    "Programada",
    "A caminho",
    "Chegou",
    "Em carregamento",
    "Carregada",
    "Finalizada",
    "Pendência",
]

STATUS_CLASSE = {
    "Programada": "programada",
    "A caminho": "a-caminho",
    "Chegou": "chegou",
    "Em carregamento": "em-carregamento",
    "Carregada": "carregada",
    "Finalizada": "finalizada",
    "Pendência": "pendencia",
}

# Aceita placa antiga (ABC1234) e Mercosul (ABC1D23)
PLACA_REGEX = re.compile(r"^[A-Z]{3}[0-9][A-Z0-9][0-9]{2}$")

# Perfis de acesso: cada tela pertence a um perfil. "admin" enxerga as duas.
PERFIS = ("operacao", "carregamento", "admin")
TELA_INICIAL = {"operacao": "operacao", "carregamento": "carregamento", "admin": "operacao"}

MAX_FALHAS_LOGIN = 5
JANELA_BLOQUEIO_MIN = 10
HASH_FALSO = generate_password_hash("senha-falsa-para-igualar-o-tempo")


# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------
@contextmanager
def banco():
    """Abre a conexão, grava (commit) ao final e sempre fecha."""
    conexao = sqlite3.connect(DB_PATH, timeout=10)
    conexao.row_factory = sqlite3.Row
    try:
        yield conexao
        conexao.commit()
    finally:
        conexao.close()


def agora():
    """Data e hora atuais de São Paulo, independente do fuso do servidor."""
    return datetime.now(FUSO).strftime("%Y-%m-%d %H:%M:%S")


def criar_banco():
    with banco() as conexao:
        conexao.execute("""
            CREATE TABLE IF NOT EXISTS operacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                motorista TEXT NOT NULL,
                cpf TEXT NOT NULL,
                placacavalo TEXT NOT NULL,
                placacarreta TEXT NOT NULL,
                dataprevista TEXT NOT NULL,
                horarioprevisto TEXT NOT NULL,
                observacao TEXT,
                status TEXT NOT NULL DEFAULT 'Programada',
                quantidade REAL DEFAULT 0,
                peso REAL DEFAULT 0,
                percentual INTEGER DEFAULT 0,
                ocorrencia TEXT,
                criadoem TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizadoem TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conexao.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT NOT NULL UNIQUE,
                senha_hash TEXT NOT NULL,
                perfil TEXT NOT NULL,
                ativo INTEGER NOT NULL DEFAULT 1,
                criadoem TEXT NOT NULL
            )
        """)
        conexao.execute("""
            CREATE TABLE IF NOT EXISTS login_falhas (
                usuario TEXT NOT NULL,
                quando TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


# ---------------------------------------------------------------------------
# Funções auxiliares
# ---------------------------------------------------------------------------
def so_digitos(texto):
    return re.sub(r"\D", "", texto or "")


def cpf_valido(cpf):
    digitos = so_digitos(cpf)
    if len(digitos) != 11 or digitos == digitos[0] * 11:
        return False
    for tamanho in (9, 10):
        soma = sum(int(digitos[i]) * (tamanho + 1 - i) for i in range(tamanho))
        if (soma * 10) % 11 % 10 != int(digitos[tamanho]):
            return False
    return True


def para_numero(valor, tipo=float, padrao=0):
    """Converte texto do formulário em número, aceitando vírgula decimal."""
    try:
        numero = float(str(valor).replace(",", "."))
        if not math.isfinite(numero):
            return padrao
        return tipo(numero)
    except (TypeError, ValueError):
        return padrao


def carregar_painel(filtro="", form=None):
    """Dados da tela Operação: lista filtrada + contagem por status."""
    consulta = "SELECT * FROM operacoes"
    parametros = ()
    if filtro in STATUS_VALIDOS:
        consulta += " WHERE status = ?"
        parametros = (filtro,)
    consulta += " ORDER BY dataprevista DESC, horarioprevisto DESC, id DESC"

    with banco() as conexao:
        operacoes = conexao.execute(consulta, parametros).fetchall()
        contagem = conexao.execute(
            "SELECT status, COUNT(*) AS total FROM operacoes GROUP BY status"
        ).fetchall()

    totais = {status: 0 for status in STATUS_VALIDOS}
    for linha in contagem:
        if linha["status"] in totais:
            totais[linha["status"]] = linha["total"]

    return {
        "operacoes": operacoes,
        "totais": totais,
        "total_geral": sum(totais.values()),
        "filtro": filtro if filtro in STATUS_VALIDOS else "",
        "status_validos": STATUS_VALIDOS,
        "form": form or {},
    }


# ---------------------------------------------------------------------------
# Filtros de formatação para os templates
# ---------------------------------------------------------------------------
@app.template_filter("status_classe")
def filtro_status_classe(status):
    return STATUS_CLASSE.get(status, "programada")


@app.template_filter("cpf")
def filtro_cpf(valor):
    d = so_digitos(valor)
    if len(d) != 11:
        return valor
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


@app.template_filter("data_br")
def filtro_data_br(valor):
    try:
        ano, mes, dia = valor.split("-")
        return f"{dia}/{mes}/{ano}"
    except (ValueError, AttributeError):
        return valor


@app.template_filter("num")
def filtro_num(valor):
    """12.5 -> '12,5' | 1234 -> '1.234' | vazio -> '0'"""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        numero = 0.0
    texto = f"{numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return texto.rstrip("0").rstrip(",")


# ---------------------------------------------------------------------------
# Segurança: sessão, perfis, proteção CSRF e cabeçalhos
# ---------------------------------------------------------------------------
def pode_acessar(tela):
    perfil = session.get("perfil")
    return perfil == "admin" or perfil == tela


def tela_inicial():
    destino = TELA_INICIAL.get(session.get("perfil"))
    return url_for(destino) if destino else url_for("login")


def exige_perfil(tela):
    """Só deixa entrar quem fez login e tem acesso à tela."""
    def decorador(funcao):
        @wraps(funcao)
        def interno(*args, **kwargs):
            if "usuario" not in session:
                return redirect(url_for("login"))
            if not pode_acessar(tela):
                abort(403)
            return funcao(*args, **kwargs)
        return interno
    return decorador


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.context_processor
def variaveis_dos_templates():
    return {"csrf_token": csrf_token, "pode_acessar": pode_acessar}


@app.before_request
def antes_de_cada_requisicao():
    if request.endpoint == "static":
        return None

    # Confere se o usuário logado ainda existe e está ativo (e atualiza o perfil)
    if "usuario" in session:
        with banco() as conexao:
            linha = conexao.execute(
                "SELECT perfil FROM usuarios WHERE usuario = ? AND ativo = 1",
                (session["usuario"],),
            ).fetchone()
        if linha is None:
            session.clear()
        else:
            session["perfil"] = linha["perfil"]

    # Todo formulário precisa trazer o código da sessão (proteção CSRF)
    if request.method == "POST":
        enviado = request.form.get("csrf_token", "").encode("utf-8")
        esperado = session.get("csrf", "").encode("utf-8")
        if not esperado or not hmac.compare_digest(enviado, esperado):
            abort(400)
    return None


@app.after_request
def cabecalhos_de_seguranca(resposta):
    resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
    resposta.headers.setdefault("X-Frame-Options", "DENY")
    resposta.headers.setdefault("Referrer-Policy", "same-origin")
    if request.endpoint != "static":
        resposta.headers["Cache-Control"] = "no-store"  # dados pessoais não ficam em cache
    return resposta


@app.errorhandler(400)
def erro_400(_erro):
    return render_template(
        "erro.html", titulo="Pedido inválido",
        mensagem="Sua sessão expirou ou o formulário não é válido. "
                 "Volte, atualize a página e tente de novo.",
    ), 400


@app.errorhandler(403)
def erro_403(_erro):
    return render_template(
        "erro.html", titulo="Sem acesso",
        mensagem="Seu usuário não tem permissão para usar esta tela.",
    ), 403


@app.errorhandler(404)
def erro_404(_erro):
    return render_template(
        "erro.html", titulo="Página não encontrada",
        mensagem="O endereço digitado não existe.",
    ), 404


# ---------------------------------------------------------------------------
# Login e saída
# ---------------------------------------------------------------------------
@app.route("/")
@app.route("/acompanhamento")  # endereço antigo
def inicio():
    if "usuario" in session and session.get("perfil") in TELA_INICIAL:
        return redirect(tela_inicial())
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if "usuario" in session and session.get("perfil") in TELA_INICIAL:
        return redirect(tela_inicial())

    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip().lower()[:64]
        senha = request.form.get("senha", "")

        with banco() as conexao:
            falhas = conexao.execute(
                "SELECT COUNT(*) FROM login_falhas WHERE usuario = ? AND quando > datetime('now', ?)",
                (usuario, f"-{JANELA_BLOQUEIO_MIN} minutes"),
            ).fetchone()[0]
            if falhas >= MAX_FALHAS_LOGIN:
                flash(f"Muitas tentativas erradas. Aguarde {JANELA_BLOQUEIO_MIN} minutos e tente de novo.", "erro")
                return render_template("login.html", usuario=usuario), 429

            linha = conexao.execute(
                "SELECT * FROM usuarios WHERE usuario = ? AND ativo = 1", (usuario,)
            ).fetchone()
            # Confere a senha mesmo se o usuário não existir, para não revelar quais existem
            senha_ok = check_password_hash(linha["senha_hash"] if linha else HASH_FALSO, senha)

            if linha is None or not senha_ok:
                conexao.execute("INSERT INTO login_falhas (usuario) VALUES (?)", (usuario,))
                conexao.execute("DELETE FROM login_falhas WHERE quando < datetime('now', '-1 day')")
                flash("Usuário ou senha incorretos.", "erro")
                return render_template("login.html", usuario=usuario)

            conexao.execute("DELETE FROM login_falhas WHERE usuario = ?", (usuario,))

        session.clear()
        session["usuario"] = linha["usuario"]
        session["perfil"] = linha["perfil"]
        session.permanent = True
        return redirect(tela_inicial())

    return render_template("login.html")


@app.route("/sair", methods=["POST"])
def sair():
    session.clear()
    flash("Você saiu do sistema.", "ok")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Tela 1: OPERAÇÃO (transportadora: cadastra e acompanha tudo)
# ---------------------------------------------------------------------------
@app.route("/operacao")
@exige_perfil("operacao")
def operacao():
    filtro = request.args.get("status", "")
    return render_template("operacao.html", **carregar_painel(filtro))


CAMPOS_OPERACAO = (
    "motorista", "cpf", "placacavalo", "placacarreta",
    "dataprevista", "horarioprevisto", "observacao",
)


def ler_formulario_operacao(form):
    """Lê e valida o formulário de operação. Devolve (dados, erros)."""
    dados = {
        "motorista": form.get("motorista", "").strip(),
        "cpf": so_digitos(form.get("cpf", "")),
        "placacavalo": re.sub(r"[\s-]", "", form.get("placacavalo", "")).upper(),
        "placacarreta": re.sub(r"[\s-]", "", form.get("placacarreta", "")).upper(),
        "dataprevista": form.get("dataprevista", ""),
        "horarioprevisto": form.get("horarioprevisto", ""),
        "observacao": form.get("observacao", "").strip(),
    }

    erros = []
    if not dados["motorista"]:
        erros.append("Informe o nome do motorista.")
    if not cpf_valido(dados["cpf"]):
        erros.append("CPF inválido. Confira os 11 dígitos.")
    if not PLACA_REGEX.match(dados["placacavalo"]):
        erros.append("Placa do cavalo inválida. Use o formato ABC1D23 ou ABC1234.")
    if not PLACA_REGEX.match(dados["placacarreta"]):
        erros.append("Placa da carreta inválida. Use o formato ABC1D23 ou ABC1234.")
    try:
        datetime.strptime(dados["dataprevista"], "%Y-%m-%d")
        datetime.strptime(dados["horarioprevisto"], "%H:%M")
    except ValueError:
        erros.append("Informe a data e o horário previstos.")

    return dados, erros


def buscar_operacao(id):
    with banco() as conexao:
        return conexao.execute(
            "SELECT * FROM operacoes WHERE id = ?", (id,)
        ).fetchone()


@app.route("/operacao/cadastrar", methods=["POST"])
@exige_perfil("operacao")
def cadastrar():
    dados, erros = ler_formulario_operacao(request.form)

    if erros:
        for erro in erros:
            flash(erro, "erro")
        # Reexibe a tela com o que foi digitado, para não perder o preenchimento
        return render_template("operacao.html", **carregar_painel(form=request.form)), 400

    dados["agora"] = agora()
    with banco() as conexao:
        conexao.execute("""
            INSERT INTO operacoes
                (motorista, cpf, placacavalo, placacarreta, dataprevista,
                 horarioprevisto, observacao, status, criadoem, atualizadoem)
            VALUES (:motorista, :cpf, :placacavalo, :placacarreta, :dataprevista,
                    :horarioprevisto, :observacao, 'Programada', :agora, :agora)
        """, dados)

    flash(f"Operação cadastrada: {dados['placacavalo']}, {dados['motorista']}.", "ok")
    return redirect(url_for("operacao"))


@app.route("/operacao/<int:id>/editar", methods=["GET", "POST"])
@exige_perfil("operacao")
def editar(id):
    atual = buscar_operacao(id)
    if atual is None:
        flash("Operação não encontrada.", "erro")
        return redirect(url_for("operacao"))

    if request.method == "POST":
        dados, erros = ler_formulario_operacao(request.form)
        if erros:
            for erro in erros:
                flash(erro, "erro")
            return render_template("editar.html", operacao=atual, form=request.form), 400

        dados["id"] = id
        dados["agora"] = agora()
        with banco() as conexao:
            conexao.execute("""
                UPDATE operacoes
                SET motorista = :motorista, cpf = :cpf,
                    placacavalo = :placacavalo, placacarreta = :placacarreta,
                    dataprevista = :dataprevista, horarioprevisto = :horarioprevisto,
                    observacao = :observacao, atualizadoem = :agora
                WHERE id = :id
            """, dados)
        flash(f"Operação {id} alterada.", "ok")
        return redirect(url_for("operacao"))

    form = {campo: atual[campo] for campo in CAMPOS_OPERACAO}
    return render_template("editar.html", operacao=atual, form=form)


@app.route("/operacao/<int:id>/excluir", methods=["POST"])
@exige_perfil("operacao")
def excluir(id):
    with banco() as conexao:
        cursor = conexao.execute("DELETE FROM operacoes WHERE id = ?", (id,))

    if cursor.rowcount == 0:
        flash("Operação não encontrada.", "erro")
    else:
        flash(f"Operação {id} excluída.", "ok")
    return redirect(url_for("operacao"))


# ---------------------------------------------------------------------------
# Tela 2: CARREGAMENTO (doca: atualiza status, carga e ocorrências)
# ---------------------------------------------------------------------------
@app.route("/carregamento")
@exige_perfil("carregamento")
def carregamento():
    with banco() as conexao:
        operacoes = conexao.execute("""
            SELECT * FROM operacoes
            WHERE status != 'Finalizada'
            ORDER BY dataprevista ASC, horarioprevisto ASC, id ASC
        """).fetchall()
    return render_template(
        "carregamento.html", operacoes=operacoes, status_validos=STATUS_VALIDOS
    )


@app.route("/carregamento/<int:id>/atualizar", methods=["POST"])
@exige_perfil("carregamento")
def atualizar_status(id):
    destino = url_for("carregamento") + f"#op-{id}"

    novo_status = request.form.get("status", "")
    ocorrencia = request.form.get("ocorrencia", "").strip()
    quantidade = max(para_numero(request.form.get("quantidade")), 0)
    peso = max(para_numero(request.form.get("peso")), 0)
    percentual = min(max(para_numero(request.form.get("percentual"), int), 0), 100)

    if novo_status not in STATUS_VALIDOS:
        flash("Status inválido. Escolha uma das opções da lista.", "erro")
        return redirect(destino)
    if novo_status == "Pendência" and not ocorrencia:
        flash("Descreva a ocorrência para registrar uma pendência.", "erro")
        return redirect(destino)

    with banco() as conexao:
        cursor = conexao.execute("""
            UPDATE operacoes
            SET status = ?, quantidade = ?, peso = ?, percentual = ?,
                ocorrencia = ?, atualizadoem = ?
            WHERE id = ?
        """, (novo_status, quantidade, peso, percentual, ocorrencia, agora(), id))

    if cursor.rowcount == 0:
        flash("Operação não encontrada. Atualize a página.", "erro")
    else:
        flash(f"Operação {id} atualizada: {novo_status}.", "ok")
    return redirect(destino)


criar_banco()

if __name__ == "__main__":
    # Uso no seu computador. Na hospedagem, o servidor (gunicorn) carrega "app:app".
    app.run(debug=not PRODUCAO)
