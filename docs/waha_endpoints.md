# WAHA Endpoints, dashboards e passos para acesso

As instâncias WAHA só ficam acessíveis (para a API Flask **e** para o painel web) depois que os contêineres estiverem rodando na VPS.
Use o `docker compose` a partir do diretório do projeto para subir tudo:

```bash
docker compose up -d
```

Verifique se os serviços estão de pé:

```bash
docker compose ps
```

Com os contêineres ativos, os serviços internos se conversam pelos hostnames Docker (coluna "URL interna") e você acessa o painel web pelo IP público ou domínio da VPS (coluna "URL do painel"). Substitua `<SEU_IP>` pelo IP/hostname real exposto pela Hostinger. Se preferir trabalhar apenas pelo túnel SSH, crie um port-forward (`ssh -L 3001:localhost:3001 user@seu_ip`).

| Empresa       | Container WAHA  | URL interna (API Flask)     | URL do painel (via navegador)        |
|---------------|-----------------|-----------------------------|--------------------------------------|
| empresa1      | `waha_empresa1` | `http://waha_empresa1:3000` | `http://<SEU_IP>:3001`               |
| empresa2      | `waha_empresa2` | `http://waha_empresa2:3000` | `http://<SEU_IP>:3002`               |
| clinica_fisio | `waha_clinica`  | `http://waha_clinica:3000`  | `http://<SEU_IP>:3003`               |

> ⚠️ Se a porta estiver bloqueada no firewall da VPS, abra-a ou configure o redirecionamento desejado. Para acessar via SSH localmente, use um túnel (ex.: `ssh -L 3003:localhost:3003 user@<SEU_IP>`), então abra `http://localhost:3003` no navegador.

## Checklist rápido

1. Fazer login via SSH na VPS.
2. Dentro do diretório do projeto, executar `docker compose up -d`.
3. Confirmar com `docker compose ps` que `waha_clinica` (e demais) estão `running`.
4. Acessar o painel correspondente usando `http://<SEU_IP>:PORTA` ou via túnel SSH.
5. Escanear o QR Code para autenticar o número de WhatsApp.

## Como configurar o webhook no painel WAHA

O `docker-compose.yml` já injeta a variável `WEBHOOK_URL` em cada container WAHA, apontando para o endpoint dinâmico da API Flask
(`http://api:5000/webhook/<empresa>`). Isso significa que, logo após subir os contêineres, o WAHA passa a chamar o webhook correto sem
você precisar mexer manualmente no painel.

Ainda assim, se quiser conferir ou ajustar diretamente pelo dashboard:

1. Abra o painel da instância desejada (ex.: `http://<SEU_IP>:3003` para `clinica_fisio`).
2. No card da sessão (ícone de engrenagem), clique em **Webhooks**.
3. Defina a URL exatamente como o ambiente docker já usa internamente:

   ```text
   http://api:5000/webhook/clinica_fisio
   ```

   > Troque `clinica_fisio` pelo slug da empresa caso esteja configurando outra instância (ex.: `empresa1`).

4. Mantenha o método padrão (`POST`) e marque os eventos `messages.*` para que o bot receba todas as mensagens e status.
5. Salve/Update. Se a sessão reiniciar, aguarde até voltar para `WORKING` e envie uma mensagem teste no WhatsApp.

> 💡 Dica: se o WAHA estiver rodando em outra máquina ou você preferir usar um endereço público, substitua `http://api:5000` pelo host
> (ou IP) onde a API Flask está acessível a partir do container WAHA. Dentro do mesmo `docker compose`, use sempre o hostname `api` e a
> porta `5000` (porta interna do Gunicorn).
