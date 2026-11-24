import React, { useRef, useEffect } from 'react';
import { Sparkles, MessageSquare, Menu, Zap, Shield, Database, Cpu } from 'lucide-react';
import MessageBubble from './MessageBubble';
import LoadingIndicator from './LoadingIndicator';
import ChatInput from './ChatInput';

const SUGGESTED_PROMPTS = [
  {
    title: "Explain Quantum Computing",
    description: "Break down qubits and superposition in simple terms.",
    prompt: "What is quantum computing and how do qubits differ from classical bits?"
  },
  {
    title: "Implement Key Failover",
    description: "Explain how automatic API key fallback works under rate limits.",
    prompt: "How does automatic API key failover ensure high availability in LLM applications?"
  },
  {
    title: "Response Caching Benefits",
    description: "Explore latency reduction and cost savings with caching.",
    prompt: "What are the benefits of LLM response caching, and how does message hashing work?"
  },
  {
    title: "SQLite Database Architecture",
    description: "Review concurrent safety and multi-turn session storage.",
    prompt: "Explain how SQLite handles session isolation and message history in a production chatbot."
  }
];

export default function ChatWindow({
  messages,
  input,
  setInput,
  onSend,
  isLoading,
  error,
  sessionId,
  onToggleSidebar
}) {
  const messagesEndRef = useRef(null);

  // Automatic scrolling to bottom when messages update
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isLoading]);

  return (
    <div className="flex-1 flex flex-col h-full bg-charcoal-950 overflow-hidden relative">
      {/* Top Navbar */}
      <header className="h-14 border-b border-charcoal-800 bg-charcoal-900/60 backdrop-blur-md flex items-center justify-between px-4 z-10 shrink-0">
        <div className="flex items-center gap-3">
          <button
            onClick={onToggleSidebar}
            className="md:hidden p-2 text-slate-400 hover:text-white rounded-lg hover:bg-charcoal-800"
          >
            <Menu className="w-5 h-5" />
          </button>
          <div className="flex items-center gap-2">
            <h2 className="font-semibold text-sm text-slate-200">Conversation</h2>
            <span className="hidden sm:inline-block text-xs text-slate-500 font-mono">
              ({sessionId.slice(0, 8)}...)
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2 text-xs">
          <span className="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-brand-purple/10 text-purple-300 border border-brand-purple/30 font-medium">
            <Zap className="w-3 h-3 text-brand-purple" />
            Key Failover Active
          </span>
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-950/40 text-emerald-300 border border-emerald-800/40 font-medium">
            <Database className="w-3 h-3 text-emerald-400" />
            LiteLLM Cache
          </span>
        </div>
      </header>

      {/* Main chat body */}
      <div className="flex-1 overflow-y-auto px-4 py-6">
        <div className="max-w-3xl mx-auto">
          {messages.length === 0 ? (
            /* Welcome Screen */
            <div className="my-8 md:my-16 text-center animate-fade-in">
              <div className="w-14 h-14 rounded-2xl bg-gradient-to-tr from-brand-purple to-brand-blue flex items-center justify-center mx-auto mb-5 shadow-xl shadow-brand-purple/20">
                <Sparkles className="w-7 h-7 text-white" />
              </div>
              <h1 className="text-2xl md:text-3xl font-bold text-white tracking-tight mb-2">
                Your AI workspace.
              </h1>
              <p className="text-sm md:text-base text-slate-400 max-w-md mx-auto mb-10">
                An intelligent assistant built for reliable conversations.
              </p>

              {/* Suggested Prompt Cards */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-left max-w-2xl mx-auto">
                {SUGGESTED_PROMPTS.map((item, idx) => (
                  <button
                    key={idx}
                    onClick={() => {
                      setInput(item.prompt);
                    }}
                    className="p-4 rounded-xl bg-charcoal-900 border border-charcoal-750 hover:border-brand-purple/50 hover:bg-charcoal-850 transition-all group shadow-sm text-left"
                  >
                    <div className="font-semibold text-xs text-slate-200 group-hover:text-brand-purple transition-colors mb-1">
                      {item.title}
                    </div>
                    <div className="text-[11px] text-slate-400 leading-snug">
                      {item.description}
                    </div>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            /* Messages list */
            <div>
              {messages.map((msg, index) => (
                <MessageBubble key={index} message={msg} />
              ))}
              {isLoading && <LoadingIndicator />}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>
      </div>

      {/* Chat Input Sticky at Bottom */}
      <div className="shrink-0">
        <ChatInput
          input={input}
          setInput={setInput}
          onSend={onSend}
          isLoading={isLoading}
          error={error}
        />
      </div>
    </div>
  );
}
