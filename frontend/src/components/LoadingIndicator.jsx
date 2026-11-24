import React from 'react';
import { Sparkles } from 'lucide-react';

export default function LoadingIndicator() {
  return (
    <div className="flex items-start gap-3 my-4 animate-fade-in">
      <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-brand-purple to-brand-blue flex items-center justify-center shrink-0 shadow-lg shadow-brand-purple/20">
        <Sparkles className="w-4 h-4 text-white animate-pulse" />
      </div>
      <div className="bg-charcoal-900 border border-charcoal-750 rounded-2xl rounded-tl-sm px-4 py-3 shadow-md max-w-[80%]">
        <div className="flex items-center gap-2">
          <span className="text-sm text-slate-300">Lyftr AI is thinking</span>
          <div className="flex items-center gap-1 ml-1">
            <span className="w-1.5 h-1.5 rounded-full bg-brand-purple animate-bounce" style={{ animationDelay: '0ms' }} />
            <span className="w-1.5 h-1.5 rounded-full bg-brand-accent animate-bounce" style={{ animationDelay: '150ms' }} />
            <span className="w-1.5 h-1.5 rounded-full bg-brand-blue animate-bounce" style={{ animationDelay: '300ms' }} />
          </div>
        </div>
      </div>
    </div>
  );
}
