"""Gerencia os usuários do CargoFlow.

    python criar_usuario.py                    cria um usuário ou troca a senha de um existente
    python criar_usuario.py --listar           mostra os usuários cadastrados
    python criar_usuario.py --desativar nome   bloqueia o acesso de um usuário

Perfis:
    operacao      acessa só a tela Operação (transportadora)
    carregamento  acessa só a tela Carregamento (doca)
    admin         acessa as duas telas
"""
import argparse
import getpass
import re
import sys

from werkzeug.security import generate_password_hash

from app import PERFIS, agora, banco

USUARIO_REGEX = re.compile(r"^[a-z0-9._-]{3,32}$")


def validar(usuario, perfil, senha):
    if not USUARIO_REGEX.match(usuario):
        return ("O usuário deve ter de 3 a 32 caracteres: letras minúsculas, "
                "números, ponto, hífen ou sublinhado.")
    if perfil not in PERFIS:
        return "Perfil inválido. Use: " + ", ".join(PERFIS) + "."
    if len(senha) < 8:
        return "A senha deve ter pelo menos 8 caracteres."
    return None


def salvar_usuario(usuario, perfil, senha):
    """Cria o usuário ou, se já existir, troca senha e perfil e reativa o acesso."""
    erro = validar(usuario, perfil, senha)
    if erro:
        raise ValueError(erro)
    with banco() as conexao:
        conexao.execute("""
            INSERT INTO usuarios (usuario, senha_hash, perfil, ativo, criadoem)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(usuario) DO UPDATE SET
                senha_hash = excluded.senha_hash,
                perfil = excluded.perfil,
                ativo = 1
        """, (usuario, generate_password_hash(senha), perfil, agora()))


def desativar_usuario(usuario):
    with banco() as conexao:
        cursor = conexao.execute("UPDATE usuarios SET ativo = 0 WHERE usuario = ?", (usuario,))
    return cursor.rowcount > 0


def listar_usuarios():
    with banco() as conexao:
        return conexao.execute(
            "SELECT usuario, perfil, ativo, criadoem FROM usuarios ORDER BY usuario"
        ).fetchall()


def main():
    parser = argparse.ArgumentParser(description="Gerencia os usuários do CargoFlow.")
    parser.add_argument("--listar", action="store_true", help="mostra os usuários")
    parser.add_argument("--desativar", metavar="USUARIO", help="bloqueia o acesso de um usuário")
    args = parser.parse_args()

    if args.listar:
        usuarios = listar_usuarios()
        if not usuarios:
            print("Nenhum usuário cadastrado.")
        for u in usuarios:
            situacao = "ativo" if u["ativo"] else "BLOQUEADO"
            print(f"{u['usuario']:<20} {u['perfil']:<13} {situacao}")
        return

    if args.desativar:
        if desativar_usuario(args.desativar.strip().lower()):
            print("Usuário bloqueado.")
        else:
            print("Usuário não encontrado.")
        return

    usuario = input("Usuário: ").strip().lower()
    perfil = input(f"Perfil ({' / '.join(PERFIS)}): ").strip().lower()
    senha = getpass.getpass("Senha (mínimo 8 caracteres): ")
    if senha != getpass.getpass("Repita a senha: "):
        sys.exit("As senhas não são iguais. Nada foi salvo.")

    try:
        salvar_usuario(usuario, perfil, senha)
    except ValueError as erro:
        sys.exit(str(erro))
    print(f"Pronto! Usuário '{usuario}' salvo com o perfil '{perfil}'.")


if __name__ == "__main__":
    main()
