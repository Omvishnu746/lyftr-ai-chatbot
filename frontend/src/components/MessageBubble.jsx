import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { User, Sparkles, Copy, Check } from 'lucide-react';
import Metadata from './Metadata';

export default function MessageBubble({ message }) {
  const isUser = message.role === 'user';
  const [copied, setCopied] = React.useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (isUser) {
    return (
      <div className="flex justify-end my-3.5">
        <div className="flex items-start gap-2.5 max-w-[85%] md:max-w-[70%]">
          <div className="bg-gradient-to-r from-brand-violet/90 to-brand-purple/90 text-white px-4 py-3 rounded-2xl rounded-tr-sm shadow-md text-sm leading-relaxed border border-brand-purple/30">
            <p className="whitespace-pre-wrap break-words">{message.content}</p>
          </div>
          <div className="w-8 h-8 rounded-lg bg-charcoal-800 border border-charcoal-700 flex items-center justify-center shrink-0 text-slate-300">
            <User className="w-4 h-4" />
          </div>
        </div>
      </div>
    );
  }

  // Assistant message
  return (
    <div
      data-testid="assistant-message"
      className="flex items-start gap-3 my-4 group animate-fade-in"
    >
      <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-brand-purple to-brand-blue flex items-center justify-center shrink-0 shadow-lg shadow-brand-purple/20 text-white">
        <Sparkles className="w-4 h-4" />
      </div>

      <div className="flex-1 bg-charcoal-900 border border-charcoal-750 rounded-2xl rounded-tl-sm px-4 py-3.5 shadow-md max-w-[90%] md:max-w-[85%] relative">
        {/* Copy button */}
        <button
          onClick={handleCopy}
          title="Copy response"
          className="absolute top-2.5 right-2.5 opacity-0 group-hover:opacity-100 p-1.5 rounded-lg bg-charcoal-800 text-slate-400 hover:text-slate-200 transition-all text-xs flex items-center gap-1 border border-charcoal-700"
        >
          {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
        </button>

        {/* Markdown rendered response */}
        <div className="markdown-content text-sm text-slate-100 leading-relaxed pr-6">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {message.content}
          </ReactMarkdown>
        </div>

        {/* Message Metadata */}
        <Metadata metadata={message.metadata} />
      </div>
    </div>
  );
}
