import React, { useRef, useEffect } from 'react';
import { ArrowUp, AlertCircle } from 'lucide-react';

export default function ChatInput({
  input,
  setInput,
  onSend,
  isLoading,
  error,
  placeholder = "Message Lyftr AI... (Shift + Enter for new line)"
}) {
  const textareaRef = useRef(null);

  // Auto-resize textarea height as user types
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 180)}px`;
    }
  }, [input]);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!isLoading && input.trim()) {
        onSend();
      }
    }
  };

  const isSendDisabled = isLoading || !input.trim();

  return (
    <div className="w-full max-w-3xl mx-auto px-4 pb-4">
      {/* Readable error alert */}
      {error && (
        <div className="mb-2 p-3 bg-rose-950/40 border border-rose-800/40 rounded-xl flex items-start gap-2.5 text-xs text-rose-300 animate-fade-in shadow-lg">
          <AlertCircle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
          <div className="flex-1">
            <span className="font-semibold">Error:</span> {error}
          </div>
        </div>
      )}

      <div className="relative flex items-end bg-charcoal-900 border border-charcoal-750 focus-within:border-brand-purple/60 focus-within:ring-1 focus-within:ring-brand-purple/40 rounded-2xl p-2 transition-all shadow-xl">
        <textarea
          ref={textareaRef}
          data-testid="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isLoading}
          rows={1}
          placeholder={placeholder}
          className="w-full bg-transparent text-slate-100 placeholder-slate-500 text-sm focus:outline-none resize-none px-3 py-2 max-h-[180px] overflow-y-auto"
        />

        <button
          data-testid="send-button"
          onClick={onSend}
          disabled={isSendDisabled}
          aria-label="Send message"
          className={`shrink-0 w-9 h-9 rounded-xl flex items-center justify-center transition-all ${
            isSendDisabled
              ? 'bg-charcoal-800 text-slate-600 cursor-not-allowed opacity-50'
              : 'bg-gradient-to-tr from-brand-purple to-brand-blue text-white hover:opacity-90 shadow-md shadow-brand-purple/20'
          }`}
        >
          <ArrowUp className="w-4 h-4 stroke-[2.5]" />
        </button>
      </div>

      <div className="flex items-center justify-between text-[11px] text-slate-500 px-3 pt-1.5">
        <span>Enter sends &bull; Shift + Enter for new line</span>
        <span>LiteLLM + Gemini Failover &bull; SQLite Persistence</span>
      </div>
    </div>
  );
}
