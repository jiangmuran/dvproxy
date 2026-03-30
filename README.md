# DVProxy

A Python FastAPI-based proxy server that translates between Anthropic/OpenAI API formats and DeepVLab's GenAI upstream format.

## Features

- **Multi-API Support**: Accepts requests in Anthropic Messages API, OpenAI Chat Completions, and OpenAI Responses API formats
- **Format Translation**: Automatically converts requests/responses to/from DeepVLab GenAI format
- **Streaming Support**: Full support for SSE streaming with proper event formatting
- **Tool/Function Calling**: Complete support for tool use including streaming of tool arguments
- **Extended Thinking**: Support for Anthropic's thinking blocks and OpenAI reasoning
- **Admin Panel**: Beautiful web-based admin interface with TOTP authentication
- **Usage Tracking**: Detailed logging with cost estimation, model breakdown, and IP tracking
- **API Key Management**: Create, rotate, and manage API keys with quotas

## Quick Start

### Installation

```bash
cd dvproxy
pip install -r requirements.txt
```

### Configuration

Create a `.env` file or set environment variables:

```env
DVPROXY_HOST=0.0.0.0
DVPROXY_PORT=8080
DVPROXY_DEBUG=false
DVPROXY_UPSTREAM_URL=https://api-code.deepvlab.ai
DVPROXY_UPSTREAM_TOKEN=your_upstream_token
DVPROXY_ADMIN_USERNAME=jmr
DVPROXY_TOTP_SECRET=JBSWY3DPEHPK3PXP
```

### Running

```bash
python main.py
```

Or with uvicorn directly:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

## API Endpoints

### Anthropic Messages API

```
POST /v1/messages
```

Accepts standard Anthropic Messages API format:

```json
{
  "model": "claude-3-5-sonnet-20241022",
  "max_tokens": 1024,
  "messages": [{ "role": "user", "content": "Hello!" }],
  "stream": true
}
```

### OpenAI Chat Completions API

```
POST /v1/chat/completions
```

Accepts standard OpenAI Chat Completions format:

```json
{
  "model": "gpt-4",
  "messages": [
    { "role": "system", "content": "You are helpful." },
    { "role": "user", "content": "Hello!" }
  ],
  "stream": true,
  "stream_options": { "include_usage": true }
}
```

### OpenAI Responses API

```
POST /v1/responses
```

Accepts the newer OpenAI Responses API format for agentic workflows:

```json
{
  "model": "gpt-4",
  "input": "Hello!",
  "instructions": "You are helpful.",
  "stream": true
}
```

### Models List

```
GET /v1/models
```

Returns available models in OpenAI format.

## Admin Panel

Access the admin panel at `/admin/login`

### Authentication

The admin panel uses TOTP (Time-based One-Time Password) for secure authentication:

1. Visit `/admin/totp-qr` to get the QR code
2. Scan with your authenticator app (Google Authenticator, Authy, etc.)
3. Login with username and 6-digit TOTP code

### Features

- **Dashboard**: Overview of global statistics, usage trends, model distribution
- **API Keys**: Create, edit, delete, and regenerate API keys
- **Analytics**: Detailed usage analysis with filters by key and time range

## API Key Authentication

Include your API key in requests using one of these methods:

```bash
# Authorization header (Bearer)
curl -H "Authorization: Bearer dvp_your_key_here" ...

# X-API-Key header
curl -H "x-api-key: dvp_your_key_here" ...
```

## Streaming Format

### Anthropic SSE Events

```
event: message_start
data: {"type":"message_start","message":{...}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":10}}

event: message_stop
data: {"type":"message_stop"}
```

### OpenAI SSE Format

```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"delta":{"role":"assistant","content":"Hello"}}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"delta":{"content":"!"}}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","choices":[{"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

## Tool Calling

### Anthropic Format

```json
{
  "tools": [
    {
      "name": "get_weather",
      "description": "Get weather for a location",
      "input_schema": {
        "type": "object",
        "properties": {
          "location": { "type": "string" }
        },
        "required": ["location"]
      }
    }
  ]
}
```

### OpenAI Format

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get weather for a location",
        "parameters": {
          "type": "object",
          "properties": {
            "location": { "type": "string" }
          },
          "required": ["location"]
        }
      }
    }
  ]
}
```

## Project Structure

```
dvproxy/
├── main.py                 # Entry point
├── requirements.txt        # Dependencies
├── app/
│   ├── main.py            # FastAPI application
│   ├── config.py          # Settings
│   ├── models/
│   │   ├── database.py    # SQLAlchemy models
│   │   └── db.py          # Database connection
│   ├── services/
│   │   ├── auth.py        # TOTP & JWT auth
│   │   ├── converter.py   # API format conversion
│   │   ├── upstream.py    # DeepVLab client
│   │   └── usage.py       # Usage tracking
│   ├── routers/
│   │   ├── anthropic.py   # /v1/messages
│   │   ├── openai.py      # /v1/chat/completions, /v1/responses
│   │   └── admin.py       # Admin API
│   ├── templates/
│   │   ├── login.html     # Admin login page
│   │   └── dashboard.html # Admin dashboard
│   └── static/
│       ├── css/admin.css  # Styles
│       └── js/admin.js    # Frontend JS
```

## Environment Variables

| Variable                     | Default                            | Description                       |
| ---------------------------- | ---------------------------------- | --------------------------------- |
| `DVPROXY_HOST`               | `0.0.0.0`                          | Server host                       |
| `DVPROXY_PORT`               | `8080`                             | Server port                       |
| `DVPROXY_DEBUG`              | `false`                            | Enable debug mode                 |
| `DVPROXY_DATABASE_URL`       | `sqlite+aiosqlite:///./dvproxy.db` | Database URL                      |
| `DVPROXY_UPSTREAM_URL`       | `https://api-code.deepvlab.ai`     | Upstream API URL                  |
| `DVPROXY_UPSTREAM_TOKEN`     | -                                  | Upstream API token                |
| `DVPROXY_ADMIN_USERNAME`     | `jmr`                              | Admin username                    |
| `DVPROXY_TOTP_SECRET`        | `JBSWY3DPEHPK3PXP`                 | TOTP secret                       |
| `DVPROXY_JWT_SECRET`         | (random)                           | JWT signing secret                |
| `DVPROXY_JWT_EXPIRE_MINUTES` | `1440`                             | JWT expiration (24h)              |
| `DVPROXY_CLIENT_VERSION`     | `1.0.93`                           | DeepVCode client version to mimic |

## License

MIT

## Contributing

Contributions welcome! Please submit PRs or open issues for bugs/features.
