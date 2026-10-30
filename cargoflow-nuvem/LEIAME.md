# CargoFlow

Sistema com duas telas que usam o mesmo banco de dados:

| Tela | Quem usa | Endereço |
|---|---|---|
| Operação | transportadora (cadastra, edita, acompanha) | `/operacao` |
| Carregamento | doca (atualiza status, carga e ocorrências) | `/carregamento` |

## Rodar no seu computador

```
pip install -r requirements.txt
python criar_usuario.py
python app.py
```

Abra `http://127.0.0.1:5000` e entre com o usuário criado.

## Usuários

Cada pessoa entra com usuário e senha. O **perfil** define a tela:

- `operacao`: só a tela Operação
- `carregamento`: só a tela Carregamento
- `admin`: as duas telas

Comandos úteis:

```
python criar_usuario.py                    # cria usuário ou troca a senha
python criar_usuario.py --listar           # lista os usuários
python criar_usuario.py --desativar nome   # bloqueia um usuário
```

Depois de 5 senhas erradas, o usuário fica bloqueado por 10 minutos.

## Colocar na nuvem

Qualquer hospedagem de Python/Flask serve, desde que tenha:

1. **Disco persistente**, para o banco `cargoflow.db` não ser apagado a cada atualização.
   Aponte o banco para esse disco com a variável `CARGOFLOW_DB`
   (exemplo: `CARGOFLOW_DB=/dados/cargoflow.db`).
2. **https** (a maioria das hospedagens oferece).
3. Comando de inicialização: `gunicorn app:app` (já está no arquivo `Procfile`).

Variáveis de ambiente:

| Variável | Para que serve |
|---|---|
| `CARGOFLOW_PRODUCAO=1` | cookies só por https e modo debug desligado. **Obrigatória na nuvem.** |
| `CARGOFLOW_DB` | caminho do banco, dentro do disco persistente |
| `CARGOFLOW_SECRET` | chave secreta das sessões (um texto longo e aleatório). Se faltar, o sistema cria uma e guarda em `chave_secreta.txt` ao lado do banco |

Depois de publicar, crie os usuários rodando `python criar_usuario.py` no terminal da hospedagem.

## Cuidados

- Nunca compartilhe `cargoflow.db` nem `chave_secreta.txt`.
- O banco guarda nome e CPF de motoristas (dados pessoais, LGPD). Dê acesso só a quem precisa e faça cópia de segurança do `cargoflow.db` com frequência.
- Use uma senha diferente para cada pessoa e bloqueie quem sair da empresa.
