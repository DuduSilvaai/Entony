# 🔥 Entony — Webhook Listener

**Evolution API (WhatsApp Labels) → Meta Conversions API (CAPI)**

Microserviço que escuta eventos de etiquetas do WhatsApp via Evolution API e dispara eventos de conversão para a Meta, fechando o loop de rastreamento de campanhas.

## Fluxo

```
WhatsApp (etiqueta "Pago") → Evolution API → Entony → Meta CAPI
                                                  ↓
                                              Supabase (audit log)
```

## Setup Rápido

### 1. Instalar dependências

```bash
cd Entony
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

### 2. Configurar `.env`

```bash
cp .env.example .env
# Editar .env com suas credenciais
```

### 3. Criar tabela no Supabase

Execute o SQL abaixo no SQL Editor do Supabase:

```sql
CREATE TABLE IF NOT EXISTS meta_conversion_logs (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    phone_hash TEXT NOT NULL,
    event_name TEXT NOT NULL DEFAULT 'Purchase',
    event_value NUMERIC(12,2) DEFAULT 0,
    currency TEXT DEFAULT 'BRL',
    fbclid TEXT,
    lead_id UUID,
    meta_response JSONB,
    tag_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'sent',
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_mcl_created ON meta_conversion_logs(created_at DESC);
CREATE INDEX idx_mcl_status ON meta_conversion_logs(status);
```

### 4. Rodar

```bash
python main.py
# ou
uvicorn main:app --port 9000 --reload
```

### 5. Configurar Webhook na Evolution API

| Campo | Valor |
|:---|:---|
| URL | `https://seu-dominio:9000/webhook/whatsapp` |
| Eventos | `LABELS_EDIT` (ou todos) |
| Header | `apikey: SUA_EVOLUTION_API_KEY` |

## Endpoints

| Método | Rota | Descrição |
|:---|:---|:---|
| `POST` | `/webhook/whatsapp` | Webhook principal (Evolution API) |
| `POST` | `/api/conversions/send` | Envio manual de conversão (teste) |
| `GET` | `/api/conversions/logs` | Logs de auditoria |
| `GET` | `/health` | Health check |

## Deploy (Docker)

```bash
docker build -t entony .
docker run -d --name entony -p 9000:9000 --env-file .env entony
```

## Deploy (PM2)

```bash
pm2 start "uvicorn main:app --host 0.0.0.0 --port 9000" --name entony
pm2 save
```
