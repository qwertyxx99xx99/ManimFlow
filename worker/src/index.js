const EXA_URL = "https://demos.exa.ai/chatbot-demo/api/chat/stream";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

const SYSTEM_PROMPT = `You are a precise assistant. Follow the user's instructions exactly.
- Never add language tags to code fences — use plain triple backticks only: \`\`\` not \`\`\`python or \`\`\`bash
- Never use heredocs (cat << EOF). Write files with python3 -c instead.
- Write your entire shell command on a single line — no literal newlines inside the code fence.
- No follow-up suggestions, no lists of questions. Be concise and direct.`;

function err(message, status = 500, type = "server_error") {
  return new Response(
    JSON.stringify({ error: { message, type, code: status } }),
    { status, headers: { ...CORS_HEADERS, "content-type": "application/json" } }
  );
}

function ok(body, headers = {}) {
  return new Response(body, { headers: { ...CORS_HEADERS, ...headers } });
}

function stripFollowups(text) {
  let t = text;
  const fenceIdx = t.indexOf("\n\n```followups");
  if (fenceIdx >= 0) t = t.slice(0, fenceIdx);
  t = t.replace(/\n\[["'].*?["']\s*(?:,\s*["'].*?["'])*\s*\]\s*$/s, "");
  return t.trimEnd();
}

function stripCodeFenceTags(text) {
  return text.replace(/^`{3,}[a-zA-Z_][a-zA-Z0-9_]*$/gm, "```")
             .replace(/^`{4,}$/gm, "```");
}

function cleanCodeFences(text) {
  const lastFenceIdx = text.lastIndexOf("```\n");
  if (lastFenceIdx === -1) return text;
  const before = text.slice(0, lastFenceIdx);
  const after = text.slice(lastFenceIdx + 4);
  const closeIdx = after.lastIndexOf("\n```");
  if (closeIdx === -1) return text;
  let body = after.slice(0, closeIdx);
  const rest = after.slice(closeIdx + 4);
  body = body.replace(/^`+\n?/, "");
  if (/python3\s+-c/.test(body)) {
    body = body.replace(/\n/g, "\\n");
  } else {
    body = body.split("\n")[0];
  }
  return before + "```\n" + body + "\n```" + rest;
}

function rewriteHeredocs(text) {
  return text.replace(
    /cat\s*<<\s*['"]?(\w+)['"]?\s*>\s*(\S+)\n([\s\S]*?)\n\1/g,
    (_, _delim, filepath, content) => {
      const escaped = content.replace(/\\/g, "\\\\").replace(/'/g, "\\'").replace(/\n/g, "\\n");
      return `python3 -c "import pathlib; pathlib.Path('${filepath}').parent.mkdir(parents=True, exist_ok=True); pathlib.Path('${filepath}').write_text('${escaped}')"`;
    }
  );
}

function toExaPayload(messages) {
  const systemMsg = messages.find((m) => m.role === "system");
  const nonSystem = messages.filter((m) => m.role !== "system");
  const last = nonSystem[nonSystem.length - 1];
  const history = nonSystem.slice(0, -1).map((m) => ({
    role: m.role,
    content: m.content,
  }));

  const systemContent = systemMsg ? systemMsg.content : SYSTEM_PROMPT;
  return {
    message: `${systemContent}\n\n${last.content}`,
    history,
    exaEnabled: false,
    model: "google/gemini-2.5-flash",
    searchType: "instant",
  };
}

async function collectExaStream(body) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = null;
  let full = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();

    for (const line of lines) {
      const t = line.trim();
      if (!t) continue;
      if (t.startsWith("event:")) {
        currentEvent = t.slice(6).trim();
      } else if (t.startsWith("data:") && currentEvent === "content") {
        try {
          full += JSON.parse(t.slice(5).trim()).content || "";
        } catch {}
      }
    }
  }

  return cleanCodeFences(rewriteHeredocs(stripCodeFenceTags(stripFollowups(full))));
}

export default {
  async fetch(request) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    if (url.pathname === "/") {
      return ok(JSON.stringify({ status: "ok", name: "manimator" }), {
        "content-type": "application/json",
      });
    }

    if (url.pathname === "/v1/models") {
      return ok(
        JSON.stringify({
          object: "list",
          data: [{ id: "manimator", object: "model", created: 0, owned_by: "exa" }],
        }),
        { "content-type": "application/json" }
      );
    }

    if (url.pathname !== "/v1/chat/completions") {
      return err("Not found", 404, "invalid_request_error");
    }

    if (request.method !== "POST") {
      return err("Method not allowed", 405, "invalid_request_error");
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return err("Invalid JSON body", 400, "invalid_request_error");
    }

    const { messages = [], stream = false } = body;
    if (!messages.length) {
      return err("messages array is required", 400, "invalid_request_error");
    }

    const exaResp = await fetch(EXA_URL, {
      method: "POST",
      headers: { "content-type": "application/json", "accept": "text/event-stream" },
      body: JSON.stringify(toExaPayload(messages)),
    });

    if (!exaResp.ok) {
      return err(`Upstream error: ${exaResp.status}`, 502);
    }

    const id = `chatcmpl-${crypto.randomUUID()}`;
    const model = body.model || "manimator";

    const fullContent = await collectExaStream(exaResp.body);

    if (stream) {
      const enc = new TextEncoder();
      const chunks = [
        { id, object: "chat.completion.chunk", model, choices: [{ index: 0, delta: { role: "assistant", content: fullContent }, finish_reason: null }] },
        { id, object: "chat.completion.chunk", model, choices: [{ index: 0, delta: {}, finish_reason: "stop" }] },
      ];
      const body2 = chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`).join("") + "data: [DONE]\n\n";
      return ok(enc.encode(body2), {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
        "x-accel-buffering": "no",
      });
    }

    return ok(
      JSON.stringify({
        id,
        object: "chat.completion",
        model,
        choices: [{ index: 0, message: { role: "assistant", content: fullContent }, finish_reason: "stop" }],
        usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
      }),
      { "content-type": "application/json" }
    );
  },
};
