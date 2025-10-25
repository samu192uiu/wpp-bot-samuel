# WAHA Endpoints e URLs de Painel

As URLs abaixo cobrem tanto o endereço interno usado pela API Flask quanto o painel web disponível para login da sessão WhatsApp em ambiente de desenvolvimento.

| Empresa          | Container WAHA      | URL interna (API Flask)      | URL do painel (host local) |
|------------------|---------------------|------------------------------|-----------------------------|
| empresa1         | `waha_empresa1`     | `http://waha_empresa1:3000`  | `http://localhost:3001`     |
| empresa2         | `waha_empresa2`     | `http://waha_empresa2:3000`  | `http://localhost:3002`     |
| clinica_fisio    | `waha_clinica`      | `http://waha_clinica:3000`   | `http://localhost:3003`     |

> ⚠️ Ajuste as portas externas no `docker-compose.override.yml` caso precise expor em outra porta local.

Para realizar um commit das alterações locais, use o comando abaixo substituindo a mensagem pelo resumo apropriado:

```bash
git add .
git commit -m "Descreva resumidamente as novas atualizações"
```

Em seguida, faça o push para o repositório remoto correspondente:

```bash
git push origin $(git branch --show-current)
```
