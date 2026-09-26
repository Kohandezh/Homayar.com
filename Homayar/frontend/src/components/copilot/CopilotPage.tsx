import { useState, useRef, useEffect } from "react";
import { Send, Bot, User, Sparkles } from "lucide-react";
import api from "@/lib/api";

interface Message {
  role: "user" | "assistant";
  content: string;
  timestamp: string;
}

const suggestions = [
  "What's the current QC score?",
  "Which streams have worst latency?",
  "Compare agent performance",
  "What incidents are open?",
  "Which region has most issues?",
];

export default function CopilotPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const chatRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (chatRef.current) {
      chatRef.current.scrollTop = chatRef.current.scrollHeight;
    }
  }, [messages]);

  const sendMessage = async (text: string) => {
    if (!text.trim() || loading) return;
    const userMsg: Message = {
      role: "user",
      content: text,
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    try {
      const res = await api.post("/copilot/chat", { message: text });
      const aiMsg: Message = {
        role: "assistant",
        content: res.data.response || "I couldn't process that request.",
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, aiMsg]);
    } catch {
      const errMsg: Message = {
        role: "assistant",
        content: "Sorry, I couldn't connect to the backend. Make sure the server is running.",
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, errMsg]);
    } finally {
      setLoading(false);
    }
  };

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      sendMessage(input);
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-56px-48px)] -m-6">
      {/* Chat Area */}
      <div ref={chatRef} className="flex-1 overflow-y-auto p-6 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-4">
            <div className="w-12 h-12 rounded-2xl bg-accent/15 flex items-center justify-center">
              <Sparkles size={24} className="text-accent" />
            </div>
            <div className="text-center">
              <h2 className="text-lg font-semibold text-text-primary">AI Copilot</h2>
              <p className="text-sm text-text-muted mt-1">Ask questions about your voice system</p>
            </div>
            <div className="flex flex-wrap gap-2 max-w-lg mt-2">
              {suggestions.map((s) => (
                <button
                  key={s}
                  onClick={() => sendMessage(s)}
                  className="px-3 py-1.5 rounded-lg bg-bg-secondary border border-border-subtle text-sm text-text-secondary hover:text-text-primary hover:border-border-strong transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`flex gap-3 ${msg.role === "user" ? "justify-end" : ""}`}>
            {msg.role === "assistant" && (
              <div className="w-7 h-7 rounded-lg bg-accent/15 flex items-center justify-center shrink-0">
                <Bot size={14} className="text-accent" />
              </div>
            )}
            <div
              className={`max-w-[70%] rounded-xl px-4 py-2.5 ${
                msg.role === "user"
                  ? "bg-accent text-white"
                  : "bg-bg-secondary border border-border-subtle text-text-primary"
              }`}
            >
              <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
              <p className={`text-[10px] mt-1 ${msg.role === "user" ? "text-white/60" : "text-text-muted"}`}>
                {new Date(msg.timestamp).toLocaleTimeString()}
              </p>
            </div>
            {msg.role === "user" && (
              <div className="w-7 h-7 rounded-lg bg-bg-elevated flex items-center justify-center shrink-0">
                <User size={14} className="text-text-secondary" />
              </div>
            )}
          </div>
        ))}

        {loading && (
          <div className="flex gap-3">
            <div className="w-7 h-7 rounded-lg bg-accent/15 flex items-center justify-center shrink-0">
              <Bot size={14} className="text-accent" />
            </div>
            <div className="bg-bg-secondary border border-border-subtle rounded-xl px-4 py-3">
              <div className="flex gap-1">
                <span className="w-2 h-2 rounded-full bg-text-muted animate-bounce" style={{ animationDelay: "0ms" }} />
                <span className="w-2 h-2 rounded-full bg-text-muted animate-bounce" style={{ animationDelay: "150ms" }} />
                <span className="w-2 h-2 rounded-full bg-text-muted animate-bounce" style={{ animationDelay: "300ms" }} />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-border-subtle p-4">
        <div className="flex items-center gap-3 bg-bg-secondary border border-border-subtle rounded-xl px-4 py-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Ask about your voice system..."
            className="flex-1 bg-transparent text-sm text-text-primary placeholder:text-text-muted outline-none"
          />
          <button
            onClick={() => sendMessage(input)}
            disabled={loading || !input.trim()}
            className="p-1.5 rounded-lg bg-accent text-white disabled:opacity-30 hover:bg-accent-secondary transition-colors"
          >
            <Send size={14} />
          </button>
        </div>
        <p className="text-[10px] text-text-muted mt-1.5 text-center">Cmd+Enter to send</p>
      </div>
    </div>
  );
}
